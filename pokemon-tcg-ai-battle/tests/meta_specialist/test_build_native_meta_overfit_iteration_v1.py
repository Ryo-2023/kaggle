from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist import native_meta_overfit_iteration_v1 as iteration
from scripts import build_native_meta_overfit_iteration_v1 as cli
from tests.meta_specialist.test_native_meta_overfit_iteration_v1 import (
    _sources,
    _write_canonical,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    baseline_path = root / "native-baseline.json"
    _write_canonical(baseline_path, baseline)
    return root, curriculum, adapter, table, baseline_path


def _args(root: Path, curriculum: Path, adapter: Path, table: Path, baseline: Path, run_root: Path):
    return [
        "--repo-root",
        str(root),
        "--curriculum-manifest",
        str(curriculum),
        "--outcome-adapter-manifest",
        str(adapter),
        "--public-advantage-table",
        str(table),
        "--native-baseline-identity",
        str(baseline),
        "--run-root",
        str(run_root),
    ]


def test_run_root_dry_run_materializes_progress_iteration_and_candidate_table(
    tmp_path, monkeypatch, capsys
):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    run_root = root / "new-run"

    assert cli.main(_args(root, curriculum, adapter, table, baseline, run_root)) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "DRY_RUN"
    assert summary["run_root"] == str(run_root.resolve())
    assert summary["processes_launched"] is False
    assert summary["cabt_started"] is False
    assert summary["training_started"] is False
    assert summary["submission_started"] is False
    assert summary["ready_for_evaluation"] is False
    assert (run_root / "progress_summary.json").is_file()
    assert (run_root / "iteration-manifest.json").is_file()
    assert (run_root / "candidate-public-advantage-table.json").is_file()
    assert (run_root / "run-manifest.json").is_file()
    assert _sha(run_root / "candidate-public-advantage-table.json") == _sha(table)


def test_run_root_is_new_and_existing_destination_is_never_overwritten(
    tmp_path, monkeypatch, capsys
):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    run_root = root / "existing-run"
    run_root.mkdir()
    sentinel = run_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    assert cli.main(_args(root, curriculum, adapter, table, baseline, run_root)) == 1
    capsys.readouterr()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in run_root.iterdir()) == ["sentinel.txt"]


def test_run_root_must_be_contained_by_repo_root(tmp_path, monkeypatch, capsys):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    outside = tmp_path.parent / "native-meta-overfit-outside"
    assert cli.main(_args(root, curriculum, adapter, table, baseline, outside)) == 1
    capsys.readouterr()
    assert not outside.exists()


def test_forged_public_table_sha_fails_closed_and_rolls_back_run_root(
    tmp_path, monkeypatch, capsys
):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    forged = root / "forged-table.json"
    payload = json.loads(table.read_text(encoding="utf-8"))
    payload["table_sha256"] = "0" * 64
    _write_canonical(forged, payload)
    run_root = root / "forged-run"

    assert cli.main(_args(root, curriculum, adapter, forged, baseline, run_root)) == 1
    capsys.readouterr()
    assert not run_root.exists()


def test_record_blocked_persists_non_promotable_input_failure(tmp_path, monkeypatch, capsys):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    forged = root / "forged-table.json"
    payload = json.loads(table.read_text(encoding="utf-8"))
    payload["table_sha256"] = "0" * 64
    _write_canonical(forged, payload)
    run_root = root / "blocked-run"

    assert cli.main(_args(root, curriculum, adapter, forged, baseline, run_root) + ["--record-blocked"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "BLOCKED"
    assert summary["ready_for_evaluation"] is False
    assert summary["candidate_artifacts_materialized"] is False
    assert (run_root / "progress_summary.json").is_file()
    assert (run_root / "run-manifest.json").is_file()
    assert not (run_root / "iteration-manifest.json").exists()


def test_execute_is_rejected_without_creating_run_root(tmp_path, monkeypatch, capsys):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    run_root = root / "execute-run"
    assert cli.main(_args(root, curriculum, adapter, table, baseline, run_root) + ["--execute"]) == 2
    assert "--execute is disabled" in capsys.readouterr().err
    assert not run_root.exists()


def test_materializer_never_launches_child_process(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    run_root = root / "no-process-run"
    called = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: called.append((args, kwargs)))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: called.append((args, kwargs)))

    result = cli.materialize_dry_run(
        repo_root=root,
        curriculum_manifest=curriculum,
        outcome_adapter_manifest=adapter,
        public_advantage_table=table,
        native_baseline_identity=baseline,
        run_root=run_root,
    )
    assert result["processes_launched"] is False
    assert called == []


def test_partial_public_table_copy_is_atomic_and_not_left_in_blocked_root(
    tmp_path, monkeypatch
):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    run_root = root / "partial-copy-run"

    def fail_after_partial_copy(source, destination, length=0):
        destination.write(b"partial-table-bytes")
        raise OSError("injected copy interruption")

    monkeypatch.setattr(cli.shutil, "copyfileobj", fail_after_partial_copy)

    result = cli.materialize_dry_run(
        repo_root=root,
        curriculum_manifest=curriculum,
        outcome_adapter_manifest=adapter,
        public_advantage_table=table,
        native_baseline_identity=baseline,
        run_root=run_root,
        record_blocked=True,
    )

    assert result["status"] == "BLOCKED"
    assert result["ready_for_evaluation"] is False
    assert not (run_root / "candidate-public-advantage-table.json").exists()
    assert sorted(path.name for path in run_root.iterdir()) == [
        "progress_summary.json",
        "run-manifest.json",
    ]


def test_competing_destination_is_never_clobbered_by_atomic_copy(
    tmp_path, monkeypatch
):
    root, curriculum, adapter, table, baseline = _prepare(tmp_path, monkeypatch)
    destination = root / "winner.json"

    def competing_link(source, target, *args, **kwargs):
        Path(target).write_bytes(b"winner-by-other-writer")
        raise FileExistsError(target)

    monkeypatch.setattr(cli.os, "link", competing_link)

    with pytest.raises(FileExistsError):
        cli._atomic_copy_new(table, destination)

    assert destination.read_bytes() == b"winner-by-other-writer"
    assert not list(root.glob(f".{destination.name}.tmp.*"))
