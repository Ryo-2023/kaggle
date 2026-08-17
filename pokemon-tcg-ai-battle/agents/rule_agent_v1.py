"""C1 challenger loop that guards Rule Agent v0 with public belief state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any

from mage_ptcg.decision_state import DecisionState
from mage_ptcg.public_belief import PublicBelief, PublicBeliefPrior, PublicBeliefSummary

from mage_ptcg.knowledge import KnowledgeRuleAdapter

from .rule_agent import choose_rule_indices, rank_rule_indices


def _rule_observation_from_state(state: DecisionState) -> dict[str, object]:
    """Rebuild the minimal Rule v0 input from the shared actor view."""
    public = state.actor_view.public_state
    selection = public["select"]
    return {
        "current": {"yourIndex": state.actor_view.actor},
        "select": {
            "context": selection["context"],
            "maxCount": selection["max_count"],
            "minCount": selection["min_count"],
            "option": [
                {
                    "type": action.action_key.option_type,
                    **dict(action.action_key.canonical_payload),
                }
                for action in state.legal_actions
            ],
            "type": selection["type"],
        },
    }


def _safe_selection_bounds(observation: object) -> tuple[list[object], int, int] | None:
    if not isinstance(observation, Mapping):
        return None
    select = observation.get("select")
    if not isinstance(select, Mapping):
        return None
    options = select.get("option")
    minimum = select.get("minCount")
    maximum = select.get("maxCount")
    if (
        not isinstance(options, list)
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
    ):
        return None
    safe_minimum = min(max(minimum, 0), len(options))
    safe_maximum = min(max(maximum, 0), len(options))
    if safe_maximum < safe_minimum:
        safe_maximum = safe_minimum
    return options, safe_minimum, safe_maximum


def _is_legal_selection(observation: object, selection: list[int]) -> bool:
    bounds = _safe_selection_bounds(observation)
    if bounds is None:
        return False
    options, minimum, maximum = bounds
    return (
        minimum <= len(selection) <= maximum
        and len(selection) == len(set(selection))
        and all(type(index) is int and 0 <= index < len(options) for index in selection)
    )


def _first_legal(observation: object) -> list[int]:
    bounds = _safe_selection_bounds(observation)
    if bounds is None:
        return []
    _options, minimum, _maximum = bounds
    return list(range(minimum))


class RuleAgentV1:
    """Instance-local C1 challenger; Rule v0 remains the action authority.

    C1 has no verified action-to-hidden-event model, so the public belief is
    used to construct and audit the decision loop but never to delete or add a
    candidate. With no permitted deck prior (the runtime default), belief is
    explicitly degraded and the selected action is exactly Rule v0's output.
    """

    def __init__(
        self,
        *,
        priors: tuple[PublicBeliefPrior, ...] = (),
        decision_timeout_ms: float = 25.0,
        clock: Callable[[], float] = time.perf_counter,
        knowledge_adapter: KnowledgeRuleAdapter | None = None,
    ) -> None:
        if isinstance(decision_timeout_ms, bool) or not isinstance(
            decision_timeout_ms, (int, float)
        ):
            raise ValueError("decision_timeout_ms must be numeric")
        if decision_timeout_ms <= 0:
            raise ValueError("decision_timeout_ms must be positive")
        self.public_belief = PublicBelief(priors)
        self.decision_timeout_ms = float(decision_timeout_ms)
        self._clock = clock
        self.knowledge_adapter = knowledge_adapter or KnowledgeRuleAdapter.create(None, None)
        self.last_state: DecisionState | None = None
        self.last_summary: PublicBeliefSummary | None = None
        self.last_source = "not_started"
        self.last_elapsed_ms = 0.0

    def reset(self) -> None:
        self.public_belief.reset()
        self.last_state = None
        self.last_summary = None
        self.last_source = "reset"
        self.last_elapsed_ms = 0.0

    def choose(self, observation: object) -> list[int] | None:
        raw_fallback = choose_rule_indices(observation)
        if raw_fallback is None:
            self.reset()
            return None

        started = self._clock()
        update = self.public_belief.update_from_observation(observation)
        self.last_state = update.decision_state
        self.last_summary = update.summary
        baseline = (
            raw_fallback
            if update.decision_state is None
            else choose_rule_indices(_rule_observation_from_state(update.decision_state))
        )
        if baseline is None:
            baseline = raw_fallback
        try:
            candidate = self.knowledge_adapter.reorder_ties(
                observation, baseline, rank_rule_indices(observation)
            )
        except Exception:  # Optional advisory boundary; the Rule v0 baseline remains safe.
            candidate = baseline
        elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)
        self.last_elapsed_ms = elapsed_ms

        if not _is_legal_selection(observation, baseline):
            fallback = _first_legal(observation)
            if _is_legal_selection(observation, fallback):
                self.last_source = "deterministic_first_legal"
                return fallback
            self.last_source = "no_valid_selection"
            return None
        if elapsed_ms > self.decision_timeout_ms:
            self.last_source = "rule_v0_timeout_fallback"
            return baseline
        if not _is_legal_selection(observation, candidate):
            self.last_source = "knowledge_invalid_candidate_fallback"
            return baseline
        if update.decision_state is None or update.summary.degraded:
            self.last_source = "knowledge_prior_rule_v0_fallback" if candidate != baseline else "rule_v0_belief_fallback"
            return candidate

        self.last_source = "knowledge_prior_public_belief_guarded_rule_v0" if candidate != baseline else "public_belief_guarded_rule_v0"
        return candidate


__all__ = ["RuleAgentV1"]
