"""Fail-closed live decoder for generic Student v3 set+cardinality weights."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from agents.rule_agent import choose_rule_indices
from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.features import action_features, state_features


class StudentV3SetRuntimeError(RuntimeError):
    """Raised when a V3 set policy cannot return an exactly legal selection."""


_RUNTIME_CLOSURE_SCHEMA_V1 = "student-v3-set-runtime-closure-v1"
_RUNTIME_CLOSURE_PATHS_V1 = {
    "cabt_json_contract": "src/mage_ptcg/meta_specialist/cabt_json_contract_v1.py",
    "cabt_trace": "src/mage_ptcg/observability/cabt_trace.py",
    "candidate_pilot": "scripts/run_student_v3_set_candidate_pilot_v1.py",
    "decision_state": "src/mage_ptcg/decision_state.py",
    "deck_io": "src/mage_ptcg/deck_io.py",
    "gpu_student_v3_set": "src/mage_ptcg/offline_scaleup/gpu_student_v3_set.py",
    "rule_v0": "agents/rule_agent.py",
    "student_dataset": "src/mage_ptcg/student/dataset.py",
    "student_features": "src/mage_ptcg/student/features.py",
    "student_model": "src/mage_ptcg/student/model.py",
    "student_v3_set_runtime": (
        "src/mage_ptcg/offline_scaleup/student_v3_set_runtime.py"
    ),
}

RULE_V0_FALLBACK_REASONS_V1 = frozenset(
    {
        "duplicate_stable_actionkey_identity",
        "ordered_selection_requires_pointer_head",
    }
)
_DUPLICATE_ACTION_IDENTITY_ERROR_V1 = (
    "duplicate stable ActionKey identity in official CABT options"
)


class StudentV3SetRuntimeTelemetry:
    """Per-game counters shared by every Student v3 agent instance."""

    def __init__(self) -> None:
        self.selection_decision_count = 0
        self.model_decision_count = 0
        self.fallback_count = 0
        self._fallback_reason_counts: Counter[str] = Counter()

    def record_selection(self) -> None:
        self.selection_decision_count += 1

    def record_model_decision(self) -> None:
        self.model_decision_count += 1

    def record_fallback(self, reason: str) -> None:
        if reason not in RULE_V0_FALLBACK_REASONS_V1:
            raise StudentV3SetRuntimeError(
                f"Rule v0 fallback reason is not explicitly allowed: {reason}"
            )
        self.fallback_count += 1
        self._fallback_reason_counts[reason] += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "selection_decision_count": self.selection_decision_count,
            "model_decision_count": self.model_decision_count,
            "fallback_count": self.fallback_count,
            "fallback_reason_counts": dict(
                sorted(self._fallback_reason_counts.items())
            ),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def student_v3_set_runtime_closure_v1() -> dict[str, object]:
    """Content-address the exact source closure used by live V3 inference."""
    repo_root = Path(__file__).resolve().parents[3]
    paths = dict(sorted(_RUNTIME_CLOSURE_PATHS_V1.items()))
    source_sha256s: dict[str, str] = {}
    for name, relative in paths.items():
        path = (repo_root / relative).resolve()
        try:
            path.relative_to(repo_root)
        except ValueError as exc:
            raise StudentV3SetRuntimeError(
                f"runtime closure source escapes repository root: {name}"
            ) from exc
        if not path.is_file():
            raise StudentV3SetRuntimeError(
                f"runtime closure source is missing: {relative}"
            )
        source_sha256s[name] = _sha256_file(path)
    payload: dict[str, object] = {
        "schema_version": _RUNTIME_CLOSURE_SCHEMA_V1,
        "source_paths": paths,
        "source_sha256s": source_sha256s,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["closure_sha256"] = hashlib.sha256(
        _RUNTIME_CLOSURE_SCHEMA_V1.encode("ascii") + b"\0" + canonical
    ).hexdigest()
    return payload


def required_max_count_from_summary(summary: Mapping[str, Any]) -> int:
    """Return the checkpoint-bound count-head width from a verified summary."""
    if not isinstance(summary, Mapping):
        raise StudentV3SetRuntimeError("V3 set training summary is not a mapping")
    model_config = summary.get("model_config")
    if not isinstance(model_config, Mapping):
        raise StudentV3SetRuntimeError("V3 set summary model_config is not a mapping")
    maximum = model_config.get("max_count")
    if type(maximum) is not int or maximum < 0:
        raise StudentV3SetRuntimeError(
            "V3 set summary model_config.max_count must be a non-negative int"
        )
    return maximum


def load_set_candidate_ranker(model_dir: Path, device: Any) -> tuple[Any, dict[str, Any]]:
    """Load the SHA-bound best V3 set checkpoint for inference."""
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        load_set_checkpoint,
    )

    try:
        model, summary = load_set_checkpoint(Path(model_dir), device)
        required_max_count_from_summary(summary)
        return model, summary
    except (GPUStudentV3SetError, OSError, ValueError, RuntimeError) as exc:
        raise StudentV3SetRuntimeError(f"V3 set checkpoint load failed: {exc}") from exc


class StudentV3SetCandidatePolicy:
    """A generic unordered selection policy bound to an exact 60-card deck."""

    def __init__(
        self,
        *,
        model: Any,
        device: Any,
        deck: list[int],
        max_count: int,
        telemetry: StudentV3SetRuntimeTelemetry | None = None,
    ) -> None:
        if len(deck) != 60 or any(type(card) is not int or card <= 0 for card in deck):
            raise StudentV3SetRuntimeError("exact positive 60-card deck is required")
        if type(max_count) is not int or max_count < 0:
            raise StudentV3SetRuntimeError("model max_count must be a non-negative int")
        if not callable(model):
            raise StudentV3SetRuntimeError("model must be callable")
        if telemetry is not None and type(telemetry) is not StudentV3SetRuntimeTelemetry:
            raise StudentV3SetRuntimeError(
                "telemetry must be an exact StudentV3SetRuntimeTelemetry"
            )
        self._model = model.eval() if hasattr(model, "eval") else model
        self._device = device
        self._max_count = max_count
        self._telemetry = telemetry or StudentV3SetRuntimeTelemetry()
        self.deck = list(deck)
        self.last_decision_trace: dict[str, Any] | None = None

    def telemetry_snapshot(self) -> dict[str, object]:
        return self._telemetry.snapshot()

    @staticmethod
    def _validate_fallback_selection(
        selected: object,
        *,
        option_count: int,
        minimum: int,
        maximum: int,
    ) -> list[int]:
        if (
            not isinstance(selected, list)
            or any(type(index) is not int for index in selected)
            or not minimum <= len(selected) <= maximum
            or len(selected) != len(set(selected))
            or any(index < 0 or index >= option_count for index in selected)
        ):
            raise StudentV3SetRuntimeError(
                "Rule v0 fallback returned an invalid legal selection"
            )
        return list(selected)

    def _rule_v0_fallback(
        self,
        observation: Mapping[str, object],
        *,
        option_count: int,
        minimum: int,
        maximum: int,
        reason: str,
    ) -> list[int]:
        selected = self._validate_fallback_selection(
            choose_rule_indices(observation),
            option_count=option_count,
            minimum=minimum,
            maximum=maximum,
        )
        self._telemetry.record_fallback(reason)
        self.last_decision_trace = {
            "status": "rule_v0_fallback",
            "fallback_reason": reason,
            "selected_count": len(selected),
        }
        return selected

    def choose(self, observation: object) -> list[int]:
        if not isinstance(observation, Mapping):
            raise StudentV3SetRuntimeError("observation is not a mapping")
        select = observation.get("select")
        if select is None:
            self.last_decision_trace = {"status": "deck_request"}
            return list(self.deck)
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
            raise StudentV3SetRuntimeError("select contract is malformed")
        options = select["option"]
        minimum = select.get("minCount")
        maximum = select.get("maxCount")
        if (
            type(minimum) is not int
            or type(maximum) is not int
            or minimum < 0
            or maximum < minimum
            or maximum > len(options)
        ):
            raise StudentV3SetRuntimeError("selection cardinality is malformed")
        try:
            ordered = is_ordered_selection(select.get("type"), select.get("context"))
        except ValueError as exc:
            raise StudentV3SetRuntimeError("selection has an unknown CABT schema") from exc
        self._telemetry.record_selection()
        if ordered:
            return self._rule_v0_fallback(
                observation,
                option_count=len(options),
                minimum=minimum,
                maximum=maximum,
                reason="ordered_selection_requires_pointer_head",
            )
        if maximum > self._max_count:
            raise StudentV3SetRuntimeError("selection maximum exceeds model count classes")
        if maximum == 0:
            self.last_decision_trace = {
                "status": "selected",
                "selected_count": 0,
                "count_source": "forced_bounds",
            }
            return []
        try:
            state = build_decision_state(observation)
        except DecisionStateError as exc:
            if str(exc) == _DUPLICATE_ACTION_IDENTITY_ERROR_V1:
                return self._rule_v0_fallback(
                    observation,
                    option_count=len(options),
                    minimum=minimum,
                    maximum=maximum,
                    reason="duplicate_stable_actionkey_identity",
                )
            raise StudentV3SetRuntimeError(
                f"decision state construction failed: {exc}"
            ) from exc
        if len(state.legal_actions) != len(options) or maximum > len(state.legal_actions):
            raise StudentV3SetRuntimeError("legal action projection does not match options")

        from mage_ptcg.offline_scaleup.gpu_student_v3_set import _torch

        torch, _nn, _functional, _loader, _dataset = _torch()
        state_rows = state_features(state.actor_view)
        action_rows = [action_features(action.action_key) for action in state.legal_actions]
        self._telemetry.record_model_decision()
        with torch.no_grad():
            state_tensor = torch.tensor(
                [state_rows], dtype=torch.float32, device=self._device
            )
            action_tensor = torch.tensor(
                [action_rows], dtype=torch.float32, device=self._device
            )
            legal_mask = torch.ones(
                (1, len(action_rows)), dtype=torch.bool, device=self._device
            )
            output = self._model(state_tensor, action_tensor, legal_mask)
        if not isinstance(output, tuple) or len(output) != 2:
            raise StudentV3SetRuntimeError("model output must be action/count logits")
        action_logits, count_logits = output
        if tuple(action_logits.shape) != (1, len(action_rows)):
            raise StudentV3SetRuntimeError("model action logit shape is invalid")
        if tuple(count_logits.shape) != (1, self._max_count + 1):
            raise StudentV3SetRuntimeError("model count logit shape is invalid")
        action_scores = action_logits[0].detach().float().cpu().tolist()
        raw_count_scores = count_logits[0].detach().float().cpu().tolist()
        if not action_scores or any(not math.isfinite(score) for score in action_scores):
            raise StudentV3SetRuntimeError("model produced a non-finite action score")
        if any(not math.isfinite(score) for score in raw_count_scores):
            raise StudentV3SetRuntimeError("model produced a non-finite count score")
        legal_count_classes = range(minimum, maximum + 1)
        count = max(legal_count_classes, key=lambda value: (raw_count_scores[value], -value))
        ranked = sorted(
            zip(state.legal_actions, action_scores, strict=True),
            key=lambda item: (
                -item[1],
                item[0].action_key.digest,
                item[0].option_index,
            ),
        )
        selected = [action.option_index for action, _score in ranked[:count]]
        if (
            not minimum <= len(selected) <= maximum
            or len(selected) != len(set(selected))
            or any(index < 0 or index >= len(options) for index in selected)
        ):
            raise StudentV3SetRuntimeError("model selected an invalid legal action set")
        self.last_decision_trace = {
            "status": "selected",
            "selected_count": count,
            "count_source": "cardinality_head",
        }
        return selected

    def as_agent(self):
        def agent(observation: object, configuration: object = None) -> list[int]:
            del configuration
            return self.choose(observation)

        return agent


__all__ = [
    "RULE_V0_FALLBACK_REASONS_V1",
    "StudentV3SetCandidatePolicy",
    "StudentV3SetRuntimeError",
    "StudentV3SetRuntimeTelemetry",
    "load_set_candidate_ranker",
    "required_max_count_from_summary",
    "student_v3_set_runtime_closure_v1",
]
