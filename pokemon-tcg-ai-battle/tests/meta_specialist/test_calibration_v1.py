"""A band must be earned by seat-balanced play against the whole panel.

The design forbids inheriting strength from a source, banding on a partial
panel, or filing an under-measured proxy as `middle`. These tests hold each.
"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.calibration_v1 import (
    CALIBRATION_SCHEMA_V1,
    CalibrationV1Error,
    MatchupResultV1,
    ReferencePanelV1,
    calibrate_opponent_v1,
    pool_epoch_identity_v1,
)


PANEL = ReferencePanelV1(reference_ids=("ref-a", "ref-b", "ref-c"))


def _matchup(reference_id: str, *, win_rate: float, games: int = 100) -> MatchupResultV1:
    """A seat-balanced record with the requested win rate."""
    per_seat = games // 2
    wins = int(round(per_seat * win_rate))
    return MatchupResultV1(
        reference_id=reference_id,
        wins_seat0=wins, draws_seat0=0, losses_seat0=per_seat - wins,
        wins_seat1=wins, draws_seat1=0, losses_seat1=per_seat - wins,
    )


def _calibrate(win_rate: float, games: int = 100, **kwargs):
    return calibrate_opponent_v1(
        opponent_id="proxy-1", panel=PANEL,
        matchups=[_matchup(name, win_rate=win_rate, games=games) for name in PANEL.reference_ids],
        **kwargs,
    )


# -- banding ---------------------------------------------------------------


def test_a_proxy_that_clearly_loses_is_banded_lower() -> None:
    result = _calibrate(0.10)
    assert result.band == "lower"
    assert result.interval_high < 0.40
    assert result.schema_version == CALIBRATION_SCHEMA_V1


def test_a_proxy_that_clearly_wins_is_banded_high() -> None:
    result = _calibrate(0.90)
    assert result.band == "high"
    assert result.interval_low > 0.60


def test_an_even_proxy_is_banded_middle() -> None:
    result = _calibrate(0.50, games=400)
    assert result.band == "middle"
    assert 0.40 < result.interval_low and result.interval_high < 0.60


def test_a_proxy_measured_on_too_few_games_is_ambiguous_not_middle() -> None:
    """Under-measurement must not be filed as a real band."""
    result = _calibrate(0.50, games=10)
    assert result.band == "ambiguous"
    assert "minimum" in result.band_reason


def test_a_proxy_whose_interval_straddles_a_threshold_is_ambiguous() -> None:
    # 100 games at 45% leaves an interval spanning the 0.40 boundary.
    result = _calibrate(0.45, games=100)
    assert result.band == "ambiguous"
    assert "straddles" in result.band_reason


def test_the_sealed_rules_travel_with_the_result() -> None:
    payload = _calibrate(0.10).to_dict()
    assert payload["rules"]["lower_threshold"] == 0.40
    assert payload["rules"]["high_threshold"] == 0.60
    assert payload["rules"]["min_games"] == 60
    # The vector it was banded from is persisted, not just the verdict.
    assert len(payload["matchups"]) == 3


# -- what a band may not be built from -------------------------------------


def test_a_seat_imbalanced_matchup_is_refused() -> None:
    """Going first is worth too much for an unbalanced record to measure a policy."""
    lopsided = MatchupResultV1(
        reference_id="ref-a",
        wins_seat0=90, draws_seat0=0, losses_seat0=10,
        wins_seat1=1, draws_seat1=0, losses_seat1=1,
    )
    with pytest.raises(CalibrationV1Error, match="seat-imbalanced"):
        calibrate_opponent_v1(
            opponent_id="proxy-1", panel=PANEL,
            matchups=[lopsided, _matchup("ref-b", win_rate=0.5), _matchup("ref-c", win_rate=0.5)],
        )


def test_a_partial_panel_is_refused() -> None:
    with pytest.raises(CalibrationV1Error, match="missing"):
        calibrate_opponent_v1(
            opponent_id="proxy-1", panel=PANEL,
            matchups=[_matchup("ref-a", win_rate=0.5), _matchup("ref-b", win_rate=0.5)],
        )


def test_an_off_panel_opponent_is_refused() -> None:
    with pytest.raises(CalibrationV1Error, match="unexpected"):
        calibrate_opponent_v1(
            opponent_id="proxy-1", panel=PANEL,
            matchups=[
                _matchup(name, win_rate=0.5) for name in ("ref-a", "ref-b", "ref-c", "ref-z")
            ],
        )


def test_a_repeated_reference_is_refused() -> None:
    with pytest.raises(CalibrationV1Error, match="more than once"):
        calibrate_opponent_v1(
            opponent_id="proxy-1", panel=PANEL,
            matchups=[_matchup("ref-a", win_rate=0.5)] * 3,
        )


def test_a_panel_must_be_ordered_and_unique() -> None:
    with pytest.raises(CalibrationV1Error, match="sorted"):
        ReferencePanelV1(reference_ids=("ref-b", "ref-a"))
    with pytest.raises(CalibrationV1Error, match="unique"):
        ReferencePanelV1(reference_ids=("ref-a", "ref-a"))
    with pytest.raises(CalibrationV1Error, match="at least two"):
        ReferencePanelV1(reference_ids=("ref-a",))


# -- pool epoch identity ----------------------------------------------------


def test_changing_any_bound_input_changes_the_pool_epoch() -> None:
    base = dict(
        deck_identity="deck-1", policy_lineage_id="a" * 64, panel=PANEL,
        calibration_schedule_id="sched-1",
    )
    original = pool_epoch_identity_v1(**base)

    assert pool_epoch_identity_v1(**{**base, "deck_identity": "deck-2"}) != original
    assert pool_epoch_identity_v1(**{**base, "policy_lineage_id": "b" * 64}) != original
    assert pool_epoch_identity_v1(**{**base, "calibration_schedule_id": "sched-2"}) != original
    assert pool_epoch_identity_v1(
        **{**base, "panel": ReferencePanelV1(reference_ids=("ref-a", "ref-b"))}
    ) != original
    # And is stable when nothing changed.
    assert pool_epoch_identity_v1(**base) == original
