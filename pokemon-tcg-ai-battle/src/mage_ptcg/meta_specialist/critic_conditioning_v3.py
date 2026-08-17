"""Stable-opponent conditioning ablation for the outcome critic."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

import torch
from torch import nn

from mage_ptcg.meta_specialist.critic_v3 import OutcomeCriticV3


class OutcomeCriticConditionedV3(OutcomeCriticV3):
    """C0 none, C1 stable category, or C2 game-seed negative control."""

    def __init__(self, *, hidden_dim: int = 256, mode: str = "none", seed: int = 0, buckets: int = 256) -> None:
        if mode not in {"none", "stable", "game-seed"} or buckets < 1:
            raise ValueError("mode/buckets are invalid")
        super().__init__(hidden_dim=hidden_dim, seed=seed)
        self.mode = mode
        self.buckets = buckets
        self.conditioning = nn.Embedding(buckets, hidden_dim)
        nn.init.zeros_(self.conditioning.weight)

    def _bucket(self, provenance: Mapping[str, object] | None) -> int:
        if self.mode == "none":
            return 0
        provenance = provenance or {}
        if self.mode == "game-seed":
            return int(provenance.get("game_seed", 0)) % self.buckets
        payload = "|".join(str(provenance.get(name, "unknown")) for name in ("opponent_family", "deck_fingerprint", "policy_family"))
        return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:4], "big") % self.buckets

    def forward(self, features: torch.Tensor, *, provenance: Mapping[str, object] | None = None):
        if self.mode == "none":
            return super().forward(features, provenance=provenance)
        condition = self.conditioning.weight[self._bucket(provenance)].to(features)
        return super().forward(features + condition, provenance=provenance)


__all__ = ["OutcomeCriticConditionedV3"]
