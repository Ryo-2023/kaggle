"""Focused contracts for immutable C2a Knowledge Pack v0 snapshots."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from mage_ptcg.knowledge import (
    ACTION_KEY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    CompatibilityReport,
    DeckEntry,
    KnowledgeConfidence,
    KnowledgeManifest,
    KnowledgePack,
    KnowledgeValidationError,
    RoleTag,
    RuntimeCompatibility,
    TeamDeck,
    build_team_deck_pack,
    check_compatibility,
    content_hash,
    deck_identity_from_card_ids,
    load_pack,
    pack_from_payload,
    serialize_pack,
    write_pack,
)
from mage_ptcg.knowledge.loader import (
    DEFAULT_CABT_VERSION,
    DEFAULT_CARD_POOL_ID,
    DEFAULT_CARD_POOL_VERSION,
)


def _deck(path: Path, values: list[int]) -> Path:
    path.write_text("\n".join(str(value) for value in values) + "\n", encoding="utf-8")
    return path


def _pack(tmp_path: Path, values: list[int] | None = None) -> KnowledgePack:
    return build_team_deck_pack(_deck(tmp_path / "deck.csv", values or [1] * 60), source="fixture")


def _target(pack: KnowledgePack, deck_id: str | None = None) -> RuntimeCompatibility:
    return RuntimeCompatibility(
        schema_version=SCHEMA_VERSION,
        action_key_schema_version=ACTION_KEY_SCHEMA_VERSION,
        cabt_version=DEFAULT_CABT_VERSION,
        card_pool_id=DEFAULT_CARD_POOL_ID,
        card_pool_version=DEFAULT_CARD_POOL_VERSION,
        deck_id=deck_id or pack.team_deck.deck_id,
    )


def test_team_deck_build_is_complete_canonical_and_conservative(tmp_path: Path) -> None:
    pack = _pack(tmp_path, [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4 + [1158] + [1205] * 2 + [1227] * 4 + [1235] * 4)

    assert sum(entry.count for entry in pack.team_deck.entries) == 60
    assert [entry.card_id for entry in pack.team_deck.entries] == sorted(entry.card_id for entry in pack.team_deck.entries)
    assert {entry.role for entry in pack.team_deck.entries} == {RoleTag.FLEX}
    assert all(entry.role_confidence.support == 0.0 for entry in pack.team_deck.entries)


@pytest.mark.parametrize("size", [59, 61])
def test_team_deck_rejects_non_sixty_cards(tmp_path: Path, size: int) -> None:
    with pytest.raises(KnowledgeValidationError, match="exactly 60"):
        _pack(tmp_path, [1] * size)


@pytest.mark.parametrize("value", [0, -1, True])
def test_team_deck_rejects_invalid_card_ids(tmp_path: Path, value: object) -> None:
    values: list[object] = [1] * 59 + [value]
    _deck(tmp_path / "deck.csv", [int(item) for item in values])
    if value is True:
        # CSV text cannot preserve bool; model-level strictness covers the boolean case.
        confidence = KnowledgeConfidence(1.0, 0.0, 0.0)
        with pytest.raises(KnowledgeValidationError, match="card_id"):
            DeckEntry(True, 1, RoleTag.FLEX, confidence, "fixture")
    else:
        with pytest.raises(KnowledgeValidationError, match="positive"):
            build_team_deck_pack(tmp_path / "deck.csv", source="fixture")


def test_duplicate_entry_and_nonpositive_count_are_rejected() -> None:
    confidence = KnowledgeConfidence(1.0, 0.0, 0.0)
    with pytest.raises(KnowledgeValidationError, match="positive int"):
        DeckEntry(1, 0, RoleTag.FLEX, confidence, "fixture")
    entries = (
        DeckEntry(1, 30, RoleTag.FLEX, confidence, "fixture"),
        DeckEntry(1, 30, RoleTag.FLEX, confidence, "fixture"),
    )
    with pytest.raises(KnowledgeValidationError, match="strictly sorted"):
        TeamDeck("deck-any", entries)


def test_confidence_and_required_metadata_validation(tmp_path: Path) -> None:
    assert KnowledgeConfidence(1.0, 0.5, 0.0).to_payload()["support"] == 0.5
    with pytest.raises(KnowledgeValidationError, match="within"):
        KnowledgeConfidence(1.1, 0.0, 0.0)
    pack = _pack(tmp_path)
    payload = pack.to_payload()
    del payload["manifest"]["source"]
    with pytest.raises(KnowledgeValidationError, match="manifest"):
        pack_from_payload(payload)
    payload = pack.to_payload()
    payload["manifest"]["schema_version"] = "future"
    with pytest.raises(KnowledgeValidationError, match="schema_version"):
        pack_from_payload(payload)


def test_round_trip_hash_tamper_and_immutable_independent_loads(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    path = tmp_path / "pack.json"
    write_pack(pack, path)
    first = load_pack(path)
    second = load_pack(path)
    assert serialize_pack(first) == serialize_pack(second)
    assert first.manifest.content_hash == second.manifest.content_hash
    assert first is not second and first.team_deck.entries is not second.team_deck.entries
    with pytest.raises(AttributeError):
        first.manifest.source = "mutated"  # type: ignore[misc]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["team_deck"]["entries"][0]["source_ref"] = "tampered"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(KnowledgeValidationError, match="content_hash"):
        load_pack(path)


def test_builder_is_order_independent_and_content_changes_change_hash(tmp_path: Path) -> None:
    first = build_team_deck_pack(_deck(tmp_path / "a.csv", [1] * 30 + [2] * 30), source="fixture")
    second = build_team_deck_pack(_deck(tmp_path / "b.csv", [2] * 30 + [1] * 30), source="fixture")
    changed = build_team_deck_pack(_deck(tmp_path / "c.csv", [1] * 29 + [2] * 31), source="fixture")
    assert serialize_pack(first) == serialize_pack(second)
    assert first.manifest.content_hash == second.manifest.content_hash
    assert first.manifest.content_hash != changed.manifest.content_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_key_schema_version", "wrong"),
        ("cabt_version", "wrong"),
        ("card_pool_id", "wrong"),
        ("deck_id", "deck-wrong"),
    ],
)
def test_compatibility_reports_each_mismatch(tmp_path: Path, field: str, value: str) -> None:
    pack = _pack(tmp_path)
    target = _target(pack)
    report = check_compatibility(pack, replace(target, **{field: value}))
    assert not report.compatible
    assert report.reasons[0].startswith(f"{field}:")


def test_schema_mismatch_is_reported_without_constructing_an_invalid_snapshot(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    report = check_compatibility(pack, replace(_target(pack), schema_version="wrong"))
    assert report == CompatibilityReport(False, report.reasons)
    assert report.reasons[0].startswith("schema_version:")


def test_deck_identity_rejects_boolean_and_is_order_independent() -> None:
    assert deck_identity_from_card_ids([1] * 30 + [2] * 30) == deck_identity_from_card_ids([2] * 30 + [1] * 30)
    with pytest.raises(KnowledgeValidationError, match=r"deck\[59\]"):
        deck_identity_from_card_ids([1] * 59 + [True])
