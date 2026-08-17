"""Episode-balanced policy/value target helpers for v3 trajectories."""

from __future__ import annotations

from collections.abc import Sequence

import torch
from torch.nn import functional as F


def outcome_label_v3(outcome: str) -> int:
    labels = {"loss": 0, "draw": 1, "win": 2}
    if outcome not in labels:
        raise ValueError("outcome must be loss, draw, or win")
    return labels[outcome]


def episode_balanced_policy_loss_v3(
    *episodes: tuple[torch.Tensor, torch.Tensor, float],
) -> torch.Tensor:
    """Mean weighted episode loss, never transition-count weighted."""
    if not episodes:
        raise ValueError("at least one episode is required")
    episode_losses: list[torch.Tensor] = []
    for logits, targets, weight in episodes:
        if logits.ndim != 1 or targets.numel() != 1 or not 0 <= int(targets.reshape(-1)[0].item()) < logits.shape[0]:
            raise ValueError("episode logits/target shape is invalid")
        if weight <= 0:
            raise ValueError("episode weight must be positive")
        episode_losses.append(F.cross_entropy(logits.view(1, -1), targets.reshape(1).long()) * weight)
    return torch.stack(episode_losses).mean()


__all__ = ["episode_balanced_policy_loss_v3", "outcome_label_v3"]
