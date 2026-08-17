"""Contracts for the single-bar V4 longrun terminal monitor."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.monitor_meta_specialist_v4_longrun import MonitorSnapshotV4, _snapshot_from_files_v4


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_snapshot_aggregates_training_and_evaluation_progress(tmp_path: Path) -> None:
    _write(tmp_path / "run-manifest.json", {
        "schema": "meta-specialist-v4-archaludon-longrun-v2",
        "status": "running", "stage": "training", "config_sha256": "a" * 64,
    })
    _write(tmp_path / "progress_summary.json", {
        "status": "running", "stage": "training", "child_stage": "training", "seed": 1,
        "epochs_completed": 2, "epochs_requested": 3, "optimizer_updates_completed": 1024,
        "latest_validation_complete_action_nll": 0.63, "latest_gradient_norm": 1.1,
        "invocation_elapsed_seconds": 123.0,
    })
    _write(tmp_path / "training-progress.json", {
        "schema": "meta-specialist-recurrent-bc-v4-progress-v1", "status": "running",
        "stage": "training", "seed": 1, "epochs_completed": 2,
        "epochs_requested": 3, "optimizer_updates_completed": 1024,
    })

    snapshot = _snapshot_from_files_v4(tmp_path)

    assert isinstance(snapshot, MonitorSnapshotV4)
    assert snapshot.status == "running"
    assert snapshot.stage == "training"
    assert snapshot.seed == 1
    assert snapshot.completed == 5
    assert snapshot.total == 6
    assert snapshot.optimizer_updates == 1024
    assert snapshot.validation_nll == 0.63


def test_snapshot_marks_finished_run_and_does_not_require_child_files(tmp_path: Path) -> None:
    _write(tmp_path / "run-manifest.json", {
        "schema": "meta-specialist-v4-archaludon-longrun-v2",
        "status": "complete", "stage": "complete", "config_sha256": "b" * 64,
        "heldout_evaluations": {"0": "seed-0.json", "1": "seed-1.json"},
    })

    snapshot = _snapshot_from_files_v4(tmp_path)

    assert snapshot.status == "complete"
    assert snapshot.stage == "complete"
    assert snapshot.completed == snapshot.total == 1
    assert snapshot.faults == 0


def test_snapshot_marks_stale_wrapper_as_evaluation_pending_after_training_finishes(tmp_path: Path) -> None:
    _write(tmp_path / "run-manifest.json", {
        "schema": "meta-specialist-v4-archaludon-longrun-v2",
        "status": "running", "stage": "training",
        "config": {"seeds": [0, 1], "epochs": 3, "games_per_seat": 8},
    })
    _write(tmp_path / "progress_summary.json", {
        "status": "running", "stage": "training", "seed": 1,
        "epochs_completed": 3, "epochs_requested": 3,
    })
    _write(tmp_path / "training-progress.json", {
        "status": "complete", "stage": "training", "seed": 1,
        "epochs_completed": 3, "epochs_requested": 3,
    })

    snapshot = _snapshot_from_files_v4(tmp_path)

    assert snapshot.status == "evaluation_pending"
    assert snapshot.stage == "evaluation_pending"
    assert snapshot.completed == 6
    assert snapshot.total == 198
