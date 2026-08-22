"""Tests for the pinned public Recipe C source and support artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from biohub.recipe_c.source import (
    RECIPE_C_SOURCE,
    RecipeCSourceContract,
    validate_source_checkout,
    validate_support_artifacts,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git_checkout(root: Path, files: dict[str, bytes]) -> str:
    root.mkdir(parents=True)
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _FakeSourceTree:
    def __init__(self, root: Path, contract: RecipeCSourceContract) -> None:
        self.root = root
        self.contract = contract

    def write_git_head(self, commit: str) -> None:
        (self.root / ".git" / "HEAD").write_text(commit + "\n")


@pytest.fixture
def fake_source_tree(tmp_path: Path) -> _FakeSourceTree:
    payloads = {
        "LICENSE": b"Apache License\n",
        RECIPE_C_SOURCE.config_relative_path: b"inference:\n  detection_threshold: 0.96875\n",
        RECIPE_C_SOURCE.notebook_relative_path: b"fake notebook\n",
    }
    root = tmp_path / "source"
    commit = _git_checkout(root, payloads)
    contract = replace(
        RECIPE_C_SOURCE,
        source_commit=commit,
        license_sha256=_sha256(payloads["LICENSE"]),
        config_sha256=_sha256(payloads[RECIPE_C_SOURCE.config_relative_path]),
        notebook_sha256=_sha256(payloads[RECIPE_C_SOURCE.notebook_relative_path]),
    )
    return _FakeSourceTree(root, contract)


class _FakeArtifact:
    def __init__(self, root: Path, checkpoint: Path) -> None:
        self.root = root
        self.checkpoint = checkpoint


@pytest.fixture
def fake_primary(tmp_path: Path) -> _FakeArtifact:
    root = tmp_path / "primary"
    checkpoint = root / RECIPE_C_SOURCE.primary_checkpoint_relative_path
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"primary fixture checkpoint")
    predictor = root / RECIPE_C_SOURCE.predictor_relative_path
    predictor.parent.mkdir(parents=True, exist_ok=True)
    predictor.write_text("# fixture predictor\n")
    return _FakeArtifact(root, checkpoint)


@pytest.fixture
def fake_secondary(tmp_path: Path) -> _FakeArtifact:
    root = tmp_path / "secondary"
    checkpoint = root / RECIPE_C_SOURCE.secondary_checkpoint_relative_path
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"secondary fixture checkpoint")
    return _FakeArtifact(root, checkpoint)


def _fixture_support_contract(fake_primary: _FakeArtifact, fake_secondary: _FakeArtifact) -> RecipeCSourceContract:
    return replace(
        RECIPE_C_SOURCE,
        primary_checkpoint_sha256=_sha256(fake_primary.checkpoint.read_bytes()),
        secondary_checkpoint_sha256=_sha256(fake_secondary.checkpoint.read_bytes()),
    )


def test_source_contract_rejects_wrong_commit(tmp_path: Path, fake_source_tree: _FakeSourceTree) -> None:
    fake_source_tree.write_git_head("0" * 40)
    with pytest.raises(ValueError, match="source commit"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)


def test_source_contract_returns_fixed_file_hashes(fake_source_tree: _FakeSourceTree) -> None:
    receipt = validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)

    assert receipt["source_commit"] == fake_source_tree.contract.source_commit
    assert receipt["license_sha256"] == fake_source_tree.contract.license_sha256
    assert receipt["config_sha256"] == fake_source_tree.contract.config_sha256
    assert receipt["notebook_sha256"] == fake_source_tree.contract.notebook_sha256
    assert receipt["license"] == fake_source_tree.contract.license
    assert receipt["primary_checkpoint_sha256"] == fake_source_tree.contract.primary_checkpoint_sha256
    assert receipt["secondary_checkpoint_sha256"] == fake_source_tree.contract.secondary_checkpoint_sha256
    assert receipt["primary_checkpoint_relative_path"] == fake_source_tree.contract.primary_checkpoint_relative_path
    assert receipt["secondary_checkpoint_relative_path"] == fake_source_tree.contract.secondary_checkpoint_relative_path
    assert receipt["primary_dataset_version"] == fake_source_tree.contract.primary_dataset_version
    assert receipt["secondary_dataset_version"] == fake_source_tree.contract.secondary_dataset_version
    assert receipt["primary_dataset_license"] == fake_source_tree.contract.primary_dataset_license
    assert receipt["secondary_dataset_license"] == fake_source_tree.contract.secondary_dataset_license
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert "/root/.kaggle" not in encoded
    assert str(fake_source_tree.root) not in encoded


def test_support_contract_requires_both_distinct_checkpoints(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = _fixture_support_contract(fake_primary, fake_secondary)
    fake_secondary.checkpoint.unlink()

    with pytest.raises(FileNotFoundError, match="seed_314159"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_validates_predictor_and_two_checkpoint_hashes(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = _fixture_support_contract(fake_primary, fake_secondary)
    receipt = validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)

    assert receipt["primary_checkpoint_sha256"] == contract.primary_checkpoint_sha256
    assert receipt["secondary_checkpoint_sha256"] == contract.secondary_checkpoint_sha256
    assert receipt["primary_checkpoint_relative_path"] == contract.primary_checkpoint_relative_path
    assert receipt["secondary_checkpoint_relative_path"] == contract.secondary_checkpoint_relative_path
    assert receipt["predictor_relative_path"] == contract.predictor_relative_path
    assert receipt["primary_dataset_version"] == contract.primary_dataset_version
    assert receipt["secondary_dataset_version"] == contract.secondary_dataset_version
    assert receipt["primary_dataset_license"] == contract.primary_dataset_license
    assert receipt["secondary_dataset_license"] == contract.secondary_dataset_license
    assert receipt["primary_checkpoint_root"] == "primary"
    assert receipt["secondary_checkpoint_root"] == "secondary"


def test_recipe_config_is_the_canonical_source_config() -> None:
    config_path = Path(__file__).parents[1] / "configs" / "biohub_095_recipe_c.yaml"
    assert _sha256(config_path.read_bytes()) == RECIPE_C_SOURCE.config_sha256
