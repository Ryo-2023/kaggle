"""Task 5B: boot a specialist agent binding from an extracted bundle root."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.entrypoint import (
    EntrypointContractError,
    build_packaged_agent,
    load_specialist_bundle,
)
from mage_ptcg.meta_specialist.package import (
    BundleSpec,
    DependencyContractIds,
    build_specialist_archive,
    derive_entrypoint_contract_id,
    extract_verified_archive,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest


def _qualified_asset(source: Path, *, cards: tuple[int, ...] | None = None):
    cards = tuple(range(1, 61)) if cards is None else cards
    deck_path = source / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = DeckAssetInput.from_path(
        asset_id="entrypoint-fixture", archetype_id="entrypoint-fixture", path=deck_path,
        source_ref="https://example.invalid/decks/entrypoint-fixture.csv", source_commit="a" * 40,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="fixture-v1",
    )
    return qualify_deck_asset(
        asset, ArchetypeSpec("entrypoint-fixture", (), (cards[0],), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _: (True, "fixture-cabt-pass"),
    )


def _build_extracted_bundle(tmp_path: Path) -> Path:
    """Build a real static-rule-bundle archive from the actual templates and extract it."""
    source = tmp_path / "source"
    source.mkdir()
    qualified = _qualified_asset(source)

    templates = Path(__file__).resolve().parents[2] / "templates" / "meta_specialist"
    for name in ("main.py", "policy_loader.py", "rule_policy_v1.py"):
        (source / name).write_bytes((templates / name).read_bytes())

    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id, selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,), foundation_init_id="b" * 64,
        joint_race_schedule_id="c" * 64, equal_transition_budget=1,
    )
    members = ("deck.csv", "main.py", "policy_loader.py", "rule_policy_v1.py")
    dependency_ids = DependencyContractIds(
        cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
        runtime_constraints_id=constraints.runtime_constraints_id,
        ladder_mechanics_id=ladder["ladder_mechanics_id"],
        entrypoint_contract_id=derive_entrypoint_contract_id(source, members, policy_members=("rule_policy_v1.py",)),
    )
    policy_bytes = (source / "rule_policy_v1.py").read_bytes()
    policy_identity = content_id("meta-specialist-static-policy-v1", [
        {"path": "rule_policy_v1.py", "sha256": hashlib.sha256(policy_bytes).hexdigest(), "size": len(policy_bytes)},
    ])
    spec = BundleSpec(
        source_root=source, members=members, deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified, deck_lock=lock,
        runtime_constraints=constraints, ladder_mechanics=ladder, dependency_contract_ids=dependency_ids,
        candidate_class="static_rule_bundle", policy_members=("rule_policy_v1.py",), model_member=None,
        policy_identity=policy_identity, checkpoint_lineage_id=None,
        checkpoint_lineage_reason="not_applicable_static_policy",
    )
    archive_path = tmp_path / "bundle.tar.gz"
    build_specialist_archive(spec, archive_path)
    extracted = extract_verified_archive(archive_path, tmp_path / "extracted")
    assert isinstance(extracted, Path)
    return extracted


def test_load_specialist_bundle_reconstructs_every_runtime_fact(tmp_path: Path) -> None:
    root = _build_extracted_bundle(tmp_path)
    loaded = load_specialist_bundle(root)

    assert loaded.candidate_class == "static_rule_bundle"
    assert loaded.model_member is None
    assert loaded.policy_members == ("rule_policy_v1.py",)
    assert loaded.qualified_deck_asset.deck_identity == loaded.deck_lock.selected_deck_identity
    assert loaded.runtime_constraints.to_payload() == RuntimeConstraintManifest.frozen_v1().to_payload()
    assert len(loaded.policy_identity) == 64


def test_load_specialist_bundle_missing_deck_csv_raises_not_fabricates(tmp_path: Path) -> None:
    """Catches a prior failure mode: silently substituting a placeholder deck."""
    root = _build_extracted_bundle(tmp_path)
    (root / "deck.csv").unlink()

    with pytest.raises(EntrypointContractError, match="deck.csv"):
        load_specialist_bundle(root)


def test_load_specialist_bundle_short_deck_csv_raises(tmp_path: Path) -> None:
    root = _build_extracted_bundle(tmp_path)
    (root / "deck.csv").write_text("1\n2\n3\n", encoding="utf-8")

    with pytest.raises(EntrypointContractError):
        load_specialist_bundle(root)


def test_load_specialist_bundle_missing_manifest_raises(tmp_path: Path) -> None:
    root = _build_extracted_bundle(tmp_path)
    (root / "meta_specialist_bundle.json").unlink()

    with pytest.raises(EntrypointContractError, match="meta_specialist_bundle.json"):
        load_specialist_bundle(root)


def test_load_specialist_bundle_tampered_policy_identity_raises(tmp_path: Path) -> None:
    """A manifest edited to claim a different policy_identity than the on-disk bytes must fail closed."""
    root = _build_extracted_bundle(tmp_path)
    manifest_path = root / "meta_specialist_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy_identity"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(EntrypointContractError, match="policy_identity"):
        load_specialist_bundle(root)


def test_load_specialist_bundle_tampered_deck_lock_id_raises(tmp_path: Path) -> None:
    root = _build_extracted_bundle(tmp_path)
    manifest_path = root / "meta_specialist_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["deck_lock"]["deck_lock_id"] = "1" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(EntrypointContractError, match="deck_lock"):
        load_specialist_bundle(root)


def test_load_specialist_bundle_empty_cabt_evidence_raises(tmp_path: Path) -> None:
    """An emptied CABT evidence field must fail closed rather than replay a blank pass.

    ``cabt_legality_evidence`` is free text with no independent redundant
    anchor elsewhere in the frozen manifest (unlike deck bytes, policy bytes,
    or the deck lock's derived IDs, each cross-checked against bytes on disk
    above): the only failure mode this layer can detect for it is a missing
    or blank value.  Genuine fabrication is prevented earlier, at build time,
    by the CLI's ``qualify-deck`` command requiring a real, deck-bound,
    externally supplied CABT evidence file before qualification ever runs.
    """
    root = _build_extracted_bundle(tmp_path)
    manifest_path = root / "meta_specialist_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["qualified_deck_asset"]["cabt_legality_evidence"] = "   "
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(EntrypointContractError, match="cabt_legality_evidence"):
        load_specialist_bundle(root)


class _RejectingFactory:
    def new_policy(self):  # pragma: no cover - must never be reached
        raise AssertionError("policy factory must not be invoked before the vocabulary gate")


def test_build_packaged_agent_rejects_test_only_vocabulary(tmp_path: Path) -> None:
    root = _build_extracted_bundle(tmp_path)
    vocabulary = make_test_card_vocabulary_v1(range(1, 2000))

    with pytest.raises(Exception, match="test-only"):
        build_packaged_agent(root, vocabulary=vocabulary, policy_factory=_RejectingFactory())


def test_build_packaged_agent_never_bypasses_the_production_vocabulary_gate(tmp_path: Path) -> None:
    """P0 has no trusted sealed card-vocabulary registry yet; this must stay fail-closed.

    See ``entrypoint.py``'s module docstring: even a vocabulary shaped like a
    genuine production candidate (``test_only=False``,
    ``usage_decision="bundle_allowed"``) is still rejected, because
    ``require_production_card_vocabulary_v1`` unconditionally raises until a
    trusted registry exists.  This test would fail the moment someone quietly
    weakened that gate to accept a caller-asserted vocabulary.
    """
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import CardVocabularyV1

    root = _build_extracted_bundle(tmp_path)
    loaded = load_specialist_bundle(root)
    would_be_production_vocabulary = CardVocabularyV1(
        recognized_card_ids=frozenset(loaded.qualified_deck_asset.card_ids),
        source_sha256=loaded.qualified_deck_asset.deck_file_sha256,
        environment_version=loaded.qualified_deck_asset.card_database_version,
        usage_decision="bundle_allowed",
        test_only=False,
        permission_decision="bundle_allowed",
    )

    with pytest.raises(Exception, match="trusted sealed registry"):
        build_packaged_agent(root, vocabulary=would_be_production_vocabulary, policy_factory=_RejectingFactory())
