from __future__ import annotations

from mage_ptcg.meta_specialist.opponent_schedule_v2 import schedule_probabilities_v2, sample_opponent_v2


def test_schedule_has_floor_and_is_seeded() -> None:
    probabilities = schedule_probabilities_v2({"meta": 0.8, "hard": 0.2}, {"meta": 0.0, "hard": 1.0}, floor=0.05)
    assert abs(sum(probabilities.values()) - 1.0) < 1e-8
    assert all(value >= 0.05 for value in probabilities.values())
    assert sample_opponent_v2(probabilities, seed=4) == sample_opponent_v2(probabilities, seed=4)
