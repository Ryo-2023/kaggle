"""Global selection must gate on integrity and on every band, and never submit."""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.global_race_v1 import (
    NON_INFERIORITY_MARGIN_V1,
    STRENGTH_BANDS_V1,
    BandResultV1,
    GlobalRaceV1Error,
    LaneChampionV1,
    select_global_submission_v1,
)


def _bands(lower=0.60, middle=0.60, high=0.60, games=400):
    scores = {"lower": lower, "middle": middle, "high": high}
    return {
        band: BandResultV1(
            band=band, games=games, score=scores[band],
            seat0_games=games // 2, seat1_games=games // 2,
        )
        for band in STRENGTH_BANDS_V1
    }


def _champion(candidate_id, *, lane=None, faults=0, illegal=0, timeouts=0, **band_kw):
    return LaneChampionV1(
        candidate_id=candidate_id, lane_id=lane or f"lane-{candidate_id}",
        deck_identity=f"deck-{candidate_id}", policy_lineage_id=candidate_id * 8,
        bands=_bands(**band_kw), logical_faults=faults,
        illegal_actions=illegal, timeouts=timeouts,
    )


# -- integrity gate ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["faults", "illegal", "timeouts"])
def test_a_single_integrity_failure_disqualifies_regardless_of_score(kind) -> None:
    """A submission that can act illegally is not a better bet than a weaker safe one."""
    unsafe = _champion("a", high=0.99, middle=0.99, lower=0.99, **{kind: 1})
    safe = _champion("b", high=0.60, middle=0.60, lower=0.60)

    selection = select_global_submission_v1([unsafe, safe])

    assert selection.primary.candidate_id == "b"
    reasons = {item.candidate_id: item.reason for item in selection.disqualified}
    assert "a" in reasons and "out of contention regardless of score" in reasons["a"]


def test_no_selection_is_made_when_every_candidate_is_unsafe() -> None:
    selection = select_global_submission_v1([
        _champion("a", faults=1), _champion("b", illegal=2),
    ])
    assert selection.primary is None and selection.backup is None
    assert "logical fault" in selection.no_selection_reason
    assert selection.to_dict()["submitted"] is False


# -- simultaneous non-inferiority ------------------------------------------


def test_winning_the_high_band_is_not_enough_if_a_weaker_band_collapses() -> None:
    """The failure the every-band rule exists to catch."""
    lopsided = _champion("spike", high=0.95, middle=0.60, lower=0.10, games=4000)
    balanced = _champion("steady", high=0.62, middle=0.62, lower=0.62, games=4000)

    selection = select_global_submission_v1([lopsided, balanced])

    assert selection.primary.candidate_id == "steady"
    reasons = {item.candidate_id: item.reason for item in selection.disqualified}
    assert "lower band" in reasons["spike"]
    assert "simultaneously" in reasons["spike"]


def test_the_high_band_decides_among_candidates_safe_everywhere() -> None:
    strong = _champion("strong", high=0.70, middle=0.62, lower=0.62, games=4000)
    weaker = _champion("weaker", high=0.66, middle=0.64, lower=0.64, games=4000)

    selection = select_global_submission_v1([strong, weaker])

    assert selection.primary.candidate_id == "strong"
    assert selection.backup.candidate_id == "weaker"


def test_a_candidate_within_the_margin_stays_eligible() -> None:
    best = _champion("best", high=0.70, middle=0.70, lower=0.70, games=4000)
    close = _champion(
        "close", high=0.70 - NON_INFERIORITY_MARGIN_V1 / 2,
        middle=0.70, lower=0.70, games=4000,
    )

    selection = select_global_submission_v1([best, close])

    assert {item.candidate_id for item in selection.eligible} == {"best", "close"}


# -- primary and backup -----------------------------------------------------


def test_at_most_one_backup_is_selected() -> None:
    field = [
        _champion("a", high=0.70, middle=0.65, lower=0.65, games=4000),
        _champion("b", high=0.68, middle=0.65, lower=0.65, games=4000),
        _champion("c", high=0.67, middle=0.65, lower=0.65, games=4000),
    ]
    selection = select_global_submission_v1(field)

    assert selection.primary.candidate_id == "a"
    assert selection.backup.candidate_id == "b"
    assert len(selection.eligible) == 3  # all eligible, but only one backup named


def test_the_backup_never_shares_the_primary_lane() -> None:
    """Two candidates from one lane share a deck and would fail together."""
    field = [
        _champion("a", lane="lane-x", high=0.70, middle=0.65, lower=0.65, games=4000),
        _champion("a2", lane="lane-x", high=0.69, middle=0.65, lower=0.65, games=4000),
        _champion("b", lane="lane-y", high=0.66, middle=0.65, lower=0.65, games=4000),
    ]
    selection = select_global_submission_v1(field)

    assert selection.primary.candidate_id == "a"
    assert selection.backup.candidate_id == "b"


def test_a_single_safe_candidate_gets_a_primary_and_no_backup() -> None:
    selection = select_global_submission_v1([_champion("only")])
    assert selection.primary.candidate_id == "only"
    assert selection.backup is None


# -- procedure and inputs ---------------------------------------------------


def test_the_pre_registered_procedure_travels_with_the_selection() -> None:
    selection = select_global_submission_v1([_champion("a"), _champion("b")])
    procedure = selection.to_dict()["procedure"]

    assert procedure["bands"] == list(STRENGTH_BANDS_V1)
    assert procedure["primary_band"] == "high"
    assert procedure["non_inferiority_margin"] == NON_INFERIORITY_MARGIN_V1
    # Family-wise correction is actually applied across the bands tested.
    assert procedure["per_band_alpha"] == pytest.approx(
        procedure["family_wise_alpha"] / procedure["comparisons"]
    )
    assert selection.procedure_id()


def test_a_seat_imbalanced_band_is_refused() -> None:
    with pytest.raises(GlobalRaceV1Error, match="seat-swapped"):
        BandResultV1(band="high", games=100, score=0.5, seat0_games=90, seat1_games=10)


def test_a_candidate_missing_a_band_is_refused() -> None:
    with pytest.raises(GlobalRaceV1Error, match="every band"):
        LaneChampionV1(
            candidate_id="a", lane_id="l", deck_identity="d", policy_lineage_id="p",
            bands={"high": _bands()["high"]}, logical_faults=0,
            illegal_actions=0, timeouts=0,
        )


def test_duplicate_candidate_ids_are_refused() -> None:
    with pytest.raises(GlobalRaceV1Error, match="unique"):
        select_global_submission_v1([_champion("a"), _champion("a")])


def test_an_empty_race_is_refused() -> None:
    with pytest.raises(GlobalRaceV1Error, match="at least one"):
        select_global_submission_v1([])
