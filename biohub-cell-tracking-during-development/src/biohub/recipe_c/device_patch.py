"""Strict, run-local device fallback patch for the public Recipe C predictor."""

from __future__ import annotations

import os
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


def _require_regular_predictor(path: Path) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError("predictor file is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("predictor symlink is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("predictor must be a regular file")
    return metadata


def _read_stable(path: Path) -> tuple[bytes, os.stat_result]:
    before = _require_regular_predictor(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise ValueError("predictor could not be opened without following symlinks") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_ino, opened.st_dev, opened.st_size) != (
            before.st_ino,
            before.st_dev,
            before.st_size,
        ):
            raise ValueError("predictor changed before it was read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = _require_regular_predictor(path)
    if (after.st_ino, after.st_dev, after.st_size) != (
        current.st_ino,
        current.st_dev,
        current.st_size,
    ):
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


def _atomic_replace(path: Path, payload: bytes, original: os.stat_result) -> None:
    parent_fd = os.open(path.parent, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    temporary_name = f".{path.name}.recipe-c-device-patch.{os.getpid()}"
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        written = 0
        while written < len(payload):
            count = os.write(temporary_fd, payload[written:])
            if count <= 0:
                raise OSError("device patch made no write progress")
            written += count
        os.fchmod(temporary_fd, stat.S_IMODE(original.st_mode))
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        current = _require_regular_predictor(path)
        if (current.st_ino, current.st_dev, current.st_size) != (
            original.st_ino,
            original.st_dev,
            original.st_size,
        ):
            raise ValueError("predictor changed before patch publication")
        os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def apply_device_fallback_patch(predictor_path: Path) -> bool:
    """Patch exactly one CUDA/CPU preimage in a regular run-local file.

    The source/support artifact is never passed here; callers must provide a
    run-local copy.  Unknown, repeated, or symlinked inputs fail before any
    write.  A byte-identical postimage is accepted as an idempotent no-op.
    """

    path = Path(predictor_path)
    source, metadata = _read_stable(path)
    preimage = DEVICE_PREIMAGE.encode("utf-8")
    postimage = DEVICE_POSTIMAGE.encode("utf-8")
    device_guard = b"if torch.cuda.is_available():"
    preimage_count = source.count(preimage)
    postimage_positions = _postimage_positions(source)
    postimage_count = len(postimage_positions)

    if preimage_count == 0 and postimage_count == 1 and source.count(device_guard) == 1:
        _compile(source)
        return False
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
    _atomic_replace(path, patched, metadata)
    return True


__all__ = ["DEVICE_POSTIMAGE", "DEVICE_PREIMAGE", "apply_device_fallback_patch"]
