"""Public-safe synthetic data generator.

Provides deterministic data generation using seed for test scenarios.
"""

from __future__ import annotations
import random
from typing import Any

def generate_synthetic_game_results(count: int = 10, seed: int = 42) -> list[dict[str, Any]]:
    """Generate deterministic game result dictionaries."""
    rng = random.Random(seed)
    results = []

    for i in range(count):
        results.append({
            "game_id": f"game-syn-{i}",
            "winner": rng.choice(["candidate", "opponent", "draw"]),
            "candidate_seat": rng.choice([0, 1]),
            "candidate_policy_id": "pol_candidate_v1",
            "opponent_policy_id": f"pol_opponent_{rng.randint(1, 3)}",
            "candidate_deck_id": "deck_a",
            "opponent_deck_id": "deck_b",
            "seed": rng.randint(0, 10000),
            "candidate_legal_rate": 1.0,
            "candidate_fallback_count": 0
        })
    return results

def generate_synthetic_records(count: int = 20, seed: int = 42) -> list[dict[str, Any]]:
    """Generate deterministic dataset training records."""
    rng = random.Random(seed)
    records = []
    for i in range(count):
        records.append({
            "episode_id": f"ep-syn-{i // 5}",
            "decision_id": f"dec-syn-{i}",
            "state_digest": f"digest-{rng.randint(100, 999)}",
            "chosen_action": rng.choice(["action_a", "action_b", "action_c"]),
            "selection_type": rng.choice(["normal", "rare_select"]),
            "context_type": rng.choice(["normal", "rare_context"]),
            "split": "train"
        })
    return records
