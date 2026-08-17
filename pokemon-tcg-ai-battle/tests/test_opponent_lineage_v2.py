from __future__ import annotations

from mage_ptcg.opponents.lineage_v2 import population_split, retired_registry


def test_retired_overlay_is_explicitly_non_reusable() -> None:
    rows = {row["policy_or_overlay_id"]: row for row in retired_registry()}
    assert rows["overlay-atomic-5a3d1ec99c5c"]["lifecycle_status"] == "RETIRED_REPEATABILITY_GATE_FAIL"
    assert "excluded" in str(rows["overlay-atomic-5a3d1ec99c5c"]["reuse_prohibition"])


def test_population_split_uses_lineages_not_decks_or_adapters() -> None:
    records = [{"lineage_id": f"l{i}", "qualification_status": "QUALIFIED_GENUINE", "category": "GENUINE_LOCAL_LINEAGE", "deck_hash": f"d{i}"} for i in range(4)]
    split = population_split(records)
    assert [row["split"] for row in split] == ["SEARCH", "SEARCH", "VALIDATION", "SEALED_HOLDOUT"]
    assert len({row["lineage_id"] for row in split}) == 4
