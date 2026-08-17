from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.cg_p1_action_conditioned_renderer_v1 import ActionConditionedConfig
from mage_ptcg.meta_specialist.cg_action_conditioned_cem_v1 import (
    rank_valid_results,
    sample_population,
    update_distribution,
)


def test_sample_population_is_deterministic_and_keeps_center() -> None:
    center = ActionConditionedConfig.default()
    first = sample_population(center, generation=0, population_size=5, seed=91)
    second = sample_population(center, generation=0, population_size=5, seed=91)
    assert first == second
    assert first[0] == center
    assert all(config.config_sha256() for config in first)


def test_rank_and_update_use_fault_free_elites() -> None:
    center = ActionConditionedConfig.default()
    positive = ActionConditionedConfig(attack_early_bonus=2000)
    negative = ActionConditionedConfig(attack_early_bonus=-2000)
    results = [
        {"config": positive, "objective": 0.12, "faults": 0, "valid": True},
        {"config": negative, "objective": 0.30, "faults": 1, "valid": True},
        {"config": center, "objective": 0.05, "faults": 0, "valid": True},
    ]
    elites = rank_valid_results(results, elite_count=2)
    assert [item["config"] for item in elites] == [positive, center]
    next_center, scales = update_distribution(center, elites)
    assert next_center.attack_early_bonus == 1000
    assert scales["attack_early_bonus"] > 0


def test_rank_requires_enough_valid_candidates() -> None:
    with pytest.raises(ValueError, match="not enough valid candidates"):
        rank_valid_results(
            [{"config": ActionConditionedConfig.default(), "objective": 0.0, "faults": 1}],
            elite_count=1,
        )
