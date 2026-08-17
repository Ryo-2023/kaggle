from __future__ import annotations

import pytest

from mage_ptcg.opponent_ingest.stratified_behavior_meta_v2 import (
    StratifiedBehaviorMetaError,
    _normalize_entries,
    _transform_entry,
    validate_stratified_lineage,
)


def _alakazam_source() -> bytes:
    return b"""POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}
SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 300,
    ABRA: 200,
    FEZANDIPITI_EX: 100,
}
ITEM_PRIORITY = {
    BUDDY_BUDDY_POFFIN: 300,
    POKE_PAD: 200,
    RARE_CANDY: 100,
}
"""


def _metal_source() -> bytes:
    return b"""import os
SEARCH_NUM_WORLDS = 3
SEARCH_LOCAL_FIXED_BUDGET = float(os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "1.0"))
POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 630,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}
_PLAN_CONFLICT_PRIORITY = [PLAN_ALAKAZAM, PLAN_EX_BLOCKER, PLAN_DRAGAPULT, PLAN_LUCARIO, PLAN_DARK]
"""


def _entries() -> list[dict[str, str]]:
    return [
        {"base_root": "a", "family": "alakazam", "variant": "ABRA_FIRST", "label": "a", "split": "META_TRAIN"},
        {"base_root": "b", "family": "comfey", "variant": "DECKOUT_AGGRESSIVE_COMFEY", "label": "b", "split": "META_TRAIN"},
        {"base_root": "c", "family": "psychic", "variant": "ZACIAN_FIRST", "label": "c", "split": "META_TRAIN"},
        {"base_root": "d", "family": "festival", "variant": "ALAKAZAM_FIRST", "label": "d", "split": "META_TRAIN"},
        {"base_root": "e", "family": "metal", "variant": "RULE_ONLY_PIPLUP_FIRST", "label": "e", "split": "META_TRAIN"},
        {"base_root": "f", "family": "alakazam", "variant": "DUNSPARCE_FIRST", "label": "f", "split": "META_DEV"},
        {"base_root": "g", "family": "psychic", "variant": "XERNEAS_FIRST", "label": "g", "split": "META_DEV"},
        {"base_root": "h", "family": "alakazam", "variant": "FEZANDIPITI_DRAW_FIRST", "label": "h", "split": "META_FINAL"},
        {"base_root": "i", "family": "festival", "variant": "SHAYMIN_SETUP_FIRST", "label": "i", "split": "META_FINAL"},
    ]


def test_normalize_requires_explicit_balanced_split_fields() -> None:
    entries = _entries()
    normalized = _normalize_entries(entries)
    assert normalized[0]["split"] == "META_TRAIN"

    bad = [dict(item) for item in entries]
    bad[-1]["split"] = "META_DEV"
    with pytest.raises(StratifiedBehaviorMetaError, match="family"):
        _normalize_entries(bad)


def test_normalize_rejects_duplicate_base_and_unknown_split() -> None:
    entries = _entries()
    entries[1]["base_root"] = entries[0]["base_root"]
    with pytest.raises(StratifiedBehaviorMetaError, match="base_root"):
        _normalize_entries(entries)

    entries = _entries()
    entries[0]["split"] = "META_HOLDOUT"
    with pytest.raises(StratifiedBehaviorMetaError, match="split"):
        _normalize_entries(entries)


def test_transform_dispatches_supported_families_and_runtime_safe_metal() -> None:
    transformed, recipe = _transform_entry(
        _alakazam_source(), family="alakazam", variant="ABRA_FIRST"
    )
    assert b"ABRA: 700" in transformed
    assert recipe.endswith("ALAKAZAM_BEHAVIOR_FAMILY_V1:ABRA_FIRST")

    transformed, recipe = _transform_entry(
        _metal_source(), family="metal", variant="RULE_ONLY_PIPLUP_FIRST"
    )
    assert b"SEARCH_NUM_WORLDS = 0" in transformed
    assert b'SEARCH_LOCAL_FIXED_BUDGET = float(os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "0.0"))' in transformed
    assert recipe.endswith("METAL_RUNTIME_SAFE_BEHAVIOR_FAMILY_V1:RULE_ONLY_PIPLUP_FIRST")


def test_lineage_requires_unique_source_commits_and_family_coverage() -> None:
    rows = [
        {"base_candidate_id": "a", "source_commit": "1", "source_family": "alakazam", "split": "META_TRAIN"},
        {"base_candidate_id": "b", "source_commit": "2", "source_family": "comfey", "split": "META_TRAIN"},
        {"base_candidate_id": "c", "source_commit": "3", "source_family": "psychic", "split": "META_TRAIN"},
        {"base_candidate_id": "d", "source_commit": "4", "source_family": "festival", "split": "META_TRAIN"},
        {"base_candidate_id": "e", "source_commit": "5", "source_family": "metal", "split": "META_TRAIN"},
        {"base_candidate_id": "f", "source_commit": "6", "source_family": "alakazam", "split": "META_DEV"},
        {"base_candidate_id": "g", "source_commit": "7", "source_family": "psychic", "split": "META_DEV"},
        {"base_candidate_id": "h", "source_commit": "8", "source_family": "alakazam", "split": "META_FINAL"},
        {"base_candidate_id": "i", "source_commit": "9", "source_family": "festival", "split": "META_FINAL"},
    ]
    report = validate_stratified_lineage(rows)
    assert report["split_counts"] == {"META_TRAIN": 5, "META_DEV": 2, "META_FINAL": 2}
    assert report["split_family_counts"]["META_TRAIN"] == {
        "alakazam": 1,
        "comfey": 1,
        "festival": 1,
        "metal": 1,
        "psychic": 1,
    }

    rows[-1]["source_commit"] = rows[-2]["source_commit"]
    with pytest.raises(StratifiedBehaviorMetaError, match="source commit"):
        validate_stratified_lineage(rows)
