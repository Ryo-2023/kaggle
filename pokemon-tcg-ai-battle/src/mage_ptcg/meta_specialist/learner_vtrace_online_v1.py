"""Consume-once online V-trace queue and numerically explicit targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch


@dataclass(frozen=True, slots=True)
class VTraceQueueItemV1:
    episode_id: str
    actor_version: int
    payload: Any


class ConsumeOnceVTraceQueueV1:
    def __init__(self, *, max_actor_lag: int = 1) -> None:
        if type(max_actor_lag) is not int or max_actor_lag < 0:
            raise ValueError("max_actor_lag must be a nonnegative int")
        self.max_actor_lag = max_actor_lag
        self._items: dict[str, VTraceQueueItemV1] = {}

    def publish(self, episode_id: str, *, version: int, payload: Any) -> None:
        if not episode_id or type(version) is not int or version < 0:
            raise ValueError("episode_id/version is invalid")
        if episode_id in self._items:
            raise KeyError(f"episode already published: {episode_id}")
        self._items[episode_id] = VTraceQueueItemV1(episode_id, version, payload)

    def consume(self, episode_id: str, *, learner_version: int) -> VTraceQueueItemV1:
        item = self._items.pop(episode_id)
        if learner_version - item.actor_version > self.max_actor_lag:
            raise RuntimeError("trajectory actor lag exceeds consume-once bound")
        return item

    def __len__(self) -> int:
        return len(self._items)


def vtrace_targets_v1(
    *, rewards: torch.Tensor, values: torch.Tensor, behavior_log_probs: torch.Tensor,
    target_log_probs: torch.Tensor, discounts: torch.Tensor, rho_bar: float = 2.0, c_bar: float = 1.0,
) -> torch.Tensor:
    if rewards.ndim != 1 or values.ndim != 1 or values.shape[0] != rewards.shape[0] + 1:
        raise ValueError("rewards/values must have shapes [T] and [T+1]")
    if any(t.shape != rewards.shape for t in (behavior_log_probs, target_log_probs, discounts)):
        raise ValueError("V-trace vectors must align")
    if rho_bar <= 0 or c_bar <= 0:
        raise ValueError("rho_bar/c_bar must be positive")
    rho = (target_log_probs - behavior_log_probs).exp()
    clipped_rho = rho.clamp(max=rho_bar)
    clipped_c = rho.clamp(max=c_bar)
    deltas = clipped_rho * (rewards + discounts * values[1:] - values[:-1])
    result = torch.zeros_like(values)
    result[-1] = values[-1]
    for index in range(rewards.shape[0] - 1, -1, -1):
        result[index] = values[index] + deltas[index] + discounts[index] * clipped_c[index] * (result[index + 1] - values[index + 1])
    return result[:-1]


__all__ = ["ConsumeOnceVTraceQueueV1", "VTraceQueueItemV1", "vtrace_targets_v1"]
