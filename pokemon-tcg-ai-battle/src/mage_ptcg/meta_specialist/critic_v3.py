"""Outcome-distribution critic and episode-balanced calibration utilities."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
import math

import torch
from torch import nn
from torch.nn import functional as F


CRITIC_V3_SCHEMA = "meta-specialist-outcome-critic-v3"


class CriticV3Error(ValueError):
    """Raised when critic inputs or calibration data are invalid."""


@dataclass(frozen=True, slots=True)
class CriticOutputV3:
    logits: torch.Tensor
    probabilities: torch.Tensor
    value: torch.Tensor


def _seeded_module(seed: int, factory):
    if type(seed) is not int:
        raise CriticV3Error("seed must be an int")
    state = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        return factory()
    finally:
        torch.random.set_rng_state(state)


class OutcomeCriticV3(nn.Module):
    """Predict ``(loss, draw, win)`` without game-seed conditioning."""

    def __init__(self, *, hidden_dim: int = 256, seed: int = 0) -> None:
        if type(hidden_dim) is not int or hidden_dim < 1:
            raise CriticV3Error("hidden_dim must be a positive int")

        def build() -> None:
            super(OutcomeCriticV3, self).__init__()
            self.hidden_dim = hidden_dim
            self.backbone = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim), nn.LayerNorm(hidden_dim), nn.GELU(),
                nn.Linear(hidden_dim, hidden_dim), nn.GELU(),
            )
            self.outcome_head = nn.Linear(hidden_dim, 3)
            nn.init.zeros_(self.outcome_head.weight)
            nn.init.zeros_(self.outcome_head.bias)

        _seeded_module(seed, build)

    def forward(self, features: torch.Tensor, *, provenance: Mapping[str, object] | None = None) -> CriticOutputV3:
        if not isinstance(features, torch.Tensor) or features.shape[-1] != self.hidden_dim:
            raise CriticV3Error(f"features must have final dimension {self.hidden_dim}")
        # ``provenance`` is intentionally accepted only as metadata.  In
        # particular, game_seed/opponent_instance_id are never converted into
        # an embedding or concatenated to the feature tensor.
        if provenance is not None and not isinstance(provenance, Mapping):
            raise CriticV3Error("provenance must be a mapping when supplied")
        logits = self.outcome_head(self.backbone(features))
        probabilities = torch.softmax(logits, dim=-1)
        value = probabilities[..., 2] - probabilities[..., 0]
        return CriticOutputV3(logits=logits, probabilities=probabilities, value=value.clamp(-1.0, 1.0))


def episode_balanced_cross_entropy_v3(
    logits_by_episode: Sequence[torch.Tensor], labels_by_episode: Sequence[torch.Tensor],
) -> torch.Tensor:
    """Average per-episode mean CE, avoiding transition-count weighting."""
    if len(logits_by_episode) != len(labels_by_episode) or not logits_by_episode:
        raise CriticV3Error("episode logits and labels must be nonempty and aligned")
    losses: list[torch.Tensor] = []
    for logits, labels in zip(logits_by_episode, labels_by_episode):
        if not isinstance(logits, torch.Tensor) or logits.ndim != 2 or logits.shape[-1] != 3:
            raise CriticV3Error("each episode logits tensor must have shape [T, 3]")
        if not isinstance(labels, torch.Tensor) or labels.ndim != 1 or labels.shape[0] != logits.shape[0]:
            raise CriticV3Error("each episode labels tensor must align with logits")
        if labels.dtype not in (torch.int64, torch.long) or torch.any((labels < 0) | (labels > 2)):
            raise CriticV3Error("outcome labels must be integer values in 0..2")
        losses.append(F.cross_entropy(logits, labels))
    return torch.stack(losses).mean()


def _validate_calibration(probabilities: torch.Tensor, labels: torch.Tensor) -> None:
    if probabilities.ndim != 2 or probabilities.shape[-1] != 3:
        raise CriticV3Error("probabilities must have shape [N, 3]")
    if labels.ndim != 1 or labels.shape[0] != probabilities.shape[0] or labels.dtype not in (torch.int64, torch.long):
        raise CriticV3Error("labels must be a matching integer vector")
    if torch.any((labels < 0) | (labels > 2)):
        raise CriticV3Error("labels must be in 0..2")
    if not torch.all(torch.isfinite(probabilities)) or not torch.allclose(probabilities.sum(-1), torch.ones(probabilities.shape[0]), atol=1e-5):
        raise CriticV3Error("probabilities must be finite and sum to one")


def calibration_metrics_v3(
    probabilities: torch.Tensor, labels: torch.Tensor, *, bins: int = 10,
) -> dict[str, float]:
    """Return CE, Brier, ECE, bounded value range and uniform baselines."""
    _validate_calibration(probabilities, labels)
    if type(bins) is not int or bins < 1:
        raise CriticV3Error("bins must be a positive int")
    n = probabilities.shape[0]
    safe = probabilities.clamp_min(1e-8)
    cross_entropy = float((-safe[torch.arange(n), labels].log()).mean().item())
    one_hot = F.one_hot(labels, num_classes=3).to(probabilities.dtype)
    brier = float(((probabilities - one_hot).square().sum(-1)).mean().item())
    uniform = torch.full_like(probabilities, 1 / 3)
    uniform_brier = float(((uniform - one_hot).square().sum(-1)).mean().item())
    confidence, prediction = probabilities.max(-1)
    ece = torch.zeros((), dtype=probabilities.dtype)
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (confidence > lower) & (confidence <= upper if index else confidence <= upper)
        if torch.any(mask):
            ece = ece + mask.float().mean() * (confidence[mask].mean() - (prediction[mask] == labels[mask]).float().mean()).abs()
    value = probabilities[:, 2] - probabilities[:, 0]
    target_value = one_hot[:, 2] - one_hot[:, 0]
    centered = value - value.mean()
    target_centered = target_value - target_value.mean()
    denominator = float((centered.square().sum() * target_centered.square().sum()).sqrt().item())
    correlation = 0.0 if denominator == 0 else float((centered * target_centered).sum().item() / denominator)
    return {
        "cross_entropy": cross_entropy, "brier": brier, "ece": float(ece.item()),
        "uniform_cross_entropy": math.log(3.0), "uniform_brier": uniform_brier,
        "value_min": float(value.min().item()), "value_max": float(value.max().item()),
        "value_outcome_correlation": correlation,
    }


__all__ = [
    "CRITIC_V3_SCHEMA", "CriticOutputV3", "CriticV3Error", "OutcomeCriticV3",
    "calibration_metrics_v3", "episode_balanced_cross_entropy_v3",
]
