"""Sensitivity analysis utility for statistical indicators.

Evaluates how modifications of OOD/fallback thresholds and confidence affect win rates.
"""

from __future__ import annotations
from typing import Any
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics

def analyze_winrate_sensitivity(
    games: list[dict[str, Any]],
    confidence_levels: list[float] = None
) -> dict[str, Any]:
    """Evaluate how varying statistical parameters shifts the win rate bounds."""
    confidence_levels = confidence_levels or [0.90, 0.95, 0.99]

    baseline_stats = evaluate_game_statistics(games, draw_weight=0.5)
    baseline_rate = baseline_stats["overall_win_rate"]

    variance_report = {}
    for conf in confidence_levels:
        from mage_ptcg.offline_training_v1_support.statistics import wilson_score_interval
        lower, upper = wilson_score_interval(
            baseline_stats["wins"],
            baseline_stats["losses"],
            baseline_stats["draws"],
            draw_weight=0.5,
            confidence=conf
        )
        variance_report[f"conf_{conf}"] = {
            "lower": lower,
            "upper": upper,
            "width": upper - lower
        }

    has_high_sensitivity = False
    warning = ""
    worst_case_conf = f"conf_{max(confidence_levels)}"
    if worst_case_conf in variance_report:
        worst_lower = variance_report[worst_case_conf]["lower"]
        if baseline_rate >= 0.50 and worst_lower < 0.45:
            has_high_sensitivity = True
            warning = "High sensitivity warning: worst-case lower bound falls below 0.45 despite baseline >= 50% win rate."

    return {
        "baseline_win_rate": baseline_rate,
        "confidence_variance": variance_report,
        "has_high_sensitivity": has_high_sensitivity,
        "warning": warning
    }
