"""The curriculum must keep a rehearsal floor, advance only on transitions, and
give its controls an equal budget.
"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.curriculum_v1 import (
    ARMS_V1,
    BANDS_V1,
    PHASE_MIXTURES_V1,
    PHASES_V1,
    REHEARSAL_FLOOR_V1,
    CurriculumPhaseV1,
    CurriculumV1Error,
    build_curriculum_schedule_v1,
    opponent_quota_v1,
)


BUDGET = 400_000


def test_every_phase_of_the_sealed_table_keeps_every_band_above_the_floor() -> None:
    """A band that drops to zero stops measuring regressions against it."""
    for phase in PHASES_V1:
        mixture = PHASE_MIXTURES_V1[phase]
        assert set(mixture) == set(BANDS_V1)
        assert abs(sum(mixture.values()) - 1.0) < 1e-9
        for band, share in mixture.items():
            assert share >= REHEARSAL_FLOOR_V1, f"{phase}/{band} fell to {share}"


def test_the_staged_arm_concentrates_on_a_higher_band_as_it_ascends() -> None:
    high = [PHASE_MIXTURES_V1[phase]["high"] for phase in ("foundation", "ascent", "top_focus")]
    assert high == sorted(high) and high[0] < high[-1]


def test_all_three_arms_get_exactly_the_same_transition_budget() -> None:
    """Otherwise the controls would not be an equal-transition comparison."""
    totals = {
        arm: build_curriculum_schedule_v1(arm=arm, total_transitions=BUDGET).total_transitions
        for arm in ARMS_V1
    }
    assert set(totals.values()) == {BUDGET}


def test_the_arms_differ_only_in_their_mixtures() -> None:
    schedules = {
        arm: build_curriculum_schedule_v1(arm=arm, total_transitions=BUDGET) for arm in ARMS_V1
    }
    per_phase_transitions = {
        arm: [phase.transitions for phase in schedule.phases]
        for arm, schedule in schedules.items()
    }
    # Same split of the budget across phases for every arm.
    assert len(set(map(tuple, per_phase_transitions.values()))) == 1
    # But different mixtures.
    staged = schedules["staged"].phases[0].mixture
    assert schedules["static_all_band"].phases[0].mixture != staged
    assert schedules["staged_without_rehearsal"].phases[0].mixture != staged


def test_the_static_control_never_changes_its_mixture() -> None:
    schedule = build_curriculum_schedule_v1(arm="static_all_band", total_transitions=BUDGET)
    mixtures = {tuple(sorted(phase.mixture.items())) for phase in schedule.phases}
    assert len(mixtures) == 1


def test_the_no_rehearsal_control_actually_drops_the_other_bands() -> None:
    schedule = build_curriculum_schedule_v1(
        arm="staged_without_rehearsal", total_transitions=BUDGET
    )
    foundation = schedule.phases[0].mixture
    assert foundation["lower"] == 1.0
    assert foundation["middle"] == 0.0 and foundation["high"] == 0.0


def test_the_phase_advances_only_on_transitions_consumed() -> None:
    schedule = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET)
    per_phase = BUDGET // len(PHASES_V1)

    assert schedule.phase_at(0).phase == "foundation"
    assert schedule.phase_at(per_phase - 1).phase == "foundation"
    assert schedule.phase_at(per_phase).phase == "ascent"
    assert schedule.phase_at(2 * per_phase).phase == "top_focus"
    assert schedule.phase_at(3 * per_phase).phase == "consolidation"


def test_past_the_budget_the_lineage_stays_in_its_final_phase() -> None:
    schedule = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET)
    assert schedule.phase_at(BUDGET * 10).phase == "consolidation"


def test_the_phase_trigger_takes_no_input_but_transitions() -> None:
    """There must be no parameter through which a live rating could enter."""
    import inspect

    from mage_ptcg.meta_specialist.curriculum_v1 import CurriculumScheduleV1

    signature = inspect.signature(CurriculumScheduleV1.phase_at)
    assert list(signature.parameters) == ["self", "transitions_completed"]


def test_a_negative_transition_count_is_refused() -> None:
    schedule = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET)
    with pytest.raises(CurriculumV1Error, match="nonnegative"):
        schedule.phase_at(-1)


def test_an_unknown_arm_is_refused() -> None:
    with pytest.raises(CurriculumV1Error, match="arm must be"):
        build_curriculum_schedule_v1(arm="whatever", total_transitions=BUDGET)


def test_a_budget_too_small_to_split_is_refused() -> None:
    with pytest.raises(CurriculumV1Error, match="at least"):
        build_curriculum_schedule_v1(arm="staged", total_transitions=2)


def test_a_mixture_that_does_not_sum_to_one_is_refused() -> None:
    with pytest.raises(CurriculumV1Error, match="sums to"):
        CurriculumPhaseV1(
            phase="ascent", mixture={"lower": 0.5, "middle": 0.2, "high": 0.2}, transitions=1,
        )


# -- quota allocation -------------------------------------------------------


def test_a_quota_sums_to_exactly_the_requested_games() -> None:
    schedule = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET)
    for phase in schedule.phases:
        for games in (1, 7, 33, 100, 999, 2000):
            quota = opponent_quota_v1(phase, games=games)
            assert sum(quota.values()) == games, (phase.phase, games)
            assert set(quota) == set(BANDS_V1)


def test_a_quota_is_deterministic() -> None:
    phase = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET).phases[1]
    assert opponent_quota_v1(phase, games=97) == opponent_quota_v1(phase, games=97)


def test_a_quota_of_zero_games_is_all_zeros_not_an_error() -> None:
    phase = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET).phases[0]
    assert opponent_quota_v1(phase, games=0) == {band: 0 for band in BANDS_V1}


def test_the_staged_quota_gives_every_band_at_least_one_game_when_there_is_room() -> None:
    """The rehearsal floor has to survive rounding, not just the table."""
    phase = build_curriculum_schedule_v1(arm="staged", total_transitions=BUDGET).phases[2]
    quota = opponent_quota_v1(phase, games=100)
    assert all(count > 0 for count in quota.values()), quota
