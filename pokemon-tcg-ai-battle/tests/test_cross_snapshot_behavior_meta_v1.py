from __future__ import annotations

import pytest

from mage_ptcg.opponent_ingest.cross_snapshot_behavior_meta_v1 import (
    CrossSnapshotBehaviorMetaError,
    _normalize_entries,
    _transform_entry,
    validate_cross_snapshot_lineage,
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
"""


def _comfey_source() -> bytes:
    return b"""COMFEY_LO_SELF_DECK_RESERVE = 4
def _comfey_lo_setup_priority(card_id, *, active):
    if active:
        return {
            COMFEY_LO_COMFEY: 1000,
            COMFEY_LO_MAWILE: 900,
            COMFEY_LO_MIMIKYU: 800,
        }.get(card_id, 0)
    return {
        COMFEY_LO_LITWICK: 1000,
        COMFEY_LO_COMFEY: 950,
        COMFEY_LO_DUNSPARCE: 900,
    }.get(card_id, 0)
"""


def test_normalize_rejects_duplicate_base_roots() -> None:
    entries = [
        {"base_root": "a", "family": "alakazam", "variant": "ABRA_FIRST", "label": "a"},
        {"base_root": "a", "family": "alakazam", "variant": "DUNSPARCE_FIRST", "label": "b"},
        {"base_root": "b", "family": "alakazam", "variant": "ABRA_FIRST", "label": "c"},
        {"base_root": "c", "family": "comfey", "variant": "DECKOUT_AGGRESSIVE_COMFEY", "label": "d"},
    ]

    with pytest.raises(CrossSnapshotBehaviorMetaError, match="base_root"):
        _normalize_entries(entries)


def test_normalize_rejects_unknown_family_and_requires_four_entries() -> None:
    entries = [
        {"base_root": "a", "family": "unknown", "variant": "X", "label": "a"},
        {"base_root": "b", "family": "alakazam", "variant": "ABRA_FIRST", "label": "b"},
        {"base_root": "c", "family": "alakazam", "variant": "DUNSPARCE_FIRST", "label": "c"},
    ]

    with pytest.raises(CrossSnapshotBehaviorMetaError, match="four"):
        _normalize_entries(entries)


def test_transform_entry_dispatches_alakazam_and_comfey() -> None:
    transformed, recipe = _transform_entry(
        _alakazam_source(), family="alakazam", variant="ABRA_FIRST"
    )
    assert b"ABRA: 700" in transformed
    assert recipe.endswith("ALAKAZAM_BEHAVIOR_FAMILY_V1:ABRA_FIRST")

    transformed, recipe = _transform_entry(
        _comfey_source(), family="comfey", variant="DECKOUT_AGGRESSIVE_COMFEY"
    )
    assert b"COMFEY_LO_SELF_DECK_RESERVE = 2" in transformed
    assert recipe.endswith("COMFEY_FACTORIAL_BEHAVIOR_FAMILY_V1:DECKOUT_AGGRESSIVE+COMFEY_SETUP_FIRST")


def test_lineage_requires_three_distinct_source_commits() -> None:
    entries = [
        {"base_candidate_id": "a", "source_commit": "1"},
        {"base_candidate_id": "b", "source_commit": "1"},
        {"base_candidate_id": "c", "source_commit": "2"},
        {"base_candidate_id": "d", "source_commit": "2"},
    ]

    with pytest.raises(CrossSnapshotBehaviorMetaError, match="source commits"):
        validate_cross_snapshot_lineage(entries)
