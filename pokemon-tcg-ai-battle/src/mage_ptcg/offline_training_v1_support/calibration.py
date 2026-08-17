"""Calibration evaluation and temperature scaling module.

Computes NLL, Brier score, ECE, and fits scalar temperature using grid search refinement.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError


def softmax(logits: list[float]) -> list[float]:
    """Compute softmax probabilities from logits safely."""
    if not logits:
        return []
    max_l = max(logits)
    exps = [math.exp(l - max_l) for l in logits]
    sum_exps = sum(exps)
    return [e / sum_exps for e in exps]


def compute_nll(predictions: Sequence[dict[str, Any]], temperature: float = 1.0) -> float:
    """Calculate the Negative Log-Likelihood (NLL) for logits at temperature T."""
    nll_sum = 0.0
    count = 0

    for pred in predictions:
        logits = pred.get("logits")
        # Fallback to probabilities if logits not available
        if not logits:
            probs = pred.get("probabilities", [])
            if not probs:
                continue
            # Reconstruct dummy logits from probabilities to apply temperature
            logits = [math.log(max(1e-12, p)) for p in probs]

        true_idx = pred.get("true_candidate_index")
        if true_idx is None or true_idx < 0 or true_idx >= len(logits):
            continue

        # Scale logits by temperature
        scaled = [l / temperature for l in logits]
        probs = softmax(scaled)

        nll_sum -= math.log(max(1e-15, probs[true_idx]))
        count += 1

    return nll_sum / count if count > 0 else 0.0


def compute_brier_score(predictions: Sequence[dict[str, Any]]) -> float:
    """Calculate the multi-class Brier score."""
    brier_sum = 0.0
    count = 0

    for pred in predictions:
        probs = pred.get("probabilities")
        if not probs:
            logits = pred.get("logits")
            if not logits:
                continue
            probs = softmax(logits)

        true_idx = pred.get("true_candidate_index")
        if true_idx is None or true_idx < 0 or true_idx >= len(probs):
            continue

        squared_errors = 0.0
        for idx, p in enumerate(probs):
            target = 1.0 if idx == true_idx else 0.0
            squared_errors += (p - target) ** 2

        brier_sum += squared_errors
        count += 1

    return brier_sum / count if count > 0 else 0.0


def compute_ece(predictions: Sequence[dict[str, Any]], num_bins: int = 10) -> tuple[float, float, list[dict[str, Any]]]:
    """Calculate Expected Calibration Error (ECE), MCE, and bin statistics."""
    bins = [[] for _ in range(num_bins)]

    for pred in predictions:
        probs = pred.get("probabilities")
        if not probs:
            logits = pred.get("logits")
            if not logits:
                continue
            probs = softmax(logits)

        if not probs:
            continue

        selected_idx = pred.get("selected_candidate_index")
        true_idx = pred.get("true_candidate_index")
        if selected_idx is None or true_idx is None:
            continue

        conf = probs[selected_idx]
        is_correct = 1.0 if selected_idx == true_idx else 0.0

        # Determine bin
        bin_idx = int(conf * num_bins)
        if bin_idx >= num_bins:
            bin_idx = num_bins - 1

        bins[bin_idx].append((conf, is_correct))

    ece = 0.0
    mce = 0.0
    total_samples = sum(len(b) for b in bins)
    bin_stats = []

    for idx, b in enumerate(bins):
        n = len(b)
        if n == 0:
            bin_stats.append({
                "bin_range": [idx / num_bins, (idx + 1) / num_bins],
                "count": 0,
                "accuracy": 0.0,
                "mean_confidence": 0.0,
            })
            continue

        mean_conf = sum(x[0] for x in b) / n
        accuracy = sum(x[1] for x in b) / n
        diff = abs(accuracy - mean_conf)

        ece += (n / total_samples) * diff
        mce = max(mce, diff)

        bin_stats.append({
            "bin_range": [idx / num_bins, (idx + 1) / num_bins],
            "count": n,
            "accuracy": accuracy,
            "mean_confidence": mean_conf,
        })

    return ece, mce, bin_stats


def fit_temperature(predictions: Sequence[dict[str, Any]], lower_bound: float = 0.1, upper_bound: float = 10.0) -> float:
    """Find the optimal scalar temperature minimizing NLL using iterative grid refinement."""
    if not predictions:
        return 1.0

    # Step 1: Broad Grid Search
    best_temp = 1.0
    best_nll = float("inf")

    # 100 steps from lower_bound to upper_bound
    steps = 100
    for idx in range(steps + 1):
        temp = lower_bound + (upper_bound - lower_bound) * (idx / steps)
        try:
            nll = compute_nll(predictions, temp)
            if nll < best_nll:
                best_nll = nll
                best_temp = temp
        except Exception:
            continue

    # Step 2: Fine-grained Refinement
    refinement_steps = 2
    for _ in range(refinement_steps):
        left = max(lower_bound, best_temp - 0.2)
        right = min(upper_bound, best_temp + 0.2)

        for idx in range(20 + 1):
            temp = left + (right - left) * (idx / 20)
            try:
                nll = compute_nll(predictions, temp)
                if nll < best_nll:
                    best_nll = nll
                    best_temp = temp
            except Exception:
                continue

    # Failure-safe fallback
    if not math.isfinite(best_nll):
        return 1.0

    return best_temp


def evaluate_group_calibration(predictions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Calculate group-specific metrics (ECE, NLL) stratified by context features."""
    groups = {
        "selection_type": defaultdict(list),
        "context_type": defaultdict(list),
        "seat": defaultdict(list),
        "fallback_used": defaultdict(list),
    }

    for pred in predictions:
        for key in groups.keys():
            val = pred.get(key)
            if val is not None:
                groups[key][str(val)].append(pred)

    report = {}
    for g_name, g_data in groups.items():
        report[g_name] = {}
        for sub_id, sub_preds in g_data.items():
            if len(sub_preds) >= 5:  # Sufficient data limit
                ece, _, _ = compute_ece(sub_preds)
                nll = compute_nll(sub_preds)
                brier = compute_brier_score(sub_preds)
                report[g_name][sub_id] = {
                    "count": len(sub_preds),
                    "ece": ece,
                    "nll": nll,
                    "brier": brier,
                }
    return report
