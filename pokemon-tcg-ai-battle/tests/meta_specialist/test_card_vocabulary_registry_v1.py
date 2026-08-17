"""The production card-vocabulary gate: only the registry-issued object may pass.

This gate is the single thing preventing a submission bundle from claiming a
qualified card vocabulary that was never qualified, so the forgery cases below
are the point of the module, not incidental coverage.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    SpecialistFeatureError,
    make_test_card_vocabulary_v1,
    require_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    CardVocabularyRegistryError,
    load_production_card_vocabulary_v1,
)

# Tampering is caught by the registry loader, revocation by the feature gate.
REVOKED = (SpecialistFeatureError, CardVocabularyRegistryError)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "configs/meta_specialist/card_vocabulary_registry_v1.json"
PINNED_CARD_DATABASE_SHA256 = (
    "a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373"
)
PINNED_CARD_IDS = frozenset(range(1, 1268))
_CSV_CANDIDATES = (
    ROOT / "data/raw/EN_Card_Data.csv",
    ROOT.parent.parent / "pokemon-tcg-ai-battle/data/raw/EN_Card_Data.csv",
)


def _csv_path() -> Path:
    for candidate in _CSV_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("EN card database is not available in this environment")


def _document() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_pins_the_exact_card_database_bytes() -> None:
    document = _document()
    actual = hashlib.sha256(_csv_path().read_bytes()).hexdigest()

    assert actual == PINNED_CARD_DATABASE_SHA256
    assert document["card_database_sha256"] == actual


def test_the_issued_vocabulary_is_production_qualified() -> None:
    vocabulary = load_production_card_vocabulary_v1()

    assert require_production_card_vocabulary_v1(vocabulary) is vocabulary
    assert vocabulary.test_only is False
    assert vocabulary.recognized_card_ids == PINNED_CARD_IDS
    assert vocabulary.source_sha256 == PINNED_CARD_DATABASE_SHA256


def test_a_test_only_vocabulary_never_qualifies() -> None:
    with pytest.raises(SpecialistFeatureError, match="test-only"):
        require_production_card_vocabulary_v1(make_test_card_vocabulary_v1(()))


def test_a_forged_vocabulary_never_qualifies_even_with_identical_fields() -> None:
    issued = load_production_card_vocabulary_v1()
    fresh = CardVocabularyV1(
        recognized_card_ids=issued.recognized_card_ids,
        source_sha256=issued.source_sha256,
        environment_version=issued.environment_version,
        usage_decision=issued.usage_decision,
        test_only=False,
        permission_decision=issued.permission_decision,
    )
    for name, forged in (
        ("copy", copy.copy(issued)),
        ("deepcopy", copy.deepcopy(issued)),
        ("replace", replace(issued)),
        ("fresh", fresh),
    ):
        assert forged is not issued, name
        assert forged == issued or name == "fresh"  # byte-identical fields
        with pytest.raises(SpecialistFeatureError, match="issued by the trusted"):
            require_production_card_vocabulary_v1(forged)


def test_mutating_an_issued_vocabulary_revokes_it() -> None:
    issued = load_production_card_vocabulary_v1()
    object.__setattr__(issued, "environment_version", "tampered")
    with pytest.raises(REVOKED):
        require_production_card_vocabulary_v1(issued)


def test_a_tampered_or_missing_registry_revokes_every_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = load_production_card_vocabulary_v1()
    assert require_production_card_vocabulary_v1(issued) is issued

    original = REGISTRY.read_bytes()
    document = _document()
    document["card_database_sha256"] = "b" * 64
    try:
        REGISTRY.write_bytes(json.dumps(document).encode("utf-8"))
        # The registry is re-read on every call, so a previously issued
        # vocabulary must stop qualifying the moment its registry changes.
        with pytest.raises(REVOKED):
            require_production_card_vocabulary_v1(issued)
        with pytest.raises(REVOKED):
            load_production_card_vocabulary_v1()
    finally:
        REGISTRY.write_bytes(original)

    assert require_production_card_vocabulary_v1(
        load_production_card_vocabulary_v1()
    ) is not None


def test_registry_document_is_self_verifying() -> None:
    document = _document()
    assert set(document) >= {"card_database_sha256", "card_vocabulary_sha256"}
    for field in ("card_database_sha256", "card_vocabulary_sha256"):
        value = document[field]
        assert isinstance(value, str) and len(value) == 64
        assert all(character in "0123456789abcdef" for character in value)
