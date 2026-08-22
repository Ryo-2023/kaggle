"""Strict, descriptor-anchored device fallback patch for Recipe C."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

DEVICE_PREIMAGE = 'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")'
DEVICE_POSTIMAGE = """if torch.cuda.is_available():
    device = torch.device("cuda")
elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")"""

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_HASH_CHUNK_SIZE = 1024 * 1024
_O_TMPFILE = getattr(os, "O_TMPFILE", 0)
_AT_EMPTY_PATH = 0x1000


@dataclass(frozen=True, slots=True)
class PublishedDevicePatch:
    """An open, fsynced predictor inode published under a fresh final name."""

    descriptor: int
    identity: tuple[int, int, int]
    sha256: str
    changed: bool

    @property
    def fd(self) -> int:
        return self.descriptor

    @property
    def digest(self) -> str:
        return self.sha256

    def read_bytes(self) -> bytes:
        if self.descriptor < 0:
            raise ValueError("published predictor handle is closed")
        before = os.fstat(self.descriptor)
        payload = os.pread(self.descriptor, before.st_size, 0)
        after = os.fstat(self.descriptor)
        if _identity(before) != _identity(after) or len(payload) != before.st_size:
            raise ValueError("published predictor changed while it was read")
        if hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("published predictor digest changed")
        return payload

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor >= 0:
            os.close(descriptor)
            object.__setattr__(self, "descriptor", -1)

    def __enter__(self) -> PublishedDevicePatch:
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_ino, metadata.st_dev, metadata.st_size


def _inode_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_ino, metadata.st_dev


def _open_parent_at(root_descriptor: int, relative_path: Path) -> tuple[int, str]:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("predictor relative path is invalid")
    descriptor = os.dup(root_descriptor)
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW
    try:
        for part in relative.parts[:-1]:
            if part in {"", "."}:
                continue
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, relative.name
    except BaseException:
        os.close(descriptor)
        raise


def _require_regular_at(parent_descriptor: int, name: str) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError("predictor file is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("predictor symlink is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("predictor must be a regular file")
    return metadata


def _read_stable_at(parent_descriptor: int, name: str) -> tuple[bytes, os.stat_result]:
    before = _require_regular_at(parent_descriptor, name)
    try:
        descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError("predictor could not be opened without following symlinks") from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise ValueError("predictor changed before it was read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = _require_regular_at(parent_descriptor, name)
    if _identity(after) != _identity(current):
        raise ValueError("predictor changed while it was read")
    return b"".join(chunks), current


def _compile(source: bytes) -> None:
    try:
        text = source.decode("utf-8")
        compile(text, "<recipe-c-staged-predictor>", "exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("patched predictor failed to compile") from exc


def _postimage_positions(source: bytes) -> list[int]:
    needle = b"if torch.cuda.is_available():"
    postimage = DEVICE_POSTIMAGE.encode("utf-8")
    positions: list[int] = []
    offset = 0
    while True:
        position = source.find(needle, offset)
        if position < 0:
            return positions
        line_start = source.rfind(b"\n", 0, position) + 1
        indent = source[line_start:position]
        if indent.strip() == b"":
            indented = postimage.replace(b"\n", b"\n" + indent)
            if source.startswith(indented, position):
                positions.append(position)
        offset = position + len(needle)


def prepare_device_fallback_patch(source: bytes) -> tuple[bytes, bool]:
    """Validate the exact device contract and return bytes ready to publish."""

    preimage = DEVICE_PREIMAGE.encode("utf-8")
    postimage = DEVICE_POSTIMAGE.encode("utf-8")
    device_guard = b"if torch.cuda.is_available():"
    preimage_count = source.count(preimage)
    postimage_count = len(_postimage_positions(source))

    if preimage_count == 0 and postimage_count == 1 and source.count(device_guard) == 1:
        _compile(source)
        return source, False
    if preimage_count == 0 and postimage_count == 1:
        raise ValueError("predictor contains an unknown or mixed device preimage")
    if preimage_count != 1:
        raise ValueError("predictor device preimage must occur exactly once")
    if postimage_count:
        raise ValueError("predictor contains an unknown or mixed device preimage")

    preimage_position = source.find(preimage)
    line_start = source.rfind(b"\n", 0, preimage_position) + 1
    indent = source[line_start:preimage_position]
    if indent.strip() != b"":
        raise ValueError("predictor device preimage must occupy a complete line")
    indented_postimage = postimage.replace(b"\n", b"\n" + indent)
    patched = source.replace(preimage, indented_postimage)
    if patched == source:
        raise ValueError("predictor device preimage was not replaced")
    _compile(patched)
    return patched, True


def _fsync_directory(parent_descriptor: int) -> None:
    try:
        os.fsync(parent_descriptor)
    except OSError as exc:
        raise OSError("device patch directory fsync failed") from exc


def _open_anonymous_temp(parent_descriptor: int) -> tuple[int, str | None, tuple[int, int]]:
    """Open an anonymous destination-directory file, failing closed if needed."""

    if _O_TMPFILE:
        try:
            descriptor = os.open(
                ".",
                os.O_RDWR | _O_TMPFILE | _NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            metadata = os.fstat(descriptor)
            return descriptor, None, _inode_identity(metadata)
        except OSError as exc:
            if exc.errno not in {
                errno.EINVAL,
                errno.ENOSYS,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
            }:
                raise

    # Some filesystems expose no O_TMPFILE.  Keep a cryptographically random
    # name only as an owned directory entry; publication itself still uses the
    # fd via linkat(AT_EMPTY_PATH).  The entry is removed only after an inode
    # ownership check, and an attacker replacement is left untouched.
    temporary_name = f".recipe-c-anonymous.{os.getpid()}.{secrets.token_hex(24)}"
    descriptor = os.open(
        temporary_name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    metadata = os.fstat(descriptor)
    return descriptor, temporary_name, _inode_identity(metadata)


def _cleanup_owned_temp(
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
    if _inode_identity(current) != owner:
        return False
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return True
    return True


def _link_anonymous(descriptor: int, destination_parent_descriptor: int, destination_name: str) -> None:
    """Hard-link an open anonymous inode using Linux linkat(AT_EMPTY_PATH)."""

    try:
        linkat = ctypes.CDLL(None, use_errno=True).linkat
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOTSUP, "anonymous fd publication is unavailable") from exc
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    result = linkat(
        descriptor,
        b"",
        destination_parent_descriptor,
        os.fsencode(destination_name),
        _AT_EMPTY_PATH,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def _hash_descriptor(descriptor: int) -> tuple[str, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("published predictor must be a regular file")
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, _HASH_CHUNK_SIZE, offset)
        if not chunk:
            break
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if _identity(before) != _identity(after):
        raise ValueError("published predictor changed while it was hashed")
    return digest.hexdigest(), after


def _publish_fresh_at(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> PublishedDevicePatch:
    """Publish a patched predictor only when its final name is still absent."""

    source, source_metadata = _read_stable_at(source_parent_descriptor, source_name)
    patched, changed = prepare_device_fallback_patch(source)
    expected_digest = hashlib.sha256(patched).hexdigest()
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    temporary_owner: tuple[int, int] | None = None
    published = False
    try:
        temporary_descriptor, temporary_name, temporary_owner = _open_anonymous_temp(
            destination_parent_descriptor,
        )
        written = 0
        while written < len(patched):
            count = os.write(temporary_descriptor, patched[written:])
            if count <= 0:
                raise OSError("device patch made no write progress")
            written += count
        os.fchmod(temporary_descriptor, stat.S_IMODE(source_metadata.st_mode))
        os.fsync(temporary_descriptor)
        try:
            _link_anonymous(temporary_descriptor, destination_parent_descriptor, destination_name)
        except FileExistsError as exc:
            raise ValueError("predictor final entry already exists") from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise ValueError("predictor final entry already exists") from exc
            raise
        temporary_identity = _identity(os.fstat(temporary_descriptor))
        published_metadata = _require_regular_at(destination_parent_descriptor, destination_name)
        if _identity(published_metadata) != temporary_identity:
            raise ValueError("predictor publication identity changed")
        _fsync_directory(destination_parent_descriptor)
        published_metadata = _require_regular_at(destination_parent_descriptor, destination_name)
        if _identity(published_metadata) != temporary_identity:
            raise ValueError("predictor publication identity changed")
        digest, hashed_metadata = _hash_descriptor(temporary_descriptor)
        if digest != expected_digest:
            raise ValueError("predictor publication digest changed before publish")
        if _identity(hashed_metadata) != _identity(published_metadata):
            raise ValueError("predictor publication identity changed while hashing")
        published_metadata = _require_regular_at(destination_parent_descriptor, destination_name)
        if _identity(published_metadata) != _identity(hashed_metadata):
            raise ValueError("predictor publication identity changed after hashing")
        if temporary_name is not None:
            if temporary_owner is None or not _cleanup_owned_temp(
                destination_parent_descriptor,
                temporary_name,
                temporary_owner,
            ):
                raise OSError("device patch temporary ownership changed during cleanup")
            _fsync_directory(destination_parent_descriptor)
        published = True
        return PublishedDevicePatch(
            descriptor=temporary_descriptor,
            identity=_identity(hashed_metadata),
            sha256=digest,
            changed=changed,
        )
    finally:
        if temporary_descriptor is not None and not published:
            if temporary_name is not None and temporary_owner is not None:
                _cleanup_owned_temp(
                    destination_parent_descriptor,
                    temporary_name,
                    temporary_owner,
                )
            os.close(temporary_descriptor)


def publish_device_fallback_patch_at(
    source_root_descriptor: int,
    source_relative_path: Path,
    destination_root_descriptor: int,
    destination_relative_path: Path,
) -> PublishedDevicePatch:
    """Patch opened source bytes into a fresh destination name, without clobbering."""

    source_parent_descriptor, source_name = _open_parent_at(
        source_root_descriptor,
        Path(source_relative_path),
    )
    try:
        destination_parent_descriptor, destination_name = _open_parent_at(
            destination_root_descriptor,
            Path(destination_relative_path),
        )
    except BaseException:
        os.close(source_parent_descriptor)
        raise
    try:
        return _publish_fresh_at(
            source_parent_descriptor,
            source_name,
            destination_parent_descriptor,
            destination_name,
        )
    finally:
        os.close(source_parent_descriptor)
        os.close(destination_parent_descriptor)


__all__ = [
    "DEVICE_POSTIMAGE",
    "DEVICE_PREIMAGE",
    "PublishedDevicePatch",
    "prepare_device_fallback_patch",
    "publish_device_fallback_patch_at",
]
