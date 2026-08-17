from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.performance_sprint_v1 import (
    EvaluationGameV1,
    FreshRolloutV1,
    PerformanceSprintConfigV1,
    PerformanceSprintHooksV1,
    PerformanceSprintV1Error,
    fresh_rollout_from_collection_summary_v1,
    make_runtime_policy_preflight_current_r2_v1,
    run_performance_sprint_v1,
)


def _write_checkpoint(path: Path, contents: bytes) -> Path:
    path.write_bytes(contents)
    return path


def _config(tmp_path: Path, *, warmup_updates: int = 1) -> PerformanceSprintConfigV1:
    return PerformanceSprintConfigV1(
        run_dir=tmp_path / "sprint",
        baseline_checkpoint=_write_checkpoint(tmp_path / "baseline.pt", b"baseline"),
        training_opponent_instance_ids=("train-a", "train-b"),
        evaluation_opponent_instance_ids=("holdout-a", "holdout-b"),
        seed_qualification_report=_write_checkpoint(tmp_path / "seed-qualification.json", b"{}"),
        value_warmup_updates=warmup_updates,
    )


def _evaluation(*, candidate: str, baseline: str) -> list[EvaluationGameV1]:
    return [
        EvaluationGameV1(arm=arm, opponent_instance_id=opponent, seat=seat, outcome=outcome)
        for arm, outcome in (("candidate", candidate), ("baseline", baseline))
        for opponent in ("holdout-a", "holdout-b")
        for seat in (0, 1)
    ]


def test_sprint_consumes_distinct_fresh_rollouts_and_publishes_reloaded_challenger(tmp_path: Path) -> None:
    baseline = _write_checkpoint(tmp_path / "baseline.pt", b"baseline")
    warmed = _write_checkpoint(tmp_path / "warmed.pt", b"warmed-value-head")
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate-actor")
    consumed: list[tuple[str, str, str]] = []

    def collect(phase: str, ordinal: int, checkpoint: Path) -> FreshRolloutV1:
        consumed.append((phase, str(ordinal), checkpoint.name))
        return FreshRolloutV1(rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / f"{phase}-{ordinal}")

    def warmup(checkpoint: Path, rollout: FreshRolloutV1) -> Path:
        assert checkpoint == baseline
        assert rollout.rollout_id == "value-warmup-0"
        return warmed

    def actor(checkpoint: Path, rollout: FreshRolloutV1) -> Path:
        assert checkpoint == warmed
        assert rollout.rollout_id == "actor-0"
        return candidate

    hooks = PerformanceSprintHooksV1(
        collect_fresh=collect,
        warmup_value_head=warmup,
        actor_update=actor,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="loss"),
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )
    result = run_performance_sprint_v1(_config(tmp_path), hooks)

    assert consumed == [("value-warmup", "0", "baseline.pt"), ("actor", "0", "warmed.pt")]
    assert result.promoted is True
    assert result.rollback_applied is False
    assert result.selected_checkpoint == result.challenger_checkpoint
    assert result.challenger_checkpoint.read_bytes() == b"candidate-actor"
    assert result.reloaded_sha256 == result.challenger_sha256
    decision = json.loads((result.run_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["selected_checkpoint"] == str(result.challenger_checkpoint)
    assert decision["evaluation"]["candidate_score"] == 1.0


def test_sprint_rolls_back_to_baseline_when_independent_challenger_score_degrades(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / f"{phase}-{ordinal}"
        ),
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="loss", baseline="win"),
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )
    result = run_performance_sprint_v1(_config(tmp_path), hooks)

    assert result.promoted is False
    assert result.rollback_applied is True
    assert result.selected_checkpoint == result.baseline_checkpoint
    assert result.challenger_checkpoint.is_file()  # retained as an auditable rejected artifact


def test_sprint_rejects_reusing_a_rollout_across_optimizer_updates(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id="reused", collection_dir=tmp_path / phase
        ),
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="loss"),
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )

    with pytest.raises(PerformanceSprintV1Error, match="already consumed"):
        run_performance_sprint_v1(_config(tmp_path), hooks)


def test_sprint_rejects_evaluation_that_omits_a_holdout_seat(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / phase
        ),
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="loss")[:-1],
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )

    with pytest.raises(PerformanceSprintV1Error, match="both arms"):
        run_performance_sprint_v1(_config(tmp_path), hooks)


def test_sprint_rejects_a_value_warmup_that_changes_actor_parameters(tmp_path: Path) -> None:
    warmed = _write_checkpoint(tmp_path / "warmed.pt", b"warmed")
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / phase
        ),
        warmup_value_head=lambda checkpoint, rollout: warmed,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="loss"),
        actor_state_sha256=lambda path: "a" * 64 if path.name == "baseline.pt" else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )

    with pytest.raises(PerformanceSprintV1Error, match="value warm-up changed actor"):
        run_performance_sprint_v1(_config(tmp_path), hooks)


def test_sprint_fails_closed_before_collection_when_baseline_does_not_runtime_load(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    collected = False

    def collect(phase: str, ordinal: int, checkpoint: Path) -> FreshRolloutV1:
        nonlocal collected
        collected = True
        return FreshRolloutV1(rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / phase)

    hooks = PerformanceSprintHooksV1(
        collect_fresh=collect,
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="loss"),
        actor_state_sha256=lambda path: "a" * 64,
        runtime_policy_preflight=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("state mismatch")),
    )

    with pytest.raises(PerformanceSprintV1Error, match="runtime policy preflight"):
        run_performance_sprint_v1(_config(tmp_path), hooks)
    assert collected is False


@pytest.mark.parametrize(
    ("checkpoint", "summary"),
    [
        (
            Path("runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-alakazam/checkpoints/checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt"),
            Path("runs/meta-specialist-actor-pool/perf-sprint-connect-alakazam/run_summary.json"),
        ),
        (
            Path("runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt"),
            Path("runs/meta-specialist-actor-pool/perf-sprint-connect-archaludon/run_summary.json"),
        ),
    ],
)
def test_compatible_v2smoke_baselines_runtime_load_and_existing_collections_validate_fresh(
    checkpoint: Path, summary: Path,
) -> None:
    if not checkpoint.is_file() or not summary.is_file():
        pytest.skip("local compatible v2smoke artifacts are unavailable")

    make_runtime_policy_preflight_current_r2_v1(checkpoint_lineage_id="performance-sprint-smoke")(checkpoint)
    rollout = fresh_rollout_from_collection_summary_v1(summary, expected_behavior_identity=checkpoint.stem[11:])

    assert rollout.collection_dir.name == "games"
    assert rollout.rollout_id.startswith("fresh-collection:")


def test_collection_summary_validator_rejects_fault_or_resumed_reuse(tmp_path: Path) -> None:
    summary = tmp_path / "run_summary.json"
    summary.write_text(json.dumps({
        "schema_version": "meta-specialist-collect-trajectories-run-summary-v1",
        "run_name": "fresh-test",
        "games_dir": str(tmp_path / "games"),
        "behavior_kind": "neural_specialist",
        "behavior_identity": "a" * 64,
        "num_games_requested": 2,
        "games_completed": 2,
        "games_faulted": 1,
        "games_timeout": 0,
        "games_resumed_skipped": 1,
        "per_lane": {"lane": {"seats": {"0": {"collected": 1}, "1": {"collected": 1}}}},
    }), encoding="utf-8")

    with pytest.raises(PerformanceSprintV1Error, match="fault-free"):
        fresh_rollout_from_collection_summary_v1(summary, expected_behavior_identity="a" * 64)


def test_quick_screen_tie_keeps_baseline(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / phase
        ),
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="win", baseline="win"),
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )

    result = run_performance_sprint_v1(_config(tmp_path), hooks)

    assert result.candidate_score == result.baseline_score
    assert result.promoted is False
    assert result.selected_checkpoint == result.baseline_checkpoint


def test_quick_screen_fault_invalidates_the_comparison(tmp_path: Path) -> None:
    candidate = _write_checkpoint(tmp_path / "candidate.pt", b"candidate")
    hooks = PerformanceSprintHooksV1(
        collect_fresh=lambda phase, ordinal, checkpoint: FreshRolloutV1(
            rollout_id=f"{phase}-{ordinal}", collection_dir=tmp_path / phase
        ),
        warmup_value_head=lambda checkpoint, rollout: checkpoint,
        actor_update=lambda checkpoint, rollout: candidate,
        evaluate=lambda challenger, reference, slots: _evaluation(candidate="fault", baseline="loss"),
        actor_state_sha256=lambda path: "a" * 64 if path != candidate else "b" * 64,
        runtime_policy_preflight=lambda checkpoint: None,
    )

    with pytest.raises(PerformanceSprintV1Error, match="fault-free"):
        run_performance_sprint_v1(_config(tmp_path), hooks)
