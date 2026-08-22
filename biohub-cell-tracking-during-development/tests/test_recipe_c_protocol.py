"""Fail-closed tests for the immutable Recipe C selection protocol."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from biohub.recipe_c.protocol import (
    PANEL_V1,
    ExperimentSpec,
    build_selection_lock,
    canonical_lock_json,
    recompute_selection_lock_id,
    validate_selection_lock,
    validate_selection_lock_payload,
    write_selection_lock,
)
from biohub.recipe_c.source import RECIPE_C_SOURCE


def _source_receipt() -> dict[str, object]:
    return {
        "source_url": RECIPE_C_SOURCE.source_url,
        "source_commit": RECIPE_C_SOURCE.source_commit,
        "license": RECIPE_C_SOURCE.license,
        "license_relative_path": RECIPE_C_SOURCE.license_relative_path,
        "license_sha256": RECIPE_C_SOURCE.license_sha256,
        "config_relative_path": RECIPE_C_SOURCE.config_relative_path,
        "config_sha256": RECIPE_C_SOURCE.config_sha256,
        "notebook_relative_path": RECIPE_C_SOURCE.notebook_relative_path,
        "notebook_sha256": RECIPE_C_SOURCE.notebook_sha256,
        "predictor_relative_path": RECIPE_C_SOURCE.predictor_relative_path,
        "predictor_sha256": RECIPE_C_SOURCE.predictor_sha256,
        "primary_checkpoint_relative_path": RECIPE_C_SOURCE.primary_checkpoint_relative_path,
        "primary_checkpoint_sha256": RECIPE_C_SOURCE.primary_checkpoint_sha256,
        "secondary_checkpoint_relative_path": RECIPE_C_SOURCE.secondary_checkpoint_relative_path,
        "secondary_checkpoint_sha256": RECIPE_C_SOURCE.secondary_checkpoint_sha256,
        "secondary_staging_relative_path": RECIPE_C_SOURCE.secondary_staging_relative_path,
        "primary_dataset": RECIPE_C_SOURCE.primary_dataset,
        "primary_dataset_version": RECIPE_C_SOURCE.primary_dataset_version,
        "primary_dataset_license": RECIPE_C_SOURCE.primary_dataset_license,
        "secondary_dataset": RECIPE_C_SOURCE.secondary_dataset,
        "secondary_dataset_version": RECIPE_C_SOURCE.secondary_dataset_version,
        "secondary_dataset_license": RECIPE_C_SOURCE.secondary_dataset_license,
    }


def _experiment(**changes: object) -> ExperimentSpec:
    values: dict[str, object] = {
        "experiment_id": "exp_recipe_c_001",
        "method_family": "recipe_c_unet_transformer_ilp",
        "hypothesis": "dual seed logits improve temporal edge calibration",
        "expected_gain": 0.05,
        "cost": "one fixed panel run",
        "risk": "compute cost without score gain",
        "novelty": "public recipe adaptation",
        "changes": "blend the two pinned public checkpoints",
        "control_id": "control_recipe_c_v1",
        "acceptance_criteria": "all five samples complete and macro improves",
        "prior_evidence_receipt_hash": None,
    }
    values.update(changes)
    return ExperimentSpec(**values)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "recipe-c.yaml"
    path.write_bytes(b"inference:\n  edge_threshold: 0.40\n")
    return path


@pytest.fixture
def valid_lock(config_path: Path) -> dict[str, object]:
    return build_selection_lock(
        _source_receipt(),
        config_path,
        "a" * 40,
        "auto",
        _experiment(),
    )


def test_panel_v1_is_exactly_ordered() -> None:
    assert PANEL_V1 == (
        "44b6_0113de3b",
        "44b6_0b24845f",
        "44b6_0c582fdc",
        "44b6_0db75fae",
        "44b6_12dfb391",
    )


def test_experiment_spec_is_frozen_and_rejects_empty_or_nonfinite() -> None:
    spec = _experiment()
    with pytest.raises(FrozenInstanceError):
        spec.experiment_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="empty"):
        _experiment(hypothesis=" ")
    with pytest.raises(ValueError, match="finite"):
        _experiment(expected_gain=float("nan"))


def test_selection_lock_rejects_changed_panel(valid_lock: dict[str, object]) -> None:
    panel = valid_lock["panel"]
    assert isinstance(panel, dict)
    panel["sample_ids"] = list(PANEL_V1[:-1])
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_reordered_panel(valid_lock: dict[str, object]) -> None:
    panel = valid_lock["panel"]
    assert isinstance(panel, dict)
    panel["sample_ids"] = list(reversed(PANEL_V1))
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


@pytest.mark.parametrize(
    "field", ("ground_truth_used_for_prediction", "ground_truth_used_for_parameter_fitting")
)
def test_selection_lock_rejects_forbidden_gt_usage(valid_lock: dict[str, object], field: str) -> None:
    usage = valid_lock["ground_truth_usage"]
    assert isinstance(usage, dict)
    usage[field] = True
    with pytest.raises(ValueError, match=r"ground truth|GT"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_recomputes_id(valid_lock: dict[str, object]) -> None:
    experiment = valid_lock["experiment"]
    assert isinstance(experiment, dict)
    experiment["hypothesis"] = "post-hoc mutation"
    with pytest.raises(ValueError, match="selection_lock_id"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_id_is_canonical_sha256(valid_lock: dict[str, object]) -> None:
    expected = hashlib.sha256(canonical_lock_json(valid_lock, without_id=True).encode()).hexdigest()
    assert valid_lock["selection_lock_id"] == expected
    assert recompute_selection_lock_id(valid_lock) == expected


def test_source_identity_uses_direct_keys_not_aliases(config_path: Path) -> None:
    source = _source_receipt()
    source["source"] = {"commit": source.pop("source_commit")}
    with pytest.raises(ValueError, match="source_commit"):
        build_selection_lock(source, config_path, "a" * 40, "cpu", _experiment())


def test_config_hash_is_raw_bytes(config_path: Path, valid_lock: dict[str, object]) -> None:
    original = valid_lock["config_sha256"]
    config_path.write_bytes(b"inference: {edge_threshold: 0.4}\n")
    changed = build_selection_lock(
        _source_receipt(), config_path, "a" * 40, "cpu", _experiment()
    )
    assert changed["config_sha256"] != original


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_selection_lock_rejects_nonfinite_payload(valid_lock: dict[str, object], value: float) -> None:
    experiment = valid_lock["experiment"]
    assert isinstance(experiment, dict)
    experiment["expected_gain"] = value
    with pytest.raises(ValueError, match=r"finite|canonical|NaN|Inf"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_unknown_field(valid_lock: dict[str, object]) -> None:
    valid_lock["unexpected_field"] = "must fail"
    with pytest.raises(ValueError, match=r"unknown|unexpected"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_absolute_and_credential_paths(valid_lock: dict[str, object]) -> None:
    valid_lock["config_relative_path"] = "/Users/private/.kaggle/kaggle.json"
    with pytest.raises(ValueError, match=r"path|credential|absolute"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_is_write_once_and_reread(tmp_path: Path, valid_lock: dict[str, object]) -> None:
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    assert validate_selection_lock(path) == valid_lock
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)
    assert path.read_text(encoding="utf-8") == canonical_lock_json(valid_lock)


@pytest.mark.parametrize("existing_kind", ["file", "directory", "symlink"])
def test_selection_lock_does_not_replace_existing_target(
    tmp_path: Path, valid_lock: dict[str, object], existing_kind: str
) -> None:
    path = tmp_path / "selection_lock.json"
    if existing_kind == "file":
        path.write_text("sentinel", encoding="utf-8")
    elif existing_kind == "directory":
        path.mkdir()
    else:
        target = tmp_path / "target"
        target.write_text("sentinel", encoding="utf-8")
        path.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)


def test_prior_evidence_requires_strong_panel_ordering_schema(config_path: Path) -> None:
    weak = {"final_score": 0.9, "sample_ids": list(PANEL_V1)}
    with pytest.raises(ValueError, match=r"prior|schema|PANEL_V1"):
        build_selection_lock(_source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [weak])


def test_prior_evidence_sets_post_prediction_scope_without_copying_receipt(
    config_path: Path,
) -> None:
    strong = {
        "schema_version": 1,
        "receipt_type": "panel_evaluation",
        "panel": {"panel_id": "PANEL_V1", "sample_ids": list(PANEL_V1)},
        "ground_truth_used_for_prediction": False,
        "ground_truth_used_for_parameter_fitting": False,
        "ground_truth_usage_scope": "post_prediction_analysis_only",
        "metrics": {"final_score": 0.9},
    }
    lock = build_selection_lock(
        _source_receipt(), config_path, "a" * 40, "cpu", _experiment(), [strong]
    )
    usage = lock["ground_truth_usage"]
    assert isinstance(usage, dict)
    assert usage["ground_truth_used_for_method_family_selection"] is True
    assert usage["ground_truth_usage_scope"] == "post_prediction_analysis_only"
    assert "metrics" not in json.dumps(lock)


def test_validate_selection_lock_rejects_tampered_file(tmp_path: Path, valid_lock: dict[str, object]) -> None:
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["requested_device"] = "cuda"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match=r"selection_lock_id|device"):
        validate_selection_lock(path)
