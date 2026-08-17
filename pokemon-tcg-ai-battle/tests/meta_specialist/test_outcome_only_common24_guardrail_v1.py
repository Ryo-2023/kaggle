from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_common24_guardrail_v1 import (
    OutcomeOnlyCommon24GuardrailError,
    build_outcome_only_common24_guardrail_v1,
    verify_outcome_only_common24_guardrail_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = REPO_ROOT / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"
BROAD = REPO_ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def test_common24_guardrail_materializes_full_evaluation_only_population() -> None:
    artifact = build_outcome_only_common24_guardrail_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        broad_config_path=BROAD,
        candidate_id="attach-plus-120",
        action_deltas={"ATTACH": 120.0},
        base_seed=14910480,
    )
    manifest = artifact["manifest"]
    assert manifest["phase"] == "COMMON24_GUARDRAIL_96"
    assert manifest["summary"]["slot_count"] == 96
    assert manifest["summary"]["train_evaluation_games"] == 80
    assert manifest["summary"]["heldout_evaluation_games"] == 16
    assert manifest["summary"]["heldout_training_exposure"] == 0
    assert len(artifact["control_games"]) == len(artifact["candidate_games"]) == 96
    assert [
        (g.opponent_id, g.seat, g.seed, g.metadata["repetition"])
        for g in artifact["control_games"]
    ] == [
        (g.opponent_id, g.seat, g.seed, g.metadata["repetition"])
        for g in artifact["candidate_games"]
    ]
    assert all(g.metadata["heldout_training_exposure"] == 0 for g in artifact["candidate_games"])


def test_common24_guardrail_roundtrip_and_mutation_rejection() -> None:
    artifact = build_outcome_only_common24_guardrail_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        broad_config_path=BROAD,
        candidate_id="attach-plus-120",
        action_deltas={"ATTACH": 120.0},
        base_seed=14910480,
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    assert verify_outcome_only_common24_guardrail_v1(manifest, repo_root=REPO_ROOT)["screen_sha256"] == manifest["screen_sha256"]
    manifest["opponent_ids"] = manifest["opponent_ids"][:-1]
    with pytest.raises(OutcomeOnlyCommon24GuardrailError, match="semantic SHA"):
        verify_outcome_only_common24_guardrail_v1(manifest, repo_root=REPO_ROOT)


def test_common24_guardrail_rejects_non_attach_candidate() -> None:
    with pytest.raises(OutcomeOnlyCommon24GuardrailError):
        build_outcome_only_common24_guardrail_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            broad_config_path=BROAD,
            candidate_id="end-plus-120",
            action_deltas={"END": 120.0},
            base_seed=14910480,
        )

