"""Failure-atomic, read-only-input staging for Recipe C.

Only a run-local copy of the public predictor repository is writable.  The
source checkout and both support artifacts are validated and fingerprinted
before and after staging; model checkpoints are copied as regular files from
opened read-only inputs.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path, PurePath, PurePosixPath
from typing import Literal

from biohub.device import DEVICE_SELECTION_ORDER

from .device_patch import PublishedDevicePatch, publish_device_fallback_patch_at
from .protocol import validate_selection_lock, validate_selection_lock_payload
from .source import (
    RECIPE_C_SOURCE,
    validate_source_checkout,
    validate_support_artifacts,
)

_HASH_CHUNK_SIZE = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_TMPFILE = getattr(os, "O_TMPFILE", 0)
_AT_EMPTY_PATH = 0x1000
_RECEIPT_FILENAME = "receipt.json"
_FAILED_FILENAME = "FAILED.json"


class _ReceiptPublishError(OSError):
    """Carry a written receipt identity through a failed publish operation."""

    def __init__(
        self,
        cause: BaseException,
        receipt_identity: tuple[int, int, int],
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.receipt_identity = receipt_identity


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    """Identity and durability evidence for one fresh repository artifact."""

    relative_path: str
    sha256: str
    size: int
    device: int
    inode: int
    fsynced: bool


@dataclass(slots=True)
class _FdLease:
    closed: bool = False

    def ensure_open(self) -> None:
        if self.closed:
            raise ValueError("runtime stage is closed")

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True, slots=True)
class FdBackedPath:
    """A path-shaped view whose filesystem operations stay anchored to an fd."""

    logical_path: Path
    root_descriptor: int
    relative_parts: tuple[str, ...]
    pinned_descriptor: int | None = None
    expected_identity: tuple[int, int, int] | None = None
    expected_sha256: str | None = None
    _lease: _FdLease = dataclass_field(default_factory=_FdLease, repr=False, compare=False)

    def _ensure_open(self) -> None:
        self._lease.ensure_open()
        descriptor = self.pinned_descriptor if self.pinned_descriptor is not None else self.root_descriptor
        if descriptor < 0:
            raise ValueError("runtime stage is closed")

    def __fspath__(self) -> str:
        self._ensure_open()
        descriptor = self.pinned_descriptor if self.pinned_descriptor is not None else self.root_descriptor
        if descriptor >= 0 and os.path.exists("/proc/self/fd"):
            if self.pinned_descriptor is not None:
                return f"/proc/self/fd/{descriptor}"
            suffix = "/".join(self.relative_parts)
            return f"/proc/self/fd/{descriptor}" + (f"/{suffix}" if suffix else "")
        return str(self.logical_path)

    def __str__(self) -> str:
        self._ensure_open()
        return str(self.logical_path)

    def __repr__(self) -> str:
        return f"FdBackedPath({self.logical_path!r})"

    def __eq__(self, other: object) -> bool:
        self._ensure_open()
        if isinstance(other, FdBackedPath):
            other._ensure_open()
            return self.logical_path == other.logical_path
        try:
            return self.logical_path == Path(other)  # type: ignore[arg-type]
        except TypeError:
            return False

    def __truediv__(self, other: str | Path) -> FdBackedPath:
        self._ensure_open()
        relative = Path(other)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("fd-backed path traversal is forbidden")
        parts = self.relative_parts + tuple(part for part in relative.parts if part not in {"", "."})
        return FdBackedPath(self.logical_path / relative, self.root_descriptor, parts, _lease=self._lease)

    @property
    def name(self) -> str:
        self._ensure_open()
        return self.logical_path.name

    @property
    def parent(self) -> FdBackedPath:
        self._ensure_open()
        if not self.relative_parts:
            return self
        return FdBackedPath(
            self.logical_path.parent,
            self.root_descriptor,
            self.relative_parts[:-1],
            _lease=self._lease,
        )

    def _stat(self) -> os.stat_result:
        self._ensure_open()
        if self.pinned_descriptor is not None:
            metadata = os.fstat(self.pinned_descriptor)
            if self.expected_identity is not None and _identity(metadata) != self.expected_identity:
                raise ValueError("fd-backed file identity changed")
            return metadata
        if not self.relative_parts:
            return os.fstat(self.root_descriptor)
        parent, name = _open_relative_parent(
            self.root_descriptor,
            Path(*self.relative_parts),
            "fd-backed path",
        )
        try:
            return os.stat(name, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)

    def stat(self) -> os.stat_result:
        return self._stat()

    def exists(self) -> bool:
        self._ensure_open()
        try:
            self._stat()
        except FileNotFoundError:
            return False
        return True

    def is_file(self) -> bool:
        return stat.S_ISREG(self._stat().st_mode)

    def is_dir(self) -> bool:
        return stat.S_ISDIR(self._stat().st_mode)

    def is_symlink(self) -> bool:
        return stat.S_ISLNK(self._stat().st_mode)

    def read_bytes(self) -> bytes:
        self._ensure_open()
        if self.pinned_descriptor is not None:
            payload, metadata = _read_descriptor_stable(self.pinned_descriptor, "fd-backed file")
            if self.expected_identity is not None and _identity(metadata) != self.expected_identity:
                raise ValueError("fd-backed file identity changed")
            if self.expected_sha256 is not None and hashlib.sha256(payload).hexdigest() != self.expected_sha256:
                raise ValueError("fd-backed file digest changed")
            return payload
        if not self.relative_parts:
            raise IsADirectoryError(str(self.logical_path))
        parent, name = _open_relative_parent(
            self.root_descriptor,
            Path(*self.relative_parts),
            "fd-backed file",
        )
        try:
            payload, _ = _read_regular_stable_at(parent, name, "fd-backed file")
            return payload
        finally:
            os.close(parent)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def resolve(self, strict: bool = False) -> Path:
        """Return a lexical label; never resolve through the mutable pathname."""

        self._ensure_open()
        if strict and not self.exists():
            raise FileNotFoundError(self.logical_path)
        return self.logical_path


@dataclass(frozen=True, slots=True)
class RuntimeStage:
    """Staged runtime with fd-backed consumer boundaries."""

    _stage_root_path: Path
    _source_root_path: Path
    _config_relative: tuple[str, ...]
    _predictor_relative: tuple[str, ...]
    _primary_checkpoint_relative: tuple[str, ...]
    _secondary_checkpoint_relative: tuple[str, ...]
    parent_descriptor: int
    stage_descriptor: int
    repo_descriptor: int
    source_descriptor: int
    predictor_descriptor: int
    predictor_identity: tuple[int, int, int]
    selection_lock_id: str
    predictor_sha256_before: str
    predictor_sha256_after: str
    resolved_device_candidates: tuple[str, ...]
    receipt: dict[str, object]
    _lease: _FdLease = dataclass_field(default_factory=_FdLease, repr=False, compare=False)

    def _ensure_open(self) -> None:
        self._lease.ensure_open()

    @property
    def stage_root(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(self._stage_root_path, self.stage_descriptor, (), _lease=self._lease)

    @property
    def destination(self) -> FdBackedPath:
        return self.stage_root

    @property
    def repo_dir(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(self._stage_root_path / "repo", self.repo_descriptor, (), _lease=self._lease)

    @property
    def repo_fd(self) -> int:
        """Return the borrowed repository descriptor; consumers must not close it."""

        self._ensure_open()
        return self.repo_descriptor

    def read_repo_bytes(self, relative: PurePath) -> bytes:
        self._ensure_open()
        parts = _repo_publish_parts(relative)
        parent_descriptor, name = _open_relative_parent(
            self.repo_descriptor,
            PurePosixPath(*parts),
            "repository artifact",
        )
        try:
            payload, _ = _read_regular_stable_at(parent_descriptor, name, "repository artifact")
            return payload
        finally:
            os.close(parent_descriptor)

    def publish_repo_bytes(
        self,
        relative: PurePath,
        payload: bytes,
        *,
        expected: Literal["absent"],
    ) -> PublishReceipt:
        self._ensure_open()
        if expected != "absent":
            raise ValueError("repository publish expected must be absent")
        parts = _repo_publish_parts(relative)
        return _publish_repo_bytes_at(self.repo_descriptor, parts, payload)

    @property
    def weights_root(self) -> FdBackedPath:
        self._ensure_open()
        return self.repo_dir / "weights"

    @property
    def source_root(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(self._source_root_path, self.source_descriptor, (), _lease=self._lease)

    @property
    def staged_config(self) -> FdBackedPath:
        self._ensure_open()
        return self.stage_root / Path(*self._config_relative)

    @property
    def config_path(self) -> FdBackedPath:
        return self.staged_config

    @property
    def predictor_path(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(
            self._stage_root_path / "repo" / Path(*self._predictor_relative),
            self.repo_descriptor,
            self._predictor_relative,
            self.predictor_descriptor,
            self.predictor_identity,
            self.predictor_sha256_after,
            self._lease,
        )

    @property
    def primary_checkpoint_path(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(
            self._stage_root_path / "repo" / "weights" / Path(*self._primary_checkpoint_relative),
            self.repo_descriptor,
            ("weights", *self._primary_checkpoint_relative),
            _lease=self._lease,
        )

    @property
    def secondary_checkpoint_path(self) -> FdBackedPath:
        self._ensure_open()
        return FdBackedPath(
            self._stage_root_path / "repo" / "weights" / Path(*self._secondary_checkpoint_relative),
            self.repo_descriptor,
            ("weights", *self._secondary_checkpoint_relative),
            _lease=self._lease,
        )

    @property
    def receipt_path(self) -> FdBackedPath:
        self._ensure_open()
        return self.stage_root / _RECEIPT_FILENAME

    def close(self) -> None:
        self._lease.close()
        first_error: BaseException | None = None
        for field in (
            "predictor_descriptor",
            "repo_descriptor",
            "source_descriptor",
            "stage_descriptor",
            "parent_descriptor",
        ):
            descriptor = getattr(self, field)
            object.__setattr__(self, field, -1)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

    def __enter__(self) -> RuntimeStage:
        self._ensure_open()
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()

    @property
    def predictor_sha256_preimage(self) -> str:
        return self.predictor_sha256_before

    @property
    def predictor_sha256_postimage(self) -> str:
        return self.predictor_sha256_after

    @property
    def predictor_sha256_pre_patch(self) -> str:
        return self.predictor_sha256_before

    @property
    def predictor_sha256_post_patch(self) -> str:
        return self.predictor_sha256_after

    @property
    def staged_config_path(self) -> FdBackedPath:
        return self.staged_config

    @property
    def device_candidates(self) -> tuple[str, ...]:
        return self.resolved_device_candidates

    @property
    def receipt_identity(self) -> dict[str, object]:
        return self.receipt


def _repo_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} relative path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("repo",):
        raise ValueError(f"{label} path must be under repo")
    return relative.relative_to("repo")


def _repo_publish_parts(relative: PurePath) -> tuple[str, ...]:
    if not isinstance(relative, PurePosixPath):
        raise ValueError("repository artifact path must be a relative PurePosixPath")
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("repository artifact path must be relative to repo")
    parts = tuple(relative.parts)
    if any(part in {"", "."} for part in parts):
        raise ValueError("repository artifact path must name an entry")
    return parts


def _validated_root(root: Path, label: str) -> tuple[Path, tuple[int, int]]:
    root = Path(root)
    try:
        metadata = os.lstat(root)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} root is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} root symlink is forbidden")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} root must be a directory")
    try:
        resolved = root.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(f"{label} root could not be resolved") from exc
    if resolved != root.absolute():
        raise ValueError(f"{label} root has a symlinked parent")
    return root, (metadata.st_ino, metadata.st_dev)


def _root_path(root: Path, label: str) -> Path:
    return _validated_root(root, label)[0]


def _relative_parts(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, PurePath):
        relative = value
    elif isinstance(value, str) and value.strip():
        relative = Path(value)
    else:
        raise ValueError(f"{label} relative path is missing")
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"{label} path must be relative")
    parts = tuple(part for part in relative.parts if part not in {"", "."})
    if not parts:
        raise ValueError(f"{label} path must name an entry")
    return parts


def _open_directory_path(path: Path, label: str) -> int:
    """Open a directory and every ancestor without following symlinks."""

    path = Path(path)
    if any(part == ".." for part in path.parts):
        raise ValueError(f"{label} path traversal is forbidden")
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    if path.is_absolute():
        anchor = path.anchor
        parts = path.parts[1:]
    else:
        anchor = "."
        parts = path.parts
    try:
        descriptor = os.open(anchor, flags)
    except OSError as exc:
        raise ValueError(f"{label} root is missing or not a directory") from exc
    try:
        for part in parts:
            if part in {"", "."}:
                continue
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"{label} root is missing") from exc
            except OSError as exc:
                raise ValueError(f"{label} root has a symlinked or non-directory ancestor") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_artifact_roots(
    roots: tuple[tuple[Path, str, tuple[int, int]], ...],
) -> tuple[int, ...]:
    """Open all immutable roots, closing already-open roots on failure."""

    descriptors: list[int] = []
    try:
        for root, label, expected_identity in roots:
            descriptor = _open_directory_path(root, label)
            try:
                metadata = os.fstat(descriptor)
                if (metadata.st_ino, metadata.st_dev) != expected_identity:
                    raise ValueError(f"{label} root changed before descriptor acquisition")
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    return tuple(descriptors)


def _descriptor_root_path(descriptor: int, label: str) -> Path:
    """Expose an opened root to legacy validators without re-resolving its name."""

    proc_fd = Path("/proc/self/fd")
    if not proc_fd.is_dir():
        raise ValueError(f"{label} fd-backed validation is unavailable")
    return proc_fd / str(descriptor)


def _open_relative_directory(root_descriptor: int, relative: object, label: str) -> int:
    parts = _relative_parts(relative, label) if relative not in {Path("."), "."} else ()
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"{label} directory is missing") from exc
            except OSError as exc:
                raise ValueError(f"{label} directory contains a symlink or non-directory") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_relative_parent(root_descriptor: int, relative: object, label: str) -> tuple[int, str]:
    parts = _relative_parts(relative, label)
    parent_parts = parts[:-1]
    parent = _open_relative_directory(root_descriptor, Path(*parent_parts), label) if parent_parts else os.dup(
        root_descriptor,
    )
    return parent, parts[-1]


def _open_staging_inputs(
    source_descriptor: int,
    primary_descriptor: int,
    secondary_descriptor: int,
    config_relative: Path,
    predictor_relative: Path,
    primary_checkpoint_relative: Path,
    secondary_checkpoint_relative: Path,
) -> tuple[tuple[int, str], tuple[int, str], tuple[int, str], tuple[int, str], int]:
    """Open all pinned input parents and the primary repo with leak-safe cleanup."""

    opened: list[int] = []

    def parent(root_descriptor: int, relative: Path, label: str) -> tuple[int, str]:
        descriptor, name = _open_relative_parent(root_descriptor, relative, label)
        opened.append(descriptor)
        return descriptor, name

    try:
        source_config = parent(source_descriptor, config_relative, "source config")
        predictor = parent(primary_descriptor, predictor_relative, "predictor")
        primary_checkpoint = parent(
            primary_descriptor,
            primary_checkpoint_relative,
            "primary checkpoint",
        )
        secondary_checkpoint = parent(
            secondary_descriptor,
            secondary_checkpoint_relative,
            "secondary checkpoint",
        )
        primary_repo = _open_relative_directory(primary_descriptor, Path("repo"), "primary support repo")
        opened.append(primary_repo)
    except BaseException:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise
    return source_config, predictor, primary_checkpoint, secondary_checkpoint, primary_repo


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise OSError("directory fsync failed") from exc


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_ino, metadata.st_dev, metadata.st_size


def _read_regular_stable_at(parent_descriptor: int, name: str, label: str) -> tuple[bytes, os.stat_result]:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} file is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} symlink is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    try:
        descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError(f"{label} could not be opened without following symlinks") from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(metadata):
            raise ValueError(f"{label} changed before it was read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or _identity(after) != _identity(current):
        raise ValueError(f"{label} changed while it was read")
    return b"".join(chunks), current


def _read_descriptor_stable(descriptor: int, label: str) -> tuple[bytes, os.stat_result]:
    """Read a retained regular-file fd without reopening its pathname."""

    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file")
    chunks: list[bytes] = []
    offset = 0
    while True:
        chunk = os.pread(descriptor, _HASH_CHUNK_SIZE, offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if _identity(before) != _identity(after):
        raise ValueError(f"{label} changed while it was read")
    return b"".join(chunks), after


def _sha256_regular_at(parent_descriptor: int, name: str, label: str) -> tuple[str, os.stat_result]:
    payload, metadata = _read_regular_stable_at(parent_descriptor, name, label)
    return hashlib.sha256(payload).hexdigest(), metadata


def _sha256_descriptor(descriptor: int, label: str) -> tuple[str, os.stat_result]:
    payload, metadata = _read_descriptor_stable(descriptor, label)
    return hashlib.sha256(payload).hexdigest(), metadata


def _open_publish_temp_at(parent_descriptor: int) -> tuple[int, str | None, tuple[int, int]]:
    if _O_TMPFILE:
        try:
            descriptor = os.open(
                ".",
                os.O_RDWR | _O_TMPFILE | _NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            metadata = os.fstat(descriptor)
            return descriptor, None, (metadata.st_ino, metadata.st_dev)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise

    temporary_name = f".recipe-c-repo-publish.{os.getpid()}.{secrets.token_hex(24)}"
    descriptor = os.open(
        temporary_name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    metadata = os.fstat(descriptor)
    return descriptor, temporary_name, (metadata.st_ino, metadata.st_dev)


def _link_publish_fd_at(descriptor: int, parent_descriptor: int, name: str) -> None:
    try:
        linkat = ctypes.CDLL(None, use_errno=True).linkat
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOTSUP, "fd-backed repository publication is unavailable") from exc
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    result = linkat(
        descriptor,
        b"",
        parent_descriptor,
        os.fsencode(name),
        _AT_EMPTY_PATH,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), name)


def _cleanup_owned_publish_temp_at(
    parent_descriptor: int,
    name: str,
    owner: tuple[int, int],
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if (current.st_ino, current.st_dev) != owner:
        return False
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return True
    return True


def _unlink_owned_publish_entry_at(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if (current.st_ino, current.st_dev) != expected_identity[:2]:
        return False
    os.unlink(name, dir_fd=parent_descriptor)
    return True


def _assert_published_repo_entry_at(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("repository publication disappeared") from exc
    if not stat.S_ISREG(metadata.st_mode) or _identity(metadata) != expected_identity:
        raise ValueError("repository publication identity changed")
    return metadata


def _publish_repo_bytes_at(
    root_descriptor: int,
    relative_parts: tuple[str, ...],
    payload: bytes,
) -> PublishReceipt:
    if not isinstance(payload, bytes):
        raise TypeError("repository publication payload must be bytes")
    relative = PurePosixPath(*relative_parts)
    parent_descriptor, name = _open_relative_parent(root_descriptor, relative, "repository artifact")
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    temporary_owner: tuple[int, int] | None = None
    published_identity: tuple[int, int, int] | None = None
    committed = False
    primary_error: BaseException | None = None
    expected_digest = hashlib.sha256(payload).hexdigest()
    try:
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("repository final entry already exists")

        temporary_descriptor, temporary_name, temporary_owner = _open_publish_temp_at(parent_descriptor)
        written = 0
        while written < len(payload):
            count = os.write(temporary_descriptor, payload[written:])
            if count <= 0:
                raise OSError("repository publication made no write progress")
            written += count
        os.fchmod(temporary_descriptor, 0o600)
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if _identity(temporary_metadata) != (
            temporary_metadata.st_ino,
            temporary_metadata.st_dev,
            len(payload),
        ):
            raise ValueError("repository temporary identity changed")

        try:
            _link_publish_fd_at(temporary_descriptor, parent_descriptor, name)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise FileExistsError("repository final entry already exists") from exc
            raise

        published_identity = _identity(temporary_metadata)
        _assert_published_repo_entry_at(parent_descriptor, name, published_identity)
        digest, hashed_metadata = _sha256_descriptor(temporary_descriptor, "repository publication")
        if digest != expected_digest or _identity(hashed_metadata) != published_identity:
            raise ValueError("repository publication digest changed")
        _fsync_directory(parent_descriptor)
        _assert_published_repo_entry_at(parent_descriptor, name, published_identity)

        if temporary_name is not None:
            if temporary_owner is None or not _cleanup_owned_publish_temp_at(
                parent_descriptor,
                temporary_name,
                temporary_owner,
            ):
                raise OSError("repository temporary ownership changed during cleanup")
            _fsync_directory(parent_descriptor)
        _assert_published_repo_entry_at(parent_descriptor, name, published_identity)
        committed = True
        return PublishReceipt(
            relative_path=relative.as_posix(),
            sha256=expected_digest,
            size=len(payload),
            device=published_identity[1],
            inode=published_identity[0],
            fsynced=True,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        if not committed and published_identity is not None:
            try:
                if _unlink_owned_publish_entry_at(parent_descriptor, name, published_identity):
                    _fsync_directory(parent_descriptor)
            except BaseException as exc:
                cleanup_error = exc
        if temporary_name is not None and temporary_owner is not None:
            try:
                if not _cleanup_owned_publish_temp_at(parent_descriptor, temporary_name, temporary_owner):
                    raise OSError("repository temporary ownership changed during cleanup")
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        try:
            os.close(parent_descriptor)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if cleanup_error is not None:
            if primary_error is not None:
                primary_error.add_note(f"repository publication cleanup failed: {cleanup_error!r}")
            else:
                raise cleanup_error


def _snapshot_tree_fd(root_descriptor: int, *, reject_symlinks: bool = False) -> dict[str, tuple[object, ...]]:
    """Snapshot an already-open artifact tree; no path is re-resolved."""

    root_metadata = os.fstat(root_descriptor)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("artifact root must be a directory")
    snapshot: dict[str, tuple[object, ...]] = {}

    def visit(directory_descriptor: int, relative_directory: Path) -> None:
        try:
            with os.scandir(directory_descriptor) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError as exc:
            raise ValueError("artifact tree could not be enumerated") from exc
        for name in names:
            relative = (relative_directory / name).as_posix()
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                # A symlink target cannot be proven to remain inside an
                # opened root across a rename race.  Fail closed for every
                # input symlink, including one that appears internal.
                if reject_symlinks or stat.S_ISLNK(metadata.st_mode):
                    raise ValueError(f"artifact contains a symlink: {relative}")
            elif stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = (
                    "directory",
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_mtime_ns,
                )
                child = os.open(
                    name,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
                try:
                    visit(child, relative_directory / name)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                digest, stable = _sha256_regular_at(
                    directory_descriptor,
                    name,
                    f"artifact file {relative}",
                )
                snapshot[relative] = (
                    "file",
                    stable.st_ino,
                    stable.st_dev,
                    stable.st_size,
                    stable.st_mtime_ns,
                    digest,
                )
            else:
                raise ValueError(f"artifact contains an unsupported file: {relative}")

    visit(root_descriptor, Path())
    return snapshot


def _read_regular_stable(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    parent_descriptor = _open_secure_directory(path.parent, create_missing=False)
    try:
        return _read_regular_stable_at(parent_descriptor, path.name, label)
    finally:
        os.close(parent_descriptor)


def _sha256_regular(path: Path, label: str) -> tuple[str, os.stat_result]:
    payload, metadata = _read_regular_stable(path, label)
    return hashlib.sha256(payload).hexdigest(), metadata


def _snapshot_tree(root: Path, *, reject_symlinks: bool = False) -> dict[str, tuple[object, ...]]:
    """Return a symlink-aware snapshot without storing absolute path values."""

    root = _root_path(root, "artifact")
    descriptor = _open_directory_path(root, "artifact")
    try:
        return _snapshot_tree_fd(descriptor, reject_symlinks=reject_symlinks)
    finally:
        os.close(descriptor)


def _open_secure_directory(path: Path, *, create_missing: bool) -> int:
    path = Path(path)
    if any(part == ".." for part in path.parts):
        raise ValueError("destination parent traversal is forbidden")
    if path.is_absolute():
        try:
            descriptor = os.open(path.anchor, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        except OSError as exc:
            raise ValueError("destination parent symlink or non-directory") from exc
        parts = path.parts[1:]
    else:
        try:
            descriptor = os.open(".", os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        except OSError as exc:
            raise ValueError("destination parent symlink or non-directory") from exc
        parts = path.parts
    try:
        for part in parts:
            if part in {"", "."}:
                continue
            try:
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_missing:
                    raise
                os.mkdir(part, 0o755, dir_fd=descriptor)
                _fsync_directory(descriptor)
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
            except OSError as exc:
                raise ValueError("destination parent symlink or non-directory") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _claim_destination(destination: Path) -> tuple[Path, int, int]:
    destination = Path(destination)
    if destination.name in {"", ".", ".."}:
        raise ValueError("destination must name a directory")
    parent_descriptor = _open_secure_directory(destination.parent, create_missing=True)
    stage_descriptor: int | None = None
    try:
        try:
            os.mkdir(destination.name, 0o755, dir_fd=parent_descriptor)
        except FileExistsError as exc:
            raise FileExistsError("destination already exists") from exc
        stage_descriptor = os.open(
            destination.name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        return destination, parent_descriptor, stage_descriptor
    except BaseException:
        if stage_descriptor is not None:
            os.close(stage_descriptor)
        os.close(parent_descriptor)
        raise


def _assert_claimed_destination(parent_descriptor: int, name: str, stage_descriptor: int) -> None:
    """Verify the claimed entry through the already-open parent descriptor."""

    opened = os.fstat(stage_descriptor)
    current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_ino, current.st_dev) != (
        opened.st_ino,
        opened.st_dev,
    ):
        raise ValueError("destination changed after exclusive claim")


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    parent_descriptor = _open_secure_directory(path.parent, create_missing=False)
    try:
        _write_json_exclusive_at(parent_descriptor, path.name, payload)
    finally:
        os.close(parent_descriptor)


def _write_json_exclusive_at(
    parent_descriptor: int,
    name: str,
    payload: Mapping[str, object],
) -> tuple[int, int, int]:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    descriptor: int | None = None
    owner: tuple[int, int] | None = None
    identity: tuple[int, int, int] | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        owner = opened.st_ino, opened.st_dev
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise OSError("receipt write made no progress")
            written += count
        os.fsync(descriptor)
        _fsync_directory(parent_descriptor)
        identity = _identity(os.fstat(descriptor))
    except BaseException:
        if owner is not None:
            try:
                current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                if (current.st_ino, current.st_dev) == owner:
                    os.unlink(name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
    assert identity is not None
    return identity


def _unlink_owned_at(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if _identity(current) != expected_identity:
        raise ValueError(f"{name} ownership changed before cleanup")
    os.unlink(name, dir_fd=parent_descriptor)
    _fsync_directory(parent_descriptor)


def _unlink_owned_inode_at(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int, int],
) -> None:
    """Retry receipt cleanup using only the retained owner inode/dev identity."""

    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_ino, current.st_dev) != expected_identity[:2]:
        raise ValueError(f"{name} ownership changed before cleanup retry")
    os.unlink(name, dir_fd=parent_descriptor)


def _publish_ready_receipt_at(
    stage_descriptor: int,
    parent_descriptor: int,
    receipt: Mapping[str, object],
) -> tuple[int, int, int]:
    """Publish READY only after the receipt and both directory entries are durable."""

    identity = _write_json_exclusive_at(stage_descriptor, _RECEIPT_FILENAME, receipt)
    try:
        _fsync_directory(stage_descriptor)
        _fsync_directory(parent_descriptor)
    except BaseException as publish_error:
        try:
            _unlink_owned_at(stage_descriptor, _RECEIPT_FILENAME, identity)
        except BaseException as cleanup_error:
            failure = _ReceiptPublishError(publish_error, identity)
            failure.add_note(f"receipt cleanup failed: {cleanup_error!r}")
            raise failure from publish_error
        raise
    return identity


def _mark_failed(stage_root: Path, selection_lock_id: str | None, exc: BaseException) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "FAILED",
        "selection_lock_id": selection_lock_id,
        "error_type": type(exc).__name__,
        "reusable": False,
    }
    try:
        _write_json_exclusive(stage_root / _FAILED_FILENAME, payload)
    except OSError:
        pass


def _mark_failed_at(
    stage_descriptor: int,
    selection_lock_id: str | None,
    exc: BaseException,
    receipt_identity: tuple[int, int, int] | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "FAILED",
        "selection_lock_id": selection_lock_id,
        "error_type": type(exc).__name__,
        "reusable": False,
    }
    try:
        if receipt_identity is not None:
            try:
                _unlink_owned_at(stage_descriptor, _RECEIPT_FILENAME, receipt_identity)
            except (OSError, ValueError):
                _unlink_owned_inode_at(stage_descriptor, _RECEIPT_FILENAME, receipt_identity)
        try:
            os.stat(_RECEIPT_FILENAME, dir_fd=stage_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return
        _write_json_exclusive_at(stage_descriptor, _FAILED_FILENAME, payload)
        _fsync_directory(stage_descriptor)
    except (OSError, ValueError):
        pass


def _copy_regular_file_at(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
    label: str,
) -> None:
    payload, source_metadata = _read_regular_stable_at(source_parent_descriptor, source_name, label)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            stat.S_IMODE(source_metadata.st_mode),
            dir_fd=destination_parent_descriptor,
        )
    except FileExistsError as exc:
        raise FileExistsError("staged destination file already exists") from exc
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("staging copy made no write progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(destination_parent_descriptor)
    after = os.stat(source_name, dir_fd=source_parent_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(after.st_mode) or _identity(after) != _identity(source_metadata):
        raise ValueError(f"{label} changed during copy")


def _ensure_directory_at(root_descriptor: int, relative: object, label: str) -> int:
    parts = () if relative in {Path("."), "."} else _relative_parts(relative, label)
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o755, dir_fd=descriptor)
            except FileExistsError:
                pass
            _fsync_directory(descriptor)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _copy_tree_at(
    source_descriptor: int,
    destination_parent_descriptor: int,
    destination_name: str,
    *,
    reject_symlinks: bool,
    exclude_relative: tuple[str, ...] | None = None,
    relative_parts: tuple[str, ...] = (),
) -> int:
    try:
        os.mkdir(destination_name, 0o755, dir_fd=destination_parent_descriptor)
    except FileExistsError as exc:
        raise FileExistsError("staged tree already exists") from exc
    destination_descriptor = os.open(
        destination_name,
        os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
        dir_fd=destination_parent_descriptor,
    )
    try:
        with os.scandir(source_descriptor) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            source_relative = (*relative_parts, name)
            if exclude_relative is not None and source_relative == exclude_relative:
                continue
            metadata = os.stat(name, dir_fd=source_descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_source = os.open(
                    name,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                    dir_fd=source_descriptor,
                )
                child_destination: int | None = None
                try:
                    child_destination = _copy_tree_at(
                        child_source,
                        destination_descriptor,
                        name,
                        reject_symlinks=reject_symlinks,
                        exclude_relative=exclude_relative,
                        relative_parts=source_relative,
                    )
                finally:
                    os.close(child_source)
                    if child_destination is not None:
                        os.close(child_destination)
                _fsync_directory(destination_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                _copy_regular_file_at(
                    source_descriptor,
                    name,
                    destination_descriptor,
                    name,
                    f"support repo file {name}",
                )
            else:
                raise ValueError("support repo contains a symlink or unsupported file")
        _fsync_directory(destination_descriptor)
        return destination_descriptor
    except BaseException:
        os.close(destination_descriptor)
        raise


def _copy_regular_file(source: Path, destination: Path, label: str) -> None:
    source_parent_descriptor = _open_secure_directory(source.parent, create_missing=False)
    destination_parent_descriptor = _open_secure_directory(destination.parent, create_missing=True)
    try:
        _copy_regular_file_at(
            source_parent_descriptor,
            source.name,
            destination_parent_descriptor,
            destination.name,
            label,
        )
    finally:
        os.close(source_parent_descriptor)
        os.close(destination_parent_descriptor)


def _copy_tree(source: Path, destination: Path, *, reject_symlinks: bool) -> None:
    source_descriptor = _open_directory_path(source, "source")
    destination_parent_descriptor = _open_secure_directory(destination.parent, create_missing=True)
    try:
        destination_descriptor = _copy_tree_at(
            source_descriptor,
            destination_parent_descriptor,
            destination.name,
            reject_symlinks=reject_symlinks,
        )
        os.close(destination_descriptor)
    finally:
        os.close(source_descriptor)
        os.close(destination_parent_descriptor)


def _lock_payload(selection_lock: Mapping[str, object] | Path) -> dict[str, object]:
    if isinstance(selection_lock, Path):
        return validate_selection_lock(selection_lock)
    if isinstance(selection_lock, Mapping):
        return validate_selection_lock_payload(selection_lock)
    raise TypeError("selection_lock must be a mapping or lock path")


def _assert_lock_identity(
    lock: Mapping[str, object],
    source_receipt: Mapping[str, object],
    support_receipt: Mapping[str, object],
) -> str:
    lock_id = lock.get("selection_lock_id")
    if not isinstance(lock_id, str) or not lock_id:
        raise ValueError("selection lock ID is missing")
    expected_fields = {
        "source_commit": source_receipt.get("source_commit"),
        "source_config_sha256": source_receipt.get("config_sha256"),
        "config_sha256": source_receipt.get("config_sha256"),
        "predictor_sha256": support_receipt.get("predictor_sha256"),
        "primary_checkpoint_sha256": support_receipt.get("primary_checkpoint_sha256"),
        "secondary_checkpoint_sha256": support_receipt.get("secondary_checkpoint_sha256"),
        "secondary_staging_relative_path": support_receipt.get("secondary_staging_relative_path"),
    }
    for field, expected in expected_fields.items():
        if expected is not None and lock.get(field) != expected:
            raise ValueError(f"selection lock {field} does not match validated artifact")
    return lock_id


def stage_recipe_c_runtime(
    source_root: Path,
    primary_support_root: Path,
    secondary_support_root: Path,
    destination: Path,
    selection_lock: Mapping[str, object] | Path,
) -> RuntimeStage:
    """Validate immutable inputs and publish one run-local staged runtime."""

    source_root, source_identity = _validated_root(Path(source_root), "source")
    primary_support_root, primary_identity = _validated_root(
        Path(primary_support_root),
        "primary support",
    )
    secondary_support_root, secondary_identity = _validated_root(
        Path(secondary_support_root),
        "secondary support",
    )
    source_descriptor, primary_descriptor, secondary_descriptor = _open_artifact_roots(
        (
            (source_root, "source", source_identity),
            (primary_support_root, "primary support", primary_identity),
            (secondary_support_root, "secondary support", secondary_identity),
        ),
    )
    destination_absolute = Path(destination).absolute()
    try:
        if _identity(os.fstat(primary_descriptor))[:2] == _identity(os.fstat(secondary_descriptor))[:2]:
            raise ValueError("primary and secondary support roots must be distinct")
        for artifact_root in (source_root, primary_support_root, secondary_support_root):
            artifact_absolute = artifact_root.absolute()
            if destination_absolute == artifact_absolute or destination_absolute.is_relative_to(artifact_absolute):
                raise ValueError("destination must be outside immutable artifacts")

        snapshots_before = {
            "source": _snapshot_tree_fd(source_descriptor),
            "primary": _snapshot_tree_fd(primary_descriptor),
            "secondary": _snapshot_tree_fd(secondary_descriptor),
        }
        source_receipt = validate_source_checkout(_descriptor_root_path(source_descriptor, "source"))
        support_receipt = validate_support_artifacts(
            _descriptor_root_path(primary_descriptor, "primary support"),
            _descriptor_root_path(secondary_descriptor, "secondary support"),
        )
        lock = _lock_payload(selection_lock)
        selection_lock_id = _assert_lock_identity(lock, source_receipt, support_receipt)
        snapshots_validated = {
            "source": _snapshot_tree_fd(source_descriptor),
            "primary": _snapshot_tree_fd(primary_descriptor),
            "secondary": _snapshot_tree_fd(secondary_descriptor),
        }
        if snapshots_validated != snapshots_before:
            raise ValueError("source/support artifact changed during validation")

        config_relative = Path(*_relative_parts(source_receipt.get("config_relative_path"), "source config"))
        predictor_relative = Path(*_relative_parts(support_receipt.get("predictor_relative_path"), "predictor"))
        predictor_repo_relative = predictor_relative.relative_to("repo")
        primary_checkpoint_relative = Path(
            *_relative_parts(support_receipt.get("primary_checkpoint_relative_path"), "primary checkpoint"),
        )
        secondary_checkpoint_relative = Path(
            *_relative_parts(support_receipt.get("secondary_checkpoint_relative_path"), "secondary checkpoint"),
        )
        (
            (source_config_parent, source_config_name),
            (predictor_parent, predictor_name),
            (primary_checkpoint_parent, primary_checkpoint_name),
            (secondary_checkpoint_parent, secondary_checkpoint_name),
            primary_repo_descriptor,
        ) = _open_staging_inputs(
            source_descriptor,
            primary_descriptor,
            secondary_descriptor,
            config_relative,
            predictor_relative,
            primary_checkpoint_relative,
            secondary_checkpoint_relative,
        )
        try:
            predictor_before_hash, _ = _sha256_regular_at(predictor_parent, predictor_name, "primary predictor")
            primary_hash, primary_metadata = _sha256_regular_at(
                primary_checkpoint_parent,
                primary_checkpoint_name,
                "primary checkpoint",
            )
            secondary_hash, secondary_metadata = _sha256_regular_at(
                secondary_checkpoint_parent,
                secondary_checkpoint_name,
                "secondary checkpoint",
            )
            if primary_hash == secondary_hash or (
                primary_metadata.st_ino,
                primary_metadata.st_dev,
            ) == (
                secondary_metadata.st_ino,
                secondary_metadata.st_dev,
            ):
                raise ValueError("primary and secondary checkpoint targets must be distinct")
            if predictor_before_hash != support_receipt.get("predictor_sha256"):
                raise ValueError("primary predictor hash changed after validation")
            if primary_hash != support_receipt.get("primary_checkpoint_sha256"):
                raise ValueError("primary checkpoint hash changed after validation")
            if secondary_hash != support_receipt.get("secondary_checkpoint_sha256"):
                raise ValueError("secondary checkpoint hash changed after validation")
            config_hash, _ = _sha256_regular_at(source_config_parent, source_config_name, "source config")
            if config_hash != source_receipt.get("config_sha256"):
                raise ValueError("source config hash changed after validation")

            stage_root: Path | None = None
            parent_descriptor: int | None = None
            stage_descriptor: int | None = None
            repo_descriptor: int | None = None
            published_predictor: PublishedDevicePatch | None = None
            runtime_stage: RuntimeStage | None = None
            receipt_identity: tuple[int, int, int] | None = None
            try:
                stage_root, parent_descriptor, stage_descriptor = _claim_destination(Path(destination))
                _fsync_directory(parent_descriptor)
                _assert_claimed_destination(parent_descriptor, stage_root.name, stage_descriptor)
                repo_descriptor = _copy_tree_at(
                    primary_repo_descriptor,
                    stage_descriptor,
                    "repo",
                    reject_symlinks=True,
                    exclude_relative=_relative_parts(predictor_repo_relative, "staged predictor"),
                )

                staged_config_parent = _ensure_directory_at(stage_descriptor, config_relative.parent, "staged config")
                try:
                    _copy_regular_file_at(
                        source_config_parent,
                        source_config_name,
                        staged_config_parent,
                        config_relative.name,
                        "source config",
                    )
                finally:
                    os.close(staged_config_parent)

                primary_stage_relative = Path(RECIPE_C_SOURCE.primary_checkpoint_relative_path)
                secondary_stage_relative = Path(RECIPE_C_SOURCE.secondary_staging_relative_path)
                primary_stage_parent = _ensure_directory_at(
                    repo_descriptor,
                    primary_stage_relative.parent,
                    "primary staged checkpoint",
                )
                try:
                    _copy_regular_file_at(
                        primary_checkpoint_parent,
                        primary_checkpoint_name,
                        primary_stage_parent,
                        primary_stage_relative.name,
                        "primary checkpoint",
                    )
                finally:
                    os.close(primary_stage_parent)
                secondary_stage_parent = _ensure_directory_at(
                    repo_descriptor,
                    secondary_stage_relative.parent,
                    "secondary staged checkpoint",
                )
                try:
                    _copy_regular_file_at(
                        secondary_checkpoint_parent,
                        secondary_checkpoint_name,
                        secondary_stage_parent,
                        secondary_stage_relative.name,
                        "secondary checkpoint",
                    )
                finally:
                    os.close(secondary_stage_parent)

                staged_before_hash = predictor_before_hash
                published_predictor = publish_device_fallback_patch_at(
                    primary_descriptor,
                    predictor_relative,
                    repo_descriptor,
                    predictor_repo_relative,
                )
                if not published_predictor.changed:
                    raise ValueError("primary predictor unexpectedly required no device patch")
                staged_after_hash, staged_predictor_metadata = _sha256_descriptor(
                    published_predictor.descriptor,
                    "patched predictor",
                )
                if staged_after_hash != published_predictor.sha256 or _identity(staged_predictor_metadata) != (
                    published_predictor.identity
                ):
                    raise ValueError("published predictor fd identity or digest mismatch")
                _compile_staged_predictor_descriptor(published_predictor.descriptor)
                _assert_published_predictor_at(
                    repo_descriptor,
                    predictor_repo_relative,
                    published_predictor,
                )

                staged_config_parent = _open_relative_directory(
                    stage_descriptor,
                    config_relative.parent,
                    "staged config",
                )
                try:
                    staged_config_hash, _ = _sha256_regular_at(
                        staged_config_parent,
                        config_relative.name,
                        "staged config",
                    )
                finally:
                    os.close(staged_config_parent)
                if staged_config_hash != config_hash:
                    raise ValueError("staged config hash mismatch")

                snapshots_after = {
                    "source": _snapshot_tree_fd(source_descriptor),
                    "primary": _snapshot_tree_fd(primary_descriptor),
                    "secondary": _snapshot_tree_fd(secondary_descriptor),
                }
                if snapshots_after != snapshots_before:
                    raise ValueError("source/support artifact changed during staging")
                _assert_claimed_destination(parent_descriptor, stage_root.name, stage_descriptor)
                _assert_published_predictor_at(
                    repo_descriptor,
                    predictor_repo_relative,
                    published_predictor,
                )
                final_predictor_hash, final_predictor_metadata = _sha256_descriptor(
                    published_predictor.descriptor,
                    "patched predictor",
                )
                if final_predictor_hash != published_predictor.sha256 or _identity(final_predictor_metadata) != (
                    published_predictor.identity
                ):
                    raise ValueError("published predictor changed before receipt")
                _assert_published_predictor_at(
                    repo_descriptor,
                    predictor_repo_relative,
                    published_predictor,
                )
                _assert_claimed_destination(parent_descriptor, stage_root.name, stage_descriptor)
                receipt: dict[str, object] = {
                    "schema_version": 1,
                    "status": "READY",
                    "selection_lock_id": selection_lock_id,
                    "roles": {
                        "repo": "repo",
                        "weights": "repo/weights",
                        "source_root": "source_root",
                        "config": config_relative.as_posix(),
                        "predictor": predictor_relative.as_posix(),
                        "primary_checkpoint": primary_stage_relative.as_posix(),
                        "secondary_checkpoint": secondary_stage_relative.as_posix(),
                    },
                    "predictor_sha256_before": staged_before_hash,
                    "predictor_sha256_after": staged_after_hash,
                    "primary_checkpoint_sha256": primary_hash,
                    "secondary_checkpoint_sha256": secondary_hash,
                    "config_sha256": staged_config_hash,
                    "resolved_device_candidates": list(DEVICE_SELECTION_ORDER),
                }
                runtime_stage = RuntimeStage(
                    _stage_root_path=stage_root,
                    _source_root_path=source_root,
                    _config_relative=tuple(config_relative.parts),
                    _predictor_relative=tuple(predictor_repo_relative.parts),
                    _primary_checkpoint_relative=tuple(primary_stage_relative.relative_to("weights").parts),
                    _secondary_checkpoint_relative=tuple(secondary_stage_relative.relative_to("weights").parts),
                    parent_descriptor=os.dup(parent_descriptor),
                    stage_descriptor=os.dup(stage_descriptor),
                    repo_descriptor=os.dup(repo_descriptor),
                    source_descriptor=os.dup(source_descriptor),
                    predictor_descriptor=os.dup(published_predictor.descriptor),
                    predictor_identity=published_predictor.identity,
                    selection_lock_id=selection_lock_id,
                    predictor_sha256_before=staged_before_hash,
                    predictor_sha256_after=staged_after_hash,
                    resolved_device_candidates=tuple(DEVICE_SELECTION_ORDER),
                    receipt=receipt,
                )
                receipt_identity = _publish_ready_receipt_at(stage_descriptor, parent_descriptor, receipt)
                return runtime_stage
            except BaseException as exc:
                publish_failure = exc if isinstance(exc, _ReceiptPublishError) else None
                failure = publish_failure.cause if publish_failure is not None else exc
                if publish_failure is not None:
                    receipt_identity = publish_failure.receipt_identity
                    for note in getattr(publish_failure, "__notes__", ()):
                        failure.add_note(note)
                if runtime_stage is not None:
                    try:
                        runtime_stage.close()
                    except BaseException as close_exc:
                        failure.add_note(f"runtime stage close failed: {close_exc!r}")
                if stage_descriptor is not None:
                    _mark_failed_at(stage_descriptor, selection_lock_id, failure, receipt_identity)
                if publish_failure is not None:
                    raise failure from exc
                raise
            finally:
                if repo_descriptor is not None:
                    os.close(repo_descriptor)
                if published_predictor is not None:
                    os.close(published_predictor.descriptor)
                if stage_descriptor is not None:
                    os.close(stage_descriptor)
                if parent_descriptor is not None:
                    os.close(parent_descriptor)
        finally:
            os.close(primary_repo_descriptor)
            os.close(source_config_parent)
            os.close(predictor_parent)
            os.close(primary_checkpoint_parent)
            os.close(secondary_checkpoint_parent)
    finally:
        os.close(source_descriptor)
        os.close(primary_descriptor)
        os.close(secondary_descriptor)


def _assert_published_predictor_at(
    root_descriptor: int,
    relative_path: Path,
    published: PublishedDevicePatch,
) -> None:
    parent_descriptor, name = _open_relative_parent(root_descriptor, relative_path, "published predictor")
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or _identity(metadata) != published.identity:
            raise ValueError("published predictor entry identity changed")
    except FileNotFoundError as exc:
        raise ValueError("published predictor entry disappeared") from exc
    finally:
        os.close(parent_descriptor)


def _compile_staged_predictor_descriptor(descriptor: int) -> None:
    payload, _ = _read_descriptor_stable(descriptor, "patched predictor")
    try:
        compile(payload.decode("utf-8"), "<recipe-c-staged-predictor>", "exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("staged predictor failed to compile") from exc


def _compile_staged_predictor_at(parent_descriptor: int, name: str) -> None:
    payload, _ = _read_regular_stable_at(parent_descriptor, name, "patched predictor")
    try:
        compile(payload.decode("utf-8"), "<recipe-c-staged-predictor>", "exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("staged predictor failed to compile") from exc


def _compile_staged_predictor(path: Path) -> None:
    parent_descriptor = _open_secure_directory(path.parent, create_missing=False)
    try:
        _compile_staged_predictor_at(parent_descriptor, path.name)
    finally:
        os.close(parent_descriptor)


__all__ = ["RuntimeStage", "stage_recipe_c_runtime"]
