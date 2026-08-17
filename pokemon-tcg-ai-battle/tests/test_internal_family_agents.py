from __future__ import annotations

import pytest
import json

from mage_ptcg.family_agents import ConfigDrivenFamilyAgent, FamilyAgentError
from mage_ptcg.offline_scaleup.candidate_runtime import CandidateRuntimeError, _deck_fingerprint, adapter_for
from mage_ptcg.offline_scaleup.multiteacher import build_registry, build_schedule


DECK = [756, 2, *([1] * 58)]
CONFIG = {
    "family_id": "MEGA_KANGASKHAN_EX",
    "anchor_ids": [756],
    "basic_ids": [756],
    "energy_ids": [2],
    "variant_id": "deck-test",
}


def _observation(*, options: list[dict[str, int]], minimum: int = 1) -> dict[str, object]:
    card = {"id": 756, "serial": 0}
    player = {"active": [], "bench": [], "hand": [card]}
    return {
        "current": {"players": [player, player], "yourIndex": 0},
        "select": {"minCount": minimum, "maxCount": max(minimum, 1), "option": options},
    }


def test_returns_bound_deck_for_bootstrap() -> None:
    assert ConfigDrivenFamilyAgent(deck=DECK, config=CONFIG).choose({"select": None}) == DECK


def test_cabt_callback_accepts_optional_configuration() -> None:
    assert ConfigDrivenFamilyAgent(deck=DECK, config=CONFIG).as_agent()({"select": None}, {"seed": 1}) == DECK


def test_family_rule_ranks_own_anchor_play_and_records_selected_rule() -> None:
    agent = ConfigDrivenFamilyAgent(deck=DECK, config=CONFIG)
    choice = agent.choose(_observation(options=[{"type": 14}, {"type": 7, "index": 0}]))
    assert choice == [1]
    assert agent.last_telemetry.fired_rule_ids == ["SETUP_BASIC"]
    assert agent.last_telemetry.fallback_used is False


def test_unselected_rule_is_not_reported_as_activation() -> None:
    agent = ConfigDrivenFamilyAgent(deck=DECK, config=CONFIG)
    choice = agent.choose(_observation(options=[{"type": 7, "index": 0}, {"type": 14}], minimum=0))
    assert choice == []
    assert agent.last_telemetry.fired_rule_ids == []


def test_bad_deck_binding_fails_closed() -> None:
    with pytest.raises(FamilyAgentError, match="anchor"):
        ConfigDrivenFamilyAgent(deck=[2] * 60, config=CONFIG)


def test_internal_adapter_rejects_mismatched_config() -> None:
    entry = {
        "opponent_id": "family-test", "opponent_type": "FAMILY_SPECIFIC", "loader": "family_specific_internal_v1",
        "runtime_fingerprint": "runtime", "deck_fingerprint": _deck_fingerprint(DECK), "family_id": "MEGA_KANGASKHAN_EX", "teacher_trust": "LIMITED",
        "provenance": {"family_config": {**CONFIG, "family_id": "OTHER"}},
    }
    with pytest.raises(CandidateRuntimeError, match="mismatched"):
        adapter_for(entry).prepare(DECK)


def test_internal_family_registry_excludes_rule_teacher_and_binds_adapter(tmp_path) -> None:
    family = {
        "opponent_id": "family-test", "opponent_type": "FAMILY_SPECIFIC", "loader": "family_specific_internal_v1",
        "runtime_fingerprint": "runtime", "deck_fingerprint": _deck_fingerprint(DECK), "family_id": "MEGA_KANGASKHAN_EX",
        "teacher_trust": "LIMITED", "validation_status": "VALIDATED", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "evidence_paths": [], "provenance": {"family_config": CONFIG},
    }
    rule = {"opponent_id": "rule", "opponent_type": "RULE_V0_DECK", "loader": "rule_v0", "runtime_fingerprint": "rule", "deck_fingerprint": _deck_fingerprint(DECK), "teacher_trust": "TRUSTED", "validation_status": "VALIDATED", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED", "evidence_paths": []}
    population = tmp_path / "population.json"
    population.write_text(json.dumps({"population_id": "p", "semantic_population_digest": "digest", "entries": [family, rule]}), encoding="utf-8")
    registry = build_registry(population_path=population, output=tmp_path / "registry.json")
    assert [row["teacher_id"] for row in registry["teachers"]] == ["family-test"]
    assert registry["teachers"][0]["candidate_adapter_type"] == "family_specific_internal_v1"
    schedule = build_schedule(registry_path=tmp_path / "registry.json", population_path=population, games=2, output=tmp_path / "schedule.json")
    assert {row["candidate_adapter_type"] for row in schedule["games"]} == {"family_specific_internal_v1"}
