"""Deterministic high-information decision selectors (O1-2 §9).

Each requested selector from the O1-2 design is either genuinely implemented
against a real signal, or explicitly reported as unavailable with a reason --
never fabricated. As of this slice, against ``offline_training``-sourced
data:

- **available**: small top-2 margin (Rule v0's own per-candidate priority
  scores, carried through by ``replay_normalize.py`` into
  ``actor_information_view["teacher_ranking"]``), endgame/prize-race state
  (``phase.py``), fallback/runtime anomaly (``DecisionRecord.fallback_used``),
  Knowledge Claim scope match (only when claims are supplied by the caller).
- **unavailable**: Rule-vs-Student and Student-vs-Search disagreement (this
  source records exactly one agent's choice per decision, never two agents'
  choices for the same decision), sharp subsequent value decrease
  (``teacher_ranking`` is Rule v0's priority score, not a learned
  position-value estimate -- using it as a value function would be an
  unsupported semantic claim), unknown archetype (no archetype classifier
  exists in this slice), latency outlier (no latency signal in this source).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import ContractError, DecisionRecord, KnowledgeClaim
from .phase import ENDGAME, PRIZE_RACE

SELECTOR_SMALL_TOP2_MARGIN = "SMALL_TOP2_MARGIN"
SELECTOR_ENDGAME_OR_PRIZE_RACE = "ENDGAME_OR_PRIZE_RACE"
SELECTOR_FALLBACK_OR_ANOMALY = "FALLBACK_OR_ANOMALY"
SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH = "KNOWLEDGE_CLAIM_SCOPE_MATCH"

UNAVAILABLE_SELECTORS: Mapping[str, str] = {
    "RULE_VS_STUDENT_DISAGREEMENT": "source records exactly one agent's choice per decision, never two",
    "STUDENT_VS_SEARCH_DISAGREEMENT": "source records exactly one agent's choice per decision, never two",
    "SHARP_SUBSEQUENT_VALUE_DECREASE": "teacher_ranking is a priority score, not a position-value estimate",
    "UNKNOWN_ARCHETYPE": "no archetype classifier exists in this slice",
    "LATENCY_OUTLIER": "no per-decision latency signal exists in offline_training's collected data",
}

_SMALL_MARGIN_THRESHOLD = 5  # in teacher_ranking's own integer priority-score units


@dataclass(frozen=True, slots=True)
class HighInformationSelection:
    selector: str
    episode_id: str
    decision_index: int
    reason: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        valid = {
            SELECTOR_SMALL_TOP2_MARGIN, SELECTOR_ENDGAME_OR_PRIZE_RACE,
            SELECTOR_FALLBACK_OR_ANOMALY, SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH,
        }
        if self.selector not in valid:
            raise ContractError(f"unknown selector {self.selector!r}")


def _top2_margin(decision: DecisionRecord) -> int | None:
    view = decision.actor_information_view
    if not view:
        return None
    ranking = view.get("teacher_ranking")
    if not isinstance(ranking, list) or len(ranking) < 2:
        return None
    scores = sorted((pair[1] for pair in ranking if isinstance(pair, (list, tuple)) and len(pair) == 2), reverse=True)
    if len(scores) < 2:
        return None
    return scores[0] - scores[1]


def select_small_top2_margin(decisions: Sequence[DecisionRecord]) -> tuple[HighInformationSelection, ...]:
    selections = []
    for decision in decisions:
        margin = _top2_margin(decision)
        if margin is not None and margin <= _SMALL_MARGIN_THRESHOLD:
            selections.append(HighInformationSelection(
                selector=SELECTOR_SMALL_TOP2_MARGIN,
                episode_id=decision.episode_id,
                decision_index=decision.decision_index,
                reason=f"top-2 teacher_ranking margin {margin} <= threshold {_SMALL_MARGIN_THRESHOLD}",
                evidence={"margin": margin, "threshold": _SMALL_MARGIN_THRESHOLD},
            ))
    return tuple(selections)


def select_endgame_or_prize_race(decisions: Sequence[DecisionRecord]) -> tuple[HighInformationSelection, ...]:
    return tuple(
        HighInformationSelection(
            selector=SELECTOR_ENDGAME_OR_PRIZE_RACE,
            episode_id=decision.episode_id,
            decision_index=decision.decision_index,
            reason=f"phase={decision.phase}",
            evidence={"phase": decision.phase},
        )
        for decision in decisions
        if decision.phase in (ENDGAME, PRIZE_RACE)
    )


def select_fallback_or_anomaly(decisions: Sequence[DecisionRecord]) -> tuple[HighInformationSelection, ...]:
    return tuple(
        HighInformationSelection(
            selector=SELECTOR_FALLBACK_OR_ANOMALY,
            episode_id=decision.episode_id,
            decision_index=decision.decision_index,
            reason="fallback_used=True",
            evidence={"fallback_used": True},
        )
        for decision in decisions
        if decision.fallback_used
    )


def select_knowledge_claim_scope_match(
    decisions: Sequence[DecisionRecord], claims: Sequence[KnowledgeClaim]
) -> tuple[HighInformationSelection, ...]:
    """Flag decisions whose ``phase`` appears in a claim's declared scope.

    Returns an empty tuple (not an error) when ``claims`` is empty -- callers
    should treat that as "unavailable: no claims supplied", not "no matches
    found", by checking ``len(claims) == 0`` themselves if that distinction
    matters to them.
    """
    selections = []
    for claim in claims:
        scope_phase = claim.scope.get("phase") if isinstance(claim.scope, Mapping) else None
        if scope_phase is None:
            continue
        for decision in decisions:
            if decision.phase == scope_phase:
                selections.append(HighInformationSelection(
                    selector=SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH,
                    episode_id=decision.episode_id,
                    decision_index=decision.decision_index,
                    reason=f"decision phase matches claim {claim.claim_id!r} scope phase {scope_phase!r}",
                    evidence={"claim_id": claim.claim_id, "phase": scope_phase},
                ))
    return tuple(selections)


def select_high_information_decisions(
    decisions: Sequence[DecisionRecord], *, claims: Sequence[KnowledgeClaim] = ()
) -> dict[str, Any]:
    """Run every available selector and report unavailable ones explicitly."""
    return {
        "selections": {
            SELECTOR_SMALL_TOP2_MARGIN: select_small_top2_margin(decisions),
            SELECTOR_ENDGAME_OR_PRIZE_RACE: select_endgame_or_prize_race(decisions),
            SELECTOR_FALLBACK_OR_ANOMALY: select_fallback_or_anomaly(decisions),
            SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH: select_knowledge_claim_scope_match(decisions, claims),
        },
        "unavailable_selectors": dict(UNAVAILABLE_SELECTORS),
        "knowledge_claims_supplied": len(claims),
    }


__all__ = [
    "SELECTOR_ENDGAME_OR_PRIZE_RACE",
    "SELECTOR_FALLBACK_OR_ANOMALY",
    "SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH",
    "SELECTOR_SMALL_TOP2_MARGIN",
    "UNAVAILABLE_SELECTORS",
    "HighInformationSelection",
    "select_endgame_or_prize_race",
    "select_fallback_or_anomaly",
    "select_high_information_decisions",
    "select_knowledge_claim_scope_match",
    "select_small_top2_margin",
]
