"""Concurrency and atomicity tests using multiprocessing."""

from __future__ import annotations
import os
import time
import multiprocessing
from pathlib import Path
import pytest
from mage_ptcg.offline_training_v1_support.contracts import FileLock, FileLockError, atomic_write_json

def _acquire_lock_process(lock_path: Path, result_queue: multiprocessing.Queue) -> None:
    """Target process function to acquire a file lock."""
    try:
        with FileLock(lock_path, timeout=0.5):
            result_queue.put("ACQUIRED")
            time.sleep(1.0)  # Hold lock for 1 sec
    except FileLockError:
        result_queue.put("TIMEOUT")
    except Exception as exc:
        result_queue.put(f"ERROR: {exc}")

def test_multiprocessing_lock_concurrency(tmp_path: Path):
    lock_path = tmp_path / "test.lock"
    result_queue = multiprocessing.Queue()

    # Start first process to acquire and hold the lock
    p1 = multiprocessing.Process(target=_acquire_lock_process, args=(lock_path, result_queue))
    p1.start()

    # Wait until p1 acquires lock
    time.sleep(0.2)

    # Start second process which should timeout trying to get the lock
    p2 = multiprocessing.Process(target=_acquire_lock_process, args=(lock_path, result_queue))
    p2.start()

    p1.join()
    p2.join()

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    assert "ACQUIRED" in results
    assert "TIMEOUT" in results

def test_atomic_write_cleanup_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dest_path = tmp_path / "dest.json"

    # Mock NamedTemporaryFile write to raise an exception
    import tempfile

    def mock_ntf(*args, **kwargs):
        raise IOError("Mocked write failure")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", mock_ntf)

    with pytest.raises(IOError, match="Mocked write failure"):
        atomic_write_json(dest_path, {"test": "data"})

    # Destination file should not exist
    assert not dest_path.exists()
    # Check that no orphan temp files are left in parent dir
    temp_files = list(tmp_path.glob("*"))
    assert len(temp_files) == 0 or (len(temp_files) == 1 and temp_files[0] == dest_path)
