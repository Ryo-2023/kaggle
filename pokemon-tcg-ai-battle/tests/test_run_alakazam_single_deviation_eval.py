from scripts.run_alakazam_single_deviation_eval import ARMS, OPPONENTS, schedule, summarize


def test_schedule_balances_arm_opponent_and_seat() -> None:
    rows = schedule(32)
    assert len(rows) == 32
    for arm in ARMS:
        for opponent in OPPONENTS:
            assert sum(row[:2] == (arm, opponent) for row in rows) == 4
            assert sum(row == (arm, opponent, 0) for row in rows) == 2
            assert sum(row == (arm, opponent, 1) for row in rows) == 2


def test_summary_counts_faults_without_counting_them_as_losses() -> None:
    rows = [
        {"arm": "rule_v0_control", "opponent": "random", "status": "DONE", "own_won": True, "steps": 10, "elapsed_seconds": 1.0},
        {"arm": "rule_v0_control", "opponent": "random", "status": "AGENT_ERROR", "own_won": False, "steps": 0, "elapsed_seconds": 1.0},
        {"arm": "single_deviation_treatment", "opponent": "random", "status": "DONE", "own_won": False, "steps": 20, "elapsed_seconds": 2.0},
    ]
    payload = summarize(rows)
    assert payload["arms"]["rule_v0_control"]["win_rate"] == 1.0
    assert payload["arms"]["rule_v0_control"]["faults"] == 1
