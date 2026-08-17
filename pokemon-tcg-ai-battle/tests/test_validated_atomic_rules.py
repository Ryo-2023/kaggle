from __future__ import annotations

from mage_ptcg.optimization.atomic_policy_compiler import AtomicInterventionSpec, AtomicRuleOverlay, SemanticActionSelector
from mage_ptcg.optimization.validated_atomic_rules import (CandidateFreeze, CandidateRuntimeBoundary,
                                                             RUNTIME_SCHEMA, repeatability_protocol,
                                                             semantic_decision, trace_equivalence)


def _spec() -> AtomicInterventionSpec:
    return AtomicInterventionSpec(1, "atomic-test", "proposal-test", "a" * 64, "ATTACK_OPENING_MAIN", "MAIN_SINGLE_COMPLETE", "OPENING", "ATTACK",
                                  SemanticActionSelector("END", "MAIN", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE"),
                                  "RULE_V0_PLANNED_DELEGATION", "DELEGATE_ON_NON_UNIQUE", 1, .25, {"compiler": "test"})


def _row(options: list[dict[str, object]]) -> dict[str, object]:
    return {"game_id": "g", "decision_index": 1, "phase": "OPENING", "selected_action_keys": ["rule"],
            "selected_option_semantics": [options[0]], "legal_options": options,
            "_game": {"own_deck_hash": "a" * 64, "run_id": "semantic-train"}}


def _option(key: str, category: str) -> dict[str, object]:
    return {"eligibility": "SEMANTIC_COMPLETE", "identity": {"action_key": key}, "action": {"action_category": category, "select_type": "MAIN"},
            "source": {"area": "NOT_APPLICABLE", "card_canonical_id": "NOT_APPLICABLE"}, "target": {"area": "NOT_APPLICABLE"}, "effect": {"attack_id": "NOT_APPLICABLE"}}


def test_trace_equivalence_and_selector_delegation_are_exact() -> None:
    spec = _spec(); overlay = AtomicRuleOverlay(1, "overlay-test", "a" * 64, (spec.intervention_id,), (spec.config_hash,))
    unique = _row([_option("rule", "ATTACK"), _option("end", "END")])
    ambiguous = _row([_option("rule", "ATTACK"), _option("end-a", "END"), _option("end-b", "END")])
    assert semantic_decision(spec, unique)["selected_action_key"] == "end"
    assert semantic_decision(spec, ambiguous)["delegation"] is True
    result = trace_equivalence(spec, overlay, [unique, ambiguous], "test")
    assert [item["status"] for item in result] == ["EXACT_EQUIVALENT", "EXACT_EQUIVALENT"]


def test_repeatability_protocol_is_fresh_balanced_and_unpaired() -> None:
    protocol = repeatability_protocol()
    assert protocol["paired"] is False and protocol["total_games"] == 512
    assert len(protocol["blocks"]) == 8
    assert protocol["gate"]["minimum_noninferior_blocks"] == 6


def test_freeze_and_runtime_default_are_fail_closed() -> None:
    spec = _spec(); overlay = AtomicRuleOverlay(1, "overlay-test", "a" * 64, (spec.intervention_id,), (spec.config_hash,))
    freeze = CandidateFreeze("incremental-validated-rule-learning-v1", spec.intervention_id, spec.intervention_id, overlay.overlay_id, "a" * 64,
                            spec.payload(), spec.config_hash, overlay.payload(), overlay.config_hash, "rule-hash", "resolver", "compiler", "trace", "search", "confirm", (),
                            {"schema": RUNTIME_SCHEMA, "candidate_only": True}, {"exact_deck_hash": "a" * 64, "rule_v0_hash": "rule-hash"}, {})
    freeze.validate()
    changed = CandidateFreeze(**{**freeze.__dict__, "candidate_config": {**freeze.candidate_config, "phase": "MID"}})
    try:
        changed.validate()
        assert False, "modified frozen candidate must be rejected"
    except ValueError:
        pass
    # Invalid configuration is rejected before a candidate controller is constructed.
    boundary = CandidateRuntimeBoundary(freeze, spec, overlay)
    # Candidate configuration is incomplete, so it cannot instantiate an overlay.
    _, telemetry = boundary.load([1] * 60, {"schema": RUNTIME_SCHEMA})
    assert telemetry["overlay_active"] is False
