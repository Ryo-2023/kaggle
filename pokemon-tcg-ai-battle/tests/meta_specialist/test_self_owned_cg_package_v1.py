"""Tests for self-owned deck identity sealing and P1 package binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (
    generate_self_owned_deck_v1,
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (
    SelfOwnedCgPackageV1Error,
    materialize_self_owned_cg_package_v1,
    verify_self_owned_cg_package_v1,
    write_self_owned_deck_artifact_v1,
)


ROOT = Path(__file__).resolve().parents[2]
CARD_DB = ROOT / "data/raw/EN_Card_Data.csv"
SPEC_PATH = ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v1.json"
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1"


def _candidate():
    catalog = load_card_catalog_v1(CARD_DB)
    spec = load_self_owned_deck_spec_v1(SPEC_PATH)
    return generate_self_owned_deck_v1(catalog=catalog, spec=spec, seed=29, ordinal=1), catalog, spec


def test_artifact_records_no_parent_and_hash_bound_identity(tmp_path: Path) -> None:
    candidate, catalog, spec = _candidate()
    root = tmp_path / "artifact"
    payload = write_self_owned_deck_artifact_v1(
        candidate,
        root,
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=spec.source_sha256,
        generator_source_sha256=hashlib.sha256(b"generator-test").hexdigest(),
    )

    assert payload["parent_deck"] is None
    assert payload["public_parent_read"] is False
    assert payload["research_only"] is True
    assert payload["authority"] == {
        "training_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
    }
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8"))["manifest_sha256"]
    assert (root / "deck.csv").read_bytes()


def test_materialized_package_excludes_public_deck_and_replaces_root_constant(tmp_path: Path) -> None:
    candidate, catalog, spec = _candidate()
    artifact_root = tmp_path / "artifact"
    write_self_owned_deck_artifact_v1(
        candidate,
        artifact_root,
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=spec.source_sha256,
        generator_source_sha256=hashlib.sha256(b"generator-test").hexdigest(),
    )
    package = tmp_path / "package"
    manifest = materialize_self_owned_cg_package_v1(
        source_package=P1_PACKAGE,
        candidate_deck=artifact_root / "deck.csv",
        output_package=package,
        candidate_id=candidate.candidate_id,
    )

    assert (package / "main.py").is_file()
    assert (package / "deck.csv").read_bytes() == (artifact_root / "deck.csv").read_bytes()
    assert (package / "cg/api.py").is_file()
    assert not (package / "submission.tar.gz").exists()
    assert manifest["parent_deck"] is None
    old_tuple = repr(tuple(int(line) for line in (P1_PACKAGE / "deck.csv").read_text().split()))
    assert old_tuple not in (package / "main.py").read_text(encoding="utf-8")
    assert verify_self_owned_cg_package_v1(package)["candidate_id"] == candidate.candidate_id


def test_package_output_is_no_clobber(tmp_path: Path) -> None:
    candidate, catalog, spec = _candidate()
    artifact_root = tmp_path / "artifact"
    write_self_owned_deck_artifact_v1(
        candidate,
        artifact_root,
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=spec.source_sha256,
        generator_source_sha256=hashlib.sha256(b"generator-test").hexdigest(),
    )
    package = tmp_path / "package"
    materialize_self_owned_cg_package_v1(
        source_package=P1_PACKAGE,
        candidate_deck=artifact_root / "deck.csv",
        output_package=package,
        candidate_id=candidate.candidate_id,
    )
    with pytest.raises(FileExistsError):
        materialize_self_owned_cg_package_v1(
            source_package=P1_PACKAGE,
            candidate_deck=artifact_root / "deck.csv",
            output_package=package,
            candidate_id=candidate.candidate_id,
        )


def test_verifier_rejects_tampered_policy(tmp_path: Path) -> None:
    candidate, catalog, spec = _candidate()
    artifact_root = tmp_path / "artifact"
    write_self_owned_deck_artifact_v1(
        candidate,
        artifact_root,
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=spec.source_sha256,
        generator_source_sha256=hashlib.sha256(b"generator-test").hexdigest(),
    )
    package = tmp_path / "package"
    materialize_self_owned_cg_package_v1(
        source_package=P1_PACKAGE,
        candidate_deck=artifact_root / "deck.csv",
        output_package=package,
        candidate_id=candidate.candidate_id,
    )
    with (package / "main.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# tamper\n")
    with pytest.raises(SelfOwnedCgPackageV1Error, match="policy SHA"):
        verify_self_owned_cg_package_v1(package)


def test_verifier_ignores_runtime_interpreter_cache(tmp_path: Path) -> None:
    candidate, catalog, spec = _candidate()
    artifact_root = tmp_path / "artifact"
    write_self_owned_deck_artifact_v1(
        candidate,
        artifact_root,
        card_database_sha256=catalog.source_sha256,
        role_spec_sha256=spec.source_sha256,
        generator_source_sha256=hashlib.sha256(b"generator-test").hexdigest(),
    )
    package = tmp_path / "package"
    materialize_self_owned_cg_package_v1(
        source_package=P1_PACKAGE,
        candidate_deck=artifact_root / "deck.csv",
        output_package=package,
        candidate_id=candidate.candidate_id,
    )
    cache = package / "cg/__pycache__"
    cache.mkdir()
    (cache / "runtime.cpython-312.pyc").write_bytes(b"cache")
    assert verify_self_owned_cg_package_v1(package)["candidate_id"] == candidate.candidate_id
