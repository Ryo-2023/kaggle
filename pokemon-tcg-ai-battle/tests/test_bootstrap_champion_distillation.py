from __future__ import annotations

import torch
from pathlib import Path

from mage_ptcg.bootstrap_champion.distillation import (
    DistillationConfig,
    behavior_cloning_loss,
    distill_bootstrap_policy,
)


def test_behavior_cloning_masks_illegal_action_scores() -> None:
    q = torch.tensor([[0.0, 100.0, 1.0]], requires_grad=True)
    legal = torch.tensor([[True, False, True]])
    selected = torch.tensor([2])
    weight = torch.tensor([1.0])

    loss = behavior_cloning_loss(q, legal, selected, weight)

    assert float(loss.detach()) < 2.0
    loss.backward()
    assert float(q.grad[0, 1]) == 0.0


def test_behavior_cloning_weight_scales_gradient_contribution() -> None:
    q = torch.zeros((2, 2), requires_grad=True)
    legal = torch.ones((2, 2), dtype=torch.bool)
    selected = torch.tensor([0, 0])
    weights = torch.tensor([1.0, 0.25])

    behavior_cloning_loss(q, legal, selected, weights).backward()

    assert abs(float(q.grad[0, 0])) > abs(float(q.grad[1, 0]))


class _TinyQ(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.linear = torch.nn.Linear(1, 1)

    def forward(self, states, actions, legal_mask):
        del states, legal_mask
        return {"q": self.linear(actions).squeeze(-1)}


def test_distillation_pads_variable_legal_action_counts(tmp_path: Path) -> None:
    examples = [
        {"state": [0.0], "actions": [[0.0], [1.0]], "legal_mask": [True, True], "selected_action": 0, "behavior_weight": 1.0},
        {"state": [0.0], "actions": [[0.0], [1.0], [2.0]], "legal_mask": [True, True, True], "selected_action": 1, "behavior_weight": 1.0},
    ]

    result = distill_bootstrap_policy(
        model=_TinyQ(),
        train_examples=examples,
        validation_examples=(),
        config=DistillationConfig(max_epochs=1, batch_size=2),
        output=tmp_path,
    )

    assert Path(result.weights_path).is_file()
