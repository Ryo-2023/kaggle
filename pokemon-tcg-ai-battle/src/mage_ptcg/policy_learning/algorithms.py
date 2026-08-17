"""Numerically guarded AWR, PPO, and V-trace primitives.

These functions are deliberately framework-local and do not create CABT
actions.  The runtime action boundary remains the legal candidate scorer.
"""
from __future__ import annotations

from typing import Any


class AlgorithmContractError(ValueError):
    """An RL tensor or hyperparameter violates a fail-closed contract."""


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - environment guard
        raise AlgorithmContractError("PyTorch is required for policy learning") from exc
    return torch


def awr_weights(advantages: Any, *, beta: float = 1.0, clip: float = 20.0) -> Any:
    """Return normalized, bounded advantage weights for offline AWR/AWAC.

    The caller supplies a detached advantage.  This avoids a policy gradient
    flowing through the critic and bounds a single noisy episode's influence.
    """
    torch = _torch()
    if beta <= 0 or clip <= 0:
        raise AlgorithmContractError("beta and clip must be positive")
    if not bool(torch.isfinite(advantages).all()):
        raise AlgorithmContractError("advantages must be finite")
    weights = torch.exp(torch.clamp(advantages.detach() / beta, max=clip))
    return weights / weights.mean().clamp_min(torch.finfo(weights.dtype).eps)


def ppo_clipped_loss(log_probability: Any, behavior_log_probability: Any, advantages: Any, *, clip_ratio: float = 0.2) -> Any:
    """Mean PPO clipped surrogate loss for recorded on-policy trajectories."""
    torch = _torch()
    if not 0 < clip_ratio < 1:
        raise AlgorithmContractError("clip_ratio must be in (0, 1)")
    if not all(bool(torch.isfinite(value).all()) for value in (log_probability, behavior_log_probability, advantages)):
        raise AlgorithmContractError("PPO inputs must be finite")
    ratio = torch.exp(log_probability - behavior_log_probability)
    unclipped = ratio * advantages
    clipped = torch.clamp(ratio, 1.0 - clip_ratio, 1.0 + clip_ratio) * advantages
    return -torch.minimum(unclipped, clipped).mean()


def generalized_advantage_estimate(rewards: Any, discounts: Any, values: Any, *, gae_lambda: float = .95) -> tuple[Any, Any]:
    """Return detached GAE advantages and value targets for one episode.

    Terminal transitions are represented by a zero discount.  This is the
    online PPO credit-assignment path; it intentionally does not reinterpret
    offline terminal labels as per-action advantages.
    """
    torch = _torch()
    if not 0.0 <= gae_lambda <= 1.0:
        raise AlgorithmContractError("GAE lambda must be in [0, 1]")
    if any(getattr(value, "ndim", None) != 1 for value in (rewards, discounts, values)):
        raise AlgorithmContractError("GAE tensors must be rank one")
    if rewards.shape != discounts.shape or rewards.shape != values.shape:
        raise AlgorithmContractError("GAE tensor shapes differ")
    if not all(bool(torch.isfinite(value).all()) for value in (rewards, discounts, values)):
        raise AlgorithmContractError("GAE inputs must be finite")
    if not bool(((discounts >= 0.0) & (discounts <= 1.0)).all()):
        raise AlgorithmContractError("GAE discounts must be in [0, 1]")
    advantages = torch.empty_like(values); accumulator = torch.zeros((), dtype=values.dtype, device=values.device)
    for index in range(values.shape[0] - 1, -1, -1):
        next_value = values[index + 1] if index + 1 < values.shape[0] else torch.zeros((), dtype=values.dtype, device=values.device)
        delta = rewards[index] + discounts[index] * next_value - values[index]
        accumulator = delta + discounts[index] * gae_lambda * accumulator
        advantages[index] = accumulator
    return advantages.detach(), (advantages + values).detach()


def vtrace_targets(
    rewards: Any,
    discounts: Any,
    values: Any,
    bootstrap_value: Any,
    target_log_probability: Any,
    behavior_log_probability: Any,
    *,
    rho_clip: float = 1.0,
    c_clip: float = 1.0,
) -> tuple[Any, Any]:
    """Compute V-trace value targets and policy advantages for one trajectory.

    Shapes are ``[T]``.  Actors may be stale; clipped importance ratios make
    that explicit rather than silently treating off-policy data as on-policy.
    """
    torch = _torch()
    if rho_clip <= 0 or c_clip <= 0:
        raise AlgorithmContractError("V-trace clipping values must be positive")
    tensors = (rewards, discounts, values, target_log_probability, behavior_log_probability)
    if any(getattr(value, "ndim", None) != 1 for value in tensors):
        raise AlgorithmContractError("V-trace trajectory tensors must be rank one")
    length = rewards.shape[0]
    if any(value.shape[0] != length for value in tensors):
        raise AlgorithmContractError("V-trace trajectory lengths differ")
    if not all(bool(torch.isfinite(value).all()) for value in (*tensors, bootstrap_value)):
        raise AlgorithmContractError("V-trace inputs must be finite")
    log_ratio = target_log_probability - behavior_log_probability
    rho = torch.exp(log_ratio).clamp(max=rho_clip)
    c = torch.exp(log_ratio).clamp(max=c_clip)
    next_values = torch.cat((values[1:], bootstrap_value.reshape(1)))
    deltas = rho * (rewards + discounts * next_values - values)
    accumulator = torch.zeros_like(bootstrap_value)
    targets = torch.empty_like(values)
    for index in range(length - 1, -1, -1):
        accumulator = deltas[index] + discounts[index] * c[index] * accumulator
        targets[index] = values[index] + accumulator
    next_targets = torch.cat((targets[1:], bootstrap_value.reshape(1)))
    advantages = rho * (rewards + discounts * next_targets - values)
    return targets.detach(), advantages.detach()
