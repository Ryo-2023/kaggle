from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.frozen_residual_v1 import ResidualCoverageSnapshotV1


def _runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_frozen_residual_cabt_eval_v1.py"
    spec = importlib.util.spec_from_file_location("run_frozen_residual_cabt_eval_v1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(runner, tmp_path: Path, *, execute: bool, games: int = 2):
    return runner._parser().parse_args([
        "--sidecar", str(tmp_path / "sidecar.pt"), "--sidecar-sha256", "0" * 64,
        "--preflight", str(tmp_path / "preflight.json"), "--preflight-sha256", "1" * 64,
        "--seed", "0", "--subject-deck-csv", str(tmp_path / "deck.csv"),
        "--subject-archetype-id", "archaludon", "--games-per-cell", str(games),
        "--output", str(tmp_path / "out.json"), *( ["--execute"] if execute else []),
    ])


def test_execute_is_required_before_any_file_or_cabt_access(tmp_path: Path):
    runner = _runner()
    with pytest.raises(ValueError, match="--execute"):
        runner.evaluate(_args(runner, tmp_path, execute=False))


def test_first_bounded_gate_is_exactly_two_games_per_cell(tmp_path: Path):
    runner = _runner()
    with pytest.raises(ValueError, match="games-per-cell"):
        runner.evaluate(_args(runner, tmp_path, execute=True, games=1))
    with pytest.raises(ValueError, match="games-per-cell"):
        runner.evaluate(_args(runner, tmp_path, execute=True, games=3))


def test_coverage_summary_preserves_observed_snapshot_and_cell_ledger():
    runner = _runner()
    from mage_ptcg.meta_specialist.frozen_residual_v1 import ResidualCoverageSnapshotV1

    snapshot = ResidualCoverageSnapshotV1(
        total_decisions=3,
        valid_context_decisions=2,
        exact_known_context=1,
        eligible_action_slots=5,
        known_action_slots=2,
        residual_applied_slots=2,
        nonzero_residual_slots=1,
        top1_change_decisions=1,
        ood_pass_through=2,
        stop_decisions=1,
        known_stop_decisions=1,
        nonzero_stop_decisions=0,
        action_type_total={"7": 3},
        action_type_known={"7": 1},
        action_type_nonzero={"7": 1},
        pass_through_reasons={"unknown_context": 2},
        residual_magnitudes=(0.0, 0.25),
    )
    summary = runner._coverage_result(snapshot, by_cell={})
    assert summary["observed"] is True
    assert summary["total_decisions"] == 3
    assert summary["nonzero_residual_slots"] == 1
    assert summary["pass_through_reasons"] == {"unknown_context": 2}


def test_coverage_serializer_marks_exact_gate_as_measured_and_coarse_as_unmeasured(tmp_path: Path):
    runner = _runner()
    snapshot = ResidualCoverageSnapshotV1(
        total_decisions=2,
        valid_context_decisions=2,
        exact_known_context=1,
        eligible_action_slots=4,
        known_action_slots=2,
        residual_applied_slots=2,
        nonzero_residual_slots=1,
        top1_change_decisions=1,
        ood_pass_through=1,
        pass_through_reasons={"unknown_context": 1},
    )
    payload = runner._coverage_result(snapshot, by_cell={"opponent:seat-0:game-0": snapshot.to_dict()})
    assert payload["observed"] is True
    assert payload["reason"] == "measured_sidecar_runtime_counters"
    assert payload["exact_known_context"] == 1
    assert payload["residual_applied_slots"] == 2
    assert payload["nonzero_residual_slots"] == 1
    assert payload["pass_through_reasons"] == {"unknown_context": 1}
    assert payload["coarse_public_bucket_observed"] is False
    assert payload["known_public_bucket"] is None
    assert payload["known_public_bucket_rate"] is None
    assert payload["by_opponent_seat_game"]["opponent:seat-0:game-0"]["total_decisions"] == 2


def test_research_evaluator_never_promotes_fault_free_smoke_to_performance_evidence():
    runner = _runner()
    flags = runner._research_evidence_flags()
    assert flags["performance_evidence"] is False
    assert flags["coverage_evidence"] is True
    assert "diagnostic" in flags["performance_evidence_reason"]
