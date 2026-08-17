"""Dueling categorical distributional Q head with legal-action masking."""
from __future__ import annotations

from typing import Any


class DistributionalQHead:  # wrapper keeps torch import optional at package import
    def __new__(cls, hidden_size: int, action_size: int, atoms: int = 51) -> Any:
        import torch
        class _Head(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__(); self.value = torch.nn.Linear(hidden_size, atoms); self.advantage = torch.nn.Sequential(torch.nn.Linear(hidden_size + action_size, hidden_size), torch.nn.ReLU(), torch.nn.Linear(hidden_size, atoms)); self.atoms = atoms
            def forward(self, state: Any, actions: Any, legal_mask: Any) -> Any:
                value = self.value(state).unsqueeze(1); expanded = state.unsqueeze(1).expand(-1, actions.shape[1], -1)
                advantage = self.advantage(torch.cat((expanded, actions), dim=-1)); masked = advantage.masked_fill(~legal_mask.unsqueeze(-1), 0.0)
                count = legal_mask.sum(dim=1, keepdim=True).clamp_min(1).unsqueeze(-1); logits = value + advantage - masked.sum(dim=1, keepdim=True) / count
                return logits.masked_fill(~legal_mask.unsqueeze(-1), float("-inf"))
        return _Head()


def expected_q(logits: Any, support: Any) -> Any:
    import torch
    probabilities = torch.softmax(logits, dim=-1)
    return (probabilities * support).sum(dim=-1)


def project_categorical(rewards: Any, discounts: Any, next_probabilities: Any, support: Any) -> Any:
    """C51 projection of an n-step target onto a fixed support."""
    import torch
    atoms = support.numel()
    # Keep support bounds on device.  Converting each scalar to ``float``
    # inserted two host synchronizations into every learner update.
    minimum, maximum = support[0], support[-1]
    delta = (maximum - minimum) / (atoms - 1)
    target = rewards.unsqueeze(1) + discounts.unsqueeze(1) * support.unsqueeze(0); target = target.clamp(minimum, maximum)
    position = (target - minimum) / delta; lower = position.floor().long(); upper = position.ceil().long()
    output = torch.zeros_like(next_probabilities)
    lower_mass = next_probabilities * (
        upper.float() - position + (lower == upper).float()
    )
    upper_mass = next_probabilities * (position - lower.float())
    # scatter_add accepts the complete [batch, atoms] index tensor.  Issuing
    # one call per atom launched 102 tiny CUDA kernels per learner update and
    # left the GPU idle between Python dispatches.
    output.scatter_add_(1, lower, lower_mass)
    output.scatter_add_(1, upper, upper_mass)
    return output
