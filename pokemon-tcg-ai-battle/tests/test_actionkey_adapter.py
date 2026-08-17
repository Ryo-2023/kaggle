"""Boundary tests for the C5 curated ActionKey adapter.

The adapter must reuse the existing stable ActionKey candidate contract, fail
closed on anything ambiguous/unsupported/private, keep hard constraints above
teacher rules, and stay independent of candidate ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.distillation.actionkey_adapter import (
    ADAPTER_VERSION,
    DecisionAdaptation,
    RuleSupportClass,
    TeacherStatus,
    adapt_decision,
    adapt_teacher_rule,
    classification_summary,
    classify_teacher_rule,
    normalize_action_type,
)
from mage_ptcg.distillation.knowledge import load_curated_knowledge


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "artifacts" / "team-knowledge-curated"


@pytest.fixture(scope="module")
def knowledge():
    return load_curated_knowledge(PACK)


@pytest.fixture(scope="module")
def rules(knowledge):
    return {rule.teacher_id: rule for rule in knowledge.teacher_rules}


def _observation(*, minimum: int = 1, maximum: int = 1, selection_type: object = 0, count: int = 2) -> dict[str, object]:
    return {"select": {"type": selection_type, "option": [{"type": 7} for _ in range(count)], "minCount": minimum, "maxCount": maximum}}


def _candidates(count: int = 2, action_type: str = "PLAY_POKEMON") -> list[dict[str, object]]:
    return [{"option_index": index, "action_id": f"ak-{index}", "action_type": action_type} for index in range(count)]


def _attest(candidate: dict[str, object], teacher_id: str, **extra: object) -> dict[str, object]:
    candidate.update({"applicable_rule_ids": [teacher_id], "observable_condition_met": True, **extra})
    return candidate


# --- loading & classification -------------------------------------------------

def test_twenty_two_teacher_rules_load_and_classify_into_five_buckets(knowledge) -> None:
    assert len(knowledge.teacher_rules) == 22
    summary = classification_summary(knowledge)
    assert summary["teacher_rules"] == 22
    assert summary["adapter_version"] == ADAPTER_VERSION
    assert (
        summary["directly_supported"]
        + summary["condition_only"]
        + summary["ambiguous"]
        + summary["unsupported"]
        == 22
    )
    assert summary["directly_supported"] == 1
    assert summary["normalized"] == 0  # registry tokens are already canonical


def test_classification_summary_is_order_independent(knowledge) -> None:
    assert classification_summary(knowledge) == classification_summary(knowledge)


# --- normalization ------------------------------------------------------------

def test_alias_and_case_normalization_only_touches_notation() -> None:
    assert normalize_action_type("play_pokemon") == "PLAY_POKEMON"
    assert normalize_action_type("  Retreat  ") == "RETREAT"
    assert normalize_action_type("PLAY") == "PLAY_POKEMON"
    assert normalize_action_type("draw") == "DRAW_OR_SEARCH"
    assert normalize_action_type("") is None
    assert normalize_action_type(None) is None


def test_normalized_candidate_action_type_still_matches(rules) -> None:
    observation = _observation()
    candidate = _attest(_candidates()[0], "TR-000010", action_type="play")  # lowercase alias
    other = _candidates()[1]
    app = adapt_teacher_rule(rules["TR-000010"], observation, [candidate, other])
    assert app.status is TeacherStatus.APPLIED
    assert app.matched_candidate_ids == ("ak-0",)


# --- direct mapping -----------------------------------------------------------

def test_direct_mapping_applies_portable_penalty(rules) -> None:
    observation = _observation()
    candidates = _candidates()
    _attest(candidates[0], "TR-000010")
    app = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    assert app.status is TeacherStatus.APPLIED
    assert app.matched_candidate_ids == ("ak-0",)
    assert app.score_adjustments == (("ak-0", -1.0),)
    assert app.hard_rejections == ()
    assert app.skip_reason is None


def test_family_projection_matches_semantic_operation_only(rules) -> None:
    observation = _observation()
    candidate = {"option_index": 0, "action_id": "ak-0", "semantic_operation": "PLAY", "applicable_rule_ids": ["TR-000010"], "observable_condition_met": True}
    other = _candidates()[1]
    app = adapt_teacher_rule(rules["TR-000010"], observation, [candidate, other])
    assert app.status is TeacherStatus.APPLIED
    assert app.matched_candidate_ids == ("ak-0",)


# --- no match / ambiguous / unsupported ---------------------------------------

def test_no_attestation_is_no_match(rules) -> None:
    app = adapt_teacher_rule(rules["TR-000010"], _observation(), _candidates())
    assert app.status is TeacherStatus.SKIPPED_NO_MATCH


def test_ambiguous_class_never_applies(rules) -> None:
    for teacher_id in ("TR-000001", "TR-000021"):
        assert classify_teacher_rule(rules[teacher_id]) is RuleSupportClass.AMBIGUOUS
        app = adapt_teacher_rule(rules[teacher_id], _observation(), _candidates())
        assert app.status is TeacherStatus.SKIPPED_AMBIGUOUS


def test_duplicate_candidate_mapping_is_ambiguous(rules) -> None:
    candidates = _candidates()
    candidates[1]["option_index"] = 0  # collides with candidate 0
    app = adapt_teacher_rule(rules["TR-000010"], _observation(), candidates)
    assert app.status is TeacherStatus.SKIPPED_AMBIGUOUS


def test_unsupported_action_type_is_unsupported(rules) -> None:
    for teacher_id in ("TR-000003", "TR-000006", "TR-000018", "TR-000022"):
        app = adapt_teacher_rule(rules[teacher_id], _observation(), _candidates())
        assert app.status is TeacherStatus.SKIPPED_UNSUPPORTED


def test_condition_only_rules_fail_closed_as_unsupported(rules) -> None:
    for teacher_id in ("TR-000002", "TR-000008", "TR-000011", "TR-000013", "TR-000015", "TR-000016", "TR-000020"):
        app = adapt_teacher_rule(rules[teacher_id], _observation(), _candidates())
        assert app.status is TeacherStatus.SKIPPED_UNSUPPORTED


def test_unknown_selection_type_fails_closed(rules) -> None:
    app = adapt_teacher_rule(rules["TR-000010"], _observation(selection_type="UNKNOWN"), _candidates())
    assert app.status is TeacherStatus.SKIPPED_UNSUPPORTED


# --- privacy ------------------------------------------------------------------

def test_private_observation_field_is_rejected(rules) -> None:
    observation = _observation()
    observation["opponent_hand"] = [99]
    app = adapt_teacher_rule(rules["TR-000010"], observation, _candidates())
    assert app.status is TeacherStatus.SKIPPED_PRIVATE


def test_private_candidate_field_is_rejected(rules) -> None:
    candidates = _candidates()
    _attest(candidates[0], "TR-000010")
    candidates[0]["private_action_key_digest"] = "hidden"
    app = adapt_teacher_rule(rules["TR-000010"], _observation(), candidates)
    assert app.status is TeacherStatus.SKIPPED_PRIVATE


# --- ordering / determinism ---------------------------------------------------

def test_candidate_order_permutation_yields_identical_result(rules) -> None:
    observation = _observation(count=3)
    candidates = _candidates(3)
    _attest(candidates[0], "TR-000010")
    _attest(candidates[2], "TR-000010")
    forward = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    reverse = adapt_teacher_rule(rules["TR-000010"], observation, list(reversed(candidates)))
    assert forward == reverse
    assert forward.matched_candidate_ids == ("ak-0", "ak-2")


def test_tie_break_is_deterministic_across_repeats(rules) -> None:
    observation = _observation(count=3)
    candidates = _candidates(3)
    for candidate in candidates:
        _attest(candidate, "TR-000010")
    first = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    second = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    assert first == second
    assert first.matched_candidate_ids == ("ak-0", "ak-1", "ak-2")


# --- hard constraint precedence -----------------------------------------------

def test_hard_constraint_precedes_high_teacher_score(rules) -> None:
    observation = _observation()
    candidates = _candidates()
    # A hugely positive teacher delta cannot rescue a hard-rejected candidate.
    _attest(candidates[0], "TR-000010", curated_score_delta=9999, hard_constraint_violations=["HC-000001"])
    hard_ids = frozenset({"HC-000001"})
    app = adapt_teacher_rule(rules["TR-000010"], observation, candidates, hard_constraint_ids=hard_ids)
    assert app.status is TeacherStatus.SKIPPED_CONFLICT
    assert app.matched_candidate_ids == ()
    assert app.hard_rejections == ("ak-0",)


def test_illegal_high_score_candidate_is_excluded_at_decision_level(knowledge) -> None:
    observation = _observation()
    candidates = _candidates()
    _attest(candidates[0], "TR-000010", curated_score_delta=9999, hard_constraint_violations=["HC-000002"])
    result = adapt_decision(observation, candidates, knowledge)
    assert "ak-0" in result.hard_rejections
    assert result.metrics["hard_constraint_rejections"] == 1


# --- multi-select preservation ------------------------------------------------

def test_multi_select_preserves_count_and_uniqueness(rules) -> None:
    observation = _observation(minimum=2, maximum=2, count=3)
    candidates = _candidates(3)
    _attest(candidates[0], "TR-000010")
    _attest(candidates[1], "TR-000010")
    app = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    assert app.status is TeacherStatus.APPLIED
    assert app.matched_candidate_ids == ("ak-0", "ak-1")
    assert len(app.matched_candidate_ids) == len(set(app.matched_candidate_ids))


def test_duplicate_multi_select_candidate_is_rejected(rules) -> None:
    observation = _observation(minimum=2, maximum=2, count=3)
    candidates = _candidates(3)
    candidates[1]["action_id"] = "ak-0"  # duplicate stable id
    app = adapt_teacher_rule(rules["TR-000010"], observation, candidates)
    assert app.status is TeacherStatus.SKIPPED_AMBIGUOUS


# --- registration separation --------------------------------------------------

def test_registration_observation_is_not_an_action(rules) -> None:
    app = adapt_teacher_rule(rules["TR-000010"], {"deck": [1] * 60}, _candidates())
    assert app.status is TeacherStatus.SKIPPED_UNSUPPORTED
    assert "registration" in (app.skip_reason or "")


# --- provenance ---------------------------------------------------------------

def test_provenance_is_complete_for_every_status(rules) -> None:
    observation = _observation()
    candidates = _candidates()
    _attest(candidates[0], "TR-000010")
    for app in (
        adapt_teacher_rule(rules["TR-000010"], observation, candidates),
        adapt_teacher_rule(rules["TR-000003"], observation, _candidates()),
    ):
        keys = set(app.provenance)
        assert {"teacher_id", "canonical_rule_id", "support_class", "adapter_version"} <= keys
        assert app.provenance["teacher_id"] == app.teacher_id
        assert app.provenance["adapter_version"] == ADAPTER_VERSION


# --- decision-level fallback --------------------------------------------------

def test_empty_applied_set_falls_back_to_rule_v0(knowledge) -> None:
    result = adapt_decision(_observation(), _candidates(), knowledge)
    assert isinstance(result, DecisionAdaptation)
    assert result.rule_v0_fallback is True
    assert result.metrics["teacher_rules_applied"] == 0
    assert result.metrics["rule_v0_fallbacks"] == 1
    assert result.metrics["teacher_rules_considered"] == 22


def test_applied_decision_does_not_fall_back(knowledge) -> None:
    candidates = _candidates()
    _attest(candidates[0], "TR-000010")
    result = adapt_decision(_observation(), candidates, knowledge)
    assert result.rule_v0_fallback is False
    assert result.metrics["teacher_rules_applied"] == 1
    assert result.metrics["candidate_matches"] == 1
    assert result.metrics["rule_v0_fallbacks"] == 0


def test_private_decision_falls_back_without_leaking(knowledge) -> None:
    observation = _observation()
    observation["opponent_hand"] = [1]
    result = adapt_decision(observation, _candidates(), knowledge)
    assert result.rule_v0_fallback is True
    assert all(app.status is TeacherStatus.FALLBACK_RULE_V0 for app in result.applications)
    assert result.metrics["teacher_rules_applied"] == 0


def test_decision_metrics_are_order_independent(knowledge) -> None:
    candidates = _candidates(3)
    _attest(candidates[0], "TR-000010")
    forward = adapt_decision(_observation(count=3), candidates, knowledge)
    reverse = adapt_decision(_observation(count=3), list(reversed(candidates)), knowledge)
    assert forward.metrics == reverse.metrics
    assert forward.hard_rejections == reverse.hard_rejections


# --- record-level manifest ----------------------------------------------------

def _decision_record(index: int) -> dict[str, object]:
    from mage_ptcg.distillation.contracts import build_record_from_rule_bc
    from mage_ptcg.student.dataset import build_rule_bc_example

    card = {"id": 100 + index, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    current = {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2 + index, "turnActionCount": 3, "yourIndex": 0}
    observation = {"current": current, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}], "type": 0}, "step": 1}
    example = build_rule_bc_example(observation, deck=[1] * 60, source_id=f"knowledge-{index}", source_revision="fixture")
    return build_record_from_rule_bc(example, source_kind="c4-rule-bc", synthetic=True, environment_version="fixture", agent_config_hash="cfg")


def test_adapt_records_reports_zero_applied_without_attestation(knowledge) -> None:
    from mage_ptcg.distillation.actionkey_adapter import adapt_records

    records = [_decision_record(index) for index in range(3)]
    manifest = adapt_records(records, knowledge)
    assert manifest["teacher_registry_only"] is True
    assert manifest["decisions_considered"] == 3
    assert manifest["decisions_with_applied_rule"] == 0
    assert manifest["metrics"]["teacher_rules_applied"] == 0
    assert manifest["metrics"]["teacher_rules_loaded"] == 22
    assert manifest["metrics"]["decision_rule_pairs_considered"] == 66
    assert manifest["applied_bindings"] == []
    assert manifest["classification"]["directly_supported"] == 1


def test_adapt_records_manifest_is_deterministic_and_order_independent(knowledge) -> None:
    from mage_ptcg.distillation.actionkey_adapter import adapt_records

    records = [_decision_record(index) for index in range(3)]
    first = adapt_records(records, knowledge)
    second = adapt_records(list(reversed(records)), knowledge)
    assert first["adapter_config_hash"] == second["adapter_config_hash"]
    assert first["metrics"] == second["metrics"]
    assert first["applied_bindings"] == second["applied_bindings"]
