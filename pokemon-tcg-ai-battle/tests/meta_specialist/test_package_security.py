"""Hostile-input tests for Task 5A structural archive primitives."""

from __future__ import annotations

import gzip
import io
from hashlib import sha256
from pathlib import Path
import tarfile

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest


def _write_one_member_archive(path: Path, *, name: str, payload: bytes = b"x") -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                info = tarfile.TarInfo(name)
                info.mode = 0o644
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))


@pytest.mark.parametrize("name", ("/abs.py", "../escape.py", "nested/../../escape.py", "bad\\path.py"))
def test_verifier_rejects_unsafe_member_path_before_manifest_parsing(
    tmp_path: Path, name: str,
) -> None:
    """Catches traversal and platform-separator paths before extraction is possible."""
    from mage_ptcg.meta_specialist.package import BundleSecurityError, verify_specialist_archive

    archive = tmp_path / "unsafe.tar.gz"
    _write_one_member_archive(archive, name=name)

    with pytest.raises(BundleSecurityError, match="unsafe member"):
        verify_specialist_archive(archive)


def test_verifier_rejects_trailing_or_concatenated_gzip_data(tmp_path: Path) -> None:
    """Catches a verifier accepting bytes outside one canonical gzip member."""
    from mage_ptcg.meta_specialist.package import BundleSecurityError, verify_specialist_archive

    archive = tmp_path / "trailing.tar.gz"
    _write_one_member_archive(archive, name="x.py")
    archive.write_bytes(archive.read_bytes() + gzip.compress(b"second", mtime=0))

    with pytest.raises(BundleSecurityError, match="gzip"):
        verify_specialist_archive(archive)


def test_builder_rejects_secret_marker_and_symlinked_ancestor(tmp_path: Path) -> None:
    """Catches source disclosure and resolution through a symlinked source root."""
    from dataclasses import replace

    from mage_ptcg.meta_specialist.package import (
        BundleSecurityError,
        BundleSpec,
        DependencyContractIds,
        derive_entrypoint_contract_id,
    )

    source = tmp_path / "source"
    source.mkdir()
    cards = tuple(range(1, 61))
    (source / "deck.csv").write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    (source / "main.py").write_text("agent = lambda observation, configuration: []\n", encoding="utf-8")
    (source / "policy_loader.py").write_text("# loader\n", encoding="utf-8")
    (source / "weights.bin").write_bytes(b"weights")
    deck_input = DeckAssetInput.from_path(
        asset_id="security-fixture", archetype_id="security-fixture", path=source / "deck.csv",
        source_ref="https://example.invalid/deck.csv", source_commit="d" * 40,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="fixture-v1",
    )
    qualified = qualify_deck_asset(
        deck_input, ArchetypeSpec("security-fixture", (), (1,), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _: (True, "fixture-cabt-pass"),
    )
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id, selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,), foundation_init_id="a" * 64,
        joint_race_schedule_id="b" * 64, equal_transition_budget=1,
    )
    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    members = ("deck.csv", "main.py", "policy_loader.py", "weights.bin")
    valid_bundle_spec = BundleSpec(
        source_root=source, members=members, deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified,
        deck_lock=lock, runtime_constraints=constraints, ladder_mechanics=ladder,
        dependency_contract_ids=DependencyContractIds(
            cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
            runtime_constraints_id=constraints.runtime_constraints_id,
            ladder_mechanics_id=ladder["ladder_mechanics_id"],
            entrypoint_contract_id=derive_entrypoint_contract_id(source, members),
        ), candidate_class="checkpointed_specialist", policy_members=("weights.bin",),
        model_member="weights.bin", policy_identity=sha256(b"weights").hexdigest(),
        checkpoint_lineage_id=lock.policy_lineage_id, checkpoint_lineage_reason=None,
    )

    (valid_bundle_spec.source_root / "main.py").write_text("KAGGLE_KEY = 'secret'\n", encoding="utf-8")
    with pytest.raises(BundleSecurityError, match="sensitive"):
        valid_bundle_spec.validate()
    (valid_bundle_spec.source_root / "main.py").write_text(
        "agent = lambda observation, configuration: []\n", encoding="utf-8",
    )

    linked = tmp_path / "linked-source"
    linked.symlink_to(valid_bundle_spec.source_root, target_is_directory=True)
    with pytest.raises(BundleSecurityError, match="symlink"):
        replace(valid_bundle_spec, source_root=linked).validate()

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(BundleSecurityError, match="symlink"):
        replace(valid_bundle_spec, source_root=linked_parent / "source").validate()
    (valid_bundle_spec.source_root / "main.py").write_text(
        "note = r'C:\\Users\\operator\\secret.txt'\n", encoding="utf-8",
    )
    with pytest.raises(BundleSecurityError, match="sensitive"):
        valid_bundle_spec.validate()


def test_verifier_rejects_nonregular_tar_members_before_any_manifest_trust(
    tmp_path: Path,
) -> None:
    """Catches symlink/device-style tar entries being treated as ordinary files."""
    from mage_ptcg.meta_specialist.package import BundleSecurityError, verify_specialist_archive

    archive_path = tmp_path / "link.tar.gz"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                info = tarfile.TarInfo("main.py")
                info.type = tarfile.SYMTYPE
                info.mode = 0o644
                info.linkname = "elsewhere.py"
                archive.addfile(info)

    with pytest.raises(BundleSecurityError, match="metadata"):
        verify_specialist_archive(archive_path)
