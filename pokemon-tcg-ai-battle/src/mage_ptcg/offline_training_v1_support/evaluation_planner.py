"""Evaluation and sample-size planner module.

Calculates recommended games based on statistical power analysis and
detects when requirements exceed the standard 100-game screening limit.
"""

from __future__ import annotations

import math
from typing import Any

from mage_ptcg.offline_training_v1_support.statistics import compute_z_score

class EvaluationPlanner:
    """Calculates sample size requirements and designs evaluation schedules."""

    def plan_sample_size(
        self,
        baseline_win_rate: float,
        target_improvement: float,
        confidence_level: float = 0.95,
        power_target: float = 0.80,
        invalid_rate_estimate: float = 0.05,
    ) -> dict[str, Any]:
        """Compute recommended sample size using two-proportion normal approximation."""
        p1 = baseline_win_rate
        p2 = min(0.999, p1 + target_improvement)

        # Standard screening contract reference
        contract_games = 100
        contract_per_seat = 50

        # Calculate z-scores
        z_alpha = compute_z_score(confidence_level)
        # Power target (e.g. 0.80) -> beta = 0.20 -> z_beta for one-sided 0.80 is compute_z_score(0.60)
        # Since compute_z_score is two-sided confidence:
        # For one-sided probability power_target (e.g. 0.80), the two-sided equivalent is 2 * power_target - 1.0 = 0.60
        # If power_target=0.80, 2*0.80-1.0 = 0.60.
        # But compute_z_score is configured with Hastings approximation which fails for p >= 0.5.
        # Let's write a simple helper or use standard mapping for power z-scores.
        # Common power z-scores:
        # 0.80 -> 0.8416
        # 0.90 -> 1.2816
        # 0.95 -> 1.6448
        power_map = {0.80: 0.84162, 0.90: 1.28155, 0.95: 1.64485}
        z_beta = power_map.get(power_target, 0.84162) # default to 0.80 power

        numerator = (z_alpha + z_beta) ** 2 * (p1 * (1.0 - p1) + p2 * (1.0 - p2))
        denominator = (p1 - p2) ** 2

        if denominator > 0.0:
            raw_n = numerator / denominator
        else:
            raw_n = 1000.0 # fallback

        # Adjust for invalid rate estimate
        multiplier = 1.0 / (1.0 - invalid_rate_estimate) if invalid_rate_estimate < 1.0 else 1.0
        adjusted_n = raw_n * multiplier
        recommended_total = int(math.ceil(adjusted_n))

        # Ensure even distribution across 2 seats (candidate_seat 0 and 1)
        if recommended_total % 2 != 0:
            recommended_total += 1
        games_per_seat = recommended_total // 2

        # Warnings
        warnings = []
        if recommended_total > contract_games:
            warnings.append(
                f"Required sample size ({recommended_total}) exceeds the standard 100-game screening contract. "
                "Evaluating this configuration with 100 games may result in lower power (higher False Negatives)."
            )

        return {
            "recommended_total_games": recommended_total,
            "games_per_seat": games_per_seat,
            "minimum_valid_games": int(math.ceil(raw_n)),
            "estimated_invalid_reserve": recommended_total - int(math.ceil(raw_n)),
            "target_effect_size": target_improvement,
            "contract_reference": {
                "total_games": contract_games,
                "games_per_seat": contract_per_seat,
            },
            "warnings": warnings,
            "assumptions": {
                "baseline_win_rate": p1,
                "target_win_rate": p2,
                "confidence_level": confidence_level,
                "power_target": power_target,
                "invalid_rate_estimate": invalid_rate_estimate,
            },
            "limitations": [
                "Normal approximation assumes large-sample behavior and may be less accurate for win rates near 0 or 1.",
                "Assumes independent trials without multi-game dependencies."
            ]
        }
