from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.optimization.semantic_trace import (OLD_TRACE_STATUS, SEMANTIC_COMPLETE,
                                                    SEMANTIC_OPTIONAL_UNKNOWN, SemanticTraceError,
                                                    audit_v2_migration, resolve_action_semantics,
                                                    semantic_equivalent, validate_semantic_decisions,
                                                    validate_v2_usage)
from mage_ptcg.optimization.semantic_failure_lab import SemanticProposalGeneratorV2_1
from mage_ptcg.optimization.semantic_failure_lab import _posterior_metrics


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 1, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False,
            "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _player(hand: list[dict[str, object]]) -> dict[str, object]:
    return {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False,
            "deckCount": 53, "discard": [], "hand": hand, "handCount": len(hand), "paralyzed": False,
            "poisoned": False, "prize": [object() for _ in range(6)]}


def _observation(*, options: list[object], maximum: int = 1, select_type: int = 0) -> dict[str, object]:
    own = _player([_card(722)]); other = _player([_card(700)])
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [own, other], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2,
            "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": maximum, "minCount": 1,
            "option": options, "type": select_type}, "step": 7}


def test_live_resolver_is_deterministic_and_keeps_actor_known_source_identity() -> None:
    observation = _observation(options=[{"type": 7, "index": 0}, {"type": 14}])
    state = build_decision_state(observation)
    first = resolve_action_semantics(state, state.legal_actions[0], observation["select"]["option"][0], decision_id="g:0")
    second = resolve_action_semantics(state, state.legal_actions[0], observation["select"]["option"][0], decision_id="g:0")
    assert first["eligibility"] == SEMANTIC_COMPLETE
    assert first["source"]["card_canonical_id"] == 722
    assert first["identity"]["action_key"] == second["identity"]["action_key"]
    assert semantic_equivalent(first, second)


def test_multi_select_and_unknown_payload_fail_closed_for_proposals() -> None:
    observation = _observation(options=[{"type": 7, "index": 0}, {"type": 14}], maximum=2)
    state = build_decision_state(observation)
    payload = resolve_action_semantics(state, state.legal_actions[0], observation["select"]["option"][0], decision_id="g:0")
    assert payload["eligibility"] == SEMANTIC_OPTIONAL_UNKNOWN
    assert payload["action"]["ordered_or_unordered"] == "ORDER_UNKNOWN"


def test_semantic_trace_validator_checks_selected_legal_and_complete_option_payloads() -> None:
    observation = _observation(options=[{"type": 14}]); state = build_decision_state(observation)
    payload = resolve_action_semantics(state, state.legal_actions[0], observation["select"]["option"][0], decision_id="g:0")
    row = {"decision_index": 0, "legal_options": [payload], "selected_action_keys": [payload["identity"]["action_key"]], "selected_option_semantics": [payload]}
    assert validate_semantic_decisions([row])["status"] == "SEMANTIC_TRACE_COMPLETE"


def test_old_trace_is_immutable_and_rejected_for_semantic_use(tmp_path: Path) -> None:
    root = tmp_path / "old"; (root / "traces").mkdir(parents=True)
    source = root / "traces" / "g.json"; source.write_text(json.dumps({"decisions": [{"legal_action_keys": ["hash"]}]}))
    before = source.read_bytes(); audit = audit_v2_migration(root)
    assert audit["source_trace_status"] == OLD_TRACE_STATUS
    assert audit["semantic_payload_status"] == "RECOLLECTION_REQUIRED"
    assert source.read_bytes() == before
    with pytest.raises(SemanticTraceError):
        validate_v2_usage(purpose="semantic_proposal")


def test_semantic_generator_only_returns_distinct_complete_rule_alternatives() -> None:
    observation = _observation(options=[{"type": 7, "index": 0}, {"type": 14}])
    state = build_decision_state(observation)
    payloads = [resolve_action_semantics(state, action, observation["select"]["option"][action.option_index], decision_id="g:0") for action in state.legal_actions]
    decision = {"game_id": "g", "decision_index": 0, "phase": "OPENING", "legal_options": payloads,
                "selected_action_keys": [payloads[0]["identity"]["action_key"]]}
    proposals = SemanticProposalGeneratorV2_1().propose(decision, failure_cluster="PLAY_OPENING_MAIN")
    assert len(proposals) == 1
    assert proposals[0].semantic_action == "END" and not proposals[0].abstention


def test_posterior_calibration_rejects_non_probability_payload() -> None:
    row = {"game_id": "g", "turn": 1, "_game": {"opponent_policy_lineage": "rule"},
           "opponent_posterior": {"families": {"UNKNOWN": .5, "MEGA_ABOMASNOW_EX": 1.5}, "confidence": .25}}
    result = _posterior_metrics([row])
    assert result["status"] == "NOT_AUDITABLE" and result["invalid_probability_rows"] == 1
