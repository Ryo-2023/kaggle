from __future__ import annotations

import pytest

from mage_ptcg.optimization.atomic_policy_compiler import (AtomicCompilerError, AtomicInterventionSpec, AtomicRuleOverlay,
                                                            GATE, SemanticActionSelector, shadow_replay,
                                                            static_gate)


def _option(key: str, category: str = "END") -> dict[str, object]:
    return {"eligibility": "SEMANTIC_COMPLETE", "identity": {"action_key": key},
            "action": {"action_category": category, "select_type": "MAIN"},
            "source": {"area": "NOT_APPLICABLE", "card_canonical_id": "NOT_APPLICABLE"},
            "target": {"area": "NOT_APPLICABLE"}, "effect": {"attack_id": "NOT_APPLICABLE"}}


def _spec() -> AtomicInterventionSpec:
    return AtomicInterventionSpec(1, "atomic-a", "proposal-a", "a" * 64, "ATTACK_MID_MAIN", "MAIN_SINGLE_COMPLETE", "MID", "ATTACK",
                                  SemanticActionSelector("END", "MAIN", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE"),
                                  "RULE_V0_PLANNED_DELEGATION", "DELEGATE_ON_NON_UNIQUE", 1, .25, {"compiler": "test"})


def test_selector_never_uses_option_index_and_delegates_on_ambiguity() -> None:
    selector = _spec().selector
    assert selector.select([_option("a"), _option("b")])[0] == "AMBIGUOUS_MATCH"
    assert selector.select([_option("a", "ATTACK")])[0] == "NO_MATCH"
    assert selector.select([_option("a")])[0] == "UNIQUE_MATCH"


def test_spec_is_exact_deck_and_rejects_future_or_result_fields() -> None:
    _spec().validate()
    bad = AtomicInterventionSpec(**{**_spec().__dict__, "provenance": {"result": "forbidden"}})
    with pytest.raises(AtomicCompilerError): bad.validate()


def test_overlay_is_exact_deck_candidate_and_not_a_rule_mutation() -> None:
    overlay = AtomicRuleOverlay(1, "overlay-a", "a" * 64, ("atomic-a",), ("b" * 64,))
    overlay.validate()
    assert overlay.payload()["fallback"] == "RULE_V0_PLANNED_DELEGATION"


def test_shadow_replay_is_deterministic_and_static_gate_requires_cross_split_support() -> None:
    spec = _spec(); rows = []
    for split in ("semantic-train", "semantic-validation", "semantic-holdout"):
        for index in range(4):
            rows.append({"game_id": f"{split}-{index}", "decision_index": index, "phase": "MID", "selected_action_keys": ["rule"], "selected_option_semantics": [_option("rule", "ATTACK")], "legal_options": [_option("rule", "ATTACK"), _option("end")], "_game": {"own_deck_hash": "a" * 64, "run_id": split}})
        for index in range(100):
            rows.append({"game_id": f"{split}-off-{index}", "decision_index": index + 4, "phase": "OPENING", "selected_action_keys": ["rule"], "selected_option_semantics": [_option("rule", "ATTACK")], "legal_options": [_option("rule", "ATTACK"), _option("end")], "_game": {"own_deck_hash": "a" * 64, "run_id": split}})
    first, second = shadow_replay(spec, rows), shadow_replay(spec, rows)
    assert first == second and sum(item["divergence"] for item in first) == 12
    gate = static_gate(spec, first)
    assert gate["status"] == "STATIC_PASS" and gate["ambiguous_selection_rate"] == 0
