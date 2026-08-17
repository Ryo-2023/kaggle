"""Replay-only AWR/CRR weighting utilities."""

from __future__ import annotations

import torch


def awr_weights_v1(advantages: torch.Tensor, *, temperature: float = 1.0, max_weight: float = 20.0) -> torch.Tensor:
    if temperature <= 0 or max_weight <= 0 or not torch.all(torch.isfinite(advantages)):
        raise ValueError("AWR temperature/max_weight or advantages are invalid")
    return (advantages / temperature).clamp(max=torch.log(torch.tensor(max_weight))).exp().clamp(max=max_weight)


def crr_weights_v1(advantages: torch.Tensor) -> torch.Tensor:
    if not torch.all(torch.isfinite(advantages)):
        raise ValueError("advantages must be finite")
    return (advantages > 0).to(advantages.dtype)


__all__ = ["awr_weights_v1", "crr_weights_v1"]
