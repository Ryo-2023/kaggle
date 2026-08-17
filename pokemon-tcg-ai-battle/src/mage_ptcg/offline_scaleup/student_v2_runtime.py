"""Fail-closed live-inference wrapper for a trained Student v2 checkpoint.

This connects the offline ``CandidateRanker`` (see ``gpu_student_v2.py``) to
CABT's legal-action contract for exactly one decision at a time.  There is no
Rule v0 fallback: malformed observations or unsafe model output raise a typed
error, which the candidate adapter records as a candidate fault rather than a
silently substituted decision.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.features import action_features, state_features


class StudentV2RuntimeError(RuntimeError):
    """Raised when the Student v2 policy cannot safely produce a decision."""


def load_candidate_ranker(model_dir: Path, device: Any) -> tuple[Any, dict[str, Any]]:
    """Reconstruct the exact ``CandidateRanker`` described by training_summary.json."""
    from mage_ptcg.offline_scaleup.gpu_student_v2 import _model, _torch
    torch, _nn, _functional, _loader, _dataset = _torch()
    summary = json.loads((model_dir / "training_summary.json").read_text(encoding="utf-8"))
    checkpoint = torch.load(model_dir / "best.pt", map_location=device, weights_only=False)
    model = _model(int(summary["hidden"]), int(summary["blocks"]), float(summary["dropout"])).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    return model, summary


class StudentV2CandidatePolicy:
    """A loaded Student v2 checkpoint bound to one exact deck."""

    def __init__(self, *, model: Any, device: Any, deck: list[int]) -> None:
        if len(deck) != 60 or any(type(card) is not int for card in deck):
            raise StudentV2RuntimeError("exact 60-card deck is required")
        self._model = model
        self._device = device
        self.deck = list(deck)
        self.last_decision_trace: dict[str, Any] | None = None

    def choose(self, observation: object) -> list[int]:
        if not isinstance(observation, Mapping):
            raise StudentV2RuntimeError("observation is not a mapping")
        select = observation.get("select")
        if select is None:
            self.last_decision_trace = {"status": "deck_request"}
            return list(self.deck)
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
            raise StudentV2RuntimeError("select contract is malformed")
        options = select["option"]
        minimum, maximum = select.get("minCount"), select.get("maxCount")
        if (
            type(minimum) is not int
            or type(maximum) is not int
            or minimum < 0
            or maximum < minimum
            or maximum > len(options)
        ):
            raise StudentV2RuntimeError("selection cardinality is malformed")
        try:
            if is_ordered_selection(select.get("type"), select.get("context")):
                raise StudentV2RuntimeError(
                    "candidate-wise Student v2 cannot decode ordered Skill labels"
                )
        except ValueError as exc:
            raise StudentV2RuntimeError("selection has an unknown CABT schema") from exc
        if maximum == 0:
            self.last_decision_trace = {"status": "selected", "selected_count": 0}
            return []
        try:
            state = build_decision_state(observation)
        except DecisionStateError as exc:
            raise StudentV2RuntimeError(f"decision state construction failed: {exc}") from exc
        count = minimum if minimum else 1
        if not 0 < count <= maximum <= len(state.legal_actions):
            raise StudentV2RuntimeError("selection bounds are invalid")
        from mage_ptcg.offline_scaleup.gpu_student_v2 import _torch
        torch, _nn, _functional, _loader, _dataset = _torch()
        state_vector = state_features(state.actor_view)
        action_rows = [action_features(action.action_key) for action in state.legal_actions]
        with torch.no_grad():
            state_t = torch.tensor([state_vector], dtype=torch.float32, device=self._device)
            action_t = torch.tensor([action_rows], dtype=torch.float32, device=self._device)
            mask_t = torch.ones((1, len(action_rows)), dtype=torch.bool, device=self._device)
            scores = self._model(state_t, action_t, mask_t)[0].tolist()
        if not scores or any(not math.isfinite(score) for score in scores):
            raise StudentV2RuntimeError("Student v2 model produced empty or non-finite scores")
        ordered = sorted(
            zip(state.legal_actions, scores, strict=True),
            key=lambda item: (-item[1], item[0].action_key.digest, item[0].option_index),
        )
        selection = [action.option_index for action, _score in ordered[:count]]
        if (
            len(selection) != count
            or len(selection) != len(set(selection))
            or any(index < 0 or index >= len(state.legal_actions) for index in selection)
        ):
            raise StudentV2RuntimeError("Student v2 model selected an invalid legal action")
        self.last_decision_trace = {"status": "selected", "selected_count": count}
        return selection

    def as_agent(self):
        def agent(observation: object, configuration: object = None) -> list[int]:
            del configuration
            return self.choose(observation)
        return agent


__all__ = ["StudentV2RuntimeError", "StudentV2CandidatePolicy", "load_candidate_ranker"]
