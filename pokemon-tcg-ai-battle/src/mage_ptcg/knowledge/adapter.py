"""Soft-prior adapter that never expands cabt's legal action set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mage_ptcg.decision_state import (
    ActionKey,
    DecisionStateError,
    build_action_key,
    build_decision_state,
)

from .compatibility import CompatibilityReport, RuntimeCompatibility, check_compatibility
from .model import KnowledgePack, KnowledgeValidationError


def _safe_action_keys(observation: object) -> dict[int, ActionKey]:
    if not isinstance(observation, Mapping):
        raise KnowledgeValidationError("observation must be a mapping")
    select = observation.get("select")
    if not isinstance(select, Mapping):
        raise KnowledgeValidationError("select must be a mapping")
    options = select.get("option")
    if not isinstance(options, list):
        raise KnowledgeValidationError("select.option must be a list")
    if any(
        isinstance(option, Mapping) and option.get("type") in {4, 15}
        for option in options
    ):
        try:
            state = build_decision_state(observation)
        except DecisionStateError as exc:
            raise KnowledgeValidationError(
                "Skill/ToolCard prior requires a verified DecisionState"
            ) from exc
        return {
            action.option_index: action.action_key for action in state.legal_actions
        }
    return {
        index: build_action_key(selection_type=select.get("type"), context=select.get("context"), option=option)
        for index, option in enumerate(options)
    }


@dataclass(frozen=True, slots=True)
class KnowledgeRuleAdapter:
    """Apply a compatible pack only as a deterministic tie-break among legal actions.

    When Knowledge is neutral, the original Rule Agent baseline order is
    retained exactly.
    """

    pack: KnowledgePack | None
    compatibility: CompatibilityReport

    @classmethod
    def create(cls, pack: KnowledgePack | None, target: RuntimeCompatibility | None) -> "KnowledgeRuleAdapter":
        """Construct a disabled adapter for absent or invalid runtime configuration."""
        if pack is None or target is None:
            return cls(pack=None, compatibility=CompatibilityReport(False, ("no Knowledge Pack",)))
        return cls(pack=pack, compatibility=check_compatibility(pack, target))

    @property
    def enabled(self) -> bool:
        """Whether this adapter is safe to apply to this runtime."""
        return self.pack is not None and self.compatibility.compatible

    def prior_score(self, action_key: ActionKey) -> float:
        """Return the additive score of all matching rules, or zero when disabled."""
        if not self.enabled or self.pack is None:
            return 0.0
        score = 0.0
        for prior in self.pack.action_priors:
            if prior.selection_type is not None and prior.selection_type != action_key.selection_type:
                continue
            if prior.context is not None and prior.context != action_key.context:
                continue
            if prior.option_type is not None and prior.option_type != action_key.option_type:
                continue
            if prior.semantic_operation is not None and prior.semantic_operation != action_key.semantic_operation:
                continue
            if prior.action_key_digest is not None and prior.action_key_digest != action_key.digest:
                continue
            score += prior.score * prior.confidence.validity * prior.confidence.support * prior.confidence.freshness
        return score

    def reorder_ties(
        self,
        observation: object,
        baseline: Sequence[int],
        ranked_scores: Sequence[tuple[int, int]] | None,
    ) -> list[int]:
        """Reorder only equal Rule-score groups; neutral Knowledge keeps baseline order."""
        original = list(baseline)
        if not self.enabled or not original or ranked_scores is None:
            return original
        try:
            keys = _safe_action_keys(observation)
            ordered: list[int] = []
            position = 0
            while position < len(ranked_scores):
                score = ranked_scores[position][1]
                group: list[int] = []
                while position < len(ranked_scores) and ranked_scores[position][1] == score:
                    group.append(ranked_scores[position][0])
                    position += 1
                scores = {index: self.prior_score(keys[index]) for index in group}
                if len(set(scores.values())) == 1:
                    ordered.extend(group)
                else:
                    ordered.extend(sorted(group, key=lambda index: (-scores[index], index)))
            return ordered[: len(original)]
        except (KnowledgeValidationError, TypeError, ValueError, KeyError):
            return original
