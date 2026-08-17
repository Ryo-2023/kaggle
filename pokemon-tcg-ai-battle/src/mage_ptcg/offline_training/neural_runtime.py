"""Fail-closed, torch-free runtime for the neural Student.

The runtime shares the exact Stable-ActionKey feature extraction used in
training, scores legal candidates with the pure-Python export core, and returns
``None`` whenever anything is unsafe so the caller falls back to Rule Agent v0.
Illegal candidates are never selectable, and ties break on the Stable ActionKey
digest then the legal option index -- identical to the linear runtime.
"""

from __future__ import annotations

import math
from pathlib import Path

from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.features import action_features, state_features

from mage_ptcg.offline_training.export import ExportError, load_export, score_candidates


class NeuralRuntimeError(ValueError):
    """Raised internally when neural inference cannot safely produce a choice."""


def _is_main_selection(value: object) -> bool:
    return value == 0 or getattr(value, "name", "").rsplit(".", 1)[-1].upper() == "MAIN"


class NeuralRuntimePolicy:
    """A loaded neural export that returns ``None`` when Rule v0 must take over."""

    def __init__(self, document: dict[str, object]):
        self._document = document
        self.last_decision_trace: dict[str, object] | None = None

    @classmethod
    def load(cls, path: str | Path | None, *, expected_feature_hash: str | None = None, expected_model_hash: str | None = None):
        if path is None:
            raise NeuralRuntimeError("neural model path is not configured")
        try:
            document = load_export(path)
        except ExportError as exc:
            raise NeuralRuntimeError(str(exc)) from exc
        if expected_feature_hash is not None and document.get("feature_schema_hash") != expected_feature_hash:
            raise NeuralRuntimeError("feature schema hash mismatch")
        if expected_model_hash is not None and document.get("model_hash") != expected_model_hash:
            raise NeuralRuntimeError("model hash mismatch")
        return cls(document)

    def choose(self, observation: object) -> list[int] | None:
        if not isinstance(observation, dict):
            self.last_decision_trace = {"status": "fallback", "reason": "observation_not_mapping"}
            return None
        select = observation.get("select")
        if not isinstance(select, dict):
            self.last_decision_trace = {"status": "fallback", "reason": "selection_not_mapping"}
            return None
        try:
            if is_ordered_selection(select.get("type"), select.get("context")):
                self.last_decision_trace = {"status": "fallback", "reason": "NeuralRuntimeError"}
                return None
        except ValueError:
            # An unknown schema must not be converted to an optional decline.
            self.last_decision_trace = {"status": "fallback", "reason": "NeuralRuntimeError"}
            return None
        if not _is_main_selection(select.get("type")) and select.get("minCount") == 0:
            self.last_decision_trace = {"status": "rule_optional_auxiliary"}
            return []
        try:
            state = build_decision_state(observation)
            minimum = select.get("minCount")
            maximum = select.get("maxCount")
            if type(minimum) is not int or type(maximum) is not int:
                raise NeuralRuntimeError("selection bounds are not integers")
            if maximum == 0:
                self.last_decision_trace = {"status": "selected", "selected_count": 0}
                return []
            count = minimum if minimum else 1
            if not 0 < count <= maximum <= len(state.legal_actions):
                raise NeuralRuntimeError("selection bounds are invalid")
            state_vector = state_features(state.actor_view)
            rows = [[*state_vector, *action_features(action.action_key)] for action in state.legal_actions]
            scores = score_candidates(self._document, rows)
            if not scores or any(not math.isfinite(score) for score in scores):
                raise NeuralRuntimeError("neural model produced empty or non-finite scores")
            ordered = sorted(
                zip(state.legal_actions, scores, strict=True),
                key=lambda item: (-item[1], item[0].action_key.digest, item[0].option_index),
            )
            selection = [action.option_index for action, _score in ordered[:count]]
            if len(selection) != count or len(selection) != len(set(selection)) or any(
                index < 0 or index >= len(state.legal_actions) for index in selection
            ):
                raise NeuralRuntimeError("neural model selected an invalid legal action")
            self.last_decision_trace = {"status": "selected", "selected_count": count}
            return selection
        except Exception as exc:
            self.last_decision_trace = {"status": "fallback", "reason": type(exc).__name__}
            return None


def score_legal_candidates(document: dict[str, object], observation: object) -> tuple[list[str], list[float]]:
    """Return (digests, scores) for the legal candidate set; used for parity checks."""
    if not isinstance(observation, dict):
        raise NeuralRuntimeError("observation is not a mapping")
    select = observation.get("select")
    if not isinstance(select, dict):
        raise NeuralRuntimeError("selection is not a mapping")
    try:
        ordered = is_ordered_selection(select.get("type"), select.get("context"))
    except ValueError as exc:
        raise NeuralRuntimeError("selection has an unknown CABT schema") from exc
    if ordered:
        raise NeuralRuntimeError(
            "candidate-wise neural Student cannot score ordered Skill labels"
        )
    state = build_decision_state(observation)
    state_vector = state_features(state.actor_view)
    rows = [[*state_vector, *action_features(action.action_key)] for action in state.legal_actions]
    scores = score_candidates(document, rows)
    return [action.action_key.digest for action in state.legal_actions], scores


__all__ = ["NeuralRuntimeError", "NeuralRuntimePolicy", "score_legal_candidates"]
