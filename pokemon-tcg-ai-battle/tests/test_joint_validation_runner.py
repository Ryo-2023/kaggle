from __future__ import annotations

from collections import Counter

from scripts import run_alakazam_joint_validation as validation


def test_validation_schedule_is_fixed_and_balances_sides_and_opponents() -> None:
    population = [
        {"opponent_id": f"opponent-{index}", "opponent_path": f"/snapshot/opponent-{index}", "opponent_policy_hash": f"policy-{index}", "opponent_deck_hash": f"deck-{index}", "deck_family": f"family-{index % 4}", "adapter_hash": "adapter", "qualification_evidence": "evidence"}
        for index in range(20)
    ]
    slots = validation._schedule(population, 256)

    assert len(slots) == 256
    assert Counter(slot["candidate_side"] for slot in slots) == {0: 128, 1: 128}
    counts = Counter(slot["opponent_id"] for slot in slots)
    assert set(counts.values()) == {12, 13}
    assert all(slot["schedule_id"] == "joint-validation-v1" for slot in slots)
    assert validation.CANDIDATES == (
        ("alakazam_baseline_v1--rule_v0", "alakazam_baseline_v1", "rule_v0"),
        ("alakazam_baseline_v1--rule_v1", "alakazam_baseline_v1", "rule_v1"),
        ("replay_453cdc7d2534--rule_v0", "replay_453cdc7d2534", "rule_v0"),
        ("replay_453cdc7d2534--rule_v1", "replay_453cdc7d2534", "rule_v1"),
    )
