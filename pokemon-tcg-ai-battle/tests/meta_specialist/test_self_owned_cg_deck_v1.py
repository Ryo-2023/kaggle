"""Tests for official-data-only self-owned deck generation."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (
    SelfOwnedDeckSpecV1,
    SelfOwnedDeckV1Error,
    canonical_deck_sha256_v1,
    generate_self_owned_deck_v1,
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
    validate_self_owned_deck_v1,
)


ROOT = Path(__file__).resolve().parents[2]
CARD_DB = ROOT / "data/raw/EN_Card_Data.csv"
SPEC_PATH = ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v1.json"


def test_catalog_and_spec_bind_to_official_1267_card_vocabulary() -> None:
    catalog = load_card_catalog_v1(CARD_DB)
    spec = load_self_owned_deck_spec_v1(SPEC_PATH)

    assert len(catalog.cards_by_id) == 1267
    assert spec.archetype_id == "fighting-lucario-scratch-v1"
    assert sum(role.count for role in spec.roles) == 60


def test_same_seed_is_reproducible_and_different_seed_changes_order() -> None:
    catalog = load_card_catalog_v1(CARD_DB)
    spec = load_self_owned_deck_spec_v1(SPEC_PATH)

    first = generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=11, ordinal=0)
    repeat = generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=11, ordinal=0)
    other = generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=12, ordinal=0)

    assert first.card_ids == repeat.card_ids
    assert first.canonical_deck_sha256 == repeat.canonical_deck_sha256
    assert first.card_ids != other.card_ids
    assert first.canonical_deck_sha256 == other.canonical_deck_sha256
    assert first.parent_deck is None
    assert first.research_only is True


def test_generated_deck_has_60_cards_and_exactly_one_ace_spec() -> None:
    catalog = load_card_catalog_v1(CARD_DB)
    spec = load_self_owned_deck_spec_v1(SPEC_PATH)
    candidate = generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=17, ordinal=2)

    validate_self_owned_deck_v1(candidate.card_ids, catalog, spec)
    assert len(candidate.card_ids) == 60
    assert sum(catalog.cards_by_id[card_id].is_ace_spec for card_id in candidate.card_ids) == 1
    assert canonical_deck_sha256_v1(candidate.card_ids) == candidate.canonical_deck_sha256
    counts_by_name = Counter(
        catalog.cards_by_id[card_id].name
        for card_id in candidate.card_ids
        if not catalog.cards_by_id[card_id].is_basic_energy
    )
    assert max(counts_by_name.values()) <= 4


def test_unknown_id_and_canonical_collision_fail_closed() -> None:
    catalog = load_card_catalog_v1(CARD_DB)
    spec = load_self_owned_deck_spec_v1(SPEC_PATH)
    candidate = generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=19, ordinal=0)

    with pytest.raises(SelfOwnedDeckV1Error, match="unknown card"):
        validate_self_owned_deck_v1(candidate.card_ids[:-1] + (99999,), catalog, spec)

    with pytest.raises(SelfOwnedDeckV1Error, match="canonical deck collision"):
        generate_self_owned_deck_v1(
            catalog=catalog,
            spec=spec,
            seed=19,
            ordinal=0,
            forbidden_canonical_hashes={candidate.canonical_deck_sha256},
        )


def test_spec_rejects_wrong_role_total(tmp_path: Path) -> None:
    payload = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    payload["roles"] = payload["roles"][:-1]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SelfOwnedDeckV1Error, match="60"):
        load_self_owned_deck_spec_v1(bad)


def test_canonical_hash_rejects_bool_card_id() -> None:
    with pytest.raises(SelfOwnedDeckV1Error):
        canonical_deck_sha256_v1([True])
