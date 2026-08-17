from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.critic_conditioning_v3 import OutcomeCriticConditionedV3


def test_stable_conditioning_ignores_game_seed_and_c2_is_negative_control() -> None:
    features = torch.ones(2, 4)
    c1 = OutcomeCriticConditionedV3(hidden_dim=4, mode="stable", seed=1)
    first = c1(features, provenance={"game_seed": 1, "opponent_family": "x", "deck_fingerprint": "d", "policy_family": "p"})
    second = c1(features, provenance={"game_seed": 999, "opponent_family": "x", "deck_fingerprint": "d", "policy_family": "p"})
    assert torch.allclose(first.logits, second.logits)
    c2 = OutcomeCriticConditionedV3(hidden_dim=4, mode="game-seed", seed=1)
    with torch.no_grad():
        c2.conditioning.weight[1, 0] = 0.1
        c2.conditioning.weight[2, 0] = -0.1
        c2.outcome_head.weight[0, 0] = 1.0
    a = c2(features, provenance={"game_seed": 1})
    b = c2(features, provenance={"game_seed": 2})
    assert not torch.allclose(a.logits, b.logits)
