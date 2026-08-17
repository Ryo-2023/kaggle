"""SP-PSRO-compatible population guards built on the existing zero-sum solver."""
from __future__ import annotations

from typing import Any

from ..league import solve_meta_strategy


def meta_strategy(payoff_matrix: list[list[float]], *, experimental_global_psro: bool = False) -> list[float]:
    # Global PSRO remains a feature-gated comparison only.
    if experimental_global_psro: raise ValueError("experimental_global_psro requires a separately validated implementation")
    return solve_meta_strategy(payoff_matrix)


def should_expand(*, meta_improvement: float, validation_improvement: float, faults: int, novel: bool, single_opponent_overfit: bool) -> dict[str, Any]:
    accepted = meta_improvement > 0 and validation_improvement > 0 and faults == 0 and novel and not single_opponent_overfit
    return {"accepted": accepted, "reasons": [] if accepted else ["meta", "validation", "fault", "novelty", "overfit"]}
