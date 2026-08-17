"""Rule-v0-backed Student policy for research-only candidate evaluation.

The Student model is never allowed to replace the deterministic Rule v0
fallback for ordered or multi-select prompts.  Only a finite, high-margin
single selection may override the baseline.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


class HybridStudentPolicyError(ValueError):
    """Raised when a hybrid policy contract is malformed."""


class HybridStudentPolicy:
    def __init__(
        self,
        *,
        student: Any,
        baseline: Callable[[dict[str, object]], list[int]],
        margin_threshold: float = 0.0,
    ) -> None:
        if not callable(getattr(student, "choose", None)):
            raise HybridStudentPolicyError("student must expose choose")
        if not callable(baseline):
            raise HybridStudentPolicyError("baseline must be callable")
        if type(margin_threshold) not in (int, float) or not math.isfinite(float(margin_threshold)) or margin_threshold < 0.0:
            raise HybridStudentPolicyError("margin_threshold must be finite and nonnegative")
        self.student = student
        self.baseline = baseline
        self.margin_threshold = float(margin_threshold)
        self.last_decision_trace: dict[str, object] = {"status": "uninitialized"}

    @staticmethod
    def _single_main_selection(observation: object) -> bool:
        if not isinstance(observation, dict):
            return False
        select = observation.get("select")
        if not isinstance(select, dict):
            return False
        return (
            select.get("type") in (0, "MAIN")
            and select.get("minCount") == 1
            and select.get("maxCount") == 1
        )

    def choose(self, observation: dict[str, object]) -> list[int]:
        if not self._single_main_selection(observation):
            # Deck registration and auxiliary prompts legitimately return a
            # 60-card list containing duplicate card IDs.  Do not apply the
            # selection uniqueness invariant outside a single MAIN choice.
            self.last_decision_trace = {
                "status": "rule_v0_fallback",
                "reason": "selection_not_single",
            }
            return self.baseline(observation)
        baseline = self.baseline(observation)
        if not isinstance(baseline, list) or len(baseline) != len(set(baseline)):
            raise HybridStudentPolicyError("baseline returned an invalid selection")
        try:
            proposal = self.student.choose(observation)
        except Exception as exc:  # model failures must never affect the baseline
            self.last_decision_trace = {
                "status": "rule_v0_fallback",
                "reason": type(exc).__name__,
            }
            return baseline
        trace = getattr(self.student, "last_decision_trace", None)
        student_trace = trace.get("student") if isinstance(trace, dict) else None
        margin = student_trace.get("score_margin") if isinstance(student_trace, dict) else None
        if (
            not isinstance(proposal, list)
            or len(proposal) != 1
            or type(proposal[0]) is not int
            or not isinstance(margin, (int, float))
            or not math.isfinite(float(margin))
            or float(margin) < self.margin_threshold
        ):
            self.last_decision_trace = {
                "status": "rule_v0_fallback",
                "reason": "margin_below_threshold" if isinstance(margin, (int, float)) else "student_trace_missing",
                "score_margin": margin,
            }
            return baseline
        self.last_decision_trace = {
            "status": "student_override",
            "score_margin": float(margin),
            "baseline_selection": list(baseline),
            "student_selection": list(proposal),
        }
        return list(proposal)


__all__ = ["HybridStudentPolicy", "HybridStudentPolicyError"]
