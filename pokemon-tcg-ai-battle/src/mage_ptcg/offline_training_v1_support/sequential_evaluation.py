"""Sequential evaluation (SPRT) engine for early stopping in screening.

Implements Wald's Sequential Probability Ratio Test (SPRT).
"""

from __future__ import annotations
import math
from typing import Any

def run_sprt_check(
    wins: int,
    losses: int,
    p0: float = 0.5,
    p1: float = 0.55,
    alpha: float = 0.05,
    beta: float = 0.1,
    max_games: int = 200,
    min_games: int = 20
) -> dict[str, Any]:
    """Run Wald's Sequential Probability Ratio Test."""
    total = wins + losses
    if total < min_games:
        return {"status": "CONTINUE", "log_likelihood_ratio": 0.0, "bounds": (0.0, 0.0)}

    lower_bound = math.log(beta / (1.0 - alpha))
    upper_bound = math.log((1.0 - beta) / alpha)

    p0_clamp = min(0.999, max(0.001, p0))
    p1_clamp = min(0.999, max(0.001, p1))

    term1 = wins * math.log(p1_clamp / p0_clamp)
    term2 = losses * math.log((1.0 - p1_clamp) / (1.0 - p0_clamp))
    llr = term1 + term2

    if llr >= upper_bound:
        status = "EVIDENCE_FOR_ALTERNATIVE"
    elif llr <= lower_bound:
        status = "EVIDENCE_FOR_NULL"
    elif total >= max_games:
        status = "MAX_SAMPLE_REACHED"
    else:
        status = "CONTINUE"

    return {
        "status": status,
        "log_likelihood_ratio": llr,
        "bounds": (lower_bound, upper_bound),
        "total_games": total
    }
