"""Structural specialist archive contracts (Task 5A only)."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_CONTRACT_SHA256_V1,
)
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec,
    DeckAssetInput,
    create_deck_lock,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest


def _qualified_asset(tmp_path: Path):
    cards = tuple(range(1, 61))
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = DeckAssetInput.from_path(
        asset_id="package-fixture",
        archetype_id="package-fixture",
        path=deck_path,
        source_ref="https://example.invalid/decks/package-fixture.csv",
        source_commit="a" * 40,
        asset_class="deck_only",
        usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2",
        card_database_version="fixture-v1",
    )
    return qualify_deck_asset(
        asset,
        ArchetypeSpec("package-fixture", (), (1,), "qualified_not_trained"),
        known_card_ids=set(cards),
        cabt_legality=lambda _: (True, "fixture-cabt-pass"),
    )


@pytest.fixture
def valid_bundle_spec(tmp_path: Path):
    from mage_ptcg.meta_specialist.package import (
        BundleSpec,
        DependencyContractIds,
        derive_entrypoint_contract_id,
    )

    source = tmp_path / "source"
    source.mkdir()
    qualified = _qualified_asset(source)
    (source / "main.py").write_text("agent = lambda observation, configuration: []\n", encoding="utf-8")
    (source / "policy_loader.py").write_text("# structural fixture\n", encoding="utf-8")
    (source / "weights.bin").write_bytes(b"fixture checkpoint bytes")
    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id,
        selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,),
        foundation_init_id="b" * 64,
        joint_race_schedule_id="c" * 64,
        equal_transition_budget=1,
    )
    members = ("deck.csv", "main.py", "policy_loader.py", "weights.bin")
    dependency_ids = DependencyContractIds(
        cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
        runtime_constraints_id=constraints.runtime_constraints_id,
        ladder_mechanics_id=ladder["ladder_mechanics_id"],
        entrypoint_contract_id=derive_entrypoint_contract_id(source, members),
    )
    return BundleSpec(
        source_root=source,
        members=members,
        deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py",
        qualified_deck_asset=qualified,
        deck_lock=lock,
        runtime_constraints=constraints,
        ladder_mechanics=ladder,
        dependency_contract_ids=dependency_ids,
        candidate_class="checkpointed_specialist",
        policy_members=("weights.bin",),
        model_member="weights.bin",
        policy_identity=sha256(b"fixture checkpoint bytes").hexdigest(),
        checkpoint_lineage_id=lock.policy_lineage_id,
        checkpoint_lineage_reason=None,
    )


def test_specialist_archive_is_deterministic_and_structurally_verified(
    tmp_path: Path, valid_bundle_spec,
) -> None:
    """Catches archive byte drift or a report that claims readiness."""
    from mage_ptcg.meta_specialist.package import (
        build_specialist_archive,
        verify_specialist_archive,
    )

    first = build_specialist_archive(valid_bundle_spec, tmp_path / "first.tar.gz")
    second = build_specialist_archive(valid_bundle_spec, tmp_path / "second.tar.gz")

    assert first.archive_sha256 == second.archive_sha256
    assert (tmp_path / "first.tar.gz").read_bytes() == (tmp_path / "second.tar.gz").read_bytes()
    report = verify_specialist_archive(tmp_path / "first.tar.gz")
    assert report.to_payload()["status"] == "structurally_verified"
    assert report.required_top_level_files == ("main.py", "deck.csv")
    assert report.policy_identity == sha256(b"fixture checkpoint bytes").hexdigest()
    assert report.deck_identity == valid_bundle_spec.qualified_deck_asset.deck_identity


def test_checkpoint_bundle_rejects_model_bytes_that_do_not_match_policy_identity(
    valid_bundle_spec,
) -> None:
    """Catches a stale manifest policy identity after checkpoint replacement."""
    from mage_ptcg.meta_specialist.package import BundleContractError

    (valid_bundle_spec.source_root / "weights.bin").write_bytes(b"changed checkpoint bytes")

    with pytest.raises(BundleContractError, match="policy_identity"):
        valid_bundle_spec.validate()


def test_direct_bundle_spec_construction_rejects_stale_checkpoint_identity(
    valid_bundle_spec,
) -> None:
    """Catches direct construction bypassing the same source validator as JSON loading."""
    from dataclasses import replace

    from mage_ptcg.meta_specialist.package import BundleContractError

    with pytest.raises(BundleContractError, match="policy_identity"):
        replace(valid_bundle_spec, policy_identity="0" * 64)


def test_local_spec_round_trip_preserves_only_relative_source_root(
    tmp_path: Path, valid_bundle_spec,
) -> None:
    """Catches process-CWD-dependent local specification parsing."""
    from mage_ptcg.meta_specialist.package import BundleSpec, write_bundle_spec

    spec_path = tmp_path / "bundle-spec.json"
    write_bundle_spec(valid_bundle_spec, spec_path)
    reloaded = BundleSpec.from_payload(
        __import__("json").loads(spec_path.read_text(encoding="utf-8")),
        spec_path=spec_path,
    )

    assert reloaded.source_root == valid_bundle_spec.source_root
    assert reloaded.to_local_payload(spec_path=spec_path)["source_root"] == "source"


def test_extract_verified_archive_materializes_only_verified_member_bytes(
    tmp_path: Path, valid_bundle_spec,
) -> None:
    """Catches extraction that trusts archive paths rather than the verified snapshot."""
    from mage_ptcg.meta_specialist.package import (
        build_specialist_archive,
        extract_verified_archive,
    )

    archive = tmp_path / "bundle.tar.gz"
    build_specialist_archive(valid_bundle_spec, archive)
    destination = tmp_path / "extracted"

    result = extract_verified_archive(archive, destination)

    assert result == destination
    assert (destination / "deck.csv").read_bytes() == (valid_bundle_spec.source_root / "deck.csv").read_bytes()
    assert (destination / "weights.bin").read_bytes() == b"fixture checkpoint bytes"
    assert (destination / "meta_specialist_bundle.json").is_file()


def test_builder_rejects_corruption_between_memory_verification_and_publish(
    tmp_path: Path, valid_bundle_spec, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches publishing a disk-corrupted sibling temporary file without rechecking it."""
    import os

    import mage_ptcg.meta_specialist.package as package

    def corrupt(descriptor: int, _: bytes) -> None:
        os.write(descriptor, b"corrupted")

    monkeypatch.setattr(package, "_write_all", corrupt)
    target = tmp_path / "corrupted.tar.gz"

    with pytest.raises(package.BundleSecurityError, match="temporary"):
        package.build_specialist_archive(valid_bundle_spec, target)

    assert not target.exists()


def test_static_policy_identity_binds_rule_bytes_but_not_the_deck(
    tmp_path: Path, valid_bundle_spec,
) -> None:
    """Catches static bundles hashing a checkpoint convention instead of their rule closure."""
    from dataclasses import replace

    from mage_ptcg.meta_specialist.package import (
        BundleContractError,
        build_specialist_archive,
    )

    rule_bytes = b"RULE_VERSION = 1\n"
    (valid_bundle_spec.source_root / "rule.py").write_bytes(rule_bytes)
    record = {
        "path": "rule.py", "sha256": sha256(rule_bytes).hexdigest(), "size": len(rule_bytes),
    }
    static_identity = sha256(
        b"meta-specialist-static-policy-v1\0"
        + json.dumps([record], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    static_spec = replace(
        valid_bundle_spec,
        members=("deck.csv", "main.py", "policy_loader.py", "rule.py"),
        candidate_class="static_rule_bundle",
        policy_members=("rule.py",),
        model_member=None,
        policy_identity=static_identity,
        checkpoint_lineage_id=None,
        checkpoint_lineage_reason="not_applicable_static_policy",
    )

    report = build_specialist_archive(static_spec, tmp_path / "static.tar.gz")
    assert report.candidate_class == "static_rule_bundle"
    assert report.policy_identity == static_identity

    (valid_bundle_spec.source_root / "rule.py").write_bytes(b"RULE_VERSION = 2\n")
    with pytest.raises(BundleContractError, match="policy_identity"):
        static_spec.validate()


def test_extraction_failure_cleans_partial_files_but_preserves_existing_empty_destination(
    tmp_path: Path, valid_bundle_spec, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches cleanup deleting a caller-owned empty destination after a write failure."""
    import mage_ptcg.meta_specialist.package as package

    archive = tmp_path / "bundle.tar.gz"
    package.build_specialist_archive(valid_bundle_spec, archive)
    destination = tmp_path / "caller-owned-empty-destination"
    destination.mkdir()

    def fail_write(_: int, __: bytes) -> None:
        raise OSError("simulated extraction write failure")

    monkeypatch.setattr(package, "_write_all", fail_write)
    with pytest.raises(OSError, match="simulated"):
        package.extract_verified_archive(archive, destination)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_extraction_rejects_a_symlinked_destination_ancestor(
    tmp_path: Path, valid_bundle_spec,
) -> None:
    """Catches fd-safe extraction being bypassed through a symlinked parent path."""
    from mage_ptcg.meta_specialist.package import (
        BundleSecurityError,
        build_specialist_archive,
        extract_verified_archive,
    )

    archive = tmp_path / "bundle.tar.gz"
    build_specialist_archive(valid_bundle_spec, archive)
    actual_parent = tmp_path / "actual-parent"
    actual_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)

    with pytest.raises(BundleSecurityError, match="symlink"):
        extract_verified_archive(archive, linked_parent / "destination")


def test_entrypoint_contract_identity_binds_declared_runtime_python_members(
    valid_bundle_spec,
) -> None:
    """Catches an unbound helper module changing package behavior undetected."""
    from dataclasses import replace

    from mage_ptcg.meta_specialist.package import (
        BundleContractError,
        derive_entrypoint_contract_id,
    )

    helper = valid_bundle_spec.source_root / "runtime_helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    members = tuple(sorted((*valid_bundle_spec.members, "runtime_helper.py")))
    expanded = replace(
        valid_bundle_spec,
        members=members,
        dependency_contract_ids=replace(
            valid_bundle_spec.dependency_contract_ids,
            entrypoint_contract_id=derive_entrypoint_contract_id(
                valid_bundle_spec.source_root, members,
            ),
        ),
    )

    helper.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(BundleContractError, match="entrypoint_contract_id"):
        expanded.validate()
