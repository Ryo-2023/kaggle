"""Candidate-only legal-action Q policy."""
from __future__ import annotations

import math
import random
from typing import Any


def select_legal_action(q_values: list[float], *, mode: str = "greedy", epsilon: float = 0.0, temperature: float = 1.0, seed: int | None = None) -> int:
    if not q_values or any(not math.isfinite(value) for value in q_values): raise ValueError("Q values must be finite legal actions")
    if mode == "greedy": return min(range(len(q_values)), key=lambda index: (-q_values[index], index))
    rng = random.Random(seed)
    if mode == "epsilon":
        if not 0 <= epsilon <= 1: raise ValueError("epsilon must be in [0,1]")
        return rng.randrange(len(q_values)) if rng.random() < epsilon else min(range(len(q_values)), key=lambda index: (-q_values[index], index))
    if mode == "boltzmann":
        if temperature <= 0: raise ValueError("temperature must be positive")
        maximum = max(q_values); weights = [math.exp((value - maximum) / temperature) for value in q_values]; return rng.choices(range(len(q_values)), weights=weights, k=1)[0]
    raise ValueError("unknown action mode")
