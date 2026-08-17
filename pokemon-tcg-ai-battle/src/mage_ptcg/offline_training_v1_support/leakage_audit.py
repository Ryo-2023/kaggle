"""Data leakage audit utility for dataset splits.

Identifies overlaps in episodes, decisions, and digests across training and validation/test splits.
"""

from __future__ import annotations
from typing import Any

def audit_split_leakage(
    train_records: list[dict[str, Any]],
    val_records: list[dict[str, Any]],
    test_records: list[dict[str, Any]] = None
) -> dict[str, Any]:
    """Audit train, validation, and test datasets for duplicate episodes, decisions, or exact duplicates."""
    test_records = test_records or []

    train_episodes = {r["episode_id"] for r in train_records if "episode_id" in r}
    val_episodes = {r["episode_id"] for r in val_records if "episode_id" in r}
    test_episodes = {r["episode_id"] for r in test_records if "episode_id" in r}

    train_decisions = {r["decision_id"] for r in train_records if "decision_id" in r}
    val_decisions = {r["decision_id"] for r in val_records if "decision_id" in r}
    test_decisions = {r["decision_id"] for r in test_records if "decision_id" in r}

    # Calculate overlap
    train_val_ep_overlap = train_episodes & val_episodes
    train_test_ep_overlap = train_episodes & test_episodes
    val_test_ep_overlap = val_episodes & test_episodes

    train_val_dec_overlap = train_decisions & val_decisions
    train_test_dec_overlap = train_decisions & test_decisions

    # Near-duplicates checking via representation hash or string comparison
    def get_sig(r: dict[str, Any]) -> str:
        clean = {k: v for k, v in r.items() if k not in ("token", "api_key", "password", "game_id")}
        return str(sorted(clean.items()))

    train_sigs = {get_sig(r) for r in train_records}
    val_sigs = {get_sig(r) for r in val_records}

    sig_overlap = train_sigs & val_sigs

    leakage_detected = (
        len(train_val_ep_overlap) > 0 or
        len(train_test_ep_overlap) > 0 or
        len(train_val_dec_overlap) > 0 or
        len(train_test_dec_overlap) > 0 or
        len(sig_overlap) > 0
    )

    issues = []
    if train_val_ep_overlap:
        issues.append(f"Episode overlap: {len(train_val_ep_overlap)} shared episodes between train and validation")
    if train_val_dec_overlap:
        issues.append(f"Decision overlap: {len(train_val_dec_overlap)} shared decisions between train and validation")
    if sig_overlap:
        issues.append(f"Semantic near-duplicates: {len(sig_overlap)} shared record signatures between train and validation")

    return {
        "leakage_detected": leakage_detected,
        "episode_overlaps": {
            "train_val": list(train_val_ep_overlap)[:10],
            "train_test": list(train_test_ep_overlap)[:10],
            "val_test": list(val_test_ep_overlap)[:10]
        },
        "decision_overlaps": {
            "train_val": list(train_val_dec_overlap)[:10],
            "train_test": list(train_test_dec_overlap)[:10]
        },
        "signature_overlaps_count": len(sig_overlap),
        "issues": issues
    }
