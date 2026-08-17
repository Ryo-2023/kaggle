from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.cg_p2_context_cem_v1 import (
    CemState,
    load_latest_checkpoint,
    rank_robust_results,
    rank_valid_results,
    sample_population,
    save_checkpoint,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig


def test_context_population_is_deterministic_and_keeps_center_first() -> None:
    center = P2ContextConfig(damaged_active_threat_attack_bonus=-6_000)
    first = sample_population(center, generation=1, population_size=6, seed=19)
    second = sample_population(center, generation=1, population_size=6, seed=19)
    assert first == second
    assert first[0] == center
    assert len(first) == 6
    for config in first:
        config.validate()


def test_context_population_rejects_mismatched_scales() -> None:
    center = P2ContextConfig.default()
    with pytest.raises(ValueError, match="scales"):
        sample_population(center, generation=0, population_size=2, scales={"near_lethal_attack_bonus": 1.0})


def test_positive_gate_filters_faults_unsafe_seats_and_negative_delta() -> None:
    config = P2ContextConfig(damaged_active_threat_attack_bonus=-6_000)
    rows = [
        {"candidate_id": "good", "config": config.as_dict(), "delta_objective": 0.02, "faults": 0, "candidate_seat_safe": True},
        {"candidate_id": "negative", "config": config.as_dict(), "delta_objective": -0.01, "faults": 0, "candidate_seat_safe": True},
        {"candidate_id": "unsafe", "config": config.as_dict(), "delta_objective": 0.10, "faults": 0, "candidate_seat_safe": False},
        {"candidate_id": "fault", "config": config.as_dict(), "delta_objective": 0.10, "faults": 1, "candidate_seat_safe": True},
    ]
    ranked = rank_valid_results(rows, elite_count=1, positive_delta_gate=True)
    assert [item["candidate_id"] for item in ranked] == ["good"]


def test_rank_requires_enough_positive_elites() -> None:
    config = P2ContextConfig.default()
    with pytest.raises(ValueError, match="not enough valid"):
        rank_valid_results(
            [{"config": config.as_dict(), "delta_objective": -0.01, "faults": 0, "candidate_seat_safe": True}],
            elite_count=1,
            positive_delta_gate=True,
        )


def test_robust_rank_requires_every_independent_block_to_be_positive_and_safe() -> None:
    config = P2ContextConfig(damaged_active_threat_attack_bonus=-6_000)
    rows = [
        {
            "candidate_id": "robust",
            "config": config.as_dict(),
            "faults": 0,
            "candidate_seat_safe": True,
            "independent_blocks": [
                {"delta_objective": 0.08, "faults": 0, "candidate_seat_safe": True},
                {"delta_objective": 0.02, "faults": 0, "candidate_seat_safe": True},
            ],
        },
        {
            "candidate_id": "reversal",
            "config": config.as_dict(),
            "faults": 0,
            "candidate_seat_safe": True,
            "independent_blocks": [
                {"delta_objective": 0.20, "faults": 0, "candidate_seat_safe": True},
                {"delta_objective": -0.01, "faults": 0, "candidate_seat_safe": True},
            ],
        },
        {
            "candidate_id": "seat-unsafe",
            "config": config.as_dict(),
            "faults": 0,
            "candidate_seat_safe": True,
            "independent_blocks": [
                {"delta_objective": 0.20, "faults": 0, "candidate_seat_safe": True},
                {"delta_objective": 0.10, "faults": 0, "candidate_seat_safe": False},
            ],
        },
    ]

    ranked = rank_robust_results(rows, elite_count=1, min_independent_blocks=2)

    assert [item["candidate_id"] for item in ranked] == ["robust"]
    assert ranked[0]["robust_delta_objective"] == 0.02
    assert ranked[0]["independent_block_count"] == 2


def test_robust_rank_rejects_missing_independent_blocks() -> None:
    config = P2ContextConfig.default()
    with pytest.raises(ValueError, match="not enough valid"):
        rank_robust_results(
            [{"config": config.as_dict(), "delta_objective": 0.5, "faults": 0, "candidate_seat_safe": True}],
            elite_count=1,
            min_independent_blocks=2,
        )


def test_update_distribution_uses_elite_mean_and_nonzero_scale_floor() -> None:
    center = P2ContextConfig.default()
    left = P2ContextConfig(near_lethal_attack_bonus=4_000)
    right = P2ContextConfig(near_lethal_attack_bonus=8_000)
    updated, scales = update_distribution(
        center,
        [{"config": left.as_dict()}, {"config": right.as_dict()}],
    )
    assert updated.near_lethal_attack_bonus == 6_000
    assert scales["near_lethal_attack_bonus"] == 2_000.0
    assert all(value >= 1.0 for value in scales.values())


def test_checkpoint_is_no_clobber_and_round_trips(tmp_path) -> None:
    center = P2ContextConfig.default()
    state = CemState(
        generation=0,
        center=center,
        scales={name: 15_000.0 for name in center.as_dict()},
        next_candidate_index=6,
        evaluated=[{"generation": 0, "update_status": "CENTER_HELD_NOT_ENOUGH_POSITIVE_ELITES"}],
        campaign_identity={"split_sha256": "a" * 64},
    )
    path = save_checkpoint(tmp_path, state)
    assert load_latest_checkpoint(tmp_path).next_candidate_index == 6
    with pytest.raises(FileExistsError):
        save_checkpoint(tmp_path, state)
    assert '"schema_version": "cg-p2-context-cem-state-v1"' in path.read_text(encoding="utf-8")
