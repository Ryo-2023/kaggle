"""Ratings calculation module.

Provides Elo and Bradley-Terry rating models using Python standard library.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError


def compute_elo(
    games: list[dict[str, Any]],
    initial_rating: float = 1500.0,
    k_factor: float = 32.0,
    draw_weight: float = 0.5,
) -> dict[str, Any]:
    """Calculate Elo ratings sequentially and deterministically."""
    if k_factor <= 0.0 or not math.isfinite(k_factor):
        raise SupportContractError(f"Invalid k_factor: {k_factor}")
    if not (0.0 <= draw_weight <= 1.0) or not math.isfinite(draw_weight):
        raise SupportContractError(f"Invalid draw_weight: {draw_weight}")
    if not math.isfinite(initial_rating):
        raise SupportContractError(f"Invalid initial_rating: {initial_rating}")

    # Exclude invalid/duplicate games and check stable ordering
    seen_games = set()
    valid_games = []
    for g in games:
        # Invalid game exclusion
        if g.get("invalid") or g.get("crash") or g.get("timeout"):
            continue
        # Duplicate game check if game_id exists
        gid = g.get("game_id")
        if gid:
            if gid in seen_games:
                continue
            seen_games.add(gid)
        valid_games.append(g)

    sorted_games = sorted(
        valid_games,
        key=lambda g: (
            str(g.get("game_id", "")),
            int(g.get("seed", 0)),
            int(g.get("candidate_seat", 0)),
        ),
    )

    ratings = {}
    game_counts = defaultdict(int)
    wins = defaultdict(int)
    losses = defaultdict(int)
    draws = defaultdict(int)

    # Pre-populate all policies to guarantee initial rating visibility
    for g in sorted_games:
        cp = g.get("candidate_policy_id")
        op = g.get("opponent_policy_id")
        if cp and cp not in ratings:
            ratings[cp] = initial_rating
        if op and op not in ratings:
            ratings[op] = initial_rating

    for g in sorted_games:
        cp = g.get("candidate_policy_id")
        op = g.get("opponent_policy_id")
        if not cp or not op:
            continue

        # Self-play guard: skip Elo calculation if policy plays against itself
        if cp == op:
            continue

        r_cp = ratings[cp]
        r_op = ratings[op]

        # Calculate expected scores
        e_cp = 1.0 / (1.0 + 10.0 ** ((r_op - r_cp) / 400.0))
        e_op = 1.0 / (1.0 + 10.0 ** ((r_cp - r_op) / 400.0))

        winner = g.get("winner")
        if winner == "candidate":
            s_cp, s_op = 1.0, 0.0
            wins[cp] += 1
            losses[op] += 1
        elif winner == "opponent":
            s_cp, s_op = 0.0, 1.0
            losses[cp] += 1
            wins[op] += 1
        else:
            s_cp, s_op = 0.5, 0.5
            draws[cp] += 1
            draws[op] += 1

        game_counts[cp] += 1
        game_counts[op] += 1

        ratings[cp] = r_cp + k_factor * (s_cp - e_cp)
        ratings[op] = r_op + k_factor * (s_op - e_op)

    result = {}
    for pid in sorted(ratings.keys()):
        g_count = game_counts[pid]
        result[pid] = {
            "rating": ratings[pid],
            "games": g_count,
            "wins": wins[pid],
            "losses": losses[pid],
            "draws": draws[pid],
            "uncertainty_indicator": 400.0 / math.sqrt(g_count) if g_count > 0 else 400.0,
            "data_sufficiency_status": "SUFFICIENT" if g_count >= 5 else "INSUFFICIENT",
        }
    return result


def check_connected(policies: list[str], matchups: dict[tuple[str, str], int]) -> bool:
    """Detect if the policy match graph is fully connected."""
    if not policies:
        return True
    adj = defaultdict(set)
    for (p1, p2), cnt in matchups.items():
        if cnt > 0:
            adj[p1].add(p2)
            adj[p2].add(p1)

    visited = set()
    queue = [policies[0]]
    visited.add(policies[0])
    while queue:
        curr = queue.pop(0)
        for neighbor in adj[curr]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return len(visited) == len(policies)


def compute_bradley_terry(
    games: list[dict[str, Any]],
    max_iter: int = 1000,
    tol: float = 1e-6,
    regularization: float = 0.01,
) -> dict[str, Any]:
    """Compute Bradley-Terry ratings using a regularized MM algorithm."""
    if max_iter <= 0:
        raise SupportContractError(f"Invalid max_iter: {max_iter}")
    if tol <= 0.0 or not math.isfinite(tol):
        raise SupportContractError(f"Invalid tol: {tol}")
    if regularization < 0.0 or not math.isfinite(regularization):
        raise SupportContractError(f"Invalid regularization: {regularization}")

    policies = set()
    seen_games = set()
    valid_games = []

    for g in games:
        if g.get("invalid") or g.get("crash") or g.get("timeout"):
            continue
        gid = g.get("game_id")
        if gid:
            if gid in seen_games:
                continue
            seen_games.add(gid)
        valid_games.append(g)

        cp = g.get("candidate_policy_id")
        op = g.get("opponent_policy_id")
        if cp:
            policies.add(cp)
        if op:
            policies.add(op)

    sorted_policies = sorted(list(policies))
    if not sorted_policies:
        return {"status": "CONVERGED", "ratings": {}}

    # One policy only handling
    if len(sorted_policies) == 1:
        return {"status": "CONVERGED", "ratings": {sorted_policies[0]: 1500.0}}

    wins = defaultdict(float)
    matchups = defaultdict(int)

    for g in valid_games:
        cp = g.get("candidate_policy_id")
        op = g.get("opponent_policy_id")
        if not cp or not op or cp == op:
            continue
        winner = g.get("winner")
        matchups[(cp, op)] += 1
        matchups[(op, cp)] += 1
        if winner == "candidate":
            wins[cp] += 1.0
        elif winner == "opponent":
            wins[op] += 1.0
        else:
            wins[cp] += 0.5
            wins[op] += 0.5

    # Graph connectivity check
    if not check_connected(sorted_policies, matchups):
        raise SupportContractError("Disconnected rating graph detected")

    # Initialize strengths evenly
    p = {policy: 1.0 / len(sorted_policies) for policy in sorted_policies}

    converged = False
    for _ in range(max_iter):
        next_p = {}
        for i in sorted_policies:
            denom = 0.0
            for j in sorted_policies:
                if i == j:
                    continue
                denom += matchups[(i, j)] / max(1e-12, p[i] + p[j])

            denom += regularization
            numerator = wins[i] + regularization
            val = numerator / denom
            next_p[i] = max(1e-12, val)

        # Normalize strengths to sum to 1 to maintain scale
        sum_p = sum(next_p.values())
        if sum_p > 0:
            for k in next_p:
                next_p[k] /= sum_p

        # Calculate max diff after normalization
        max_diff = 0.0
        for i in sorted_policies:
            max_diff = max(max_diff, abs(next_p[i] - p[i]))

        p = next_p
        if max_diff < tol:
            converged = True
            break

    if not converged:
        return {"status": "NOT_CONVERGED", "ratings": {}}

    ratings = {}
    for policy, weight in p.items():
        w = max(1e-12, weight)
        rating_val = 400.0 * math.log10(w * len(sorted_policies)) + 1500.0
        ratings[policy] = rating_val

    return {"status": "CONVERGED", "ratings": ratings}
