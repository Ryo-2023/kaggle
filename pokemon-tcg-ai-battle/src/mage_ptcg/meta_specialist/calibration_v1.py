"""Band an opponent by measured cross-play, never by where it came from (Slice L7).

The design's rule: "Calibrate every proxy against a fixed multi-policy reference
panel using a seat-balanced cross-play matrix.  Persist the matchup vector and
interval; assign ``lower``, ``middle``, ``high``, or ``ambiguous`` only by
pre-sealed rules."

Three consequences shape this module:

**A band is earned by play, not inherited.**  An opponent's Kaggle medal, its
source, or the strength of the deck it was built from say nothing here.  The
only input to a band is the measured matchup vector against the reference panel.

**Seat balance is a precondition, not a preference.**  Going first is worth a
great deal in this game, so an unbalanced matchup measures the seat as much as
the policy.  :func:`calibrate_opponent_v1` refuses a matchup whose seats are not
balanced rather than reporting a number that conflates the two.

**Uncertainty gets its own band.**  ``ambiguous`` exists so that a proxy
measured on too few games is *not* filed as ``middle``.  The confidence interval
is computed from the sample actually played, and a band is assigned only when
the whole interval sits on one side of a sealed threshold; otherwise the answer
is ``ambiguous`` and the caller must play more games or leave it out.

The thresholds and the minimum sample are module constants and are part of the
persisted result, so a stored calibration can be checked against the rules that
produced it rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping, Sequence


CALIBRATION_SCHEMA_V1 = "meta-specialist-opponent-calibration-v1"

BANDS_V1: tuple[str, ...] = ("lower", "middle", "high", "ambiguous")


@dataclass(frozen=True, slots=True)
class EvaluationSuiteSeparationV1:
    """Separate ascent_suite and top_band_suite for fixed bundle evaluation."""

    ascent_suite: tuple[str, ...]
    top_band_suite: tuple[str, ...]

# Sealed banding rules. A proxy is `lower` when it loses to the panel clearly,
# `high` when it beats the panel clearly, `middle` in between -- and only when
# the interval is entirely inside one region.
_LOWER_THRESHOLD_V1 = 0.40
_HIGH_THRESHOLD_V1 = 0.60
# Below this many games the interval is too wide for any band to be meaningful.
_MIN_GAMES_PER_OPPONENT_V1 = 60
# 95% normal-approximation interval on the mean score.
_INTERVAL_Z_V1 = 1.959964


class CalibrationV1Error(ValueError):
    """Raised when a matchup cannot be calibrated under the sealed rules."""


@dataclass(frozen=True, slots=True)
class MatchupResultV1:
    """One opponent's measured record against one reference policy.

    ``score`` counts a win as 1 and a draw as 0.5, which is what a band is
    defined over; wins/draws/losses are retained so the score cannot be
    asserted independently of the games that produced it.
    """

    reference_id: str
    wins_seat0: int
    draws_seat0: int
    losses_seat0: int
    wins_seat1: int
    draws_seat1: int
    losses_seat1: int

    def __post_init__(self) -> None:
        if type(self.reference_id) is not str or not self.reference_id:
            raise CalibrationV1Error("reference_id must be a nonempty string")
        for name in (
            "wins_seat0", "draws_seat0", "losses_seat0",
            "wins_seat1", "draws_seat1", "losses_seat1",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise CalibrationV1Error(f"{name} must be a nonnegative int")
        if self.games == 0:
            raise CalibrationV1Error(f"matchup against {self.reference_id} has no games")

    @property
    def games_seat0(self) -> int:
        return self.wins_seat0 + self.draws_seat0 + self.losses_seat0

    @property
    def games_seat1(self) -> int:
        return self.wins_seat1 + self.draws_seat1 + self.losses_seat1

    @property
    def games(self) -> int:
        return self.games_seat0 + self.games_seat1

    @property
    def score(self) -> float:
        """Mean score in ``[0, 1]``: a win is 1, a draw 0.5, a loss 0."""
        points = (
            self.wins_seat0 + self.wins_seat1
            + 0.5 * (self.draws_seat0 + self.draws_seat1)
        )
        return points / self.games

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "wins_seat0": self.wins_seat0, "draws_seat0": self.draws_seat0,
            "losses_seat0": self.losses_seat0,
            "wins_seat1": self.wins_seat1, "draws_seat1": self.draws_seat1,
            "losses_seat1": self.losses_seat1,
            "games": self.games, "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class ReferencePanelV1:
    """The fixed panel every proxy is measured against.

    Frozen and content-addressed: changing the panel changes the identity, and
    the design makes a changed panel a new ``pool_epoch`` rather than a silent
    recalibration of existing results.
    """

    reference_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.reference_ids) < 2:
            raise CalibrationV1Error("a reference panel needs at least two policies")
        if any(type(item) is not str or not item for item in self.reference_ids):
            raise CalibrationV1Error("every reference_id must be a nonempty string")
        if len(set(self.reference_ids)) != len(self.reference_ids):
            raise CalibrationV1Error("reference_ids must be unique")
        if list(self.reference_ids) != sorted(self.reference_ids):
            raise CalibrationV1Error("reference_ids must be in sorted order")

    def panel_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:reference-panel:v1\0"
            + json.dumps(list(self.reference_ids), separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {"reference_ids": list(self.reference_ids), "panel_id": self.panel_id()}


@dataclass(frozen=True, slots=True)
class OpponentCalibrationV1:
    """One opponent's band, the vector it came from, and the rules that assigned it."""

    schema_version: str
    opponent_id: str
    panel_id: str
    matchups: tuple[MatchupResultV1, ...]
    games: int
    score: float
    interval_low: float
    interval_high: float
    band: str
    band_reason: str
    rules: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "opponent_id": self.opponent_id,
            "panel_id": self.panel_id,
            "matchups": [item.to_dict() for item in self.matchups],
            "games": self.games,
            "score": self.score,
            "interval_low": self.interval_low,
            "interval_high": self.interval_high,
            "band": self.band,
            "band_reason": self.band_reason,
            "rules": dict(self.rules),
        }


def _sealed_rules_v1() -> dict[str, object]:
    return {
        "lower_threshold": _LOWER_THRESHOLD_V1,
        "high_threshold": _HIGH_THRESHOLD_V1,
        "min_games": _MIN_GAMES_PER_OPPONENT_V1,
        "interval_z": _INTERVAL_Z_V1,
    }


def _score_interval_v1(score: float, games: int) -> tuple[float, float]:
    """Normal-approximation interval on a mean score in ``[0, 1]``.

    The per-game variance bound ``0.25`` is used rather than the observed
    variance so a lucky streak of identical results cannot report a zero-width
    interval and thereby earn a band it has not established.
    """
    half_width = _INTERVAL_Z_V1 * math.sqrt(0.25 / games)
    return max(0.0, score - half_width), min(1.0, score + half_width)


def _assign_band_v1(low: float, high: float, games: int) -> tuple[str, str]:
    if games < _MIN_GAMES_PER_OPPONENT_V1:
        return "ambiguous", (
            f"{games} games is below the {_MIN_GAMES_PER_OPPONENT_V1}-game minimum; "
            "no band is assigned rather than filing an under-measured proxy as middle"
        )
    if high < _LOWER_THRESHOLD_V1:
        return "lower", f"interval [{low:.3f}, {high:.3f}] is entirely below {_LOWER_THRESHOLD_V1}"
    if low > _HIGH_THRESHOLD_V1:
        return "high", f"interval [{low:.3f}, {high:.3f}] is entirely above {_HIGH_THRESHOLD_V1}"
    if low > _LOWER_THRESHOLD_V1 and high < _HIGH_THRESHOLD_V1:
        return "middle", (
            f"interval [{low:.3f}, {high:.3f}] lies strictly between "
            f"{_LOWER_THRESHOLD_V1} and {_HIGH_THRESHOLD_V1}"
        )
    return "ambiguous", (
        f"interval [{low:.3f}, {high:.3f}] straddles a threshold; more games are "
        "needed before this proxy can be banded"
    )


def calibrate_opponent_v1(
    *,
    opponent_id: str,
    panel: ReferencePanelV1,
    matchups: Sequence[MatchupResultV1],
    seat_balance_tolerance: int = 0,
) -> OpponentCalibrationV1:
    """Band one opponent from its seat-balanced cross-play against the whole panel.

    Every panel member must have been played, exactly once: a partial panel
    would band an opponent on a subset it might happen to match up well against,
    which is the comparison the fixed panel exists to prevent.
    """
    if type(opponent_id) is not str or not opponent_id:
        raise CalibrationV1Error("opponent_id must be a nonempty string")
    if type(panel) is not ReferencePanelV1:
        raise CalibrationV1Error("panel must be a ReferencePanelV1")
    if type(seat_balance_tolerance) is not int or seat_balance_tolerance < 0:
        raise CalibrationV1Error("seat_balance_tolerance must be a nonnegative int")

    measured = [item for item in matchups]
    if any(type(item) is not MatchupResultV1 for item in measured):
        raise CalibrationV1Error("every matchup must be a MatchupResultV1")
    seen = [item.reference_id for item in measured]
    if len(set(seen)) != len(seen):
        raise CalibrationV1Error("a reference policy appears more than once in the matchups")
    if set(seen) != set(panel.reference_ids):
        missing = sorted(set(panel.reference_ids) - set(seen))
        extra = sorted(set(seen) - set(panel.reference_ids))
        raise CalibrationV1Error(
            f"matchups do not cover the panel exactly (missing={missing}, unexpected={extra})"
        )

    for item in measured:
        imbalance = abs(item.games_seat0 - item.games_seat1)
        if imbalance > seat_balance_tolerance:
            raise CalibrationV1Error(
                f"matchup against {item.reference_id} is seat-imbalanced "
                f"({item.games_seat0} vs {item.games_seat1}); going first is worth too much "
                "for an unbalanced record to measure the policy rather than the seat"
            )

    ordered = tuple(sorted(measured, key=lambda item: item.reference_id))
    games = sum(item.games for item in ordered)
    # Weight by games so a panel member played more often counts more, matching
    # what a pooled record over the same games would give.
    score = sum(item.score * item.games for item in ordered) / games
    low, high = _score_interval_v1(score, games)
    band, reason = _assign_band_v1(low, high, games)
    return OpponentCalibrationV1(
        schema_version=CALIBRATION_SCHEMA_V1,
        opponent_id=opponent_id,
        panel_id=panel.panel_id(),
        matchups=ordered,
        games=games,
        score=score,
        interval_low=low,
        interval_high=high,
        band=band,
        band_reason=reason,
        rules=_sealed_rules_v1(),
    )


def pool_epoch_identity_v1(
    *,
    deck_identity: str,
    policy_lineage_id: str,
    panel: ReferencePanelV1,
    calibration_schedule_id: str,
) -> str:
    """Identity of the calibration regime a pool epoch is bound to.

    The design: "Changing a deck, policy, reference panel, or calibration
    schedule creates a new ``pool_epoch``."  Deriving the epoch's identity from
    exactly those four inputs makes that mechanical -- a changed input yields a
    different identity, so trajectories collected under the old regime cannot be
    mistaken for the new one.
    """
    for name, value in (
        ("deck_identity", deck_identity),
        ("policy_lineage_id", policy_lineage_id),
        ("calibration_schedule_id", calibration_schedule_id),
    ):
        if type(value) is not str or not value:
            raise CalibrationV1Error(f"{name} must be a nonempty string")
    if type(panel) is not ReferencePanelV1:
        raise CalibrationV1Error("panel must be a ReferencePanelV1")
    payload = {
        "deck_identity": deck_identity,
        "policy_lineage_id": policy_lineage_id,
        "panel_id": panel.panel_id(),
        "calibration_schedule_id": calibration_schedule_id,
        "rules": _sealed_rules_v1(),
    }
    return hashlib.sha256(
        b"mage_ptcg:pool-epoch:v1\0"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "BANDS_V1",
    "CALIBRATION_SCHEMA_V1",
    "CalibrationV1Error",
    "MatchupResultV1",
    "OpponentCalibrationV1",
    "ReferencePanelV1",
    "calibrate_opponent_v1",
    "pool_epoch_identity_v1",
]
