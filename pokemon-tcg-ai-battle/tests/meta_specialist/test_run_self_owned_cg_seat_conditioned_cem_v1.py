from __future__ import annotations

from scripts.run_self_owned_cg_seat_conditioned_cem_v1 import passes_research_gate


def test_research_gate_requires_positive_repeats_and_seat_safety() -> None:
    base = {
        "faults": 0,
        "repeat_deltas": [0.10, 0.02],
        "repeat_seat_gaps": [0.01, 0.02],
        "repeat_opponent_seat_gaps": [{"a": 0.01}, {"a": 0.02}],
    }
    assert passes_research_gate(base) is True
    assert passes_research_gate({**base, "repeat_deltas": [0.10, -0.01]}) is False
    assert passes_research_gate({**base, "repeat_seat_gaps": [0.06, 0.02]}) is False
    assert passes_research_gate({**base, "repeat_opponent_seat_gaps": [{"a": 0.20}, {"a": 0.02}]}) is False


def test_research_gate_rejects_faults_and_missing_diagnostics() -> None:
    assert passes_research_gate({
        "faults": 1,
        "repeat_deltas": [0.10, 0.02],
        "repeat_seat_gaps": [0.01, 0.02],
        "repeat_opponent_seat_gaps": [{"a": 0.01}, {"a": 0.02}],
    }) is False
    assert passes_research_gate({"faults": 0}) is False
