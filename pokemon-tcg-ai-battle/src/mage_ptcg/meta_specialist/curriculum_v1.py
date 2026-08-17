"""One lineage's ascent through opponent bands, with a rehearsal floor (Slice L7).

The design: "One lineage continues through ``foundation``, ``ascent``,
``top_focus``, and ``consolidation``.  The initial mixture is the frozen table
in the design spec; past bands retain a nonzero rehearsal floor.  Run
equal-transition controls ``static_all_band`` and ``staged_without_rehearsal``
on the same exogenous pool.  Live Kaggle medal/rating is never an observation or
phase trigger."

What that makes structural here:

**The mixture is a sealed table, not a computation.**  Phases index into
:data:`PHASE_MIXTURES_V1`; nothing derives a mixture from recent performance,
because a mixture that reacts to results is a different experiment than the one
the controls are designed to isolate.

**Every past band keeps a nonzero share.**  A curriculum that drops a band
entirely stops measuring whether the policy still beats it, so regressions
against earlier opponents become invisible.  The frozen table is validated at
import: a phase whose predecessors are not all present with a positive share is
a bug in the table, not something to be silently tolerated.

**Phases advance on transitions consumed, and on nothing else.**  The trigger is
the count of environment transitions -- an endogenous quantity produced by this
lineage.  There is deliberately no code path by which a Kaggle medal, rating, or
leaderboard placement can advance a phase.

**The controls share the schedule.**  ``static_all_band`` and
``staged_without_rehearsal`` are produced by the same builder from the same
transition budget, so a comparison against them differs in the mixture and
nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


CURRICULUM_SCHEMA_V1 = "meta-specialist-ascent-curriculum-v1"

PHASES_V1: tuple[str, ...] = ("foundation", "ascent", "top_focus", "consolidation")
# The opponent bands a mixture is defined over, weakest first. `ambiguous` is
# deliberately absent: an unbanded proxy has no place in a strength schedule.
BANDS_V1: tuple[str, ...] = ("lower", "middle", "high")

ARMS_V1: tuple[str, ...] = ("staged", "static_all_band", "staged_without_rehearsal")

# The frozen mixture table. Each phase concentrates on its own band while every
# earlier band keeps a rehearsal share.
PHASE_MIXTURES_V1: Mapping[str, Mapping[str, float]] = {
    "foundation":    {"lower": 0.70, "middle": 0.25, "high": 0.05},
    "ascent":        {"lower": 0.25, "middle": 0.55, "high": 0.20},
    "top_focus":     {"lower": 0.10, "middle": 0.25, "high": 0.65},
    "consolidation": {"lower": 0.20, "middle": 0.35, "high": 0.45},
}

# No band may fall to zero once it has been trained against.
REHEARSAL_FLOOR_V1 = 0.05


class CurriculumV1Error(ValueError):
    """Raised when a phase, mixture, or schedule violates the sealed rules."""


def _validate_mixture_table_v1() -> None:
    """Check the frozen table at import: a bad table is a bug, not a runtime surprise."""
    for phase in PHASES_V1:
        mixture = PHASE_MIXTURES_V1.get(phase)
        if mixture is None:
            raise CurriculumV1Error(f"the frozen mixture table has no entry for {phase!r}")
        if set(mixture) != set(BANDS_V1):
            raise CurriculumV1Error(f"{phase} mixture must cover exactly {list(BANDS_V1)}")
        total = sum(mixture.values())
        if abs(total - 1.0) > 1e-9:
            raise CurriculumV1Error(f"{phase} mixture sums to {total}, not 1.0")
        for band, share in mixture.items():
            if share < REHEARSAL_FLOOR_V1:
                raise CurriculumV1Error(
                    f"{phase} gives {band} a share of {share}, below the "
                    f"{REHEARSAL_FLOOR_V1} rehearsal floor"
                )


_validate_mixture_table_v1()


@dataclass(frozen=True, slots=True)
class CurriculumPhaseV1:
    """One phase: its name, its sealed mixture, and the transition count that ends it."""

    phase: str
    mixture: Mapping[str, float]
    transitions: int

    def __post_init__(self) -> None:
        if self.phase not in PHASES_V1:
            raise CurriculumV1Error(f"phase must be one of {list(PHASES_V1)}")
        if type(self.transitions) is not int or self.transitions < 1:
            raise CurriculumV1Error("transitions must be a positive int")
        if set(self.mixture) != set(BANDS_V1):
            raise CurriculumV1Error(f"mixture must cover exactly {list(BANDS_V1)}")
        total = sum(self.mixture.values())
        if abs(total - 1.0) > 1e-9:
            raise CurriculumV1Error(f"mixture sums to {total}, not 1.0")
        if any(share < 0.0 for share in self.mixture.values()):
            raise CurriculumV1Error("a mixture share cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "mixture": dict(self.mixture),
            "transitions": self.transitions,
        }


@dataclass(frozen=True, slots=True)
class CurriculumScheduleV1:
    """A whole arm: ordered phases over one fixed transition budget."""

    schema_version: str
    arm: str
    phases: tuple[CurriculumPhaseV1, ...]

    def __post_init__(self) -> None:
        if self.arm not in ARMS_V1:
            raise CurriculumV1Error(f"arm must be one of {list(ARMS_V1)}")
        if not self.phases:
            raise CurriculumV1Error("a schedule needs at least one phase")

    @property
    def total_transitions(self) -> int:
        return sum(phase.transitions for phase in self.phases)

    def phase_at(self, transitions_completed: int) -> CurriculumPhaseV1:
        """Which phase a lineage is in after consuming this many transitions.

        The only trigger is the endogenous transition count.  There is no
        parameter here through which a Kaggle medal or rating could enter.
        """
        if type(transitions_completed) is not int or transitions_completed < 0:
            raise CurriculumV1Error("transitions_completed must be a nonnegative int")
        boundary = 0
        for phase in self.phases:
            boundary += phase.transitions
            if transitions_completed < boundary:
                return phase
        # Past the budget the lineage stays in its final phase rather than
        # falling off the end or silently restarting.
        return self.phases[-1]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "arm": self.arm,
            "phases": [phase.to_dict() for phase in self.phases],
            "total_transitions": self.total_transitions,
        }


def _split_budget_v1(total_transitions: int, parts: int) -> list[int]:
    """Split a budget into ``parts`` that sum to exactly the budget."""
    if type(total_transitions) is not int or total_transitions < parts:
        raise CurriculumV1Error(
            f"total_transitions must be an int of at least {parts} (one per phase)"
        )
    base, remainder = divmod(total_transitions, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _without_rehearsal_v1(phase: str) -> dict[str, float]:
    """The phase's own band only: the control that removes the rehearsal floor."""
    focus = {
        "foundation": "lower", "ascent": "middle",
        "top_focus": "high", "consolidation": "high",
    }[phase]
    return {band: (1.0 if band == focus else 0.0) for band in BANDS_V1}


def build_curriculum_schedule_v1(
    *, arm: str, total_transitions: int,
) -> CurriculumScheduleV1:
    """Build one arm's schedule over a fixed transition budget.

    All three arms are built here from the same budget so an equal-transition
    comparison is structural: the arms differ in their mixtures and in nothing
    else, including how the budget is divided.

    * ``staged`` -- the sealed table, rehearsal floor intact.
    * ``static_all_band`` -- one uniform mixture for the whole run, so staging
      itself is the only thing the comparison removes.
    * ``staged_without_rehearsal`` -- the same staging, but each phase trains
      only against its own band, isolating what the rehearsal floor buys.
    """
    if arm not in ARMS_V1:
        raise CurriculumV1Error(f"arm must be one of {list(ARMS_V1)}")
    budget = _split_budget_v1(total_transitions, len(PHASES_V1))

    phases: list[CurriculumPhaseV1] = []
    uniform = {band: 1.0 / len(BANDS_V1) for band in BANDS_V1}
    for phase, transitions in zip(PHASES_V1, budget, strict=True):
        if arm == "staged":
            mixture: Mapping[str, float] = dict(PHASE_MIXTURES_V1[phase])
        elif arm == "static_all_band":
            mixture = dict(uniform)
        else:
            mixture = _without_rehearsal_v1(phase)
        phases.append(
            CurriculumPhaseV1(phase=phase, mixture=mixture, transitions=transitions)
        )

    schedule = CurriculumScheduleV1(
        schema_version=CURRICULUM_SCHEMA_V1, arm=arm, phases=tuple(phases),
    )
    if schedule.total_transitions != total_transitions:
        raise CurriculumV1Error(
            "the split did not preserve the transition budget; an arm comparison "
            "would not be equal-transition"
        )
    return schedule


def opponent_quota_v1(
    phase: CurriculumPhaseV1, *, games: int,
) -> dict[str, int]:
    """Turn a phase's mixture into whole game counts that sum to ``games``.

    Largest-remainder allocation, so the quota sums to exactly the requested
    number rather than drifting by a rounding error per band.
    """
    if type(phase) is not CurriculumPhaseV1:
        raise CurriculumV1Error("phase must be a CurriculumPhaseV1")
    if type(games) is not int or games < 0:
        raise CurriculumV1Error("games must be a nonnegative int")
    if games == 0:
        return {band: 0 for band in BANDS_V1}

    exact = {band: phase.mixture[band] * games for band in BANDS_V1}
    quota = {band: int(value) for band, value in exact.items()}
    shortfall = games - sum(quota.values())
    # Give the remaining games to the largest fractional parts; ties break by
    # band order so the allocation is deterministic.
    ranked = sorted(
        BANDS_V1, key=lambda band: (-(exact[band] - quota[band]), BANDS_V1.index(band))
    )
    for band in ranked[:shortfall]:
        quota[band] += 1
    if sum(quota.values()) != games:
        raise CurriculumV1Error("quota allocation did not sum to the requested games")
    return quota


__all__ = [
    "ARMS_V1",
    "BANDS_V1",
    "CURRICULUM_SCHEMA_V1",
    "PHASES_V1",
    "PHASE_MIXTURES_V1",
    "REHEARSAL_FLOOR_V1",
    "CurriculumPhaseV1",
    "CurriculumScheduleV1",
    "CurriculumV1Error",
    "build_curriculum_schedule_v1",
    "opponent_quota_v1",
]
