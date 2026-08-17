"""Deterministic, explainable game-phase segmentation baseline.

No phase concept exists anywhere else in this repository today (confirmed by
inventory: only pipeline/orchestration "phases" exist, e.g.
``offline_training.runstate.PHASES``, unrelated to in-game phase). This is a
from-scratch, rule-based classifier over signals present in a normalized
decision's public state (turn, prize counts on both sides, active-Pokemon
presence) — never an ML classifier, per the O1-2 mandate. Every result
carries a human-readable reason and the raw evidence it was computed from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

OPENING = "OPENING"
SETUP = "SETUP"
BOARD_DEVELOPMENT = "BOARD_DEVELOPMENT"
FIRST_MAJOR_EXCHANGE = "FIRST_MAJOR_EXCHANGE"
PRIZE_RACE = "PRIZE_RACE"
ENDGAME = "ENDGAME"
UNKNOWN = "UNKNOWN"

VALID_PHASES = frozenset({OPENING, SETUP, BOARD_DEVELOPMENT, FIRST_MAJOR_EXCHANGE, PRIZE_RACE, ENDGAME, UNKNOWN})

_STARTING_PRIZES = 6


@dataclass(frozen=True, slots=True)
class PhaseClassification:
    phase: str
    reason: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(f"phase must be one of {sorted(VALID_PHASES)}, got {self.phase!r}")


def classify_phase(
    *,
    turn: int | None,
    self_prize_count: int | None,
    opponent_prize_count: int | None,
    self_active_present: bool | None,
    opponent_active_present: bool | None,
    termination_proximate: bool = False,
) -> PhaseClassification:
    """Classify one decision's game phase from public, per-decision signals.

    Precedence (most to least confident): termination proximity > prize
    counts (a hard, unambiguous TCG signal) > turn number > board occupancy.
    Any missing required signal falls through to a lower-confidence rule
    rather than guessing; if nothing matches, returns ``UNKNOWN`` with the
    reason explaining what was missing.
    """
    evidence: dict[str, Any] = {
        "turn": turn,
        "self_prize_count": self_prize_count,
        "opponent_prize_count": opponent_prize_count,
        "self_active_present": self_active_present,
        "opponent_active_present": opponent_active_present,
        "termination_proximate": termination_proximate,
    }

    if termination_proximate:
        return PhaseClassification(ENDGAME, "episode termination is imminent at this decision", evidence)

    if self_prize_count is not None and opponent_prize_count is not None:
        if self_prize_count <= 1 or opponent_prize_count <= 1:
            return PhaseClassification(
                ENDGAME, "a side has 1 or 0 prize cards remaining (game-ending threshold)", evidence
            )
        if self_prize_count <= _STARTING_PRIZES // 2 or opponent_prize_count <= _STARTING_PRIZES // 2:
            return PhaseClassification(
                PRIZE_RACE, "a side has taken at least half of its starting prizes", evidence
            )
        if self_prize_count < _STARTING_PRIZES or opponent_prize_count < _STARTING_PRIZES:
            return PhaseClassification(
                FIRST_MAJOR_EXCHANGE, "the first prize card of the game has been taken by either side", evidence
            )

    if turn is None:
        return PhaseClassification(UNKNOWN, "turn number is unavailable and no prize signal applied", evidence)

    if turn <= 1:
        return PhaseClassification(OPENING, "turn 0 or 1: opening hand / initial setup turn", evidence)

    if self_active_present is False or opponent_active_present is False:
        return PhaseClassification(
            SETUP, "a side has not yet established an active Pokemon at this decision", evidence
        )

    if turn <= 3:
        return PhaseClassification(
            BOARD_DEVELOPMENT, "early turn (<=3) with no prize signal and both sides have an active Pokemon", evidence
        )

    return PhaseClassification(UNKNOWN, "no phase rule matched the available signals", evidence)


__all__ = [
    "BOARD_DEVELOPMENT",
    "ENDGAME",
    "FIRST_MAJOR_EXCHANGE",
    "OPENING",
    "PRIZE_RACE",
    "SETUP",
    "UNKNOWN",
    "VALID_PHASES",
    "PhaseClassification",
    "classify_phase",
]
