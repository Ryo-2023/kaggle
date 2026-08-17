from __future__ import annotations

from scripts.run_cg_historical_fresh_confirmation_v1 import aggregate_arm_rows


def test_aggregate_arm_rows_preserves_faults_and_seat_gap() -> None:
    rows = [
        {"outcome": "win", "seat": 0},
        {"outcome": "loss", "seat": 0},
        {"outcome": "draw", "seat": 1},
        {"outcome": "fault", "seat": 1},
    ]

    result = aggregate_arm_rows(rows)

    assert result["requested_games"] == 4
    assert result["wins"] == 1
    assert result["draws"] == 1
    assert result["losses"] == 1
    assert result["faults"] == 1
    assert result["score_rate"] == 0.375
    assert result["seat_gap"] == 0.25
