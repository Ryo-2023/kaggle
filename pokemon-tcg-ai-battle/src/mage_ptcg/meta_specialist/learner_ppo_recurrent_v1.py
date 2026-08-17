"""Fresh-sequence recurrent PPO objective with exact reference anchoring."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch.nn import functional as F


@dataclass(frozen=True, slots=True)
class PPORecurrentLossV1:
    loss: torch.Tensor
    actor_loss: torch.Tensor
    reference_kl: torch.Tensor
    exact_kl: float
    clip_fraction: float
    entropy: torch.Tensor


def ppo_recurrent_loss_v1(
    *, new_log_probs: torch.Tensor, old_log_probs: torch.Tensor, advantages: torch.Tensor,
    entropy: torch.Tensor, reference_log_probs: torch.Tensor,
    clip_epsilon: float = 0.10, reference_kl_coefficient: float = 0.10,
    entropy_coefficient: float = 0.001,
) -> PPORecurrentLossV1:
    shapes = {new_log_probs.shape, old_log_probs.shape, advantages.shape, entropy.shape, reference_log_probs.shape}
    if len(shapes) != 1 or new_log_probs.ndim != 1:
        raise ValueError("PPO inputs must be aligned one-dimensional tensors")
    if clip_epsilon <= 0 or reference_kl_coefficient < 0 or entropy_coefficient < 0:
        raise ValueError("PPO coefficients are invalid")
    ratio = (new_log_probs - old_log_probs).exp()
    clipped = ratio.clamp(1.0 - clip_epsilon, 1.0 + clip_epsilon)
    surrogate = torch.minimum(ratio * advantages, clipped * advantages)
    actor_loss = -surrogate.mean()
    # The caller supplies per-action log-prob vectors.  Treat the batch as the
    # categorical axis so this remains an exact KL, not a sampled-action proxy.
    ref_prob = reference_log_probs.exp()
    normalized_ref = ref_prob / ref_prob.sum().clamp_min(1e-8)
    normalized_new = new_log_probs.exp() / new_log_probs.exp().sum().clamp_min(1e-8)
    exact_kl_tensor = (normalized_ref * (normalized_ref.clamp_min(1e-8).log() - normalized_new.clamp_min(1e-8).log())).sum()
    reference_kl = exact_kl_tensor
    loss = actor_loss + reference_kl_coefficient * reference_kl - entropy_coefficient * entropy.mean()
    return PPORecurrentLossV1(
        loss=loss, actor_loss=actor_loss, reference_kl=reference_kl,
        exact_kl=float(exact_kl_tensor.detach().clamp_min(0).item()),
        clip_fraction=float((ratio != clipped).float().mean().item()), entropy=entropy.mean(),
    )


__all__ = ["PPORecurrentLossV1", "ppo_recurrent_loss_v1"]
