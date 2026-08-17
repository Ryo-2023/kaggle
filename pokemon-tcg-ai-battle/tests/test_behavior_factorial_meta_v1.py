from __future__ import annotations

import pytest

from mage_ptcg.opponent_ingest.derived_internal_meta_v1 import DerivedInternalMetaError
from mage_ptcg.opponent_ingest.behavior_factorial_meta_v1 import (
    ALAKAZAM_FACTORIAL_VARIANTS_V1,
    COMFEY_FACTORIAL_VARIANTS_V1,
    _replace_alakazam_factorial_behavior,
    _replace_comfey_factorial_behavior,
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


def test_factorial_transform_composes_two_disjoint_axes() -> None:
    transformed, recipe = _replace_alakazam_factorial_behavior(_alakazam_source(), "ABRA_POFFIN")

    assert ALAKAZAM_FACTORIAL_VARIANTS_V1 == (
        "ABRA_POFFIN",
        "ABRA_FEZANDIPITI",
        "DUNSPARCE_POFFIN",
        "DUNSPARCE_FEZANDIPITI",
    )
    assert recipe.endswith(":ABRA_FIRST+POFFIN_FIRST")
    assert transformed != _alakazam_source()
    assert b"ALAKAZAM: 400" in transformed
    assert b"ABRA: 700" in transformed
    assert b"BUDDY_BUDDY_POFFIN: 600" in transformed


def test_factorial_transform_composes_setup_axis() -> None:
    transformed, recipe = _replace_alakazam_factorial_behavior(_alakazam_source(), "DUNSPARCE_FEZANDIPITI")

    assert recipe.endswith(":DUNSPARCE_FIRST+FEZANDIPITI_DRAW_FIRST")
    assert b"DUNSPARCE: 700" in transformed
    assert b"FEZANDIPITI_EX: 500" in transformed


def test_factorial_transform_rejects_unknown_or_ambiguous_variant() -> None:
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_factorial_behavior(_alakazam_source(), "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_factorial_behavior(_alakazam_source(), "ABRA_FIRST+POFFIN_FIRST")


def test_factorial_transform_fails_closed_on_duplicate_source_tables() -> None:
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_factorial_behavior(_alakazam_source() + _alakazam_source(), "ABRA_POFFIN")


def test_comfey_factorial_transform_composes_reserve_and_setup_axes() -> None:
    transformed, recipe = _replace_comfey_factorial_behavior(_comfey_source(), "DECKOUT_AGGRESSIVE_COMFEY")

    assert COMFEY_FACTORIAL_VARIANTS_V1 == (
        "DECKOUT_AGGRESSIVE_COMFEY",
        "DECKOUT_AGGRESSIVE_LITWICK",
        "DECKOUT_CONSERVATIVE_COMFEY",
        "DECKOUT_CONSERVATIVE_LITWICK",
    )
    assert recipe.endswith(":DECKOUT_AGGRESSIVE+COMFEY_SETUP_FIRST")
    assert b"COMFEY_LO_SELF_DECK_RESERVE = 2" in transformed
    assert b"COMFEY_LO_MAWILE: 1000" in transformed


def test_comfey_factorial_transform_rejects_unknown_variant() -> None:
    with pytest.raises(DerivedInternalMetaError):
        _replace_comfey_factorial_behavior(_comfey_source(), "DECKOUT_AGGRESSIVE")
