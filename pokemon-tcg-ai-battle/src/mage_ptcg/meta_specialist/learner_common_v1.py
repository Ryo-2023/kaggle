"""Shared diagnostics and numerically explicit learner primitives."""

from __future__ import annotations

from collections.abc import Sequence
import math

import torch
from torch.nn import functional as F


class LearnerCommonError(ValueError):
    pass


def _masked_log_probs(logits: torch.Tensor, legal_mask: torch.Tensor) -> torch.Tensor:
    if logits.shape != legal_mask.shape or logits.ndim < 1 or legal_mask.dtype is not torch.bool:
        raise LearnerCommonError("logits and legal_mask must have the same shape and boolean mask")
    if torch.any(legal_mask.sum(-1) < 1):
        raise LearnerCommonError("every decision must have at least one legal action")
    return torch.log_softmax(logits.masked_fill(~legal_mask, -torch.inf), dim=-1)


def normalized_entropy_v1(logits: torch.Tensor, legal_mask: torch.Tensor) -> torch.Tensor:
    log_probs = _masked_log_probs(logits, legal_mask)
    probs = log_probs.exp()
    entropy = -(probs * log_probs.masked_fill(~legal_mask, 0.0)).sum(-1)
    count = legal_mask.sum(-1).to(logits.dtype)
    denominator = torch.where(count > 1, count.log(), torch.ones_like(count))
    return torch.where(count > 1, entropy / denominator, torch.zeros_like(entropy))


def exact_policy_drift_v1(old_logits: torch.Tensor, new_logits: torch.Tensor, legal_mask: torch.Tensor) -> dict[str, float]:
    old_log = _masked_log_probs(old_logits, legal_mask)
    new_log = _masked_log_probs(new_logits, legal_mask)
    old = old_log.exp()
    new = new_log.exp()
    forward = (old * (old_log - new_log).masked_fill(~legal_mask, 0.0)).sum(-1).mean()
    reverse = (new * (new_log - old_log).masked_fill(~legal_mask, 0.0)).sum(-1).mean()
    tv = 0.5 * (old - new).abs().sum(-1).mean()
    old_choice = old.masked_fill(~legal_mask, -1).argmax(-1)
    new_choice = new.masked_fill(~legal_mask, -1).argmax(-1)
    old_top = old.topk(2, dim=-1).values if old.shape[-1] >= 2 else old
    new_top = new.topk(2, dim=-1).values if new.shape[-1] >= 2 else new
    return {
        "forward_kl": float(forward.item()), "reverse_kl": float(reverse.item()),
        "total_variation": float(tv.item()), "argmax_flip_rate": float((old_choice != new_choice).float().mean().item()),
        "old_top1_margin": float((old_top[:, 0] - old_top[:, 1]).mean().item()) if old.shape[-1] >= 2 else 0.0,
        "new_top1_margin": float((new_top[:, 0] - new_top[:, 1]).mean().item()) if new.shape[-1] >= 2 else 0.0,
        "old_entropy": float(normalized_entropy_v1(old_logits, legal_mask).mean().item()),
        "new_entropy": float(normalized_entropy_v1(new_logits, legal_mask).mean().item()),
    }


def vtrace_effective_kernel_v1(c_values: torch.Tensor, *, gamma: float = 1.0, threshold: float = 0.01) -> dict[str, object]:
    if c_values.ndim != 1 or not torch.all(torch.isfinite(c_values)) or torch.any(c_values < 0):
        raise LearnerCommonError("c_values must be a finite nonnegative vector")
    if not 0 <= gamma <= 1 or threshold <= 0:
        raise LearnerCommonError("gamma must be in [0,1] and threshold positive")
    running = 1.0
    weights: list[float] = []
    for depth, value in enumerate(c_values.tolist(), start=1):
        running *= float(gamma) * float(value)
        weights.append(running)
    eligible = [depth for depth, value in enumerate(weights, start=1) if value >= threshold]
    return {
        "weights": weights,
        "median_w_d5": weights[4] if len(weights) >= 5 else (weights[-1] if weights else 0.0),
        "median_w_d10": weights[9] if len(weights) >= 10 else (weights[-1] if weights else 0.0),
        "median_w_d20": weights[19] if len(weights) >= 20 else (weights[-1] if weights else 0.0),
        "max_depth_weight_ge_0_01": max(eligible, default=0),
        "effective_horizon_90pct": float(sum(value >= threshold for value in weights)),
    }


def advantage_diagnostics_v1(advantages: torch.Tensor, outcomes: torch.Tensor | None = None) -> dict[str, float]:
    if advantages.ndim != 1 or not torch.all(torch.isfinite(advantages)):
        raise LearnerCommonError("advantages must be a finite vector")
    raw_mean = advantages.mean()
    raw_std = advantages.std(unbiased=False)
    median = advantages.median()
    mad = (advantages - median).abs().median()
    if outcomes is None:
        correlation = 0.0
    else:
        if outcomes.shape != advantages.shape:
            raise LearnerCommonError("outcomes must align with advantages")
        a, b = advantages - raw_mean, outcomes.to(advantages.dtype) - outcomes.to(advantages.dtype).mean()
        denominator = (a.square().sum() * b.square().sum()).sqrt()
        correlation = 0.0 if denominator.item() == 0 else float((a * b).sum().item() / denominator.item())
    return {
        "raw_mean": float(raw_mean.item()), "raw_std": float(raw_std.item()),
        "normalized_mean": 0.0 if raw_std.item() == 0 else float(((advantages - raw_mean) / raw_std).mean().item()),
        "normalized_std": 0.0 if raw_std.item() == 0 else 1.0,
        "median": float(median.item()), "mad": float(mad.item()),
        "positive_fraction": float((advantages > 0).float().mean().item()),
        "outcome_correlation": correlation,
    }


def episode_balanced_mean_v1(values_by_episode: Sequence[torch.Tensor]) -> torch.Tensor:
    if not values_by_episode or any(value.ndim == 0 or value.numel() == 0 for value in values_by_episode):
        raise LearnerCommonError("values_by_episode must contain nonempty tensors")
    return torch.stack([value.mean() for value in values_by_episode]).mean()


__all__ = [
    "LearnerCommonError", "advantage_diagnostics_v1", "episode_balanced_mean_v1",
    "exact_policy_drift_v1", "normalized_entropy_v1", "vtrace_effective_kernel_v1",
]
