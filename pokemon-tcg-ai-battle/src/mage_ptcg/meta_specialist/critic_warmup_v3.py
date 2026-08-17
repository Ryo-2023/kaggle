"""Monte-Carlo outcome critic warm-up with episode-balanced loss."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import torch
from torch.nn import functional as F

from mage_ptcg.meta_specialist.critic_v3 import OutcomeCriticV3, calibration_metrics_v3, episode_balanced_cross_entropy_v3


def warmup_critic_v3(
    critic: OutcomeCriticV3,
    episodes: Sequence[
        tuple[torch.Tensor, torch.Tensor]
        | tuple[torch.Tensor, torch.Tensor, Mapping[str, object] | None]
    ], *,
    epochs: int = 5, learning_rate: float = 2e-4,
) -> dict[str, object]:
    if not isinstance(critic, OutcomeCriticV3) or not episodes or epochs < 1:
        raise ValueError("critic/episodes/epochs are invalid")
    if any(
        len(episode) not in (2, 3)
        or episode[0].ndim != 2
        or episode[1].ndim != 1
        or episode[0].shape[0] != episode[1].shape[0]
        or (len(episode) == 3 and episode[2] is not None and not isinstance(episode[2], Mapping))
        for episode in episodes
    ):
        raise ValueError("each episode must contain aligned [T,D] features and [T] labels")

    def unpack(episode):
        features, labels = episode[:2]
        provenance = episode[2] if len(episode) == 3 else None
        return features, labels, provenance

    def metrics() -> dict[str, float]:
        with torch.no_grad():
            probabilities = torch.cat([
                critic(features, provenance=provenance).probabilities
                for features, _, provenance in map(unpack, episodes)
            ])
            labels = torch.cat([labels for _, labels, _ in map(unpack, episodes)]).to(torch.long)
        return calibration_metrics_v3(probabilities, labels)

    initial = metrics()
    optimizer = torch.optim.Adam(critic.parameters(), lr=learning_rate)
    for _ in range(epochs):
        critic.train()
        optimizer.zero_grad()
        logits = [critic(features, provenance=provenance).logits for features, _, provenance in map(unpack, episodes)]
        labels = [target.to(torch.long) for _, target, _ in map(unpack, episodes)]
        loss = episode_balanced_cross_entropy_v3(logits, labels)
        loss.backward()
        optimizer.step()
    final = metrics()
    return {"initial": initial, "final": final, "epochs": epochs}


__all__ = ["warmup_critic_v3"]
