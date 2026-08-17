"""Adversarial tests for immutable, single-open regular-file snapshots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mage_ptcg.exact_file import (
    ExactFileSnapshotError,
    read_exact_regular_file,
    require_snapshot_path_unchanged,
)


def test_snapshot_reads_one_exact_open_and_binds_bytes_hash_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.bin"
    payload = b"exact bytes\n"
    path.write_bytes(payload)
    original_open = os.open
    opens = 0

    def counting_open(target, flags, *args, **kwargs):
        nonlocal opens
        if Path(target) == path:
            opens += 1
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", counting_open)
    snapshot = read_exact_regular_file(path, max_bytes=1024)

    assert opens == 1
    assert snapshot.path == path
    assert snapshot.payload == payload
    assert snapshot.sha256 == hashlib.sha256(payload).hexdigest()
    require_snapshot_path_unchanged(snapshot)


def test_snapshot_accepts_an_empty_regular_file_as_exact_empty_bytes(tmp_path: Path) -> None:
    """An EOF-only read is still a valid exact regular-file snapshot."""
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    snapshot = read_exact_regular_file(path, max_bytes=1)

    assert snapshot.payload == b""
    assert snapshot.size == 0
    assert snapshot.sha256 == hashlib.sha256(b"").hexdigest()
    require_snapshot_path_unchanged(snapshot)


def test_snapshot_runs_the_final_fstat_after_empty_file_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final descriptor identity check is mandatory even when no bytes are read."""
    from mage_ptcg import exact_file

    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    original_fstat = exact_file.os.fstat
    calls = 0

    def fail_only_final_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("final fstat failed")
        return original_fstat(descriptor)

    monkeypatch.setattr(exact_file.os, "fstat", fail_only_final_fstat)
    with pytest.raises(ExactFileSnapshotError, match="final fstat failed"):
        read_exact_regular_file(path, max_bytes=1)
    assert calls == 2


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "device"])
def test_snapshot_rejects_symlink_and_nonregular_paths(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"payload")
    if kind == "symlink":
        path = tmp_path / "link.bin"
        path.symlink_to(target)
    elif kind == "directory":
        path = tmp_path / "directory"
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is unavailable on this platform")
        path = tmp_path / "fifo"
        os.mkfifo(path)
    else:
        path = Path("/dev/null")
        if not path.exists():
            pytest.skip("device fixture is unavailable on this platform")

    with pytest.raises(ExactFileSnapshotError, match="regular|symlink|no-follow"):
        read_exact_regular_file(path, max_bytes=1024)


def test_snapshot_rejects_oversize_before_reading_payload(tmp_path: Path) -> None:
    path = tmp_path / "oversize.bin"
    path.write_bytes(b"x" * 1025)

    with pytest.raises(ExactFileSnapshotError, match="maximum|size"):
        read_exact_regular_file(path, max_bytes=1024)


@pytest.mark.parametrize("mutation", ["replace", "grow", "truncate"])
def test_snapshot_rejects_path_or_file_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "raced.bin"
    path.write_bytes(b"a" * (128 * 1024))
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"b" * (128 * 1024))
    original_read = exact_file.os.read
    mutated = False

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, count)
        if chunk and not mutated:
            mutated = True
            if mutation == "replace":
                replacement.replace(path)
            elif mutation == "grow":
                with path.open("ab") as handle:
                    handle.write(b"growth")
            else:
                path.write_bytes(b"short")
        return chunk

    monkeypatch.setattr(exact_file.os, "read", racing_read)
    with pytest.raises(ExactFileSnapshotError, match="changed|size"):
        read_exact_regular_file(path, max_bytes=256 * 1024)


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"])
def test_snapshot_fails_closed_without_required_open_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "input.bin"
    path.write_bytes(b"payload")
    monkeypatch.delattr(exact_file.os, flag, raising=False)

    with pytest.raises(ExactFileSnapshotError, match=flag):
        read_exact_regular_file(path, max_bytes=1024)


@pytest.mark.parametrize("bad_flag", [True, "1", None])
def test_snapshot_requires_exact_integer_open_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_flag: object
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "input.bin"
    path.write_bytes(b"payload")
    monkeypatch.setattr(exact_file.os, "O_NONBLOCK", bad_flag)

    with pytest.raises(ExactFileSnapshotError, match="O_NONBLOCK"):
        read_exact_regular_file(path, max_bytes=1024)


def test_snapshot_freezes_relative_path_before_cwd_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    elsewhere = tmp_path / "elsewhere"
    source.mkdir()
    elsewhere.mkdir()
    (source / "input.bin").write_bytes(b"payload")
    monkeypatch.chdir(source)

    snapshot = read_exact_regular_file(Path("input.bin"), max_bytes=1024)
    monkeypatch.chdir(elsewhere)

    assert snapshot.path == source / "input.bin"
    require_snapshot_path_unchanged(snapshot)


@pytest.mark.parametrize("failure_point", ["fstat", "read"])
def test_snapshot_closes_descriptor_once_on_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "input.bin"
    path.write_bytes(b"payload")
    original_close = exact_file.os.close
    original_fstat = exact_file.os.fstat
    original_read = exact_file.os.read
    closes = 0

    def counting_close(descriptor: int) -> None:
        nonlocal closes
        closes += 1
        original_close(descriptor)

    def failing_fstat(descriptor: int):
        raise OSError("fstat failed")

    def failing_read(descriptor: int, count: int):
        raise OSError("read failed")

    monkeypatch.setattr(exact_file.os, "close", counting_close)
    monkeypatch.setattr(
        exact_file.os,
        "fstat",
        failing_fstat if failure_point == "fstat" else original_fstat,
    )
    monkeypatch.setattr(
        exact_file.os,
        "read",
        failing_read if failure_point == "read" else original_read,
    )

    with pytest.raises(ExactFileSnapshotError, match="read|fstat"):
        read_exact_regular_file(path, max_bytes=1024)
    assert closes == 1


def test_snapshot_close_failure_never_masks_the_primary_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "input.bin"
    path.write_bytes(b"payload")
    original_close = exact_file.os.close
    closes = 0

    def failing_read(descriptor: int, count: int) -> bytes:
        raise OSError("primary read failed")

    def close_then_fail(descriptor: int) -> None:
        nonlocal closes
        closes += 1
        original_close(descriptor)
        raise OSError("secondary close failed")

    monkeypatch.setattr(exact_file.os, "read", failing_read)
    monkeypatch.setattr(exact_file.os, "close", close_then_fail)

    with pytest.raises(ExactFileSnapshotError, match="primary read failed"):
        read_exact_regular_file(path, max_bytes=1024)
    assert closes == 1


def test_snapshot_reports_close_failure_after_an_otherwise_successful_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg import exact_file

    path = tmp_path / "input.bin"
    path.write_bytes(b"payload")
    original_close = exact_file.os.close

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError("close failed")

    monkeypatch.setattr(exact_file.os, "close", close_then_fail)
    with pytest.raises(ExactFileSnapshotError, match="close exact file snapshot"):
        read_exact_regular_file(path, max_bytes=1024)
