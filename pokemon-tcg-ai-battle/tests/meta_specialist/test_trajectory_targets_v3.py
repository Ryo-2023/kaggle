from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.trajectory_targets_v3 import (
    outcome_label_v3,
    episode_balanced_policy_loss_v3,
)


def test_outcome_labels_and_episode_balanced_policy_loss() -> None:
    assert outcome_label_v3("win") == 2
    assert outcome_label_v3("draw") == 1
    assert outcome_label_v3("loss") == 0
    loss = episode_balanced_policy_loss_v3(
        (torch.tensor([1.0, 2.0]), torch.tensor([1]), 1.0),
        (torch.tensor([0.0, 3.0]), torch.tensor([0]), 0.5),
    )
    assert loss.item() >= 0
