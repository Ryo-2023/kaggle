"""curriculum の band 配分を実在の相手へ写す層の契約 (正典 §13)。"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.curriculum_opponents_v1 import (
    CurriculumOpponentsV1Error,
    band_map_from_manifest_v1,
    opponents_by_band_v1,
    select_phase_opponents_v1,
)
from mage_ptcg.meta_specialist.curriculum_v1 import (
    build_curriculum_schedule_v1,
    opponent_quota_v1,
)


def _phase(name: str):
    schedule = build_curriculum_schedule_v1(arm="staged", total_transitions=4000)
    return next(p for p in schedule.phases if p.phase == name)


_MANIFEST = {
    "calibrations": [
        {"opponent_id": "low_a", "band": "lower"},
        {"opponent_id": "low_b", "band": "lower"},
        {"opponent_id": "mid_a", "band": "middle"},
        {"opponent_id": "high_a", "band": "high"},
        {"opponent_id": "unknown_a", "band": "ambiguous"},
        {"opponent_id": "failed_a", "band": None, "error": "no completed games"},
    ]
}


def test_only_decided_bands_enter_the_map() -> None:
    """`ambiguous` と未 banding を既定で使わないこと.

    `ambiguous` は証拠不足の表明である。ここで拾うと、測っていない強度で
    curriculum が層化される。
    """
    band_map = band_map_from_manifest_v1(_MANIFEST)
    assert set(band_map) == {"low_a", "low_b", "mid_a", "high_a"}


def test_the_schedule_matches_the_phase_quota_exactly() -> None:
    """各 band の局数が curriculum の quota と一致すること."""
    phase = _phase("top_focus")
    band_map = band_map_from_manifest_v1(_MANIFEST)
    games = 40
    schedule = select_phase_opponents_v1(
        phase, band_map=band_map, available=list(band_map), games=games
    )
    assert len(schedule) == games

    quota = opponent_quota_v1(phase, games=games)
    got = {"lower": 0, "middle": 0, "high": 0}
    for opponent_id in schedule:
        got[band_map[opponent_id]] += 1
    assert got == quota


def test_members_of_a_band_are_cycled_rather_than_concentrated() -> None:
    """band 内に複数いるとき 1 体へ集中しないこと."""
    phase = _phase("foundation")
    band_map = band_map_from_manifest_v1(_MANIFEST)
    schedule = select_phase_opponents_v1(
        phase, band_map=band_map, available=list(band_map), games=40
    )
    lower_used = {o for o in schedule if band_map[o] == "lower"}
    assert lower_used == {"low_a", "low_b"}


def test_an_unfillable_band_fails_rather_than_rebalancing() -> None:
    """quota を満たせない band があれば失敗すること.

    他 band で埋めると「top_focus を走らせた」と記録しながら実際は lower 中心、
    という最も気付きにくいずれになる。
    """
    phase = _phase("top_focus")
    band_map = {"low_a": "lower", "mid_a": "middle"}  # high が居ない
    with pytest.raises(CurriculumOpponentsV1Error) as excinfo:
        select_phase_opponents_v1(phase, band_map=band_map, available=list(band_map), games=40)
    assert "high" in str(excinfo.value)


def test_grouping_is_stable_and_ignores_unavailable_opponents() -> None:
    band_map = band_map_from_manifest_v1(_MANIFEST)
    grouped = opponents_by_band_v1(band_map, available=["low_b", "high_a"])
    assert grouped == {"lower": ["low_b"], "middle": [], "high": ["high_a"]}
