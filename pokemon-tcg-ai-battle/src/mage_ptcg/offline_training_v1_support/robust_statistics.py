"""Robust statistics and comparison tests expansion.

Implements exact binomial checks, FDR correction, trimmed means, and MAD.
"""

from __future__ import annotations
import math
from typing import Any

def compute_trimmed_mean(values: list[float], trim_ratio: float = 0.1) -> float:
    """Calculate the trimmed mean of a sample."""
    if not values:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    k = int(n * trim_ratio)

    trimmed = sorted_vals[k:n-k] if k > 0 else sorted_vals
    if not trimmed:
        return 0.0
    return sum(trimmed) / len(trimmed)

def compute_median_absolute_deviation(values: list[float]) -> float:
    """Compute the Median Absolute Deviation (MAD) of a sample."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(values)
    median = sorted_vals[n // 2]

    deviations = [abs(x - median) for x in sorted_vals]
    deviations.sort()
    return deviations[n // 2]

def exact_binomial_test(wins: int, total: int, p0: float = 0.5) -> float:
    """Calculate the exact two-sided binomial p-value using combination logic."""
    if total == 0:
        return 1.0

    def comb(n: int, k: int) -> int:
        if k < 0 or k > n:
            return 0
        if k == 0 or k == n:
            return 1
        k = min(k, n - k)
        c = 1
        for i in range(k):
            c = c * (n - i) // (i + 1)
        return c

    p_k = []
    for k in range(total + 1):
        try:
            val = comb(total, k) * (p0 ** k) * ((1.0 - p0) ** (total - k))
        except OverflowError:
            val = 0.0
        p_k.append(val)

    target_prob = p_k[wins]
    p_value = sum(p for p in p_k if p <= target_prob * (1.0 + 1e-9))
    return min(1.0, max(0.0, p_value))

def holm_bonferroni_correction(p_values: list[float]) -> list[float]:
    """Apply Holm-Bonferroni correction to control Family-Wise Error Rate (FWER)."""
    n = len(p_values)
    if n == 0:
        return []

    indexed_p = sorted(enumerate(p_values), key=lambda x: x[1])

    adjusted = [0.0] * n
    max_adj = 0.0
    for rank, (orig_idx, p) in enumerate(indexed_p):
        adj = p * (n - rank)
        max_adj = max(max_adj, adj)
        adjusted[orig_idx] = min(1.0, max_adj)

    return adjusted
