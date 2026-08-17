"""Population buckets with policy-hash-uniform sampling."""
from __future__ import annotations

from collections import defaultdict
import random
from typing import Any, Iterable


def validate_population(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values = list(entries); identities: set[tuple[str, str, str]] = set()
    for value in values:
        key = (str(value.get("policy_hash", "")), str(value.get("deck_hash", "")), str(value.get("source_lineage", "")))
        if not all(key) or key in identities: raise ValueError("population identity is missing or duplicated")
        identities.add(key)
    return values


def sample_bucketed(entries: Iterable[dict[str, Any]], *, weights: dict[str, float], seed: int) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in validate_population(entries): groups[str(value.get("bucket", "unknown"))].append(value)
    usable = {name: value for name, value in groups.items() if weights.get(name, 0.0) > 0}
    if not usable: raise ValueError("no weighted population bucket")
    rng = random.Random(seed); bucket = rng.choices(sorted(usable), weights=[weights[name] for name in sorted(usable)], k=1)[0]
    # First deduplicate by policy hash to make submission copies weight-neutral.
    by_policy: dict[str, dict[str, Any]] = {}
    for entry in sorted(usable[bucket], key=lambda item: str(item["opponent_id"])): by_policy.setdefault(str(entry["policy_hash"]), entry)
    return rng.choice([by_policy[key] for key in sorted(by_policy)])
