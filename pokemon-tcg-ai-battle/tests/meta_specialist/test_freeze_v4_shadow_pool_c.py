from __future__ import annotations

from pathlib import Path

from scripts.freeze_v4_shadow_pool_c import (
    SHADOW_C_IDS,
    build_payload,
    canonical_deck_sha256,
)


ROOT = Path(__file__).resolve().parents[2]


def test_shadow_c_freeze_is_deck_disjoint_and_records_shared_policy_limit() -> None:
    output = ROOT / "runs/meta-specialist-v4-shadow-pool-20260812-c/shadow_pool_manifest.json"
    payload = build_payload(output=output, frozen_at="2026-08-12T00:00:00+09:00")

    assert payload["selection_status"] == "frozen_untouched_shadow_c_not_yet_evaluated"
    assert [row["id"] for row in payload["candidates"]] == list(SHADOW_C_IDS)
    checks = payload["identity_checks"]
    assert checks["candidate_count"] == 6
    assert checks["deck_hash_unique_within_cohort"] is True
    assert checks["deck_hash_disjoint_from_prior_cohorts"] is True
    assert checks["policy_hash_disjoint_from_prior_cohorts"] is True
    assert checks["policy_hash_unique_within_cohort"] is False
    assert len(checks["shared_policy_groups"]) == 1
    assert checks["shared_policy_groups"][0]["ids"] == list(SHADOW_C_IDS)


def test_canonical_deck_hash_uses_sorted_card_ids() -> None:
    assert canonical_deck_sha256([3, 1, 2]) == canonical_deck_sha256([1, 3, 2])

