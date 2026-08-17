"""Bounded, immutable snapshots of one explicitly named regular file."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


class ExactFileSnapshotError(ValueError):
    """Raised when an exact regular-file snapshot cannot be established."""


@dataclass(frozen=True, slots=True)
class ExactFileSnapshot:
    """Exact bytes and identity metadata captured from one open descriptor."""

    path: Path
    payload: bytes
    sha256: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _snapshot_identity(snapshot: ExactFileSnapshot) -> tuple[int, int, int, int, int, int]:
    return (
        snapshot.device,
        snapshot.inode,
        snapshot.mode,
        snapshot.size,
        snapshot.mtime_ns,
        snapshot.ctime_ns,
    )


def _required_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value <= 0:
        raise ExactFileSnapshotError(f"required secure open flag {name} is unavailable")
    return value


def require_snapshot_path_unchanged(snapshot: ExactFileSnapshot) -> None:
    """Fail if the original path no longer names the snapshotted regular file."""
    if type(snapshot) is not ExactFileSnapshot:
        raise ExactFileSnapshotError("snapshot must be an ExactFileSnapshot")
    try:
        linked = os.stat(snapshot.path, follow_symlinks=False)
    except OSError as exc:
        raise ExactFileSnapshotError("snapshot path changed or became unavailable") from exc
    if not stat.S_ISREG(linked.st_mode) or _identity(linked) != _snapshot_identity(snapshot):
        raise ExactFileSnapshotError("snapshot path changed after the exact read")


def read_exact_regular_file(
    path: str | Path,
    *,
    max_bytes: int,
) -> ExactFileSnapshot:
    """Read one bounded, no-follow regular-file snapshot from exactly one open.

    The descriptor and pathname identities must remain stable for the complete
    read.  Returned ``bytes`` are the long-lived identity carrier; callers may
    recheck the pathname with :func:`require_snapshot_path_unchanged` when a
    current path binding is also required.
    """
    if not isinstance(path, (str, Path)) or (isinstance(path, str) and not path):
        raise ExactFileSnapshotError("snapshot path must be explicit")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ExactFileSnapshotError("snapshot maximum size must be a positive integer")
    no_follow = _required_open_flag("O_NOFOLLOW")
    nonblock = _required_open_flag("O_NONBLOCK")
    close_on_exec = _required_open_flag("O_CLOEXEC")

    # Freeze the caller's CWD-relative meaning without resolving or following
    # the final path component.  Every later binding check uses this same path.
    source_path = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | no_follow | nonblock | close_on_exec
    try:
        descriptor = os.open(source_path, flags)
    except OSError as exc:
        raise ExactFileSnapshotError(
            f"snapshot path must name a no-follow regular file: {exc}"
        ) from exc

    primary_error: BaseException | None = None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ExactFileSnapshotError("snapshot path must name a regular file")
        if before.st_size > max_bytes:
            raise ExactFileSnapshotError(
                f"snapshot file size exceeds the {max_bytes}-byte maximum"
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            allowance = max_bytes + 1 - total
            chunk = os.read(descriptor, min(64 * 1024, allowance))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ExactFileSnapshotError(
                    f"snapshot file size exceeds the {max_bytes}-byte maximum"
                )
        after = os.fstat(descriptor)
    except ExactFileSnapshotError as exc:
        primary_error = exc
    except OSError as exc:
        primary_error = ExactFileSnapshotError(f"could not read exact file snapshot: {exc}")
        primary_error.__cause__ = exc
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if primary_error is None:
                primary_error = ExactFileSnapshotError(
                    f"could not close exact file snapshot descriptor: {exc}"
                )
                primary_error.__cause__ = exc
    if primary_error is not None:
        raise primary_error

    payload = b"".join(chunks)
    if _identity(before) != _identity(after) or len(payload) != before.st_size:
        raise ExactFileSnapshotError("snapshot file changed size or metadata during read")
    snapshot = ExactFileSnapshot(
        path=source_path,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )
    require_snapshot_path_unchanged(snapshot)
    return snapshot


__all__ = [
    "ExactFileSnapshot",
    "ExactFileSnapshotError",
    "read_exact_regular_file",
    "require_snapshot_path_unchanged",
]
