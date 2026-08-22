"""Strict, descriptor-anchored device fallback patch for Recipe C."""

from __future__ import annotations

import os
import secrets
import stat
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


def _cleanup_temp(parent_descriptor: int, name: str, owner: tuple[int, int] | None) -> bool:
    if owner is None:
        return True
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
    except OSError:
        return False
    return True


def _cleanup_temp_best_effort(
    parent_descriptor: int,
    name: str,
    owner: tuple[int, int] | None,
) -> None:
    """Remove only an owned temp entry without masking the original failure."""

    if _cleanup_temp(parent_descriptor, name, owner):
        try:
            _fsync_directory(parent_descriptor)
        except OSError:
            pass


def _publish_fresh_at(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> bool:
    """Publish a patched predictor only when its final name is still absent."""

    source, source_metadata = _read_stable_at(source_parent_descriptor, source_name)
    patched, changed = prepare_device_fallback_patch(source)
    temporary_name = f".{destination_name}.recipe-c-device-patch.{os.getpid()}.{secrets.token_hex(12)}"
    temporary_descriptor: int | None = None
    temporary_owner: tuple[int, int] | None = None
    temporary_removed = False
    try:
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=destination_parent_descriptor,
        )
        # Record ownership immediately, before any write can fail.
        temporary_owner = _inode_identity(os.fstat(temporary_descriptor))
        written = 0
        while written < len(patched):
            count = os.write(temporary_descriptor, patched[written:])
            if count <= 0:
                raise OSError("device patch made no write progress")
            written += count
        os.fchmod(temporary_descriptor, stat.S_IMODE(source_metadata.st_mode))
        os.fsync(temporary_descriptor)
        try:
            os.link(
                temporary_name,
                destination_name,
                src_dir_fd=destination_parent_descriptor,
                dst_dir_fd=destination_parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError("predictor final entry already exists") from exc
        published_metadata = _require_regular_at(destination_parent_descriptor, destination_name)
        if _inode_identity(published_metadata) != temporary_owner:
            raise ValueError("predictor publication identity changed")
        _fsync_directory(destination_parent_descriptor)
        published_metadata = _require_regular_at(destination_parent_descriptor, destination_name)
        if _inode_identity(published_metadata) != temporary_owner:
            raise ValueError("predictor publication identity changed")
        if not _cleanup_temp(destination_parent_descriptor, temporary_name, temporary_owner):
            raise OSError("device patch temporary ownership changed during cleanup")
        temporary_removed = True
        _fsync_directory(destination_parent_descriptor)
        return changed
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if not temporary_removed:
            _cleanup_temp_best_effort(
                destination_parent_descriptor,
                temporary_name,
                temporary_owner,
            )


def publish_device_fallback_patch_at(
    source_root_descriptor: int,
    source_relative_path: Path,
    destination_root_descriptor: int,
    destination_relative_path: Path,
) -> bool:
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
    "prepare_device_fallback_patch",
    "publish_device_fallback_patch_at",
]
