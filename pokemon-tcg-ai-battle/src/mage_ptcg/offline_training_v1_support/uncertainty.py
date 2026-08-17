"""Uncertainty diagnostics tool for decision predictions.

Provides approximate diagnostics like predictive entropy and top-2 margin.
Does not claim strict aleatoric/epistemic decomposition.
"""

from __future__ import annotations
import math
from typing import Any

def calculate_predictive_entropy(probs: list[float]) -> float:
    """Compute Shannon entropy for a probability distribution."""
    total = sum(probs)
    if total == 0:
        return 0.0
    normalized = [p / total for p in probs]

    entropy = 0.0
    for p in normalized:
        if p > 0.0:
            entropy -= p * math.log(p)
    return entropy

def calculate_top2_margin(probs: list[float]) -> float:
    """Compute the difference between the highest and second highest probability."""
    if len(probs) < 2:
        return 1.0
    sorted_probs = sorted(probs, reverse=True)
    return sorted_probs[0] - sorted_probs[1]

def diagnose_decision_uncertainty(
    probs: list[float],
    teacher_preds: list[str] = None,
    is_ood: bool = False,
    is_fallback: bool = False
) -> dict[str, Any]:
    """Diagnose decision uncertainty using approximate indicators."""
    teacher_preds = teacher_preds or []

    entropy = calculate_predictive_entropy(probs)
    margin = calculate_top2_margin(probs)

    teacher_disagreement = 0.0
    if len(teacher_preds) > 1:
        unique_preds = set(teacher_preds)
        teacher_disagreement = (len(unique_preds) - 1) / (len(teacher_preds) - 1)

    approximate_uncertainty = entropy * 0.5 + (1.0 - margin) * 0.3
    if is_ood:
        approximate_uncertainty += 0.5
    if is_fallback:
        approximate_uncertainty += 0.2

    return {
        "predictive_entropy": entropy,
        "top2_margin": margin,
        "teacher_disagreement": teacher_disagreement,
        "is_ood": is_ood,
        "is_fallback": is_fallback,
        "approximate_uncertainty_score": min(2.0, max(0.0, approximate_uncertainty))
    }
