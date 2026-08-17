from __future__ import annotations

from mage_ptcg.meta_specialist.cg_p1_margin_gated_renderer_v1 import MarginGatedConfig
from mage_ptcg.meta_specialist.cg_margin_gated_cem_v1 import (
    rank_valid_results,
    sample_population,
    update_distribution,
)


def test_sample_population_is_deterministic_and_keeps_center() -> None:
    center = MarginGatedConfig.default()
    first = sample_population(center, generation=1, population_size=4, seed=17)
    second = sample_population(center, generation=1, population_size=4, seed=17)
    assert first == second
    assert first[0] == center
    assert all(item.config_sha256() for item in first)


def test_rank_filters_faults_and_sorts_objective() -> None:
    center = MarginGatedConfig.default()
    rows = [
        {"config": center.as_dict(), "objective": 0.1, "faults": 0},
        {"config": {"lethal_bonus": 1000}, "objective": 0.2, "faults": 0},
        {"config": {"lethal_bonus": 2000}, "objective": 0.9, "faults": 1},
    ]
    ranked = rank_valid_results(rows, elite_count=2)
    assert [round(float(item["objective"]), 3) for item in ranked] == [0.2, 0.1]


def test_update_distribution_returns_bounded_center_and_scales() -> None:
    center = MarginGatedConfig.default()
    updated, scales = update_distribution(
        center,
        [
            {"config": {"lethal_bonus": 1000, "score_margin": 5000}},
            {"config": {"lethal_bonus": 3000, "score_margin": 7000}},
        ],
    )
    assert updated.lethal_bonus == 2000
    assert updated.score_margin == 6000
    assert set(scales) == set(center.as_dict())
    updated.validate()
