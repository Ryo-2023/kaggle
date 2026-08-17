"""Multi-objective candidate analysis module.

Identifies Pareto frontier candidates, filters by hard safety limits,
and computes weighted normalization rankings.
"""

from __future__ import annotations

from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError

METRIC_DIRECTIONS = {
    "win_rate": "higher",
    "lower_confidence_bound": "higher",
    "legal_rate": "higher",
    "offline_top-1": "higher",
    "privacy_violations": "lower",
    "crash_rate": "lower",
    "timeout_rate": "lower",
    "fallback_rate": "lower",
    "latency_p95": "lower",
    "package_size": "lower",
    "offline_NLL": "lower",
    "calibration_ECE": "lower",
    "OOD_rate": "lower",
}

class CandidateAnalyzer:
    """Performs Pareto analysis and applies hard safety constraints to model candidates."""

    def analyze_candidates(
        self,
        candidates: dict[str, dict[str, float]],
        safety_limits: dict[str, float],
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Analyze candidates based on multi-objective metrics and safety limits."""
        if not candidates:
            return {"verdicts": {}, "pareto_frontier": []}

        # Weight validation if provided
        if weights:
            for k, w in weights.items():
                if w < 0.0:
                    raise SupportContractError(f"Negative weight {k}={w} is invalid.")

        results = {}
        pareto_frontier = []

        # 1. Apply hard safety limits first
        for cid, metrics in candidates.items():
            blocked = False
            reasons = []
            for limit_key, limit_val in safety_limits.items():
                if limit_key in metrics:
                    val = metrics[limit_key]
                    direction = METRIC_DIRECTIONS.get(limit_key, "lower")
                    if direction == "lower" and val > limit_val:
                        blocked = True
                        reasons.append(f"{limit_key} too high: {val} > {limit_val}")
                    elif direction == "higher" and val < limit_val:
                        blocked = True
                        reasons.append(f"{limit_key} too low: {val} < {limit_val}")

            if blocked:
                results[cid] = {
                    "verdict": "SAFETY_BLOCKED",
                    "reasons": reasons,
                    "is_dominated": True, # Safety blocked are treated as dominated/unusable
                }
            else:
                results[cid] = {
                    "verdict": "REVIEW_CANDIDATE",
                    "reasons": [],
                    "is_dominated": False,
                }

        # 2. Pareto Frontier Detection on non-blocked candidates
        active_candidates = {cid: metrics for cid, metrics in candidates.items() if results[cid]["verdict"] != "SAFETY_BLOCKED"}

        for cid, metrics in active_candidates.items():
            is_dominated = False
            for other_cid, other_metrics in active_candidates.items():
                if cid == other_cid:
                    continue
                # Check if other_cid dominates cid
                # other dominates self if other is better-or-equal in all metrics, and strictly better in at least one
                better_or_equal = True
                strictly_better = False

                for key in METRIC_DIRECTIONS.keys():
                    # Handle missing metrics: skip comparison for that key
                    if key not in metrics or key not in other_metrics:
                        continue

                    val_self = metrics[key]
                    val_other = other_metrics[key]
                    direction = METRIC_DIRECTIONS[key]

                    if direction == "higher":
                        if val_other < val_self:
                            better_or_equal = False
                        if val_other > val_self:
                            strictly_better = True
                    else: # lower
                        if val_other > val_self:
                            better_or_equal = False
                        if val_other < val_self:
                            strictly_better = True

                if better_or_equal and strictly_better:
                    is_dominated = True
                    break

            if is_dominated:
                results[cid]["is_dominated"] = True
                results[cid]["verdict"] = "DOMINATED"
            else:
                pareto_frontier.append(cid)

        # 3. Normalized Score Calculation (for display only)
        # Compute min/max of metrics for normalization
        mins = {}
        maxs = {}
        for key in METRIC_DIRECTIONS.keys():
            vals = [m[key] for m in candidates.values() if key in m]
            if vals:
                mins[key] = min(vals)
                maxs[key] = max(vals)

        for cid, metrics in candidates.items():
            if results[cid]["verdict"] == "SAFETY_BLOCKED":
                results[cid]["normalized_score"] = 0.0
                continue

            score = 0.0
            total_weight = 0.0
            for key, w in (weights or {}).items():
                if key in metrics and key in mins and key in maxs:
                    rng = maxs[key] - mins[key]
                    val = metrics[key]
                    # Normalize to [0, 1]
                    norm_val = (val - mins[key]) / rng if rng > 0.0 else 1.0
                    if METRIC_DIRECTIONS[key] == "lower":
                        norm_val = 1.0 - norm_val
                    score += norm_val * w
                    total_weight += w

            results[cid]["normalized_score"] = score / total_weight if total_weight > 0.0 else 0.0

        return {
            "verdicts": results,
            "pareto_frontier": sorted(pareto_frontier),
        }
