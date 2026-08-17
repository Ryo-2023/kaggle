from __future__ import annotations

from pathlib import Path

from scripts.run_cg_fresh_meta_confirmation_v1 import (
    FRESH_MEDAL_IDS,
    P1_PACKAGE,
    P2_PACKAGE,
    RESERVED_MEDAL_IDS,
    _aggregate_arm,
    build_fresh_meta_games,
    validate_fresh_meta_ids,
)


ROOT = Path(__file__).resolve().parents[2]


def test_fresh_medal_holdout_is_unique_and_excludes_consumed_medals() -> None:
    assert len(FRESH_MEDAL_IDS) == 24
    assert len(set(FRESH_MEDAL_IDS)) == 24
    assert "medal_0001_77a53ffc" not in FRESH_MEDAL_IDS
    assert "medal_0004_01501d64" not in FRESH_MEDAL_IDS
    assert validate_fresh_meta_ids(FRESH_MEDAL_IDS)["status"] == "PASS"


def test_reserved_medal_holdout_is_valid_but_outside_completed_batch() -> None:
    assert len(RESERVED_MEDAL_IDS) == 10
    assert set(RESERVED_MEDAL_IDS).isdisjoint(FRESH_MEDAL_IDS)
    assert validate_fresh_meta_ids(RESERVED_MEDAL_IDS[:2])["status"] == "PASS"


def test_fresh_meta_game_builder_keeps_candidate_control_pairs_aligned() -> None:
    games = build_fresh_meta_games(
        candidate_package=P2_PACKAGE,
        control_package=P1_PACKAGE,
        refs=FRESH_MEDAL_IDS[:2],
        base_seed=50123000,
        repetitions=1,
    )
    assert len(games) == 8  # two opponents × two seats × one repetition × two arms
    candidate = [game for game in games if game.metadata["arm_id"] == "p2_candidate"]
    control = [game for game in games if game.metadata["arm_id"] == "p1_control"]
    assert len(candidate) == len(control) == 4
    assert {(g.metadata["pair_key"], g.seed) for g in candidate} == {
        (g.metadata["pair_key"], g.seed) for g in control
    }
    assert all(g.metadata["meta_provenance"] == "fresh_unused" for g in games)


def test_aggregate_arm_reports_score_and_seat_gap() -> None:
    rows = [
        {"outcome": "win", "seat": 0, "opponent_id": "a"},
        {"outcome": "loss", "seat": 1, "opponent_id": "a"},
        {"outcome": "draw", "seat": 0, "opponent_id": "b"},
        {"outcome": "fault", "seat": 1, "opponent_id": "b"},
    ]
    result = _aggregate_arm(rows)
    assert result["wins"] == 1
    assert result["draws"] == 1
    assert result["losses"] == 1
    assert result["faults"] == 1
    assert result["score_rate"] == 0.375
    assert result["seat_gap"] == 0.75
