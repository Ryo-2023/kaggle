"""Select one primary and at most one backup from lane champions (Slice L8).

The design: "Lane champions enter a candidate-independent, sealed, paired and
seat-swapped Global Submission Race.  Primary selection uses the high-strength
result with simultaneous non-inferiority safety in every strength band, zero
logical fault/illegal/timeout, and the pre-registered family-wise procedure.
Select one primary and at most one backup; do not auto-submit."

Each clause is a way for a candidate to be *disqualified*, and this module
implements them as exactly that rather than as tie-breakers:

**Zero faults is a gate, not a penalty.**  A candidate with a single illegal
action, logical fault, or timeout is removed from contention regardless of how
well it scored.  A submission that can produce an illegal action is not a
better bet than a slightly weaker one that cannot.

**Winning the high band is not enough.**  A candidate must also be
non-inferior in *every* band simultaneously.  A policy that beats the field at
high strength while collapsing against weak opponents is not a safe submission,
and picking on the high band alone would hide exactly that.

**The procedure is pre-registered.**  The bands, the non-inferiority margin, and
the family-wise correction are sealed inputs recorded in the result, so a
selection can be checked against the rule that produced it instead of trusted.

**Nothing here submits.**  The module returns a recommendation.  Kaggle
submission is a manual, human-executed action, and there is deliberately no code
path from a selection to an upload.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping, Sequence


GLOBAL_RACE_SCHEMA_V1 = "meta-specialist-global-submission-race-v1"

# Strength bands a candidate must be simultaneously safe in. Ordered weakest
# first; `high` is the band primary selection reads.
STRENGTH_BANDS_V1: tuple[str, ...] = ("lower", "middle", "high")
PRIMARY_BAND_V1 = "high"

# Pre-registered non-inferiority margin: a candidate may trail the best
# candidate in a band by at most this much before it is unsafe.
NON_INFERIORITY_MARGIN_V1 = 0.05
# Family-wise correction across the bands tested simultaneously.
FAMILY_WISE_ALPHA_V1 = 0.05
_INTERVAL_Z_V1 = 1.959964


class GlobalRaceV1Error(ValueError):
    """Raised when a race cannot be judged under the pre-registered procedure."""


def _canonical_bytes_v1(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BandResultV1:
    """One candidate's paired, seat-swapped record in one strength band."""

    band: str
    games: int
    score: float
    seat0_games: int
    seat1_games: int

    def __post_init__(self) -> None:
        if self.band not in STRENGTH_BANDS_V1:
            raise GlobalRaceV1Error(f"band must be one of {list(STRENGTH_BANDS_V1)}")
        if type(self.games) is not int or self.games < 1:
            raise GlobalRaceV1Error("games must be a positive int")
        if type(self.score) is not float or self.score != self.score:
            raise GlobalRaceV1Error("score must be a real float")
        if not 0.0 <= self.score <= 1.0:
            raise GlobalRaceV1Error("score must lie in [0, 1]")
        for name in ("seat0_games", "seat1_games"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise GlobalRaceV1Error(f"{name} must be a nonnegative int")
        if self.seat0_games + self.seat1_games != self.games:
            raise GlobalRaceV1Error("seat games must sum to games")
        if self.seat0_games != self.seat1_games:
            raise GlobalRaceV1Error(
                f"band {self.band} is not seat-swapped "
                f"({self.seat0_games} vs {self.seat1_games}); going first is worth too much "
                "for an unbalanced record to compare candidates"
            )

    def interval_half_width(self) -> float:
        return _INTERVAL_Z_V1 * math.sqrt(0.25 / self.games)

    def to_dict(self) -> dict[str, object]:
        return {
            "band": self.band, "games": self.games, "score": self.score,
            "seat0_games": self.seat0_games, "seat1_games": self.seat1_games,
        }


@dataclass(frozen=True, slots=True)
class LaneChampionV1:
    """One lane's champion, with its per-band record and its integrity counts."""

    candidate_id: str
    lane_id: str
    deck_identity: str
    policy_lineage_id: str
    bands: Mapping[str, BandResultV1]
    logical_faults: int
    illegal_actions: int
    timeouts: int

    def __post_init__(self) -> None:
        for name in ("candidate_id", "lane_id", "deck_identity", "policy_lineage_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise GlobalRaceV1Error(f"{name} must be a nonempty string")
        if set(self.bands) != set(STRENGTH_BANDS_V1):
            raise GlobalRaceV1Error(
                f"a candidate must have a result in every band {list(STRENGTH_BANDS_V1)}"
            )
        for band, result in self.bands.items():
            if type(result) is not BandResultV1 or result.band != band:
                raise GlobalRaceV1Error(f"bands[{band!r}] must be a BandResultV1 for that band")
        for name in ("logical_faults", "illegal_actions", "timeouts"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise GlobalRaceV1Error(f"{name} must be a nonnegative int")

    @property
    def integrity_failures(self) -> int:
        return self.logical_faults + self.illegal_actions + self.timeouts

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "lane_id": self.lane_id,
            "deck_identity": self.deck_identity,
            "policy_lineage_id": self.policy_lineage_id,
            "bands": {band: result.to_dict() for band, result in sorted(self.bands.items())},
            "logical_faults": self.logical_faults,
            "illegal_actions": self.illegal_actions,
            "timeouts": self.timeouts,
        }


@dataclass(frozen=True, slots=True)
class DisqualificationV1:
    """Why one candidate is out of contention."""

    candidate_id: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"candidate_id": self.candidate_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class GlobalSelectionV1:
    """The recommendation, and every disqualification behind it."""

    schema_version: str
    primary: LaneChampionV1 | None
    backup: LaneChampionV1 | None
    eligible: tuple[LaneChampionV1, ...]
    disqualified: tuple[DisqualificationV1, ...]
    procedure: Mapping[str, object]
    no_selection_reason: str | None

    def procedure_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:global-race-procedure:v1\0" + _canonical_bytes_v1(dict(self.procedure))
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "primary": None if self.primary is None else self.primary.to_dict(),
            "backup": None if self.backup is None else self.backup.to_dict(),
            "eligible": [item.candidate_id for item in self.eligible],
            "disqualified": [item.to_dict() for item in self.disqualified],
            "procedure": dict(self.procedure),
            "procedure_id": self.procedure_id(),
            "no_selection_reason": self.no_selection_reason,
            "submitted": False,
        }


def _pre_registered_procedure_v1(candidates: int) -> dict[str, object]:
    """The sealed rule, including the family-wise correction actually applied."""
    comparisons = max(1, len(STRENGTH_BANDS_V1))
    return {
        "bands": list(STRENGTH_BANDS_V1),
        "primary_band": PRIMARY_BAND_V1,
        "non_inferiority_margin": NON_INFERIORITY_MARGIN_V1,
        "family_wise_alpha": FAMILY_WISE_ALPHA_V1,
        # Bonferroni across the simultaneously tested bands.
        "per_band_alpha": FAMILY_WISE_ALPHA_V1 / comparisons,
        "comparisons": comparisons,
        "candidates": candidates,
        "interval_z": _INTERVAL_Z_V1,
    }


def _integrity_disqualification_v1(candidate: LaneChampionV1) -> str | None:
    if candidate.integrity_failures == 0:
        return None
    parts = []
    if candidate.logical_faults:
        parts.append(f"{candidate.logical_faults} logical fault(s)")
    if candidate.illegal_actions:
        parts.append(f"{candidate.illegal_actions} illegal action(s)")
    if candidate.timeouts:
        parts.append(f"{candidate.timeouts} timeout(s)")
    return (
        f"{', '.join(parts)}; zero is required, so this candidate is out of contention "
        "regardless of score"
    )


def _worst_band_shortfall_v1(
    candidate: LaneChampionV1, best_per_band: Mapping[str, float],
) -> tuple[float, str, str] | None:
    """Return this candidate's worst ``(shortfall, band, reason)``, or ``None`` if safe."""
    worst: tuple[float, str, str] | None = None
    for band in STRENGTH_BANDS_V1:
        result = candidate.bands[band]
        # Non-inferiority allowing for the candidate's own uncertainty: its
        # upper bound must clear (field best - margin).
        upper = result.score + result.interval_half_width()
        floor = best_per_band[band] - NON_INFERIORITY_MARGIN_V1
        shortfall = floor - upper
        if shortfall > 0.0 and (worst is None or shortfall > worst[0]):
            worst = (shortfall, band, (
                f"inferior in the {band} band: {result.score:.3f} "
                f"(upper {upper:.3f}) against a safe-field best of {best_per_band[band]:.3f}, "
                f"below the {NON_INFERIORITY_MARGIN_V1:.2f} non-inferiority margin. Every "
                "band must be safe simultaneously, not just the primary one"
            ))
    return worst


def _eliminate_unsafe_v1(
    surviving: Sequence[LaneChampionV1],
) -> tuple[list[LaneChampionV1], list[DisqualificationV1]]:
    """Eliminate to a fixed point, so only safe candidates define the standard.

    A candidate that is itself unsafe must not set the bar the others have to
    clear.  Left uncorrected, one entrant that dominates the high band while
    collapsing against weak opponents would disqualify every balanced candidate
    for being "inferior at high strength" -- and then be disqualified itself,
    leaving nothing selected even though a safe candidate existed.

    So the worst offender is removed one at a time and the field best is
    recomputed, until every remaining candidate is non-inferior to the field of
    candidates that are themselves non-inferior.  Removing singly (rather than
    sweeping every failure at once) keeps one extreme entrant from taking the
    balanced field down with it.
    """
    remaining = list(surviving)
    disqualified: list[DisqualificationV1] = []
    while len(remaining) > 1:
        best_per_band = {
            band: max(candidate.bands[band].score for candidate in remaining)
            for band in STRENGTH_BANDS_V1
        }
        offenders = []
        for candidate in remaining:
            worst = _worst_band_shortfall_v1(candidate, best_per_band)
            if worst is not None:
                offenders.append((worst[0], candidate.candidate_id, candidate, worst[2]))
        if not offenders:
            break
        # Largest shortfall first; candidate_id breaks ties deterministically.
        offenders.sort(key=lambda item: (-item[0], item[1]))
        _shortfall, _identifier, candidate, reason = offenders[0]
        remaining.remove(candidate)
        disqualified.append(DisqualificationV1(candidate.candidate_id, reason))
    return remaining, disqualified


def select_global_submission_v1(
    candidates: Sequence[LaneChampionV1],
) -> GlobalSelectionV1:
    """Pick one primary and at most one backup, or explain why neither exists.

    Never submits anything, and never returns a candidate that failed a gate:
    when nothing qualifies, ``primary`` is ``None`` and ``no_selection_reason``
    says why, because recommending the least-bad disqualified candidate would
    defeat the gates.
    """
    if not candidates:
        raise GlobalRaceV1Error("a global race needs at least one candidate")
    for candidate in candidates:
        if type(candidate) is not LaneChampionV1:
            raise GlobalRaceV1Error("every candidate must be a LaneChampionV1")
    identifiers = [candidate.candidate_id for candidate in candidates]
    if len(set(identifiers)) != len(identifiers):
        raise GlobalRaceV1Error("candidate_id must be unique within a race")

    procedure = _pre_registered_procedure_v1(len(candidates))
    disqualified: list[DisqualificationV1] = []

    # Gate 1: integrity. Applied first so an unsafe candidate never contributes
    # its score to the field it would be compared against.
    surviving: list[LaneChampionV1] = []
    for candidate in candidates:
        reason = _integrity_disqualification_v1(candidate)
        if reason is None:
            surviving.append(candidate)
        else:
            disqualified.append(DisqualificationV1(candidate.candidate_id, reason))

    if not surviving:
        return GlobalSelectionV1(
            schema_version=GLOBAL_RACE_SCHEMA_V1, primary=None, backup=None, eligible=(),
            disqualified=tuple(disqualified), procedure=procedure,
            no_selection_reason=(
                "every candidate recorded a logical fault, illegal action, or timeout"
            ),
        )

    # Gate 2: simultaneous non-inferiority across every band, judged against a
    # field that has itself passed the same test.
    eligible, unsafe = _eliminate_unsafe_v1(surviving)
    disqualified.extend(unsafe)
    disqualified.sort(key=lambda item: item.candidate_id)
    if not eligible:
        return GlobalSelectionV1(
            schema_version=GLOBAL_RACE_SCHEMA_V1, primary=None, backup=None, eligible=(),
            disqualified=tuple(disqualified), procedure=procedure,
            no_selection_reason=(
                "no candidate was simultaneously non-inferior in every strength band"
            ),
        )

    # Primary is decided on the high-strength band among candidates already
    # proven safe everywhere; ties break on total games, then id.
    ordered = tuple(sorted(
        eligible,
        key=lambda item: (
            -item.bands[PRIMARY_BAND_V1].score,
            -sum(result.games for result in item.bands.values()),
            item.candidate_id,
        ),
    ))
    primary = ordered[0]
    # At most one backup, and only from a different lane: two candidates from the
    # same lane share a deck and fail together.
    backup = next(
        (item for item in ordered[1:] if item.lane_id != primary.lane_id), None
    )
    return GlobalSelectionV1(
        schema_version=GLOBAL_RACE_SCHEMA_V1, primary=primary, backup=backup,
        eligible=ordered, disqualified=tuple(disqualified), procedure=procedure,
        no_selection_reason=None,
    )


__all__ = [
    "FAMILY_WISE_ALPHA_V1",
    "GLOBAL_RACE_SCHEMA_V1",
    "NON_INFERIORITY_MARGIN_V1",
    "PRIMARY_BAND_V1",
    "STRENGTH_BANDS_V1",
    "BandResultV1",
    "DisqualificationV1",
    "GlobalRaceV1Error",
    "GlobalSelectionV1",
    "LaneChampionV1",
    "select_global_submission_v1",
]
