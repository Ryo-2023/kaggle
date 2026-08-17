"""Fail-closed Student v0 inference over cabt's current legal option set."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from mage_ptcg.decision_state import ActionKey, DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection

from .features import runtime_action_features, runtime_action_id, state_features
from .model import ModelValidationError, StudentV0Model


class StudentModelError(ValueError):
    """Raised internally when Student inference cannot safely produce a choice."""


def _is_main_selection(value: object) -> bool:
    return value == 0 or getattr(value, "name", "").rsplit(".", 1)[-1].upper() == "MAIN"


def _public_action_trace_digest(action_key: ActionKey) -> str:
    """Hash the C1-redacted ActionKey payload, never its private identity core."""
    payload = action_key.to_public_trace_payload()
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RuntimeStudentPolicy:
    """A loaded model that returns ``None`` whenever Rule v0 must take over."""

    def __init__(self, model: StudentV0Model):
        self._model = model
        self.last_decision_trace: dict[str, object] | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "RuntimeStudentPolicy":
        if path is None:
            raise StudentModelError("Student model path is not configured")
        try:
            return cls(StudentV0Model.load(path))
        except ModelValidationError as exc:
            raise StudentModelError(str(exc)) from exc

    def choose(self, observation: object) -> list[int] | None:
        """Return only valid legal indices, or ``None`` for deterministic fallback."""
        if not isinstance(observation, dict):
            self.last_decision_trace = {"status": "fallback", "reason": "observation_not_mapping", "student": {"status": "failed"}}
            return None
        select = observation.get("select")
        if not isinstance(select, dict):
            self.last_decision_trace = {"status": "fallback", "reason": "selection_not_mapping", "student": {"status": "failed"}}
            return None
        try:
            if is_ordered_selection(select.get("type"), select.get("context")):
                self.last_decision_trace = {"status": "fallback", "reason": "StudentModelError", "student": {"status": "failed"}}
                return None
        except ValueError:
            # This must run before every optional/zero-selection shortcut:
            # an unrecognized schema is not an authorized empty decision.
            self.last_decision_trace = {"status": "fallback", "reason": "StudentModelError", "student": {"status": "failed"}}
            return None
        if not _is_main_selection(select.get("type")) and select.get("minCount") == 0:
            # Rule v0 deliberately declines optional auxiliary prompts.
            self.last_decision_trace = {"status": "rule_optional_auxiliary", "student": {"status": "not_requested"}}
            return []
        try:
            state = build_decision_state(observation)
            minimum = select.get("minCount")
            maximum = select.get("maxCount")
            if type(minimum) is not int or type(maximum) is not int:
                raise StudentModelError("selection bounds are not integers")
            if maximum == 0:
                self.last_decision_trace = {"status": "selected", "student": {"status": "selected", "selected_action_key_digests": []}}
                return []
            count = minimum if minimum else 1
            if not 0 < count <= maximum <= len(state.legal_actions):
                raise StudentModelError("selection bounds are invalid")
            state_vector = state_features(state.actor_view)
            rows = [
                [
                    *state_vector,
                    *runtime_action_features(
                        action.action_key, domain=self._model.feature_domain
                    ),
                ]
                for action in state.legal_actions
            ]
            scores = self._model.score_vector(rows)
            if not scores or not all(math.isfinite(score) for score in scores):
                raise StudentModelError("Student produced empty or non-finite scores")
            ordered = sorted(
                zip(state.legal_actions, scores, strict=True),
                key=lambda item: (
                    -item[1],
                    runtime_action_id(
                        item[0].action_key, domain=self._model.feature_domain
                    ),
                    item[0].option_index,
                ),
            )
            score_margin = 0.0
            if len(scores) > 1:
                ordered_scores = sorted(scores, reverse=True)
                score_margin = float(ordered_scores[0] - ordered_scores[1])
            selection = [action.option_index for action, _score in ordered[:count]]
            if len(selection) != count or len(selection) != len(set(selection)) or any(index < 0 or index >= len(state.legal_actions) for index in selection):
                raise StudentModelError("Student selected an invalid legal action")
            trace = state.to_trace_payload()
            trace["student"] = {
                "selected_action_key_digests": [
                    _public_action_trace_digest(action.action_key)
                    for action, _score in ordered[:count]
                ],
                "score_margin": score_margin,
                "status": "selected",
            }
            self.last_decision_trace = trace
            return selection
        except (DecisionStateError, ModelValidationError, StudentModelError, TypeError, ValueError) as exc:
            self.last_decision_trace = {"status": "fallback", "reason": type(exc).__name__, "student": {"status": "failed"}}
            return None


__all__ = ["RuntimeStudentPolicy", "StudentModelError"]
