"""Data quality profiler for offline training dataset.

Analyzes missing values, duplicate rates, constant fields, and distributions safely.
"""

from __future__ import annotations
import math
from typing import Any

def profile_dataset(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Profile data quality and return aggregate stats and status."""
    if not records:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "record_count": 0,
            "issues": ["No records provided for profiling"]
        }

    record_count = len(records)

    # Track statistics
    decisions = set()
    episodes = set()
    null_counts: dict[str, int] = {}
    constant_candidate: dict[str, set[Any]] = {}

    dup_digests = 0
    seen_digests = set()

    conflicting_labels = 0
    decision_labels: dict[str, set[str]] = {}

    for r in records:
        # Core identification
        dec_id = r.get("decision_id")
        ep_id = r.get("episode_id")
        if dec_id:
            decisions.add(dec_id)
        if ep_id:
            episodes.add(ep_id)

        # Check nulls/missingness
        for k, v in r.items():
            if v is None:
                null_counts[k] = null_counts.get(k, 0) + 1
            constant_candidate.setdefault(k, set()).add(str(v))

        # Duplicate digests check (using simple str representation as fallback)
        r_copy = {k: v for k, v in r.items() if k not in ("token", "api_key", "password")}
        digest_str = str(sorted(r_copy.items()))
        if digest_str in seen_digests:
            dup_digests += 1
        seen_digests.add(digest_str)

        # Conflicting labels check
        chosen = r.get("chosen_action")
        if dec_id and chosen:
            decision_labels.setdefault(dec_id, set()).add(str(chosen))

    # Calculate conflict rate
    for dec_id, labels in decision_labels.items():
        if len(labels) > 1:
            conflicting_labels += 1

    # Find constant/near-constant fields
    constant_fields = []
    for k, values in constant_candidate.items():
        if len(values) == 1:
            constant_fields.append(k)

    # Status determination
    issues = []
    status = "PASS"

    dup_rate = dup_digests / record_count if record_count > 0 else 0.0
    conflict_rate = conflicting_labels / len(decisions) if decisions else 0.0

    if dup_rate > 0.1:
        issues.append(f"High duplicate rate: {dup_rate:.2%}")
        status = "PASS_WITH_WARNINGS"
    if conflict_rate > 0.05:
        issues.append(f"High conflicting label rate: {conflict_rate:.2%}")
        status = "FAIL"
    if null_counts.get("decision_id", 0) > 0:
        issues.append("Missing decision_id in some records")
        status = "FAIL"

    return {
        "status": status,
        "record_count": record_count,
        "episode_count": len(episodes),
        "decision_count": len(decisions),
        "duplicate_rate": dup_rate,
        "conflicting_label_rate": conflict_rate,
        "constant_fields": constant_fields,
        "null_counts": null_counts,
        "issues": issues
    }
