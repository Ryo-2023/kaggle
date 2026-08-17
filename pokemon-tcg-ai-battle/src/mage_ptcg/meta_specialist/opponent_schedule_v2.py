"""Fixed/adaptive opponent mixture with an explicit minimum sampling floor."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping


def schedule_probabilities_v2(
    meta_weights: Mapping[str, float], hard_negative_rates: Mapping[str, float], *,
    uncertainty: Mapping[str, float] | None = None, alpha: float = 1.0, beta: float = 0.5,
    eta: float = 0.25, floor: float = 0.01,
) -> dict[str, float]:
    keys = tuple(sorted(meta_weights))
    if not keys or set(hard_negative_rates) != set(keys) or floor < 0 or floor * len(keys) >= 1:
        raise ValueError("opponent schedule domains/floor are invalid")
    uncertainty = uncertainty or {key: 1.0 for key in keys}
    raw = {
        key: max(1e-12, float(meta_weights[key])) ** alpha
        * max(1e-12, float(hard_negative_rates[key])) ** beta
        * max(1e-12, float(uncertainty.get(key, 1.0))) ** eta
        for key in keys
    }
    total = sum(raw.values())
    remaining = 1.0 - floor * len(keys)
    return {key: floor + remaining * raw[key] / total for key in keys}


def sample_opponent_v2(probabilities: Mapping[str, float], *, seed: int) -> str:
    keys = tuple(sorted(probabilities))
    if not keys or any(value < 0 for value in probabilities.values()) or abs(sum(probabilities.values()) - 1.0) > 1e-6:
        raise ValueError("probabilities must be a normalized nonnegative mapping")
    rng = random.Random(seed)
    threshold = rng.random()
    running = 0.0
    for key in keys:
        running += probabilities[key]
        if threshold <= running:
            return key
    return keys[-1]


__all__ = ["sample_opponent_v2", "schedule_probabilities_v2"]
