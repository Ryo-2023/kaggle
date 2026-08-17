from __future__ import annotations

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig
from scripts.run_cg_p2_context_cem_v1 import (
    ContextCemCampaignConfig,
    combine_independent_results,
    update_after_generation,
)


def test_campaign_config_requires_elite_count_for_robust_targets() -> None:
    config = ContextCemCampaignConfig(
        population_size=4,
        elite_count=2,
        independent_blocks=2,
        independent_candidate_count=1,
    )
    try:
        config.validate()
    except ValueError as exc:
        assert "below elite_count" in str(exc)
    else:  # pragma: no cover - the assertion above is the expected branch
        raise AssertionError("robust target count must cover the elite count")


def test_generation_update_holds_center_when_positive_elite_gate_fails() -> None:
    center = P2ContextConfig.default()
    scales = {name: 15_000.0 for name in center.as_dict()}
    next_center, next_scales, elites, status = update_after_generation(
        center,
        scales,
        [{"config": center.as_dict(), "delta_objective": -0.01, "faults": 0, "candidate_seat_safe": True}],
        elite_count=1,
    )
    assert next_center == center
    assert next_scales == scales
    assert elites == ()
    assert status == "CENTER_HELD_NOT_ENOUGH_POSITIVE_ELITES"


def test_generation_update_moves_to_positive_elite_mean() -> None:
    center = P2ContextConfig.default()
    scales = {name: 15_000.0 for name in center.as_dict()}
    left = P2ContextConfig(near_lethal_attack_bonus=4_000)
    right = P2ContextConfig(near_lethal_attack_bonus=8_000)
    rows = [
        {"candidate_id": "left", "config": left.as_dict(), "delta_objective": 0.01, "faults": 0, "candidate_seat_safe": True},
        {"candidate_id": "right", "config": right.as_dict(), "delta_objective": 0.02, "faults": 0, "candidate_seat_safe": True},
    ]
    next_center, next_scales, elites, status = update_after_generation(
        center,
        scales,
        rows,
        elite_count=2,
    )
    assert next_center.near_lethal_attack_bonus == 6_000
    assert next_scales["near_lethal_attack_bonus"] == 2_000.0
    assert len(elites) == 2
    assert status == "UPDATED_FROM_POSITIVE_ELITES"


def test_generation_update_uses_worst_independent_block_for_robust_gate() -> None:
    center = P2ContextConfig.default()
    scales = {name: 15_000.0 for name in center.as_dict()}
    candidate = P2ContextConfig(near_lethal_attack_bonus=6_000)
    rows = [
        {
            "candidate_id": "candidate",
            "config": candidate.as_dict(),
            "faults": 0,
            "candidate_seat_safe": True,
            "independent_blocks": [
                {"delta_objective": 0.10, "faults": 0, "candidate_seat_safe": True},
                {"delta_objective": 0.01, "faults": 0, "candidate_seat_safe": True},
            ],
        }
    ]

    next_center, _next_scales, elites, status = update_after_generation(
        center,
        scales,
        rows,
        elite_count=1,
        robust_gate=True,
    )

    assert next_center.near_lethal_attack_bonus == 6_000
    assert len(elites) == 1
    assert elites[0]["robust_delta_objective"] == 0.01
    assert status == "UPDATED_FROM_ROBUST_POSITIVE_ELITES"


def test_combine_independent_results_binds_blocks_by_config_hash() -> None:
    config = P2ContextConfig(near_lethal_attack_bonus=6_000)
    base = {
        "candidate_id": "screen-candidate",
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "delta_objective": 0.20,
        "faults": 0,
        "candidate_seat_safe": True,
    }
    blocks = [
        {"results": [{"config": config.as_dict(), "config_sha256": config.config_sha256(), "delta_objective": 0.05, "faults": 0, "candidate_seat_safe": True}]},
        {"results": [{"config": config.as_dict(), "config_sha256": config.config_sha256(), "delta_objective": 0.02, "faults": 0, "candidate_seat_safe": True}]},
    ]

    combined = combine_independent_results([base], blocks)

    assert len(combined) == 1
    assert combined[0]["config"] == config.as_dict()
    assert [block["delta_objective"] for block in combined[0]["independent_blocks"]] == [0.05, 0.02]


def test_combine_ignores_a_block_with_mismatched_declared_hash() -> None:
    config = P2ContextConfig(near_lethal_attack_bonus=6_000)
    other = P2ContextConfig(near_lethal_attack_bonus=8_000)
    base = {"config": config.as_dict(), "config_sha256": "f" * 64}
    blocks = [
        {
            "results": [
                {
                    "config": other.as_dict(),
                    "config_sha256": config.config_sha256(),
                    "delta_objective": 0.05,
                    "faults": 0,
                    "candidate_seat_safe": True,
                }
            ]
        }
    ]

    combined = combine_independent_results([base], blocks)

    assert combined[0]["independent_blocks"] == []
