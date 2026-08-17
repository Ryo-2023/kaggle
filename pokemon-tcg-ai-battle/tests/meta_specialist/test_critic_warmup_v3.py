from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.critic_v3 import OutcomeCriticV3
from mage_ptcg.meta_specialist.critic_conditioning_v3 import OutcomeCriticConditionedV3
from mage_ptcg.meta_specialist.critic_warmup_v3 import warmup_critic_v3


def test_critic_warmup_is_episode_balanced_and_reports_calibration() -> None:
    critic = OutcomeCriticV3(hidden_dim=4, seed=2)
    episodes = ((torch.ones(2, 4), torch.tensor([2, 2])), (torch.zeros(1, 4), torch.tensor([0])))
    result = warmup_critic_v3(critic, episodes, epochs=1, learning_rate=1e-2)
    assert result["initial"]["uniform_brier"] >= 0
    assert result["final"]["brier"] >= 0


def test_conditioned_critic_warmup_accepts_stable_provenance() -> None:
    critic = OutcomeCriticConditionedV3(hidden_dim=4, mode="stable", seed=3)
    episodes = (
        (torch.ones(2, 4), torch.tensor([2, 2]), {"opponent_family": "a"}),
        (torch.zeros(1, 4), torch.tensor([0]), {"opponent_family": "b"}),
    )
    result = warmup_critic_v3(critic, episodes, epochs=1, learning_rate=1e-2)
    assert result["final"]["value_min"] >= -1.0
    assert result["final"]["value_max"] <= 1.0
