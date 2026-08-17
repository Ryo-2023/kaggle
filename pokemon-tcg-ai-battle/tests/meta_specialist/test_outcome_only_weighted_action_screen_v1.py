from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_weighted_action_screen_v1 import (
    OutcomeOnlyWeightedActionScreenError,
    build_outcome_only_weighted_action_screen_v1,
    select_weighted_slots_v1,
    verify_outcome_only_weighted_action_screen_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = REPO_ROOT / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"


def test_weighted_slots_are_deterministic_balanced_and_quota_preserving() -> None:
    artifact = build_outcome_only_weighted_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="attach-plus-120",
        action_deltas={"ATTACH": 120.0},
    )
    manifest = artifact["manifest"]
    slots = tuple(manifest["slots"])
    assert len(slots) == 48
    assert sum(int(row["seat"] == 0) for row in slots) == 24
    assert sum(int(row["seat"] == 1) for row in slots) == 24
    assert all(row["split"] == "META_TRAIN" for row in slots)
    assert len({row["stratum_key"] for row in slots}) == 48
    assert slots == select_weighted_slots_v1(json.loads(SCHEDULE.read_text()), base_seed=manifest["seed_base"])


@pytest.mark.parametrize("deltas", [{"PLAY": 120.0}, {"EVOLVE": -120.0}, {"ATTACH": 121.0}, {"END": 0.0}])
def test_weighted_screen_rejects_unapproved_surface(deltas: dict[str, float]) -> None:
    with pytest.raises(OutcomeOnlyWeightedActionScreenError):
        build_outcome_only_weighted_action_screen_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            candidate_id="bad",
            action_deltas=deltas,
        )


def test_weighted_screen_roundtrip_and_paired_games() -> None:
    artifact = build_outcome_only_weighted_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="end-plus-120",
        action_deltas={"END": 120.0},
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    assert verify_outcome_only_weighted_action_screen_v1(manifest, repo_root=REPO_ROOT)["screen_sha256"] == manifest["screen_sha256"]
    control = artifact["control_games"]
    candidate = artifact["candidate_games"]
    assert len(control) == len(candidate) == 48
    keys = lambda games: [(g.opponent_id, g.seat, g.seed, g.metadata["stratum_key"]) for g in games]
    assert keys(control) == keys(candidate)
    assert all(g.metadata["heldout_exposure"] == 0 for g in candidate)
    assert all(g.metadata["opponent_usage_boundary"] == "local_eval_only" for g in candidate)


def test_weighted_screen_rejects_manifest_mutation() -> None:
    artifact = build_outcome_only_weighted_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="attach-plus-120",
        action_deltas={"ATTACH": 120.0},
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["slots"] = manifest["slots"][:-1]
    with pytest.raises(OutcomeOnlyWeightedActionScreenError, match="semantic SHA"):
        verify_outcome_only_weighted_action_screen_v1(manifest, repo_root=REPO_ROOT)

