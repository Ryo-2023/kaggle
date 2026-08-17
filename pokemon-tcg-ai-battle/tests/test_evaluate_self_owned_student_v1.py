"""Small deterministic checks for Student evaluation summaries."""

from scripts.evaluate_self_owned_student_v1 import _summarize


def test_summary_counts_wins_draws_losses_and_seats() -> None:
    rows = [
        {"status": "DONE", "winner": 0, "subject_seat": 0},
        {"status": "DONE", "winner": 2, "subject_seat": 1},
        {"status": "DONE", "winner": 0, "subject_seat": 1},
        {"status": "AGENT_ERROR", "winner": None, "subject_seat": 0},
    ]
    summary = _summarize(rows)
    assert summary["done"] == 3
    assert summary["faults"] == 1
    assert summary["wins"] == 1
    assert summary["draws"] == 1
    assert summary["losses"] == 1
    assert summary["wins_by_seat"] == {"0": 1, "1": 0}
