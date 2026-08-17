"""Evaluation statistics module.

Provides win rate metrics, Wilson score intervals, and stratified bootstrap.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError


def compute_z_score(confidence: float) -> float:
    """Approximate the z-score for a given two-sided confidence level."""
    z_map = {0.90: 1.644853, 0.95: 1.959964, 0.99: 2.575829}
    if confidence in z_map:
        return z_map[confidence]
    # Hastings (1955) approximation for normal inverse
    alpha = 1.0 - confidence
    p = alpha / 2.0
    if p <= 0.0 or p >= 0.5:
        return 1.959964  # Fallback to 95%
    t = math.sqrt(-2.0 * math.log(p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    z = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
    return z


def wilson_score_interval(
    wins: float, losses: float, draws: float, draw_weight: float = 0.5, confidence: float = 0.95
) -> tuple[float, float]:
    """Calculate the Wilson score interval for the win rate."""
    trials = wins + losses + draws
    if trials == 0:
        return 0.0, 0.0
    successes = wins + draw_weight * draws
    p = successes / trials
    z = compute_z_score(confidence)

    denominator = 1 + (z**2) / trials
    center = p + (z**2) / (2 * trials)
    spread = z * math.sqrt(max(0.0, (p * (1 - p) / trials) + (z**2) / (4 * (trials**2))))

    lower = (center - spread) / denominator
    upper = (center + spread) / denominator
    return max(0.0, lower), min(1.0, upper)


def run_stratified_bootstrap(
    games: list[dict[str, Any]],
    draw_weight: float = 0.5,
    num_samples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """Calculate bootstrap confidence interval stratified by candidate_seat."""
    if not (0.0 < confidence < 1.0):
        raise SupportContractError(f"Invalid confidence level: {confidence}")
    if not (0.0 <= draw_weight <= 1.0):
        raise SupportContractError(f"Invalid draw weight: {draw_weight}")
    if not games:
        return 0.0, 0.0

    # Ensure input order invariance by sorting games deterministically
    from mage_ptcg.offline_training_v1_support.contracts import digest
    sorted_games = sorted(games, key=lambda g: str(g.get("game_id") or digest(g)))

    # Stratify by seat
    strata: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for g in sorted_games:
        seat = g.get("candidate_seat", 0)
        strata[seat].append(g)

    # Empty stratum handling
    non_empty_strata = {seat: data for seat, data in strata.items() if data}
    if not non_empty_strata:
        return 0.0, 0.0

    rng = random.Random(seed)
    bootstrap_means = []

    for _ in range(num_samples):
        resampled_wins = 0.0
        resampled_losses = 0.0
        resampled_draws = 0.0
        resampled_total = 0

        for seat, stratum_games in non_empty_strata.items():
            n = len(stratum_games)
            # Sample with replacement
            samples = [rng.choice(stratum_games) for _ in range(n)]
            for s in samples:
                winner = s.get("winner")
                is_invalid = bool(s.get("invalid", False))
                is_crash = bool(s.get("crash", False))
                is_timeout = bool(s.get("timeout", False))

                # Apply invalid/crash/timeout policy: force opponent winner
                if is_invalid or is_crash or is_timeout:
                    winner = "opponent"

                if winner == "candidate":
                    resampled_wins += 1
                elif winner == "opponent":
                    resampled_losses += 1
                elif winner == "draw":
                    resampled_draws += 1
                else:
                    resampled_losses += 1
            resampled_total += n

        if resampled_total > 0:
            rate = (resampled_wins + draw_weight * resampled_draws) / resampled_total
            bootstrap_means.append(rate)
        else:
            bootstrap_means.append(0.0)

    bootstrap_means.sort()
    lower_idx = max(0, int(num_samples * ((1.0 - confidence) / 2.0)))
    upper_idx = min(num_samples - 1, int(num_samples * (1.0 - (1.0 - confidence) / 2.0)))
    return bootstrap_means[lower_idx], bootstrap_means[upper_idx]


def evaluate_game_statistics(games: Iterable[dict[str, Any]], draw_weight: float = 0.5) -> dict[str, Any]:
    """Aggregate per-game records to produce detailed evaluation metrics."""
    if not (0.0 <= draw_weight <= 1.0):
        raise SupportContractError(f"Invalid draw weight: {draw_weight}")
    wins = 0
    losses = 0
    draws = 0
    invalids = 0
    crashes = 0
    timeouts = 0

    total_legal_actions = 0
    total_actions = 0
    total_fallbacks = 0

    seat_stats: dict[int, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0, "games": 0})
    deck_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0, "games": 0})
    opponent_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0, "games": 0})

    game_list = list(games)
    for g in game_list:
        # Schema check
        required = {"game_id", "winner", "candidate_seat"}
        if not required.issubset(g):
            raise SupportContractError(f"Missing required fields in game result: {required - set(g)}")

        winner = g["winner"]
        if winner not in ("candidate", "opponent", "draw"):
            raise SupportContractError(f"Invalid winner status: {winner}")

        seat = int(g["candidate_seat"])
        deck_id = str(g.get("candidate_deck_id", "default_deck"))
        opp_id = str(g.get("opponent_policy_id", "default_opponent"))

        is_invalid = bool(g.get("invalid", False))
        is_crash = bool(g.get("crash", False))
        is_timeout = bool(g.get("timeout", False))

        if is_invalid:
            invalids += 1
        if is_crash:
            crashes += 1
        if is_timeout:
            timeouts += 1

        # Apply invalid/crash/timeout policy: candidate cannot win
        if is_invalid or is_crash or is_timeout:
            winner = "opponent"

        # Check for non-finite rates
        legal_rate = g.get("candidate_legal_rate", 1.0)
        if not isinstance(legal_rate, (int, float)) or not math.isfinite(legal_rate):
            raise SupportContractError("Non-finite candidate_legal_rate rejected")

        total_legal_actions += legal_rate
        total_actions += 1
        total_fallbacks += int(g.get("candidate_fallback_count", 0))

        if winner == "candidate":
            wins += 1
            seat_stats[seat]["wins"] += 1
            deck_stats[deck_id]["wins"] += 1
            opponent_stats[opp_id]["wins"] += 1
        elif winner == "opponent":
            losses += 1
            seat_stats[seat]["losses"] += 1
            deck_stats[deck_id]["losses"] += 1
            opponent_stats[opp_id]["losses"] += 1
        else:
            draws += 1
            seat_stats[seat]["draws"] += 1
            deck_stats[deck_id]["draws"] += 1
            opponent_stats[opp_id]["draws"] += 1

        seat_stats[seat]["games"] += 1
        deck_stats[deck_id]["games"] += 1
        opponent_stats[opp_id]["games"] += 1

    total_games = len(game_list)
    overall_win_rate = (wins + draw_weight * draws) / total_games if total_games > 0 else 0.0

    wilson_lower, wilson_upper = wilson_score_interval(wins, losses, draws, draw_weight)
    boot_lower, boot_upper = run_stratified_bootstrap(game_list, draw_weight, num_samples=1000)

    # Format aggregations
    seat_report = {}
    for seat, s in seat_stats.items():
        tg = s["games"]
        wr = (s["wins"] + draw_weight * s["draws"]) / tg if tg > 0 else 0.0
        seat_report[str(seat)] = {**s, "win_rate": wr}

    deck_report = {}
    for did, d in deck_stats.items():
        tg = d["games"]
        wr = (d["wins"] + draw_weight * d["draws"]) / tg if tg > 0 else 0.0
        deck_report[did] = {**d, "win_rate": wr}

    opponent_report = {}
    for oid, o in opponent_stats.items():
        tg = o["games"]
        wr = (o["wins"] + draw_weight * o["draws"]) / tg if tg > 0 else 0.0
        opponent_report[oid] = {**o, "win_rate": wr}

    return {
        "total_games": total_games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "invalid_count": invalids,
        "crash_count": crashes,
        "timeout_count": timeouts,
        "overall_win_rate": overall_win_rate,
        "wilson_interval": [wilson_lower, wilson_upper],
        "bootstrap_interval": [boot_lower, boot_upper],
        "legal_action_rate": total_legal_actions / total_actions if total_actions > 0 else 1.0,
        "fallback_count": total_fallbacks,
        "fallback_rate": total_fallbacks / total_actions if total_actions > 0 else 0.0,
        "seat_breakdown": seat_report,
        "deck_breakdown": deck_report,
        "opponent_breakdown": opponent_report,
    }
