from scripts.run_alakazam_deck_search import _schedule, _summary


def test_schedule_balances_four_opponents_and_seats() -> None:
    rows = _schedule(32)
    assert len(rows) == 32
    for opponent in ("random", "deterministic", "rule_v1", "setup-heavy"):
        assert sum(row[0] == opponent for row in rows) == 8
        assert sum(row == (opponent, 0) for row in rows) == 4


def test_stage1_gate_is_fixed_before_results() -> None:
    gate = {"minimum_completed": 8, "minimum_overall_win_rate": 0.375, "minimum_worst_opponent_win_rate": 0.125}
    rows = [{"opponent": "random", "seat": index % 2, "won": True, "status": "DONE", "steps": 10, "elapsed_seconds": 1.0} for index in range(8)]
    summary = _summary(rows, gate, "stage2")
    assert summary["stage2_status"] == "PASS"
