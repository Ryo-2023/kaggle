from __future__ import annotations

from typing import Any

import pytest

from mage_ptcg.student.hybrid import HybridStudentPolicy, HybridStudentPolicyError


class _FakeStudent:
    def __init__(self, selection: list[int] | None, margin: float | None) -> None:
        self.selection = selection
        self.last_decision_trace: dict[str, Any] = {
            "student": {"score_margin": margin}
        }

    def choose(self, _observation: object) -> list[int] | None:
        return self.selection


def _single_select() -> dict[str, object]:
    return {
        "select": {
            "type": "MAIN",
            "context": None,
            "minCount": 1,
            "maxCount": 1,
        }
    }


def test_hybrid_keeps_rule_v0_when_student_margin_is_below_threshold() -> None:
    baseline = lambda _obs: [0]
    policy = HybridStudentPolicy(
        student=_FakeStudent([1], 0.1),
        baseline=baseline,
        margin_threshold=0.2,
    )
    assert policy.choose(_single_select()) == [0]
    assert policy.last_decision_trace["status"] == "rule_v0_fallback"
    assert policy.last_decision_trace["reason"] == "margin_below_threshold"


def test_hybrid_allows_high_confidence_single_selection() -> None:
    baseline = lambda _obs: [0]
    policy = HybridStudentPolicy(
        student=_FakeStudent([1], 0.4),
        baseline=baseline,
        margin_threshold=0.2,
    )
    assert policy.choose(_single_select()) == [1]
    assert policy.last_decision_trace["status"] == "student_override"


def test_hybrid_never_overrides_multi_select_or_unknown_trace() -> None:
    baseline = lambda _obs: [0, 1]
    policy = HybridStudentPolicy(
        student=_FakeStudent([1], 0.9),
        baseline=baseline,
        margin_threshold=0.0,
    )
    observation = {
        "select": {"type": "MAIN", "context": None, "minCount": 1, "maxCount": 2}
    }
    assert policy.choose(observation) == [0, 1]
    assert policy.last_decision_trace["reason"] == "selection_not_single"


def test_hybrid_rejects_invalid_threshold() -> None:
    with pytest.raises(HybridStudentPolicyError):
        HybridStudentPolicy(student=_FakeStudent([0], 1.0), baseline=lambda _obs: [0], margin_threshold=-1.0)
