"""Deterministic fuzzing tests harness.

Provides properties and randomized structural input testing using
standard Python random.Random(seed).
"""

from __future__ import annotations

import random
import math
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    canonical_json,
    digest,
    walk_safe,
    SupportContractError,
)
from mage_ptcg.offline_training_v1_support.schedule import generate_schedule
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics
from mage_ptcg.offline_training_v1_support.sampling import priority_sample

def run_fuzz_tests(seed: int = 42) -> dict[str, Any]:
    """Execute property-style deterministic fuzz check cases."""
    rng = random.Random(seed)
    errors = []

    # 1. 100-500 random canonical JSON cases
    for i in range(200):
        # Generate random nested structures
        val = {
            "str_key": f"val_{rng.randint(0, 1000)}",
            "int_key": rng.randint(-100000, 100000),
            "float_key": rng.random() * 100.0,
            "bool_key": rng.choice([True, False]),
            "list_key": [rng.randint(0, 10) for _ in range(5)],
            "dict_key": {
                "nested_str": "test",
                "nested_float": rng.random()
            }
        }
        try:
            walk_safe(val)
            c1 = canonical_json(val)
            # Ensure sorting order invariance
            shuffled_dict = dict(sorted(val.items(), key=lambda x: rng.random()))
            c2 = canonical_json(shuffled_dict)
            if c1 != c2:
                errors.append(f"Canonical JSON order mismatch at index {i}")
        except Exception as exc:
            errors.append(f"Canonical JSON validation error: {exc}")

    # 2. 100 random schedules
    for i in range(100):
        config = {
            "schema_version": "support-schedule-config-v1",
            "candidate_policies": [f"pol_{rng.randint(1, 5)}"],
            "opponent_policies": [f"pol_{rng.randint(6, 10)}"],
            "candidate_decks": [f"deck_{rng.randint(1, 5)}"],
            "opponent_decks": [f"deck_{rng.randint(6, 10)}"],
            "seats": [0, 1],
            "base_seed": rng.randint(0, 10000),
            "repetitions": rng.randint(1, 5),
        }
        try:
            sch = generate_schedule(config)
            # Property: balance seat difference <= 1
            seats = [item["candidate_seat"] for item in sch]
            s0 = seats.count(0)
            s1 = seats.count(1)
            if abs(s0 - s1) > 1:
                errors.append(f"Schedule seat imbalance > 1: {s0} vs {s1} at index {i}")
        except Exception as exc:
            errors.append(f"Schedule fuzz error: {exc}")

    # 3. 100 random game-result sets
    for i in range(100):
        games = []
        for j in range(rng.randint(1, 20)):
            games.append({
                "game_id": f"game_{i}_{j}",
                "candidate_policy_id": f"pol_{rng.randint(1, 3)}",
                "opponent_policy_id": f"pol_{rng.randint(4, 6)}",
                "candidate_deck_id": "deck_a",
                "opponent_deck_id": "deck_b",
                "candidate_seat": rng.choice([0, 1]),
                "seed": rng.randint(0, 1000),
                "winner": rng.choice(["candidate", "opponent", "draw"]),
                "candidate_legal_rate": rng.random() * 0.1 + 0.9,
                "candidate_fallback_count": rng.randint(0, 3),
            })
        try:
            stats = evaluate_game_statistics(games)
            if stats["total_games"] != len(games):
                errors.append(f"Game statistics count mismatch at index {i}")
        except Exception as exc:
            errors.append(f"Game stats fuzz error: {exc}")

    # 4. 100 random sampling inputs
    for i in range(100):
        records = []
        for j in range(50):
            records.append({
                "episode_id": f"ep_{j}",
                "decision_id": f"dec_{j}",
                "state_digest": f"s_{j}",
                "teacher_action_key": f"act_{rng.randint(1, 3)}",
                "student_action_key": f"act_{rng.randint(1, 3)}",
                "priority_score": rng.random() * 2.0,
                "student_confidence": rng.random(),
                "selection_type": rng.choice(["normal", "rare_select"]),
                "context_type": rng.choice(["normal", "rare_context"]),
            })
        weight_config = {
            "uniform": rng.random() * 2.0,
            "disagreement": rng.random() * 2.0,
            "hard_state_score": rng.random() * 2.0,
            "rare_selection_type": rng.random() * 2.0,
            "rare_context_type": rng.random() * 2.0,
            "teacher_confidence": rng.random() * 2.0,
            "held_out_error": rng.random() * 2.0,
            "runtime_fallback": rng.random() * 2.0,
        }
        try:
            sampled, manifest = priority_sample(records, weight_config, sampled_count=10, replacement=False, seed=rng.randint(0, 1000))
            if len(sampled) != 10:
                errors.append(f"Priority sampling size mismatch at index {i}")
        except Exception as exc:
            errors.append(f"Sampling fuzz error: {exc}")

    status = "SUCCESS" if not errors else "FAILED"
    return {
        "status": status,
        "errors_count": len(errors),
        "errors": errors[:10], # limit visible errors
    }
