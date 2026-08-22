"""Tests for the pinned public Recipe C source and support artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from biohub.recipe_c.source import (
    RECIPE_C_SOURCE,
    RecipeCSourceContract,
    canonical_json,
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
        "source_code.py": b"value = 'clean'\n",
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
    predictor = fake_primary.root / RECIPE_C_SOURCE.predictor_relative_path
    return replace(
        RECIPE_C_SOURCE,
        predictor_sha256=_sha256(predictor.read_bytes()),
        primary_checkpoint_sha256=_sha256(fake_primary.checkpoint.read_bytes()),
        secondary_checkpoint_sha256=_sha256(fake_secondary.checkpoint.read_bytes()),
    )


def test_source_contract_rejects_wrong_commit(tmp_path: Path, fake_source_tree: _FakeSourceTree) -> None:
    fake_source_tree.write_git_head("0" * 40)
    with pytest.raises(ValueError, match="source commit"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)


def test_source_contract_rejects_tracked_dirty_checkout(fake_source_tree: _FakeSourceTree) -> None:
    (fake_source_tree.root / "LICENSE").write_text("changed\n")

    with pytest.raises(ValueError, match=r"clean|dirty|modification"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)


def test_source_contract_rejects_untracked_checkout(fake_source_tree: _FakeSourceTree) -> None:
    (fake_source_tree.root / "UNTRACKED_SECRET").write_text("do not record\n")

    with pytest.raises(ValueError, match=r"clean|dirty|untracked|modification"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)


def test_source_contract_rejects_nested_checkout_root(fake_source_tree: _FakeSourceTree) -> None:
    nested = fake_source_tree.root / "nested"
    nested.mkdir()

    with pytest.raises(ValueError, match=r"root|top-level"):
        validate_source_checkout(nested, contract=fake_source_tree.contract)


def _index_marker(root: Path, relative: str) -> str:
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-v", "-z"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    entry = next(item for item in output.split("\0") if item.endswith(f" {relative}"))
    return entry[0]


@pytest.mark.parametrize(
    ("index_flag", "expected_marker"),
    [("--assume-unchanged", "h"), ("--skip-worktree", "S")],
)
def test_source_contract_rejects_hidden_tracked_mutation_without_changing_index(
    fake_source_tree: _FakeSourceTree,
    index_flag: str,
    expected_marker: str,
) -> None:
    source_file = fake_source_tree.root / "source_code.py"
    subprocess.run(
        ["git", "-C", str(fake_source_tree.root), "update-index", index_flag, "source_code.py"],
        check=True,
    )
    before_marker = _index_marker(fake_source_tree.root, "source_code.py")
    assert before_marker == expected_marker
    source_file.write_text("value = 'tampered'\n")

    with pytest.raises(ValueError, match=r"index|flag|assume|skip|clean"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)

    assert _index_marker(fake_source_tree.root, "source_code.py") == before_marker


@pytest.mark.parametrize("field", ["license_sha256", "config_sha256", "notebook_sha256"])
def test_source_contract_rejects_fixed_file_hash_mismatch(
    fake_source_tree: _FakeSourceTree,
    field: str,
) -> None:
    contract = replace(fake_source_tree.contract, **{field: "0" * 64})

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_source_checkout(fake_source_tree.root, contract=contract)


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
    assert receipt["predictor_sha256"] == fake_source_tree.contract.predictor_sha256
    assert receipt["primary_dataset_version"] == fake_source_tree.contract.primary_dataset_version
    assert receipt["secondary_dataset_version"] == fake_source_tree.contract.secondary_dataset_version
    assert receipt["primary_dataset_license"] == fake_source_tree.contract.primary_dataset_license
    assert receipt["secondary_dataset_license"] == fake_source_tree.contract.secondary_dataset_license
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert "/root/.kaggle" not in encoded
    assert str(fake_source_tree.root) not in encoded


def test_source_contract_pins_predictor_sha256() -> None:
    assert RECIPE_C_SOURCE.predictor_sha256 == (
        "c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9"
    )


def test_support_contract_requires_both_distinct_checkpoints(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = _fixture_support_contract(fake_primary, fake_secondary)
    fake_secondary.checkpoint.unlink()

    with pytest.raises(FileNotFoundError, match="seed_314159"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_rejects_same_root(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = _fixture_support_contract(fake_primary, fake_secondary)

    with pytest.raises(ValueError, match="distinct roots"):
        validate_support_artifacts(fake_primary.root, fake_primary.root, contract=contract)


def test_support_contract_rejects_identical_checkpoints(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    fake_secondary.checkpoint.write_bytes(fake_primary.checkpoint.read_bytes())
    contract = _fixture_support_contract(fake_primary, fake_secondary)

    with pytest.raises(ValueError, match="distinct"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_rejects_checkpoint_hash_mismatch(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = replace(
        _fixture_support_contract(fake_primary, fake_secondary),
        primary_checkpoint_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_rejects_checkpoint_symlink_escape(
    tmp_path: Path,
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    outside = tmp_path / "outside-checkpoint.pth"
    outside.write_bytes(b"outside checkpoint")
    fake_primary.checkpoint.unlink()
    fake_primary.checkpoint.symlink_to(outside)
    contract = replace(
        _fixture_support_contract(fake_primary, fake_secondary),
        primary_checkpoint_sha256=_sha256(outside.read_bytes()),
    )

    with pytest.raises(ValueError, match=r"outside|escape|root"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_rejects_predictor_symlink_escape(
    tmp_path: Path,
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    predictor = fake_primary.root / RECIPE_C_SOURCE.predictor_relative_path
    outside = tmp_path / "outside-predictor.py"
    outside.write_text("# outside code\n")
    predictor.unlink()
    predictor.symlink_to(outside)
    contract = _fixture_support_contract(fake_primary, fake_secondary)

    with pytest.raises(ValueError, match=r"outside|escape|root"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_support_contract_rejects_intermediate_symlink_escape(
    tmp_path: Path,
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    outside_weights = tmp_path / "outside-weights"
    outside_checkpoint = outside_weights / "split_0" / "edge_predictor_best.pth"
    outside_checkpoint.parent.mkdir(parents=True)
    outside_checkpoint.write_bytes(b"outside checkpoint")
    intermediate = fake_primary.root / "weights" / "unet_transformer"
    backup = fake_primary.root / "weights" / "unet_transformer-original"
    intermediate.rename(backup)
    intermediate.symlink_to(outside_weights, target_is_directory=True)
    contract = replace(
        _fixture_support_contract(fake_primary, fake_secondary),
        primary_checkpoint_sha256=_sha256(outside_checkpoint.read_bytes()),
    )

    with pytest.raises(ValueError, match=r"outside|escape|root"):
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
    assert receipt["predictor_sha256"] == contract.predictor_sha256
    assert receipt["primary_dataset_version"] == contract.primary_dataset_version
    assert receipt["secondary_dataset_version"] == contract.secondary_dataset_version
    assert receipt["primary_dataset_license"] == contract.primary_dataset_license
    assert receipt["secondary_dataset_license"] == contract.secondary_dataset_license
    assert receipt["primary_checkpoint_root"] == "primary"
    assert receipt["secondary_checkpoint_root"] == "secondary"


def test_support_contract_rejects_predictor_hash_mismatch(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
) -> None:
    contract = replace(
        _fixture_support_contract(fake_primary, fake_secondary),
        predictor_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


_CONTRACT_PATH_FIELDS = (
    "license_relative_path",
    "config_relative_path",
    "notebook_relative_path",
    "predictor_relative_path",
    "primary_checkpoint_relative_path",
    "secondary_checkpoint_relative_path",
    "secondary_staging_relative_path",
)
_INVALID_CONTRACT_PATHS = ("/tmp/credential-leak", "../escape", "", ".")


@pytest.mark.parametrize("field", _CONTRACT_PATH_FIELDS)
@pytest.mark.parametrize("invalid_path", _INVALID_CONTRACT_PATHS)
def test_source_contract_rejects_invalid_contract_paths(
    fake_source_tree: _FakeSourceTree,
    field: str,
    invalid_path: str,
) -> None:
    contract = replace(fake_source_tree.contract, **{field: invalid_path})

    with pytest.raises((ValueError, FileNotFoundError), match=r"path|relative|root|file"):
        validate_source_checkout(fake_source_tree.root, contract=contract)


@pytest.mark.parametrize("field", _CONTRACT_PATH_FIELDS)
@pytest.mark.parametrize("invalid_path", _INVALID_CONTRACT_PATHS)
def test_support_contract_rejects_invalid_contract_paths(
    fake_primary: _FakeArtifact,
    fake_secondary: _FakeArtifact,
    field: str,
    invalid_path: str,
) -> None:
    contract = replace(_fixture_support_contract(fake_primary, fake_secondary), **{field: invalid_path})

    with pytest.raises((ValueError, FileNotFoundError), match=r"path|relative|root|file"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root, contract=contract)


def test_recipe_config_is_the_canonical_source_config() -> None:
    config_path = Path(__file__).parents[1] / "configs" / "biohub_095_recipe_c.yaml"
    assert _sha256(config_path.read_bytes()) == RECIPE_C_SOURCE.config_sha256


def test_source_contract_fails_closed_when_yaml_parser_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    fake_source_tree: _FakeSourceTree,
) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(RuntimeError, match="PyYAML"):
        validate_source_checkout(fake_source_tree.root, contract=fake_source_tree.contract)


def test_source_contract_rejects_non_json_config_values(tmp_path: Path) -> None:
    payloads = {
        "LICENSE": b"Apache License\n",
        RECIPE_C_SOURCE.config_relative_path: b"inference:\n  bad: .nan\n",
        RECIPE_C_SOURCE.notebook_relative_path: b"fake notebook\n",
    }
    root = tmp_path / "nan-source"
    commit = _git_checkout(root, payloads)
    contract = replace(
        RECIPE_C_SOURCE,
        source_commit=commit,
        license_sha256=_sha256(payloads["LICENSE"]),
        config_sha256=_sha256(payloads[RECIPE_C_SOURCE.config_relative_path]),
        notebook_sha256=_sha256(payloads[RECIPE_C_SOURCE.notebook_relative_path]),
    )

    with pytest.raises(ValueError, match=r"JSON|NaN|Out of range"):
        validate_source_checkout(root, contract=contract)


def test_recipe_contract_does_not_expose_mutable_config_values() -> None:
    assert not hasattr(RECIPE_C_SOURCE, "config_values")


def test_canonical_json_is_deterministic_and_refuses_nan() -> None:
    first = {"b": {"z": 2, "a": 1}, "a": [3, 4]}
    second = {"a": [3, 4], "b": {"a": 1, "z": 2}}
    assert canonical_json(first) == canonical_json(second)
    with pytest.raises(ValueError, match=r"NaN|Out of range"):
        canonical_json({"value": float("nan")})
