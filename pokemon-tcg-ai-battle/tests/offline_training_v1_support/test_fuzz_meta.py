"""Deterministic fuzz and metamorphic tests for support platform utilities."""

from __future__ import annotations
import random
import pytest
from mage_ptcg.offline_training_v1_support.contracts import canonical_json, digest, walk_safe
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics
from mage_ptcg.offline_training_v1_support.ratings import compute_elo, compute_bradley_terry

def test_metamorphic_round_trip():
    rng = random.Random(42)
    # 500 cases round-trip stability
    for i in range(500):
        val = {
            "id": i,
            "name": f"name_{rng.randint(0, 100)}",
            "nested": {
                "val": rng.random(),
                "flag": rng.choice([True, False])
            },
            "list": [rng.randint(0, 100) for _ in range(3)]
        }
        walk_safe(val)
        c1 = canonical_json(val)
        h1 = digest(val)

        # Parse and re-serialize
        import json
        parsed = json.loads(c1)
        c2 = canonical_json(parsed)
        h2 = digest(parsed)

        assert c1 == c2
        assert h1 == h2

def test_metamorphic_order_invariance_ratings():
    # Construct a guaranteed connected match topology: round robin between pol_1, pol_2, pol_3
    matches = []
    for i in range(5):
        matches.append({"game_id": f"g_{i}_1", "candidate_policy_id": "pol_1", "opponent_policy_id": "pol_2", "winner": "candidate"})
        matches.append({"game_id": f"g_{i}_2", "candidate_policy_id": "pol_1", "opponent_policy_id": "pol_2", "winner": "opponent"})
        matches.append({"game_id": f"g_{i}_3", "candidate_policy_id": "pol_2", "opponent_policy_id": "pol_3", "winner": "candidate"})
        matches.append({"game_id": f"g_{i}_4", "candidate_policy_id": "pol_2", "opponent_policy_id": "pol_3", "winner": "opponent"})
        matches.append({"game_id": f"g_{i}_5", "candidate_policy_id": "pol_3", "opponent_policy_id": "pol_1", "winner": "candidate"})
        matches.append({"game_id": f"g_{i}_6", "candidate_policy_id": "pol_3", "opponent_policy_id": "pol_1", "winner": "opponent"})

    # Shuffle matches
    rng = random.Random(999)
    matches_shuffled = list(matches)
    rng.shuffle(matches_shuffled)

    # BT matches rating must be order-invariant
    bt_orig = compute_bradley_terry(matches)["ratings"]
    bt_shuf = compute_bradley_terry(matches_shuffled)["ratings"]

    assert len(bt_orig) == 3
    # Use float comparison tolerance for BT
    for policy, rating in bt_orig.items():
        assert abs(rating - bt_shuf[policy]) < 1e-9

def test_metamorphic_seat_swapping():
    # Swap seat 0 and 1, and ensure statistical metrics are symmetrically swapped.
    games_0 = [
        {"game_id": "g1", "candidate_policy_id": "pol_a", "opponent_policy_id": "pol_b", "candidate_seat": 0, "winner": "candidate", "candidate_legal_rate": 1.0, "candidate_fallback_count": 0},
        {"game_id": "g2", "candidate_policy_id": "pol_a", "opponent_policy_id": "pol_b", "candidate_seat": 1, "winner": "opponent", "candidate_legal_rate": 1.0, "candidate_fallback_count": 0},
    ]

    games_1 = [
        {"game_id": "g1", "candidate_policy_id": "pol_a", "opponent_policy_id": "pol_b", "candidate_seat": 1, "winner": "candidate", "candidate_legal_rate": 1.0, "candidate_fallback_count": 0},
        {"game_id": "g2", "candidate_policy_id": "pol_a", "opponent_policy_id": "pol_b", "candidate_seat": 0, "winner": "opponent", "candidate_legal_rate": 1.0, "candidate_fallback_count": 0},
    ]

    stats_0 = evaluate_game_statistics(games_0)
    stats_1 = evaluate_game_statistics(games_1)

    assert stats_0["total_games"] == stats_1["total_games"]
    assert stats_0["wins"] == stats_1["wins"]
