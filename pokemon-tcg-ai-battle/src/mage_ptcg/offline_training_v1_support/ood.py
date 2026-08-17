"""Out-of-Distribution (OOD) diagnostics module.

Calculates OOD scores based on probabilities, margins, entropies, and unknown contexts.
"""

from __future__ import annotations

import math
from typing import Any

from mage_ptcg.offline_training_v1_support.calibration import softmax


def calculate_entropy(probs: list[float]) -> float:
    """Calculate Shannon entropy for a list of probabilities."""
    entropy = 0.0
    for p in probs:
        if p > 0.0:
            entropy -= p * math.log(p)
    return entropy


def compute_ood_diagnostics(
    record: dict[str, Any],
    prob_threshold: float = 0.3,
    entropy_threshold: float = 1.8,
    margin_threshold: float = 0.05,
) -> dict[str, Any]:
    """Analyze decision record signals to compute offline OOD safety status."""
    probs = record.get("probabilities")
    if not probs:
        logits = record.get("logits")
        if logits:
            probs = softmax(logits)
        else:
            probs = []

    reasons = []
    ood_score = 0.0

    # 1. Probabilistic uncertainty signals
    if probs:
        sorted_probs = sorted(probs, reverse=True)
        max_prob = sorted_probs[0]

        # Max probability drop
        if max_prob < prob_threshold:
            reasons.append("LOW_MAX_PROB")
            ood_score += 1.0

        # High entropy
        entropy = calculate_entropy(probs)
        if entropy > entropy_threshold:
            reasons.append("HIGH_ENTROPY")
            ood_score += 0.8

        # Low margin
        if len(sorted_probs) >= 2:
            margin = sorted_probs[0] - sorted_probs[1]
            if margin < margin_threshold:
                reasons.append("LOW_MARGIN")
                ood_score += 1.0

    # 2. Structural OOD signals
    sel_type = record.get("selection_type")
    if sel_type in ("unknown_select", "rare_select"):
        reasons.append("UNKNOWN_SELECTION_TYPE")
        ood_score += 1.5

    ctx_type = record.get("context_type")
    if ctx_type in ("unknown_context", "rare_context"):
        reasons.append("UNKNOWN_CONTEXT_TYPE")
        ood_score += 1.5

    if bool(record.get("feature_schema_mismatch", False)):
        reasons.append("FEATURE_SCHEMA_MISMATCH")
        ood_score += 2.0

    # Fallback recommendations (Offline recommendation, does not affect active runtime)
    rec_fallback = "STANDARD_ACT"
    if ood_score >= 2.0:
        rec_fallback = "RULE_AGENT_FALLBACK"

    safe_summary = {
        "reason_count": len(reasons),
        "reasons": reasons,
        "selection_type": sel_type,
        "context_type": ctx_type,
    }

    return {
        "schema_version": "support-ood-diagnostics-v1",
        "ood_score": ood_score,
        "reason_codes": sorted(reasons),
        "threshold_source": "static_offline_thresholds",
        "recommended_fallback_category": rec_fallback,
        "safe_summary": safe_summary,
    }
