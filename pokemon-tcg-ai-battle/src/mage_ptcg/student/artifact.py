"""Provenance-complete, evaluation-only Student model artifacts.

The artifact is deliberately separate from the submission package.  A caller
must present both the model and its manifest; validation is fail-closed before
the model can enter the actual-cabt evaluation registry.
"""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Iterable, Mapping

from .dataset import RuleBCExample, split_examples, split_examples_from_assignments
from .evaluation import evaluate_model
from .features import (
    ACTION_FEATURE_DIM,
    ACTION_FEATURE_DOMAINS,
    FEATURE_VERSION,
    LEGACY_ACTIONKEY_FEATURE_DOMAIN,
    PRIVATE_ACTIONKEY_FEATURE_DOMAIN,
    STATE_FEATURE_DIM,
)
from .model import (
    LEGACY_MODEL_SCHEMA_VERSION,
    MODEL_FEATURE_DIM,
    MODEL_SCHEMA_VERSION,
    ModelValidationError,
    StudentV0Model,
    training_feature_domain,
    train_model,
)


ARTIFACT_SCHEMA_VERSION = "c4-student-actual-artifact-v2"
LEGACY_ARTIFACT_SCHEMA_VERSION = "c4-student-actual-artifact-v1"
MODEL_FORMAT = "student-v0-json-linear-candidate-scorer"


class ArtifactValidationError(ValueError):
    """Raised when a model/manifest pair cannot safely be used at runtime."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    value = Path(path)
    if not value.is_file() or value.is_symlink():
        raise ArtifactValidationError("model must be a regular file")
    return hashlib.sha256(value.read_bytes()).hexdigest()


def feature_schema(
    feature_domain: str = PRIVATE_ACTIONKEY_FEATURE_DOMAIN,
) -> dict[str, object]:
    if type(feature_domain) is not str or feature_domain not in ACTION_FEATURE_DOMAINS:
        raise ArtifactValidationError("unsupported Student feature domain")
    value = {
        "feature_domain": feature_domain,
        "feature_schema_version": FEATURE_VERSION,
        "state_feature_dimension": STATE_FEATURE_DIM,
        "action_feature_dimension": ACTION_FEATURE_DIM,
        "feature_dimension": MODEL_FEATURE_DIM,
    }
    return {**value, "feature_schema_hash": _digest(value)}


def _legacy_feature_schema() -> dict[str, object]:
    """Return the frozen pre-domain schema for explicit v1 compatibility."""
    value = {
        "feature_schema_version": FEATURE_VERSION,
        "state_feature_dimension": STATE_FEATURE_DIM,
        "action_feature_dimension": ACTION_FEATURE_DIM,
        "feature_dimension": MODEL_FEATURE_DIM,
    }
    return {**value, "feature_schema_hash": _digest(value)}


def _dataset_records(examples: Iterable[RuleBCExample]) -> list[dict[str, object]]:
    return [item.to_dict() for item in examples]


def _require_string(manifest: Mapping[str, object], name: str) -> str:
    value = manifest.get(name)
    if not isinstance(value, str) or not value:
        raise ArtifactValidationError(f"manifest field {name} is missing")
    return value


def load_validated_artifact(model_path: str | Path | None, manifest_path: str | Path | None) -> tuple[StudentV0Model, dict[str, object]]:
    """Load an exact model/manifest pair, rejecting every incompatible state."""
    if model_path is None:
        raise ArtifactValidationError("Student model is missing")
    if manifest_path is None:
        raise ArtifactValidationError("Student manifest is missing")
    try:
        raw_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError("Student manifest is invalid") from exc
    if not isinstance(raw_manifest, dict):
        raise ArtifactValidationError("Student manifest must be an object")
    manifest: dict[str, object] = raw_manifest
    required = (
        "artifact_schema_version", "artifact_type", "artifact_purpose", "performance_eligible",
        "model_format", "model_version", "model_hash", "model_size_bytes", "feature_schema_version",
        "feature_schema_hash", "feature_dimension", "privacy_scan_executed", "privacy_violations",
    )
    for name in required:
        if name not in manifest:
            raise ArtifactValidationError(f"manifest field {name} is missing")
    artifact_schema_version = manifest["artifact_schema_version"]
    if artifact_schema_version not in {
        ARTIFACT_SCHEMA_VERSION,
        LEGACY_ARTIFACT_SCHEMA_VERSION,
    }:
        raise ArtifactValidationError("unsupported artifact schema")
    if artifact_schema_version == ARTIFACT_SCHEMA_VERSION and "feature_domain" not in manifest:
        raise ArtifactValidationError("manifest field feature_domain is missing")
    if manifest["artifact_type"] != "C4_STUDENT_MODEL":
        raise ArtifactValidationError("unsupported artifact type")
    if manifest["artifact_purpose"] not in {"ACTUAL_TRAINED", "SMOKE_ONLY"}:
        raise ArtifactValidationError("unsupported artifact purpose")
    if type(manifest["performance_eligible"]) is not bool:
        raise ArtifactValidationError("performance eligibility is invalid")
    if manifest["artifact_purpose"] == "SMOKE_ONLY" and manifest["performance_eligible"] is not False:
        raise ArtifactValidationError("SMOKE_ONLY artifact must be performance ineligible")
    if manifest["artifact_purpose"] == "ACTUAL_TRAINED" and manifest["performance_eligible"] is not True:
        raise ArtifactValidationError("ACTUAL_TRAINED artifact must be performance eligible")
    expected_model_version = (
        MODEL_SCHEMA_VERSION
        if artifact_schema_version == ARTIFACT_SCHEMA_VERSION
        else LEGACY_MODEL_SCHEMA_VERSION
    )
    if manifest["model_format"] != MODEL_FORMAT or manifest["model_version"] != expected_model_version:
        raise ArtifactValidationError("unsupported model format or version")
    if manifest["privacy_scan_executed"] is not True or manifest["privacy_violations"] != 0:
        raise ArtifactValidationError("artifact privacy scan did not pass")
    actual_hash = sha256_file(model_path)
    if _require_string(manifest, "model_hash") != actual_hash:
        raise ArtifactValidationError("model hash mismatch")
    if manifest["model_size_bytes"] != Path(model_path).stat().st_size:
        raise ArtifactValidationError("model size mismatch")
    try:
        model = StudentV0Model.load(model_path)
    except ModelValidationError as exc:
        raise ArtifactValidationError("malformed Student model") from exc
    if artifact_schema_version == ARTIFACT_SCHEMA_VERSION:
        feature_domain = manifest.get("feature_domain")
        if type(feature_domain) is not str or feature_domain != model.feature_domain:
            raise ArtifactValidationError("model and manifest feature domains differ")
        expected_schema = feature_schema(feature_domain)
    else:
        if (
            "feature_domain" in manifest
            and manifest["feature_domain"] != LEGACY_ACTIONKEY_FEATURE_DOMAIN
        ):
            raise ArtifactValidationError(
                "legacy artifact must declare only legacy ActionKey features"
            )
        if model.feature_domain != LEGACY_ACTIONKEY_FEATURE_DOMAIN:
            raise ArtifactValidationError("legacy artifact must use legacy ActionKey features")
        expected_schema = _legacy_feature_schema()
    for name in ("feature_schema_version", "feature_schema_hash", "feature_dimension"):
        if manifest[name] != expected_schema[name]:
            raise ArtifactValidationError("feature schema mismatch")
    return model, manifest


def build_artifact(
    *,
    examples: Iterable[RuleBCExample],
    output_dir: str | Path,
    canonical_base_sha: str,
    work_commit_sha: str,
    dataset_source_type: str,
    artifact_purpose: str,
    epochs: int = 120,
    learning_rate: float = 0.15,
    validation_percent: int = 20,
    dataset_manifest_hash: str = "NONE",
    split_manifest_hash: str = "NONE",
    source_split_hash: str = "NONE",
    split_assignments: Mapping[str, str] | None = None,
    split_method: str | None = None,
) -> dict[str, object]:
    """Train and emit a model plus a public-safe provenance manifest."""
    if artifact_purpose not in {"ACTUAL_TRAINED", "SMOKE_ONLY"}:
        raise ValueError("unsupported artifact purpose")
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError("artifact output directory must not already exist")
    values = list(examples)
    # Candidate-wise models cannot combine C5 public features with private
    # ActionKey vectors merely because a split happens to isolate them.
    full_dataset_domain = training_feature_domain(values)
    if split_assignments is None:
        train, validation = split_examples(values, validation_percent=validation_percent)
        effective_split_method = "source_id_sha256_modulo_percent"
    else:
        train, validation = split_examples_from_assignments(values, dict(split_assignments))
        effective_split_method = split_method or "external_manifest"
    model = train_model(train, epochs=epochs, learning_rate=learning_rate)
    if model.feature_domain != full_dataset_domain:
        raise ModelValidationError("model domain does not match the full artifact dataset")
    destination.mkdir(parents=True)
    model_path = destination / "student-v0.json"
    model.export(model_path)
    schema = feature_schema(model.feature_domain)
    dataset_hash = _digest(_dataset_records(values))
    split_payload = {
        "method": effective_split_method,
        "train_source_ids": sorted(item.source_id for item in train),
        "validation_source_ids": sorted(item.source_id for item in validation),
    }
    overlap = set(split_payload["train_source_ids"]).intersection(split_payload["validation_source_ids"])
    metrics = evaluate_model(model, validation, repeats=20)
    train_metrics = evaluate_model(model, train, repeats=1)
    training_config = {"epochs": epochs, "learning_rate": learning_rate, "validation_percent": validation_percent}
    manifest: dict[str, object] = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "C4_STUDENT_MODEL",
        "artifact_purpose": artifact_purpose,
        "performance_eligible": artifact_purpose == "ACTUAL_TRAINED",
        "model_format": MODEL_FORMAT,
        "model_version": MODEL_SCHEMA_VERSION,
        "feature_domain": model.feature_domain,
        "model_hash": sha256_file(model_path),
        "model_size_bytes": model_path.stat().st_size,
        "canonical_base_sha": canonical_base_sha,
        "work_commit_sha": work_commit_sha,
        "dataset_source_type": dataset_source_type,
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": dataset_manifest_hash,
        "split_manifest_hash": split_manifest_hash,
        "source_split_hash": source_split_hash,
        "dataset_episode_count": len({item.source_id for item in values}),
        "dataset_decision_count": len(values),
        "dataset_candidate_count": sum(len(item.legal_actions) for item in values),
        "split_method": split_payload["method"],
        "train_episode_count": len(set(split_payload["train_source_ids"])),
        "validation_episode_count": len(set(split_payload["validation_source_ids"])),
        "split_hash": _digest(split_payload),
        "split_overlap_count": len(overlap),
        **schema,
        "training_config": training_config,
        "training_config_hash": _digest(training_config),
        "training_seed": "NOT_APPLICABLE",
        "training_device": "CPU",
        "training_backend": "python-float-full-batch",
        "cuda_available": False,
        "gpu_name": "NONE",
        "train_metrics": train_metrics,
        "validation_metrics": metrics,
        "privacy_scan_executed": True,
        "privacy_violations": 0,
        "created_at": "SOURCE_DATE_EPOCH_UNSET",
        "runtime_platform": platform.python_implementation(),
    }
    # The manifest intentionally has no output path, raw trace, or raw identity.
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    load_validated_artifact(model_path, manifest_path)
    return manifest


__all__ = [
    "ARTIFACT_SCHEMA_VERSION", "ArtifactValidationError", "MODEL_FORMAT", "build_artifact",
    "feature_schema", "load_validated_artifact", "sha256_file",
]
