"""Experiment comparison and statistical significance module.

Implements paired comparisons, exact binomial tests, bootstrap intervals,
Holm correction, and effect size calculations without SciPy.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Sequence

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError

class ExperimentComparer:
    """Compares win/loss rates between candidate policies across paired game seeds."""

    def exact_binomial_test(self, successes: int, trials: int) -> float:
        """Calculate two-sided exact binomial test p-value for p=0.5."""
        if trials == 0:
            return 1.0
        # If successes is exactly half, p-value is 1.0
        if successes * 2 == trials:
            return 1.0

        k = min(successes, trials - successes)
        # Sum of combinatorics
        p_val = 0.0
        for i in range(k + 1):
            p_val += math.comb(trials, i) * (0.5 ** trials)
        return min(1.0, 2.0 * p_val)

    def apply_holm_correction(self, p_values: list[float]) -> list[float]:
        """Apply Holm-Bonferroni correction to a list of raw p-values."""
        n = len(p_values)
        if n == 0:
            return []

        # Keep track of original indices
        indexed_p = sorted(enumerate(p_values), key=lambda x: x[1])
        adjusted = [0.0] * n

        max_adj = 0.0
        for rank, (orig_idx, p) in enumerate(indexed_p):
            # Holm formula: adjusted p_i = p_i * (n - rank)
            adj = p * (n - rank)
            max_adj = max(max_adj, adj)
            adjusted[orig_idx] = min(1.0, max_adj)

        return adjusted

    def compare_paired(
        self,
        games_a: Sequence[dict[str, Any]],
        games_b: Sequence[dict[str, Any]],
        confidence: float = 0.95,
        num_bootstrap: int = 1000,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Compare two game results sets that should be paired by seed and seat."""
        if not (0.0 < confidence < 1.0):
            raise SupportContractError(f"Invalid confidence: {confidence}")

        # Pair games by (seed, candidate_deck_id, opponent_policy_id, candidate_seat)
        def get_pair_key(g: dict[str, Any]) -> tuple[Any, ...]:
            return (
                g.get("seed"),
                g.get("candidate_deck_id"),
                g.get("opponent_policy_id"),
                g.get("candidate_seat"),
            )

        pairs_a = {}
        for g in games_a:
            if g.get("invalid") or g.get("crash") or g.get("timeout"):
                continue
            k = get_pair_key(g)
            if k in pairs_a:
                # Duplicate pair check
                raise SupportContractError(f"Duplicate key in policy A games: {k}")
            pairs_a[k] = g

        pairs_b = {}
        for g in games_b:
            if g.get("invalid") or g.get("crash") or g.get("timeout"):
                continue
            k = get_pair_key(g)
            if k in pairs_b:
                raise SupportContractError(f"Duplicate key in policy B games: {k}")
            pairs_b[k] = g

        # Match pairs
        common_keys = set(pairs_a.keys()) & set(pairs_b.keys())
        unpaired_a = len(pairs_a) - len(common_keys)
        unpaired_b = len(pairs_b) - len(common_keys)

        if not common_keys:
            return {
                "status": "INVALID_COMPARISON",
                "message": "Zero paired game outcomes found between A and B.",
                "unpaired_a": unpaired_a,
                "unpaired_b": unpaired_b,
            }

        warnings = []
        if unpaired_a > 0 or unpaired_b > 0:
            warnings.append(f"Unpaired records present: A={unpaired_a}, B={unpaired_b}")

        # Extract wins, losses, draws
        wins_a = 0
        wins_b = 0
        draws = 0

        paired_list = []
        seat_counts = defaultdict(int)

        for k in sorted(list(common_keys)):
            ga = pairs_a[k]
            gb = pairs_b[k]

            seat = k[3]
            seat_counts[seat] += 1

            # winner 'candidate' -> policy won
            win_a = 1.0 if ga["winner"] == "candidate" else (0.5 if ga["winner"] == "draw" else 0.0)
            win_b = 1.0 if gb["winner"] == "candidate" else (0.5 if gb["winner"] == "draw" else 0.0)

            paired_list.append((win_a, win_b, seat))

            if win_a > win_b:
                wins_a += 1
            elif win_b > win_a:
                wins_b += 1
            else:
                draws += 1

        total_pairs = len(paired_list)

        # Seat imbalance check
        if len(seat_counts) > 1:
            seats = list(seat_counts.values())
            if abs(seats[0] - seats[1]) > 1:
                warnings.append(f"Seat imbalance warning: candidate_seat counts are {dict(seat_counts)}")

        # Exact binomial test (excluding ties)
        non_ties = wins_a + wins_b
        p_val = self.exact_binomial_test(wins_a, non_ties)

        # Effect size (mean difference)
        diffs = [wa - wb for wa, wb, _ in paired_list]
        mean_diff = sum(diffs) / total_pairs

        # Paired stratified bootstrap for Confidence Interval
        # Stratify pairs by candidate_seat
        strata = defaultdict(list)
        for pair in paired_list:
            strata[pair[2]].append(pair)

        rng = random.Random(seed)
        boot_diffs = []
        for _ in range(num_bootstrap):
            resampled_diffs = []
            for seat, stratum_pairs in strata.items():
                n = len(stratum_pairs)
                resamples = [rng.choice(stratum_pairs) for _ in range(n)]
                for wa, wb, _ in resamples:
                    resampled_diffs.append(wa - wb)
            boot_diffs.append(sum(resampled_diffs) / len(resampled_diffs))

        boot_diffs.sort()
        lower_idx = max(0, int(num_bootstrap * ((1.0 - confidence) / 2.0)))
        upper_idx = min(num_bootstrap - 1, int(num_bootstrap * (1.0 - (1.0 - confidence) / 2.0)))
        ci_lower = boot_diffs[lower_idx]
        ci_upper = boot_diffs[upper_idx]

        # Overall verdict
        alpha = 1.0 - confidence
        if total_pairs < 30:
            status = "INSUFFICIENT_EVIDENCE"
            warnings.append(f"Small sample warning: only {total_pairs} pairs available")
        elif p_val < alpha:
            if mean_diff > 0.0:
                status = "EVIDENCE_FAVORS_A"
            else:
                status = "EVIDENCE_FAVORS_B"
        else:
            status = "NO_CLEAR_DIFFERENCE"

        return {
            "status": status,
            "total_pairs": total_pairs,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "draws": draws,
            "raw_p_value": p_val,
            "effect_size": mean_diff,
            "confidence_interval": [ci_lower, ci_upper],
            "unpaired_a": unpaired_a,
            "unpaired_b": unpaired_b,
            "warnings": warnings,
        }
