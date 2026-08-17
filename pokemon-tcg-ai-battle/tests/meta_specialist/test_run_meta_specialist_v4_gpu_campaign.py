"""Contracts for the one-paste V4 GPU training and held-out campaign."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_meta_specialist_v4_gpu_campaign.py"
    spec = importlib.util.spec_from_file_location("run_meta_specialist_v4_gpu_campaign", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lane(tmp_path: Path, lane: str):
    selection = tmp_path / f"{lane}-selection.json"
    selection.write_text(json.dumps({"lane": lane}), encoding="utf-8")
    deck = tmp_path / f"{lane}.csv"
    deck.write_text("deck\n", encoding="utf-8")
    return {
        "lane": lane,
        "selection_manifest": selection,
        "selection_manifest_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "subject_deck_csv": deck,
        "subject_archetype_id": lane,
    }


def _write_training_report(
    output: Path, lane: str, *, selection_manifest_sha256: str = "a" * 64,
) -> dict[str, dict[str, str]]:
    provenance: dict[str, dict[str, str]] = {}
    checkpoint_root = output.parent / f"{output.stem}-checkpoints"
    for seed in (0, 1):
        checkpoint = checkpoint_root / f"seed-{seed}" / "best-recurrent-bc-v4.pt"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(f"{lane}-{seed}".encode())
        provenance[str(seed)] = {
            "best_checkpoint_path": str(checkpoint),
            "best_checkpoint_file_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "best_checkpoint_tensor_state_sha256": f"{seed + 1:064x}",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema": "meta-specialist-recurrent-bc-v4-research-report",
        "lane": lane,
        "device": "cuda:0",
        "selection_manifest_file_sha256": selection_manifest_sha256,
        "coverage_target": {
            "episodes_per_partition": 32,
            "components_per_partition": 32,
            "require_positive_stop": True,
        },
        "decoder_coverage_by_partition": {
            partition: {"positive_stop_target_rows": 1} for partition in ("train", "validation")
        },
        "seed_results": provenance,
    }), encoding="utf-8")
    return provenance


def _write_eval(output: Path, provenance: dict[str, str], *, faults: int = 0) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": "meta-specialist-v4-heldout-checkpoint-strength-v1",
        "checkpoint": {
            "path": str(Path(provenance["best_checkpoint_path"]).resolve()),
            "file_sha256": provenance["best_checkpoint_file_sha256"],
            "tensor_state_sha256": provenance["best_checkpoint_tensor_state_sha256"],
        },
        "opponent_ids": [f"opponent-{index}" for index in range(6)],
        "games_per_seat": 2,
        "requested_games": 24,
        "faults": faults,
        "comparison_status": "invalid_faults" if faults else "valid",
    }), encoding="utf-8")


def test_default_alakazam_campaign_uses_the_teacher_subject_deck() -> None:
    """Model comparisons must not confound policy quality with a deck swap."""
    runner = _load_runner()
    lane = next(item for item in runner.DEFAULT_LANES if item["lane"] == "alakazam")
    expected = runner.ROOT / "opponents" / "nihei_alakazam" / "deck.csv"

    assert Path(lane["subject_deck_csv"]).resolve() == expected.resolve()


def test_campaign_runs_two_lanes_and_reuses_verified_training_and_evaluations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a later resume reruns already verified GPU/CABT artifacts."""
    runner = _load_runner()
    lanes = tuple(_lane(tmp_path, name) for name in ("alakazam", "archaludon"))
    monkeypatch.setattr(runner, "DEFAULT_LANES", lanes)
    calls: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> None:
        assert check is True
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        if command[1].endswith("run_meta_specialist_v4_bc.py"):
            manifest = Path(command[command.index("--selection-manifest") + 1])
            _write_training_report(
                output, json.loads(manifest.read_text(encoding="utf-8"))["lane"],
                selection_manifest_sha256=command[command.index("--selection-manifest-sha256") + 1],
            )
            return
        checkpoint = Path(command[command.index("--checkpoint") + 1])
        lane = next(item["lane"] for item in lanes if item["lane"] in output.name)
        report = json.loads((tmp_path / "campaign" / f"{lane}-training.json").read_text(encoding="utf-8"))
        provenance = next(
            value for value in report["seed_results"].values()
            if Path(value["best_checkpoint_path"]) == checkpoint
        )
        _write_eval(output, provenance)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    argv = ["--output-root", str(tmp_path / "campaign")]
    assert runner.main(argv) == 0
    assert len(calls) == 6
    training_calls = [call for call in calls if call[1].endswith("run_meta_specialist_v4_bc.py")]
    evaluation_calls = [call for call in calls if call[1].endswith("measure_v4_checkpoint_strength.py")]
    assert len(training_calls) == 2
    assert len(evaluation_calls) == 4
    for call in training_calls:
        assert call[call.index("--device") + 1] == "cuda:0"
        assert call[call.index("--max-records") + 1] == "8192"
        assert call[call.index("--episodes-per-partition") + 1] == "32"
        assert call[call.index("--components-per-partition") + 1] == "32"
        assert "--require-positive-stop" in call
    assert all(call[call.index("--opponent-count") + 1] == "6" for call in evaluation_calls)
    assert all(call[call.index("--games-per-seat") + 1] == "2" for call in evaluation_calls)
    assert runner.main(argv) == 0
    assert len(calls) == 6
    summary = json.loads((tmp_path / "campaign" / "campaign-summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "complete"
    assert summary["config"]["games_per_seat"] == 2
    assert set(summary["lanes"]) == {"alakazam", "archaludon"}


def test_campaign_refuses_faulted_heldout_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Breaks if a faulty 24-game result can be recorded as campaign-complete."""
    runner = _load_runner()
    lane = _lane(tmp_path, "alakazam")
    monkeypatch.setattr(runner, "DEFAULT_LANES", (lane,))

    def fake_run(command: list[str], check: bool) -> None:
        output = Path(command[command.index("--output") + 1])
        if command[1].endswith("run_meta_specialist_v4_bc.py"):
            _write_training_report(
                output, "alakazam",
                selection_manifest_sha256=command[command.index("--selection-manifest-sha256") + 1],
            )
        else:
            report = json.loads((tmp_path / "campaign" / "alakazam-training.json").read_text(encoding="utf-8"))
            _write_eval(output, report["seed_results"]["0"], faults=1)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(runner.CampaignError, match="fault"):
        runner.main(["--output-root", str(tmp_path / "campaign")])
    assert not (tmp_path / "campaign" / "campaign-summary.json").exists()


def test_campaign_rejects_evaluation_with_checkpoint_sha_not_in_training_report(tmp_path: Path) -> None:
    """Breaks if a CABT result for another checkpoint can pass a resumed campaign."""
    runner = _load_runner()
    report = tmp_path / "training.json"
    result = _write_training_report(report, "alakazam")["0"]
    provenance = {
        "path": str(Path(result["best_checkpoint_path"]).resolve()),
        "file_sha256": result["best_checkpoint_file_sha256"],
        "tensor_state_sha256": result["best_checkpoint_tensor_state_sha256"],
    }
    evaluation = tmp_path / "eval.json"
    _write_eval(evaluation, {
        "best_checkpoint_path": provenance["path"],
        "best_checkpoint_file_sha256": "f" * 64,
        "best_checkpoint_tensor_state_sha256": provenance["tensor_state_sha256"],
    })

    with pytest.raises(runner.CampaignError, match="file SHA-256"):
        runner._validate_evaluation_report(
            evaluation, provenance, games_per_seat=2, opponent_count=6,
        )
