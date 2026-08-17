"""Stratified analysis and Simpson's paradox detection.

Compares sub-population metrics to overall win rate trends to identify statistical inconsistencies.
"""

from __future__ import annotations
from typing import Any
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics

def detect_simpsons_paradox(
    games: list[dict[str, Any]],
    stratify_by: str = "candidate_seat"
) -> dict[str, Any]:
    """Analyze if the sub-population trends conflict with the overall trend (Simpson's Paradox)."""
    if not games:
        return {"paradox_detected": False, "reason": "No games provided"}

    overall_stats = evaluate_game_statistics(games)
    overall_win_rate = overall_stats["overall_win_rate"]

    strata: dict[Any, list[dict[str, Any]]] = {}
    for g in games:
        val = g.get(stratify_by)
        if val is not None:
            strata.setdefault(val, []).append(g)

    strata_win_rates = {}
    small_cells = []

    for s_val, s_games in strata.items():
        s_stats = evaluate_game_statistics(s_games)
        s_rate = s_stats["overall_win_rate"]
        strata_win_rates[s_val] = s_rate

        if len(s_games) < 5:
            small_cells.append(str(s_val))

    paradox_detected = False
    reasons = []

    if len(strata_win_rates) >= 2:
        rates = list(strata_win_rates.values())
        if overall_win_rate >= 0.50 and all(r < 0.50 for r in rates):
            paradox_detected = True
            reasons.append("Overall candidate win rate >= 50% but all sub-populations have win rate < 50%")
        elif overall_win_rate < 0.50 and all(r >= 0.50 for r in rates):
            paradox_detected = True
            reasons.append("Overall candidate win rate < 50% but all sub-populations have win rate >= 50%")

    return {
        "paradox_detected": paradox_detected,
        "overall_win_rate": overall_win_rate,
        "strata_win_rates": strata_win_rates,
        "small_cells": small_cells,
        "reasons": reasons
    }
