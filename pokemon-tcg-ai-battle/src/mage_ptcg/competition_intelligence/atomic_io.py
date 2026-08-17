"""Atomic, durable writes for Competition Intelligence canonical artifacts.

Mirrors ``mage_ptcg.offline_training.runstate.atomic_write_json``'s
fsync-backed sequence (temp sibling -> flush -> fsync -> replace -> parent-dir
fsync). Kept as a local copy in this sidecar rather than an import from
``offline_training`` so that package never needs to depend on this one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes


def fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write ``data`` to ``path`` so a crash never leaves a partial file there."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, destination)
    fsync_dir(destination.parent)


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value) + b"\n")


def append_jsonl_line(path: str | Path, value: Any) -> None:
    """Append one canonical-JSON line, fsync'd.

    This is append-only and not itself a crash-atomic replace of the whole
    file; callers must hold the run's single-writer lock
    (``runstate.run_lock``) before calling this so concurrent writers never
    interleave partial lines.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json_bytes(value) + b"\n"
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = ["append_jsonl_line", "atomic_write_bytes", "atomic_write_json", "fsync_dir"]
