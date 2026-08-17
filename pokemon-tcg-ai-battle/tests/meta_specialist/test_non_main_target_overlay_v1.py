from __future__ import annotations

import json
import importlib
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.non_main_target_overlay_v1 import (
    NON_MAIN_TARGET_CONFIG_V1,
    NonMainTargetOverlayError,
    build_non_main_target_agent,
    build_non_main_target_screen_v1,
    verify_non_main_target_screen_v1,
)
from main import make_rule_agent


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = REPO_ROOT / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"


def _target_observation(*, main: bool = False, damage_a: int = 10, hp_a: int = 10) -> dict[str, object]:
    return {
        "current": {"yourIndex": 0},
        "select": {
            "type": 0 if main else 1,
            "minCount": 1,
            "maxCount": 1,
            "option": [
                {"type": 13, "damage": damage_a, "hp": hp_a, "playerIndex": 1},
                {"type": 13, "damage": 111, "hp": 120, "playerIndex": 1},
            ],
        },
    }


def test_non_main_lethal_overlay_is_bounded_and_changes_only_public_target_ranking() -> None:
    candidate = build_non_main_target_agent(candidate_id="nonmain-target-lethal-d120-v1", deck=None, seed=7)
    baseline = make_rule_agent(deck=None, seed=7)
    observation = _target_observation(damage_a=10, hp_a=10)

    assert baseline(observation) == [1]
    assert candidate(observation) == [0]
    assert candidate.stats["eligible_non_main_decisions"] == 1
    assert candidate.stats["changed_target_decisions"] == 1


def test_main_selection_and_malformed_public_options_use_exact_rule_fallback() -> None:
    candidate = build_non_main_target_agent(candidate_id="nonmain-target-lethal-d120-v1", deck=None, seed=7)
    baseline = make_rule_agent(deck=None, seed=7)
    main_observation = _target_observation(main=True)
    malformed = _target_observation()
    malformed["select"]["option"][0]["damage"] = "hidden"

    assert candidate(main_observation) == baseline(main_observation)
    assert candidate(malformed) == baseline(malformed)
    assert candidate.stats["main_fallback_decisions"] == 1
    assert candidate.stats["malformed_fallback_decisions"] == 1


def test_manifest_binds_target_config_and_roundtrips_strictly() -> None:
    artifact = build_non_main_target_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="nonmain-target-lethal-d120-v1",
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))

    assert manifest["target_config"] == NON_MAIN_TARGET_CONFIG_V1
    assert manifest["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
        "longrun_authority": False,
    }
    assert manifest["research_only"] is True
    assert manifest["execution_allowed"] is False
    assert verify_non_main_target_screen_v1(manifest, repo_root=REPO_ROOT)["screen_sha256"] == manifest["screen_sha256"]
    assert len(artifact["control_games"]) == len(artifact["candidate_games"]) == 48
    for game in artifact["control_games"] + artifact["candidate_games"]:
        assert game.metadata["schema_version"] == manifest["schema_version"]
        assert game.metadata["screen_sha256"] == manifest["screen_sha256"]
        assert game.policy_sha256 in {
            manifest["control_policy_sha256"],
            manifest["candidate_policy_sha256"],
        }


def test_manifest_rejects_target_config_mutation() -> None:
    artifact = build_non_main_target_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="nonmain-target-lethal-d120-v1",
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["target_config"]["lethal_bonus_delta"] = 121.0
    with pytest.raises(NonMainTargetOverlayError, match="semantic SHA"):
        verify_non_main_target_screen_v1(manifest, repo_root=REPO_ROOT)


def test_runner_ref_resolves_to_spawn_callable() -> None:
    from mage_ptcg.meta_specialist.non_main_target_overlay_v1 import RUNNER_REF_V1

    module_name, function_name = RUNNER_REF_V1.split(":", 1)
    assert callable(getattr(importlib.import_module(module_name), function_name))


def test_candidate_factory_returns_plain_function_shape() -> None:
    candidate = build_non_main_target_agent(candidate_id="nonmain-target-lethal-d120-v1", seed=7)
    assert callable(candidate)
    assert candidate.__name__ == "nonmain-target-lethal-d120-v1_research_only"
    assert isinstance(candidate.stats, dict)
