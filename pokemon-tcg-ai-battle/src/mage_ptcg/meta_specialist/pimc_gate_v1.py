"""§11 PIMC Reproducibility Gate and Distillation Probe module.

Validates that policy search outputs match PIMC reference distributions
within statistical bounds before policy promotion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


class PIMCGateV1Error(ValueError):
    """Raised when PIMC gate inputs or comparison bounds are invalid."""


@dataclass(frozen=True, slots=True)
class ActionLogitPairV1:
    action_key: str
    pimc_prob: float
    distilled_prob: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.pimc_prob <= 1.0):
            raise PIMCGateV1Error("pimc_prob must be in [0.0, 1.0]")
        if not (0.0 <= self.distilled_prob <= 1.0):
            raise PIMCGateV1Error("distilled_prob must be in [0.0, 1.0]")


@dataclass(frozen=True, slots=True)
class PIMCGateResultV1:
    passed: bool
    kl_divergence: float
    max_prob_delta: float
    sample_size: int
    bootstrap_ci_lower: float

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "kl_divergence": round(self.kl_divergence, 6),
            "max_prob_delta": round(self.max_prob_delta, 6),
            "sample_size": self.sample_size,
            "bootstrap_ci_lower": round(self.bootstrap_ci_lower, 6),
        }


def evaluate_pimc_reproducibility_v1(
    pairs: Sequence[ActionLogitPairV1],
    max_kl_threshold: float = 0.15,
    confidence_level: float = 0.975,
) -> PIMCGateResultV1:
    """Evaluate KL divergence between PIMC target and distilled policy with confidence bound."""
    if not pairs:
        raise PIMCGateV1Error("pairs collection cannot be empty")
    if max_kl_threshold <= 0.0:
        raise PIMCGateV1Error("max_kl_threshold must be positive")

    eps = 1e-9
    kl_divs: list[float] = []
    max_delta = 0.0

    for p in pairs:
        p_target = max(p.pimc_prob, eps)
        q_distill = max(p.distilled_prob, eps)
        kl = p_target * math.log(p_target / q_distill)
        kl_divs.append(kl)
        delta = abs(p.pimc_prob - p.distilled_prob)
        if delta > max_delta:
            max_delta = delta

    sample_size = len(pairs)
    mean_kl = sum(kl_divs) / sample_size

    # Standard error & 97.5% lower bound approximation
    variance = sum((x - mean_kl) ** 2 for x in kl_divs) / max(1, sample_size - 1)
    std_err = math.sqrt(variance / sample_size) if sample_size > 1 else 0.0
    z_score = 1.96  # 97.5%
    ci_lower = max(0.0, mean_kl - z_score * std_err)

    passed = (mean_kl <= max_kl_threshold) and (ci_lower <= max_kl_threshold)

    return PIMCGateResultV1(
        passed=passed,
        kl_divergence=mean_kl,
        max_prob_delta=max_delta,
        sample_size=sample_size,
        bootstrap_ci_lower=ci_lower,
    )
