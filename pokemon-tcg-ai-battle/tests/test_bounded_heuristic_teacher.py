from __future__ import annotations

import pytest

from mage_ptcg.optimization.bounded_heuristic_teacher import BoundedHeuristicTeacher, HeuristicTeacherError
from mage_ptcg.optimization.semantic_trace import SEMANTIC_COMPLETE


def _option(key: str, index: int, action: str) -> dict:
    return {"eligibility": SEMANTIC_COMPLETE, "identity": {"action_key": key, "option_index": index}, "action": {"action_category": action}}


def test_teacher_includes_rule_breakdown_and_is_deterministic() -> None:
    row = {"legal_options": [_option("rule", 0, "END"), _option("setup", 1, "EVOLVE")], "rule_selected_action_keys": ["rule"]}
    teacher = BoundedHeuristicTeacher()
    first = teacher.rank(row, cluster_id="END_OPENING_MAIN")
    second = teacher.rank(row, cluster_id="END_OPENING_MAIN")
    assert [item.action_key for item in first.ranking] == ["setup", "rule"]
    assert not first.abstained and first.confidence > 0
    assert first.payload() == second.payload()
    assert first.ranking[0].components[0].name == "immediate_legality"


def test_teacher_rejects_outcome_and_abstains_without_rule() -> None:
    with pytest.raises(HeuristicTeacherError):
        BoundedHeuristicTeacher().rank({"result": 1, "legal_options": []}, cluster_id="X")
    decision = BoundedHeuristicTeacher().rank({"legal_options": [_option("x", 0, "ATTACK")]}, cluster_id="ATTACK_MID_MAIN")
    assert decision.abstained
