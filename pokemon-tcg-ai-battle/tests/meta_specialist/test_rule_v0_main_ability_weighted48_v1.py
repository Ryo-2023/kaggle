from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.rule_v0_main_ability_weighted48_v1 import (
    CANDIDATE_ID_V1,
    SCHEMA_V1,
    RuleV0MainAbilityWeighted48Error,
    build_rule_v0_main_ability_agent_v1,
    build_rule_v0_main_ability_weighted48_v1,
    verify_rule_v0_main_ability_weighted48_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"
)


def _main_observation(options: list[dict[str, object]]) -> dict[str, object]:
    return {
        "select": {"type": 0, "minCount": 1, "maxCount": 1, "option": options},
        "current": {"yourIndex": 0},
    }


def test_ability_overlay_changes_only_main_action_rank_and_falls_back_exactly() -> None:
    candidate = build_rule_v0_main_ability_agent_v1(deck=[1] * 60, seed=0)
    options = [
        {"type": 7, "index": 0},  # Rule v0 PLAY score 400
        {"type": 10, "index": 1},  # Rule v0 ABILITY score 300 + 120
    ]
    assert candidate(_main_observation(options)) == [1]

    non_main = {"select": {"type": 1, "minCount": 1, "maxCount": 1, "option": options}}
    baseline = candidate(non_main)
    assert baseline == [0]
    assert candidate({"select": None}) == [1] * 60
    assert candidate({"select": {"type": 0, "minCount": 1, "maxCount": 1, "option": None}}) == []


def test_ability_weighted_materialization_is_sealed_and_paired() -> None:
    artifact = build_rule_v0_main_ability_weighted48_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id=CANDIDATE_ID_V1,
    )
    manifest = artifact["manifest"]
    assert manifest["schema_version"] == SCHEMA_V1
    assert manifest["phase"] == "MAIN_ABILITY_WEIGHTED48"
    assert manifest["action_deltas"] == {"ABILITY": 120.0}
    assert manifest["candidate_config"]["scope"] == "MAIN_ONLY"
    assert manifest["candidate_config"]["fallback"] == "RULE_V0_EXACT"
    assert manifest["summary"]["slot_count"] == 48
    assert manifest["summary"]["seat_counts"] == {"0": 24, "1": 24}
    assert manifest["summary"]["heldout_exposure"] == 0
    assert manifest["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
        "longrun_authority": False,
    }
    assert verify_rule_v0_main_ability_weighted48_v1(
        json.loads(json.dumps(manifest)), repo_root=REPO_ROOT
    )["screen_sha256"] == manifest["screen_sha256"]

    control = artifact["control_games"]
    candidate = artifact["candidate_games"]
    assert len(control) == len(candidate) == 48
    assert [game.runner_ref for game in candidate] == [manifest["runner_ref"]] * 48
    assert [
        (game.opponent_id, game.seat, game.seed, game.metadata["stratum_key"])
        for game in control
    ] == [
        (game.opponent_id, game.seat, game.seed, game.metadata["stratum_key"])
        for game in candidate
    ]
    assert all(game.metadata["heldout_exposure"] == 0 for game in candidate)
    assert all(game.metadata["opponent_usage_boundary"] == "local_eval_only" for game in candidate)
    assert all(game.metadata["synthetic_opponent"] is False for game in candidate)


def test_ability_weighted_manifest_mutation_is_rejected() -> None:
    artifact = build_rule_v0_main_ability_weighted48_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id=CANDIDATE_ID_V1,
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["candidate_config"]["action_deltas"] = {"ABILITY": 119.0}
    with pytest.raises(RuleV0MainAbilityWeighted48Error, match="semantic SHA"):
        verify_rule_v0_main_ability_weighted48_v1(manifest, repo_root=REPO_ROOT)


def test_ability_weighted_rejects_non_ability_candidate_id_or_delta() -> None:
    with pytest.raises(RuleV0MainAbilityWeighted48Error):
        build_rule_v0_main_ability_weighted48_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            candidate_id="other",
        )


def test_ability_factory_exposes_bounded_override_and_fallback_telemetry() -> None:
    candidate = build_rule_v0_main_ability_agent_v1(deck=[1] * 60, seed=0)
    assert getattr(candidate, "telemetry_schema") == "rule-v0-main-ability-telemetry-v1"
    assert candidate(_main_observation([{"type": 7, "index": 0}, {"type": 10, "index": 1}])) == [1]
    assert candidate({"select": {"type": 1, "minCount": 1, "maxCount": 1, "option": [{"type": 10, "index": 0}]}}) == [0]
    telemetry = getattr(candidate, "telemetry")
    assert telemetry["eligible_main_observations"] == 1
    assert telemetry["override_attempts"] == 1
    assert telemetry["override_applied"] == 1
    assert telemetry["fallback_count"] == 0
