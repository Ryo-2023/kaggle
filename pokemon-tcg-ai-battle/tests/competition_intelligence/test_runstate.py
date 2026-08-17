"""Run manifest / lock tests: creation, resume, stale-lock recovery, contention."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence import runstate


class TestLoadOrCreate:
    def test_creates_new_run_with_expected_layout(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        state = runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc123", config_hash="h1", resume=False)
        assert state.manifest["run_id"] == "run-1"
        assert state.manifest["schema_version"] == runstate.MANIFEST_SCHEMA_VERSION
        for name in ("raw", "source_manifests", "normalized", "derived", "snapshots", "reports", "quarantine", "state"):
            assert (run_dir / name).exists()

    def test_creating_twice_without_resume_fails(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        with pytest.raises(runstate.RunStateError):
            runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)

    def test_resume_requires_existing_manifest(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "does-not-exist"
        with pytest.raises(runstate.RunStateError):
            runstate.load_or_create(run_dir, run_id="x", git_commit="abc", config_hash="h1", resume=True)

    def test_resume_rejects_config_hash_mismatch(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        with pytest.raises(runstate.RunStateError):
            runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="DIFFERENT", resume=True)

    def test_resume_increments_resume_count(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        state = runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=True)
        assert state.manifest["resume_count"] == 1

    def test_corrupt_manifest_schema_is_rejected(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        state = runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        manifest = json.loads(state.paths.manifest.read_text(encoding="utf-8"))
        manifest["schema_version"] = "wrong"
        state.paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(runstate.RunStateError):
            runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=True)


class TestRecordIngestedSource:
    def test_records_are_deduplicated_and_sorted(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        state = runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        state.record_ingested_source("b")
        state.record_ingested_source("a")
        state.record_ingested_source("a")  # duplicate, no-op
        assert state.manifest["ingested_source_ids"] == ["a", "b"]


class TestLock:
    def test_reentrant_within_same_process(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        paths = runstate.RunPaths(run_dir)
        paths.root.mkdir(parents=True)
        runstate.acquire_lock(paths, "run-1")
        runstate.acquire_lock(paths, "run-1")  # must not deadlock or raise
        runstate.release_lock(paths)

    def test_stale_lock_from_dead_pid_is_recovered(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        paths = runstate.RunPaths(run_dir)
        paths.root.mkdir(parents=True)
        # a PID astronomically unlikely to be alive
        dead_payload = {"pid": 2**30, "hostname": "nowhere", "process_start_marker": None, "created_at": "x", "run_id": "run-1"}
        paths.lock.write_text(json.dumps(dead_payload), encoding="utf-8")
        runstate.acquire_lock(paths, "run-1")  # should recover, not raise
        assert json.loads(paths.lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
        runstate.release_lock(paths)

    def test_active_lock_from_another_pid_blocks(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        paths = runstate.RunPaths(run_dir)
        paths.root.mkdir(parents=True)
        # A real, genuinely-alive process that is not us, with a real
        # process_start_marker and matching hostname, so the staleness check
        # correctly identifies it as an active lock rather than recovering it.
        process = subprocess.Popen(["sleep", "30"])
        try:
            time.sleep(0.05)  # let /proc/<pid>/stat populate
            live_payload = {
                "pid": process.pid,
                "hostname": socket.gethostname(),
                "process_start_marker": runstate.process_start_marker(process.pid),
                "created_at": "x",
                "run_id": "run-1",
            }
            paths.lock.write_text(json.dumps(live_payload), encoding="utf-8")
            with pytest.raises(runstate.RunStateError):
                runstate.acquire_lock(paths, "run-1")
        finally:
            process.terminate()
            process.wait(timeout=5)
            if paths.lock.exists():
                paths.lock.unlink()

    def test_run_lock_context_manager_releases_on_exception(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        paths = runstate.RunPaths(run_dir)
        paths.root.mkdir(parents=True)
        with pytest.raises(ValueError):
            with runstate.run_lock(paths, "run-1"):
                raise ValueError("boom")
        assert not paths.lock.exists()


class TestEventsAppend:
    def test_append_event_is_durable_jsonl(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-1"
        state = runstate.load_or_create(run_dir, run_id="run-1", git_commit="abc", config_hash="h1", resume=False)
        state.append_event("custom_event", detail="x")
        lines = state.paths.events.read_text(encoding="utf-8").strip().splitlines()
        assert any(json.loads(line)["type"] == "custom_event" for line in lines)
