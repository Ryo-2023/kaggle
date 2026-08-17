"""Deterministic failure hypotheses (O1-2 §8): hypotheses, never ground truth.

Defines the full O1 failure taxonomy as constants, but this baseline only
*generates* hypotheses for the categories where normalized ``DecisionRecord``/
``EpisodeRecord`` data actually carries a grounded signal today:

- ``TIMEOUT_FALLBACK_RUNTIME`` -- direct: ``DecisionRecord.fallback_used``.
- ``SETUP_FAILURE`` -- a side is still without an active Pokemon by turn 3
  (the ``phase`` classifier's own ``SETUP`` signal, sustained past the
  ``BOARD_DEVELOPMENT`` cutoff).
- ``PRIZE_RACE`` -- the acting side is trailing in prize progress during
  ``PRIZE_RACE``/``ENDGAME`` phases.
- ``UNKNOWN_OPPONENT_RESPONSE`` -- the source did not reveal legal options at
  all (``legal_action_keys is None``); never true for
  ``offline_training``-sourced data (which always reveals legal options) but
  correct for other future external sources.

The remaining taxonomy members (``ENERGY_SEQUENCING``, ``SEARCH_TARGET``,
``EVOLUTION_SEQUENCING``, ``BENCH_LIABILITY``, ``ATTACKER_CHAIN``,
``RESOURCE_OVERCOMMITMENT``, ``RESOURCE_UNDERUSE``, ``MISSED_KNOCKOUT_ROUTE``,
``RETREAT_SWITCH_DECISION``, ``DISRUPTION_TIMING``,
``DECK_CONSTRUCTION_DISADVANTAGE``, ``STOCHASTIC_DRAW_DISADVANTAGE``) all
require either a card-role database this repository does not have (DEC-010)
or an oracle/Search/Student comparison signal not produced by this source --
this baseline never emits them rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical import digest
from .contracts import ContractError, DecisionRecord, EpisodeRecord
from .phase import ENDGAME, PRIZE_RACE, SETUP

FAILURE_HYPOTHESIS_SCHEMA_VERSION = "failure-hypothesis-v1"

SETUP_FAILURE = "SETUP_FAILURE"
ENERGY_SEQUENCING = "ENERGY_SEQUENCING"
SEARCH_TARGET = "SEARCH_TARGET"
EVOLUTION_SEQUENCING = "EVOLUTION_SEQUENCING"
BENCH_LIABILITY = "BENCH_LIABILITY"
ATTACKER_CHAIN = "ATTACKER_CHAIN"
PRIZE_RACE_CATEGORY = "PRIZE_RACE"
RESOURCE_OVERCOMMITMENT = "RESOURCE_OVERCOMMITMENT"
RESOURCE_UNDERUSE = "RESOURCE_UNDERUSE"
MISSED_KNOCKOUT_ROUTE = "MISSED_KNOCKOUT_ROUTE"
RETREAT_SWITCH_DECISION = "RETREAT_SWITCH_DECISION"
DISRUPTION_TIMING = "DISRUPTION_TIMING"
UNKNOWN_OPPONENT_RESPONSE = "UNKNOWN_OPPONENT_RESPONSE"
TIMEOUT_FALLBACK_RUNTIME = "TIMEOUT_FALLBACK_RUNTIME"
DECK_CONSTRUCTION_DISADVANTAGE = "DECK_CONSTRUCTION_DISADVANTAGE"
STOCHASTIC_DRAW_DISADVANTAGE = "STOCHASTIC_DRAW_DISADVANTAGE"
UNKNOWN = "UNKNOWN"

ALL_CATEGORIES = frozenset({
    SETUP_FAILURE, ENERGY_SEQUENCING, SEARCH_TARGET, EVOLUTION_SEQUENCING, BENCH_LIABILITY,
    ATTACKER_CHAIN, PRIZE_RACE_CATEGORY, RESOURCE_OVERCOMMITMENT, RESOURCE_UNDERUSE,
    MISSED_KNOCKOUT_ROUTE, RETREAT_SWITCH_DECISION, DISRUPTION_TIMING, UNKNOWN_OPPONENT_RESPONSE,
    TIMEOUT_FALLBACK_RUNTIME, DECK_CONSTRUCTION_DISADVANTAGE, STOCHASTIC_DRAW_DISADVANTAGE, UNKNOWN,
})

# Categories this baseline is capable of generating today; the rest of
# ALL_CATEGORIES exists as taxonomy but is never emitted (see module docstring).
IMPLEMENTED_CATEGORIES = frozenset({
    TIMEOUT_FALLBACK_RUNTIME, SETUP_FAILURE, PRIZE_RACE_CATEGORY, UNKNOWN_OPPONENT_RESPONSE,
})


@dataclass(frozen=True, slots=True)
class FailureHypothesis:
    schema_version: str
    category: str
    confidence: float
    evidence: Mapping[str, Any]
    episode_id: str
    decision_index_start: int
    decision_index_end: int
    phase: str
    public_only: bool
    oracle_only: bool
    reason: str
    limitations: str

    def __post_init__(self) -> None:
        if self.schema_version != FAILURE_HYPOTHESIS_SCHEMA_VERSION:
            raise ContractError(f"unsupported FailureHypothesis schema_version {self.schema_version!r}")
        if self.category not in ALL_CATEGORIES:
            raise ContractError(f"unknown failure hypothesis category {self.category!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractError("confidence must be within [0, 1]")
        if self.decision_index_end < self.decision_index_start:
            raise ContractError("decision_index_end must be >= decision_index_start")
        if self.public_only and self.oracle_only:
            raise ContractError("a hypothesis cannot be both public_only and oracle_only")

    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "category": self.category,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
            "episode_id": self.episode_id,
            "decision_index_start": self.decision_index_start,
            "decision_index_end": self.decision_index_end,
            "phase": self.phase,
            "public_only": self.public_only,
            "oracle_only": self.oracle_only,
            "reason": self.reason,
            "limitations": self.limitations,
        }
        return digest(payload, domain="failure-hypothesis")


_SETUP_FAILURE_TURN_THRESHOLD = 3


def generate_failure_hypotheses(
    episode: EpisodeRecord, decisions: Sequence[DecisionRecord]
) -> tuple[FailureHypothesis, ...]:
    """Generate deterministic hypotheses for one episode's decisions.

    Never exported directly as Student training labels by this function or
    any caller in this slice -- these are hypotheses for human/report review
    and future evidence-graded Knowledge Claims, not ground truth.
    """
    hypotheses: list[FailureHypothesis] = []
    ordered = sorted((d for d in decisions if d.episode_id == episode.episode_id), key=lambda d: d.decision_index)

    for decision in ordered:
        if decision.fallback_used:
            hypotheses.append(FailureHypothesis(
                schema_version=FAILURE_HYPOTHESIS_SCHEMA_VERSION,
                category=TIMEOUT_FALLBACK_RUNTIME,
                confidence=1.0,
                evidence={"fallback_used": True, "decision_index": decision.decision_index},
                episode_id=episode.episode_id,
                decision_index_start=decision.decision_index,
                decision_index_end=decision.decision_index,
                phase=decision.phase,
                public_only=True,
                oracle_only=False,
                reason="fallback_used=True is a direct runtime signal from the source data",
                limitations="none for this category: fallback_used is authoritative when populated by the source",
            ))
        if decision.legal_action_keys is None:
            hypotheses.append(FailureHypothesis(
                schema_version=FAILURE_HYPOTHESIS_SCHEMA_VERSION,
                category=UNKNOWN_OPPONENT_RESPONSE,
                confidence=1.0,
                evidence={"legal_action_keys": None, "decision_index": decision.decision_index},
                episode_id=episode.episode_id,
                decision_index_start=decision.decision_index,
                decision_index_end=decision.decision_index,
                phase=decision.phase,
                public_only=True,
                oracle_only=False,
                reason="source did not reveal legal options at this decision",
                limitations="cannot distinguish 'source limitation' from 'opponent choice was withheld intentionally'",
            ))

    setup_run: list[DecisionRecord] = []
    for decision in ordered:
        if decision.phase == SETUP:
            setup_run.append(decision)
        elif setup_run:
            if setup_run[-1].turn_index >= _SETUP_FAILURE_TURN_THRESHOLD:
                hypotheses.append(_setup_failure_hypothesis(episode, setup_run))
            setup_run = []
    if setup_run and setup_run[-1].turn_index >= _SETUP_FAILURE_TURN_THRESHOLD:
        hypotheses.append(_setup_failure_hypothesis(episode, setup_run))

    for decision in ordered:
        if decision.phase not in (PRIZE_RACE, ENDGAME):
            continue
        board = decision.board_summary or {}
        self_state = board.get("self") if isinstance(board, Mapping) else None
        opponent_state = board.get("opponent") if isinstance(board, Mapping) else None
        self_prizes = self_state.get("prize_count") if isinstance(self_state, Mapping) else None
        opponent_prizes = opponent_state.get("prize_count") if isinstance(opponent_state, Mapping) else None
        if isinstance(self_prizes, int) and isinstance(opponent_prizes, int) and self_prizes > opponent_prizes:
            hypotheses.append(FailureHypothesis(
                schema_version=FAILURE_HYPOTHESIS_SCHEMA_VERSION,
                category=PRIZE_RACE_CATEGORY,
                confidence=0.5,
                evidence={
                    "self_prize_count": self_prizes, "opponent_prize_count": opponent_prizes,
                    "decision_index": decision.decision_index,
                },
                episode_id=episode.episode_id,
                decision_index_start=decision.decision_index,
                decision_index_end=decision.decision_index,
                phase=decision.phase,
                public_only=True,
                oracle_only=False,
                reason="the acting side has more prizes remaining than the opponent during a late-game phase",
                limitations=(
                    "this flags a situational disadvantage, not a specific decision error; "
                    "does not identify which prior action (if any) caused it"
                ),
            ))

    return tuple(hypotheses)


def _setup_failure_hypothesis(episode: EpisodeRecord, run: Sequence[DecisionRecord]) -> FailureHypothesis:
    return FailureHypothesis(
        schema_version=FAILURE_HYPOTHESIS_SCHEMA_VERSION,
        category=SETUP_FAILURE,
        confidence=0.6,
        evidence={
            "setup_decision_count": len(run),
            "last_setup_turn": run[-1].turn_index,
            "threshold_turn": _SETUP_FAILURE_TURN_THRESHOLD,
        },
        episode_id=episode.episode_id,
        decision_index_start=run[0].decision_index,
        decision_index_end=run[-1].decision_index,
        phase=SETUP,
        public_only=True,
        oracle_only=False,
        reason=f"still classified SETUP at turn {run[-1].turn_index} (threshold {_SETUP_FAILURE_TURN_THRESHOLD})",
        limitations="the SETUP phase boundary is itself a heuristic (see phase.py); this compounds that uncertainty",
    )


__all__ = [
    "ALL_CATEGORIES",
    "ATTACKER_CHAIN",
    "BENCH_LIABILITY",
    "DECK_CONSTRUCTION_DISADVANTAGE",
    "DISRUPTION_TIMING",
    "ENERGY_SEQUENCING",
    "EVOLUTION_SEQUENCING",
    "FAILURE_HYPOTHESIS_SCHEMA_VERSION",
    "IMPLEMENTED_CATEGORIES",
    "MISSED_KNOCKOUT_ROUTE",
    "PRIZE_RACE_CATEGORY",
    "RESOURCE_OVERCOMMITMENT",
    "RESOURCE_UNDERUSE",
    "RETREAT_SWITCH_DECISION",
    "SEARCH_TARGET",
    "SETUP_FAILURE",
    "STOCHASTIC_DRAW_DISADVANTAGE",
    "TIMEOUT_FALLBACK_RUNTIME",
    "UNKNOWN",
    "UNKNOWN_OPPONENT_RESPONSE",
    "FailureHypothesis",
    "generate_failure_hypotheses",
]
