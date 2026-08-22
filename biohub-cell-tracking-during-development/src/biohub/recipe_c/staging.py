"""Failure-atomic, read-only-input staging for Recipe C.

Only a run-local copy of the public predictor repository is writable.  The
source checkout and both support artifacts are validated and fingerprinted
before and after staging; model checkpoints remain symlinks to their original
read-only files.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from biohub.device import DEVICE_SELECTION_ORDER

from .device_patch import apply_device_fallback_patch
from .protocol import validate_selection_lock, validate_selection_lock_payload
from .source import (
    RECIPE_C_SOURCE,
    validate_source_checkout,
    validate_support_artifacts,
)

_HASH_CHUNK_SIZE = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_RECEIPT_FILENAME = "receipt.json"
_FAILED_FILENAME = "FAILED.json"


@dataclass(frozen=True, slots=True)
class RuntimeStage:
    """Paths and immutable identity for one staged Recipe C run."""

    stage_root: Path
    repo_dir: Path
    weights_root: Path
    source_root: Path
    staged_config: Path
    selection_lock_id: str
    predictor_sha256_before: str
    predictor_sha256_after: str
    resolved_device_candidates: tuple[str, ...]
    receipt: dict[str, object]
    receipt_path: Path

    @property
    def destination(self) -> Path:
        return self.stage_root

    @property
    def config_path(self) -> Path:
        return self.staged_config

    @property
    def predictor_path(self) -> Path:
        return self.repo_dir / _repo_relative(RECIPE_C_SOURCE.predictor_relative_path, "predictor")

    @property
    def primary_checkpoint_path(self) -> Path:
        return self.weights_root / Path(RECIPE_C_SOURCE.primary_checkpoint_relative_path).relative_to(
            "weights",
        )

    @property
    def secondary_checkpoint_path(self) -> Path:
        return self.weights_root / Path(RECIPE_C_SOURCE.secondary_staging_relative_path).relative_to(
            "weights",
        )

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
    def staged_config_path(self) -> Path:
        return self.staged_config

    @property
    def device_candidates(self) -> tuple[str, ...]:
        return self.resolved_device_candidates

    @property
    def receipt_identity(self) -> dict[str, object]:
        return self.receipt


def _relative_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} relative path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"{label} path must be relative")
    return root / relative


def _repo_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} relative path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("repo",):
        raise ValueError(f"{label} path must be under repo")
    return relative.relative_to("repo")


def _root_path(root: Path, label: str) -> Path:
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
    return root


def _read_regular_stable(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} file is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} symlink is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"{label} could not be opened without following symlinks") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_ino, opened.st_dev, opened.st_size) != (
            metadata.st_ino,
            metadata.st_dev,
            metadata.st_size,
        ):
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
    current = os.lstat(path)
    if not stat.S_ISREG(current.st_mode) or (after.st_ino, after.st_dev, after.st_size) != (
        current.st_ino,
        current.st_dev,
        current.st_size,
    ):
        raise ValueError(f"{label} changed while it was read")
    return b"".join(chunks), current


def _sha256_regular(path: Path, label: str) -> tuple[str, os.stat_result]:
    payload, metadata = _read_regular_stable(path, label)
    return hashlib.sha256(payload).hexdigest(), metadata


def _snapshot_tree(root: Path, *, reject_symlinks: bool = False) -> dict[str, tuple[object, ...]]:
    """Return a symlink-aware snapshot without storing absolute path values."""

    root = _root_path(root, "artifact")
    resolved_root = root.resolve(strict=True)
    snapshot: dict[str, tuple[object, ...]] = {}

    def visit(directory: Path, relative_directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError as exc:
            raise ValueError("artifact tree could not be enumerated") from exc
        for name in names:
            path = directory / name
            relative = (relative_directory / name).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                if reject_symlinks:
                    raise ValueError("support repo symlink is forbidden")
                target = os.readlink(path)
                try:
                    resolved_target = path.resolve(strict=True)
                except (FileNotFoundError, RuntimeError) as exc:
                    raise ValueError(f"artifact contains a dangling symlink: {relative}") from exc
                if not resolved_target.is_relative_to(resolved_root):
                    raise ValueError(f"artifact contains an external symlink: {relative}")
                snapshot[relative] = (
                    "symlink",
                    target,
                    metadata.st_ino,
                    metadata.st_dev,
                )
            elif stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = (
                    "directory",
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_mtime_ns,
                )
                visit(path, relative_directory / name)
            elif stat.S_ISREG(metadata.st_mode):
                digest, stable = _sha256_regular(path, f"artifact file {relative}")
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

    visit(root, Path())
    return snapshot


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
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
            except OSError as exc:
                raise ValueError("destination parent symlink or non-directory") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _claim_destination(destination: Path) -> tuple[Path, int]:
    destination = Path(destination)
    if destination.name in {"", ".", ".."}:
        raise ValueError("destination must name a directory")
    parent_descriptor = _open_secure_directory(destination.parent, create_missing=True)
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
        try:
            os.fsync(parent_descriptor)
        except OSError:
            # The ownership claim is still exclusive if directory fsync is
            # unavailable on a platform/filesystem; file receipts are fsynced
            # separately before publication.
            pass
    finally:
        os.close(parent_descriptor)
    return destination, stage_descriptor


def _assert_claimed_stage(stage_root: Path, stage_descriptor: int) -> None:
    """Reject a destination that was renamed or replaced after claiming it."""

    opened = os.fstat(stage_descriptor)
    current = os.lstat(stage_root)
    if not stat.S_ISDIR(current.st_mode) or (current.st_ino, current.st_dev) != (
        opened.st_ino,
        opened.st_dev,
    ):
        raise ValueError("destination changed after exclusive claim")


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


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


def _copy_regular_file(source: Path, destination: Path, label: str) -> None:
    payload, source_metadata = _read_regular_stable(source, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            stat.S_IMODE(source_metadata.st_mode),
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
    after = os.lstat(source)
    if not stat.S_ISREG(after.st_mode) or (after.st_ino, after.st_dev, after.st_size) != (
        source_metadata.st_ino,
        source_metadata.st_dev,
        source_metadata.st_size,
    ):
        raise ValueError(f"{label} changed during copy")


def _copy_tree(source: Path, destination: Path, *, reject_symlinks: bool) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("staged tree already exists")
    destination.mkdir(parents=True)
    snapshot = _snapshot_tree(source, reject_symlinks=reject_symlinks)
    del snapshot
    with os.scandir(source) as iterator:
        names = sorted(entry.name for entry in iterator)
    for name in names:
        source_path = source / name
        destination_path = destination / name
        metadata = os.lstat(source_path)
        if stat.S_ISDIR(metadata.st_mode):
            _copy_tree(source_path, destination_path, reject_symlinks=reject_symlinks)
        elif stat.S_ISREG(metadata.st_mode):
            _copy_regular_file(source_path, destination_path, f"support repo file {name}")
        else:
            raise ValueError("support repo contains a symlink or unsupported file")


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


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return left.resolve(strict=True) == right.resolve(strict=True)


def stage_recipe_c_runtime(
    source_root: Path,
    primary_support_root: Path,
    secondary_support_root: Path,
    destination: Path,
    selection_lock: Mapping[str, object] | Path,
) -> RuntimeStage:
    """Validate immutable inputs and publish one run-local staged runtime."""

    source_root = _root_path(Path(source_root), "source")
    primary_support_root = _root_path(Path(primary_support_root), "primary support")
    secondary_support_root = _root_path(Path(secondary_support_root), "secondary support")
    if _same_file(primary_support_root, secondary_support_root):
        raise ValueError("primary and secondary support roots must be distinct")
    destination_absolute = Path(destination).absolute()
    for artifact_root in (source_root, primary_support_root, secondary_support_root):
        artifact_absolute = artifact_root.absolute()
        if destination_absolute == artifact_absolute or destination_absolute.is_relative_to(artifact_absolute):
            raise ValueError("destination must be outside immutable artifacts")

    snapshots_before = {
        "source": _snapshot_tree(source_root),
        "primary": _snapshot_tree(primary_support_root),
        "secondary": _snapshot_tree(secondary_support_root),
    }
    source_receipt = validate_source_checkout(source_root)
    support_receipt = validate_support_artifacts(primary_support_root, secondary_support_root)
    lock = _lock_payload(selection_lock)
    selection_lock_id = _assert_lock_identity(lock, source_receipt, support_receipt)

    snapshots_validated = {
        "source": _snapshot_tree(source_root),
        "primary": _snapshot_tree(primary_support_root),
        "secondary": _snapshot_tree(secondary_support_root),
    }
    if snapshots_validated != snapshots_before:
        raise ValueError("source/support artifact changed during validation")

    config_path = _relative_path(source_root, source_receipt.get("config_relative_path"), "source config")
    primary_repo = primary_support_root / "repo"
    if not primary_repo.is_dir() or primary_repo.is_symlink():
        raise ValueError("primary support repo is missing or not a directory")
    predictor = _relative_path(primary_support_root, support_receipt.get("predictor_relative_path"), "predictor")
    primary_checkpoint = _relative_path(
        primary_support_root,
        support_receipt.get("primary_checkpoint_relative_path"),
        "primary checkpoint",
    )
    secondary_checkpoint = _relative_path(
        secondary_support_root,
        support_receipt.get("secondary_checkpoint_relative_path"),
        "secondary checkpoint",
    )
    predictor_before_hash, _ = _sha256_regular(predictor, "primary predictor")
    primary_hash, _ = _sha256_regular(primary_checkpoint, "primary checkpoint")
    secondary_hash, _ = _sha256_regular(secondary_checkpoint, "secondary checkpoint")
    if primary_hash == secondary_hash or _same_file(primary_checkpoint, secondary_checkpoint):
        raise ValueError("primary and secondary checkpoint targets must be distinct")
    if predictor_before_hash != support_receipt.get("predictor_sha256"):
        raise ValueError("primary predictor hash changed after validation")
    if primary_hash != support_receipt.get("primary_checkpoint_sha256"):
        raise ValueError("primary checkpoint hash changed after validation")
    if secondary_hash != support_receipt.get("secondary_checkpoint_sha256"):
        raise ValueError("secondary checkpoint hash changed after validation")
    config_hash, _ = _sha256_regular(config_path, "source config")
    if config_hash != source_receipt.get("config_sha256"):
        raise ValueError("source config hash changed after validation")

    stage_root: Path | None = None
    stage_descriptor: int | None = None
    try:
        stage_root, stage_descriptor = _claim_destination(Path(destination))
        _assert_claimed_stage(stage_root, stage_descriptor)
        repo_dir = stage_root / "repo"
        _copy_tree(primary_repo, repo_dir, reject_symlinks=True)

        staged_config = stage_root / Path(source_receipt["config_relative_path"])
        _copy_regular_file(config_path, staged_config, "source config")

        weights_root = repo_dir / "weights"
        primary_staged = weights_root / Path(RECIPE_C_SOURCE.primary_checkpoint_relative_path).relative_to("weights")
        secondary_staged = weights_root / Path(RECIPE_C_SOURCE.secondary_staging_relative_path).relative_to("weights")
        primary_staged.parent.mkdir(parents=True, exist_ok=True)
        secondary_staged.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(primary_checkpoint.absolute(), primary_staged)
        os.symlink(secondary_checkpoint.absolute(), secondary_staged)

        staged_predictor = repo_dir / _repo_relative(support_receipt["predictor_relative_path"], "predictor")
        staged_before_hash, _ = _sha256_regular(staged_predictor, "staged predictor")
        if staged_before_hash != predictor_before_hash:
            raise ValueError("staged predictor pristine hash mismatch")
        apply_device_fallback_patch(staged_predictor)
        staged_after_hash, _ = _sha256_regular(staged_predictor, "patched predictor")
        _compile_staged_predictor(staged_predictor)

        if not primary_staged.is_symlink() or not secondary_staged.is_symlink():
            raise ValueError("staged checkpoint links are missing")
        if not _same_file(primary_staged.resolve(strict=True), primary_checkpoint):
            raise ValueError("staged primary checkpoint target mismatch")
        if not _same_file(secondary_staged.resolve(strict=True), secondary_checkpoint):
            raise ValueError("staged secondary checkpoint target mismatch")
        staged_config_hash, _ = _sha256_regular(staged_config, "staged config")
        if staged_config_hash != config_hash:
            raise ValueError("staged config hash mismatch")

        snapshots_after = {
            "source": _snapshot_tree(source_root),
            "primary": _snapshot_tree(primary_support_root),
            "secondary": _snapshot_tree(secondary_support_root),
        }
        if snapshots_after != snapshots_before:
            raise ValueError("source/support artifact changed during staging")

        _assert_claimed_stage(stage_root, stage_descriptor)
        receipt: dict[str, object] = {
            "schema_version": 1,
            "status": "READY",
            "selection_lock_id": selection_lock_id,
            "roles": {
                "repo": "repo",
                "weights": "repo/weights",
                "source_root": "source_root",
                "config": Path(source_receipt["config_relative_path"]).as_posix(),
                "predictor": Path(support_receipt["predictor_relative_path"]).as_posix(),
                "primary_checkpoint": Path(RECIPE_C_SOURCE.primary_checkpoint_relative_path).as_posix(),
                "secondary_checkpoint": Path(RECIPE_C_SOURCE.secondary_staging_relative_path).as_posix(),
            },
            "predictor_sha256_before": staged_before_hash,
            "predictor_sha256_after": staged_after_hash,
            "primary_checkpoint_sha256": primary_hash,
            "secondary_checkpoint_sha256": secondary_hash,
            "config_sha256": staged_config_hash,
            "resolved_device_candidates": list(DEVICE_SELECTION_ORDER),
        }
        receipt_path = stage_root / _RECEIPT_FILENAME
        _write_json_exclusive(receipt_path, receipt)
        os.fsync(stage_descriptor)
        _assert_claimed_stage(stage_root, stage_descriptor)
        return RuntimeStage(
            stage_root=stage_root,
            repo_dir=repo_dir,
            weights_root=weights_root,
            source_root=source_root,
            staged_config=staged_config,
            selection_lock_id=selection_lock_id,
            predictor_sha256_before=staged_before_hash,
            predictor_sha256_after=staged_after_hash,
            resolved_device_candidates=tuple(DEVICE_SELECTION_ORDER),
            receipt=receipt,
            receipt_path=receipt_path,
        )
    except BaseException as exc:
        if stage_root is not None:
            _mark_failed(stage_root, selection_lock_id, exc)
        raise
    finally:
        if stage_descriptor is not None:
            os.close(stage_descriptor)


def _compile_staged_predictor(path: Path) -> None:
    payload, _ = _read_regular_stable(path, "patched predictor")
    try:
        compile(payload.decode("utf-8"), "<recipe-c-staged-predictor>", "exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("staged predictor failed to compile") from exc


__all__ = ["RuntimeStage", "stage_recipe_c_runtime"]
