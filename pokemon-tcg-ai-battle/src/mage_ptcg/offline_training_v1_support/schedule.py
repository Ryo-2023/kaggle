"""Deterministic paired evaluation schedule generator and validator.

Generates seat-balanced schedules and verifies execution results against them.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    canonical_json,
    digest,
)


def generate_schedule(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate a deterministic seat-balanced evaluation schedule from a config."""
    schema_version = config.get("schema_version")
    if schema_version != "support-schedule-config-v1":
        raise SupportContractError(f"Unsupported schedule config schema: {schema_version}")

    c_policies = sorted(config.get("candidate_policies", []))
    o_policies = sorted(config.get("opponent_policies", []))
    c_decks = sorted(config.get("candidate_decks", []))
    o_decks = sorted(config.get("opponent_decks", []))
    base_seed = int(config.get("base_seed", 42))
    repetitions = int(config.get("repetitions", 1))

    if not c_policies or not o_policies or not c_decks or not o_decks:
        raise SupportContractError("Schedules require non-empty policy and deck lists.")

    if repetitions <= 0:
        raise SupportContractError("Repetitions must be positive.")

    # Compute a config hash to verify configuration identity
    config_hash = digest({
        "candidate_policies": c_policies,
        "opponent_policies": o_policies,
        "candidate_decks": c_decks,
        "opponent_decks": o_decks,
        "base_seed": base_seed,
        "repetitions": repetitions,
    }, domain="schedule-config")

    games = []
    game_idx = 0

    # Stable nested loop to generate pairings deterministically
    for cp in c_policies:
        for op in o_policies:
            for cd in c_decks:
                for od in o_decks:
                    for rep in range(repetitions):
                        # Alternating seats for balance
                        # If repetitions is odd, the seat assignment diff remains <= 1
                        seat = 0 if (rep % 2 == 0) else 1

                        # Seed generation is deterministic
                        seed = base_seed + game_idx

                        games.append({
                            "candidate_policy_id": cp,
                            "opponent_policy_id": op,
                            "candidate_deck_id": cd,
                            "opponent_deck_id": od,
                            "candidate_seat": seat,
                            "repetition": rep,
                            "seed": seed,
                        })
                        game_idx += 1

    # Ensure stable ordering before hashing schedule
    # Add index and hash to each schedule game record
    schedule_hash = digest(games, domain="schedule-data")

    schedule_records = []
    for idx, g in enumerate(games):
        record = {
            "schema_version": "support-schedule-record-v1",
            "schedule_id": f"sch_{config_hash[:8]}_{idx:06d}",
            "game_index": idx,
            "seed": g["seed"],
            "candidate_policy_id": g["candidate_policy_id"],
            "opponent_policy_id": g["opponent_policy_id"],
            "candidate_deck_id": g["candidate_deck_id"],
            "opponent_deck_id": g["opponent_deck_id"],
            "candidate_seat": g["candidate_seat"],
            "repetition": g["repetition"],
            "schedule_hash": schedule_hash,
            "config_hash": config_hash,
        }
        schedule_records.append(record)

    return schedule_records


def match_key(record: dict[str, Any]) -> tuple:
    """Generate a lookup key to join schedules and actual game outcomes."""
    return (
        record.get("candidate_policy_id"),
        record.get("opponent_policy_id"),
        record.get("candidate_deck_id"),
        record.get("opponent_deck_id"),
        int(record.get("candidate_seat", 0)),
        int(record.get("seed", 0))
    )


def validate_games_against_schedule(
    schedule: list[dict[str, Any]], games: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Validate actual game outcomes against the planned evaluation schedule."""
    # Build schedule lookup map
    schedule_map = {}
    for sch_rec in schedule:
        key = match_key(sch_rec)
        if key in schedule_map:
            raise SupportContractError(f"Duplicate key in schedule: {key}")
        schedule_map[key] = sch_rec

    completed = {}
    duplicates = []
    unmatched = []

    for game in games:
        key = match_key(game)
        if key not in schedule_map:
            unmatched.append(game)
            continue

        if key in completed:
            duplicates.append({
                "key": list(key),
                "existing": completed[key]["game_id"],
                "duplicate": game["game_id"]
            })
            continue

        completed[key] = game

    missing = []
    for key, sch_rec in schedule_map.items():
        if key not in completed:
            missing.append(sch_rec)

    return {
        "total_scheduled": len(schedule),
        "total_completed": len(completed),
        "missing_count": len(missing),
        "duplicate_count": len(duplicates),
        "unmatched_count": len(unmatched),
        "missing": missing,
        "duplicates": duplicates,
        "unmatched": unmatched,
    }
