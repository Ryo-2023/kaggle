import os
import sys

from mage_ptcg.evaluation.isolated_runtime import run_isolated


def test_normal_child_writes_a_result_shard(tmp_path):
    result = run_isolated((sys.executable, "-c", "print('ok')"), cwd=tmp_path, shard_path=tmp_path / "normal.json", timeout_seconds=3)
    assert result.status == "NORMAL_EXIT"
    assert (tmp_path / "normal.json").is_file()


def test_sigsegv_child_is_recorded_without_killing_parent(tmp_path):
    result = run_isolated((sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"), cwd=tmp_path, shard_path=tmp_path / "segv.json", timeout_seconds=3)
    assert result.status == "SIGSEGV"
    assert result.signal_number == 11


def test_timeout_child_is_terminated_as_a_process_group(tmp_path):
    result = run_isolated((sys.executable, "-c", "import time; time.sleep(30)"), cwd=tmp_path, shard_path=tmp_path / "timeout.json", timeout_seconds=0.05)
    assert result.status == "TIMEOUT"
    assert result.timed_out is True
