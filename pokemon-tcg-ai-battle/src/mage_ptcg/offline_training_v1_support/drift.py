"""Distribution drift calculation utility.

Computes Total Variation Distance (TVD), Jensen-Shannon Divergence (JSD),
and Population Stability Index (PSI) for categorical datasets.
"""

from __future__ import annotations
import math
from typing import Any

def compute_tvd(p_counts: dict[Any, int], q_counts: dict[Any, int]) -> float:
    """Compute Total Variation Distance between two frequency distributions."""
    total_p = sum(p_counts.values())
    total_q = sum(q_counts.values())

    if total_p == 0 or total_q == 0:
        return 1.0

    p_probs = {k: v / total_p for k, v in p_counts.items()}
    q_probs = {k: v / total_q for k, v in q_counts.items()}

    all_keys = set(p_probs.keys()) | set(q_probs.keys())
    tvd = 0.5 * sum(abs(p_probs.get(k, 0.0) - q_probs.get(k, 0.0)) for k in all_keys)
    return tvd

def compute_psi(p_counts: dict[Any, int], q_counts: dict[Any, int], epsilon: float = 1e-4) -> float:
    """Compute Population Stability Index (PSI)."""
    total_p = sum(p_counts.values())
    total_q = sum(q_counts.values())

    if total_p == 0 or total_q == 0:
        return 999.0  # extreme drift indicator

    all_keys = set(p_counts.keys()) | set(q_counts.keys())

    psi = 0.0
    for k in all_keys:
        expected = p_counts.get(k, 0) / total_p
        actual = q_counts.get(k, 0) / total_q

        expected = max(epsilon, expected)
        actual = max(epsilon, actual)

        psi += (actual - expected) * math.log(actual / expected)

    return psi

def detect_categorical_drift(
    dataset_a: list[dict[str, Any]],
    dataset_b: list[dict[str, Any]],
    feature_name: str,
    tvd_threshold: float = 0.1,
    psi_threshold: float = 0.2
) -> dict[str, Any]:
    """Calculate and compare categorical distributions between dataset A and B."""
    counts_a: dict[Any, int] = {}
    counts_b: dict[Any, int] = {}

    for r in dataset_a:
        val = r.get(feature_name)
        if val is not None:
            counts_a[val] = counts_a.get(val, 0) + 1

    for r in dataset_b:
        val = r.get(feature_name)
        if val is not None:
            counts_b[val] = counts_b.get(val, 0) + 1

    tvd = compute_tvd(counts_a, counts_b)
    psi = compute_psi(counts_a, counts_b)

    drift_detected = tvd > tvd_threshold or psi > psi_threshold

    return {
        "feature": feature_name,
        "tvd": tvd,
        "psi": psi,
        "drift_detected": drift_detected,
        "thresholds": {"tvd": tvd_threshold, "psi": psi_threshold}
    }
