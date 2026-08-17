"""Generic unordered set + cardinality Student v3.

This module is deliberately separate from Student v2.  It consumes one
actor-visible decision per source row and preserves the complete unordered
teacher selection, including an explicit empty selection.  Teacher identity,
opponent identity, and seat metadata are retained only for audit and never
enter the model features.

The only supported purpose is a self-owned initialisation artifact.  This
module does not grant training, promotion, packaging, or submission authority.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import pickle
import random
import re
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence

from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.dataset import RuleBCExample
from mage_ptcg.student.features import (
    ACTION_FEATURE_DIM,
    FEATURE_VERSION,
    STATE_FEATURE_DIM,
    state_features_payload,
)
from mage_ptcg.student.model import _action_feature_vector


SOURCE_SCHEMA = "offline-scaleup-student-v3-set-source-v1"
GPU_SET_DATASET_SCHEMA = "offline-scaleup-gpu-set-dataset-v1"
STUDENT_V3_SET_SCHEMA = "offline-scaleup-student-v3-set-v1"
CHECKPOINT_SCHEMA = "offline-scaleup-student-v3-set-checkpoint-v1"
WEIGHT_SIDECAR_SCHEMA = "offline-scaleup-student-v3-weight-sidecar-v1"
TEACHER_BRIDGE_SCHEMA = "meta-specialist-teacher-student-v3-set-bridge-v2"
PURPOSE = "DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY"
SPLITS = ("train", "validation", "test")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "record_id",
        "split",
        "episode_id",
        "near_duplicate_id",
        "near_duplicate_ubiquitous",
        "candidate_outcome",
        "sample_weight",
        "rule_bc_example",
        "provenance",
        "authority",
    }
)
_GPU_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "source_dataset",
        "source_dataset_sha256",
        "catalog_sha256",
        "bridge_manifest_path",
        "bridge_manifest_sha256",
        "bridge_sha256",
        "selected_teacher_ids",
        "synthetic_test_only",
        "feature_schema_version",
        "state_dimension",
        "action_dimension",
        "max_count_class",
        "records",
        "episodes",
        "record_id_unique",
        "episode_leakage",
        "non_ubiquitous_near_duplicate_leakage",
        "ubiquitous_near_duplicate_ids",
        "shards",
        "deterministic_order",
        "feature_boundary",
        "authority",
        "dataset_sha256",
    }
)
_GPU_SHARD_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "state",
        "actions",
        "offsets",
        "target_set",
        "target_count",
        "min_count",
        "max_count",
        "metadata",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "catalog_sha256",
        "snapshot_sha256",
        "source_record_sha256",
        "teacher_policy_sha256",
        "teacher_deck_sha256",
        "teacher_manifest_sha256",
        "native_code_bundled",
        "native_deck_bundled",
    }
)
_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}
_RULE_BC_EXAMPLE_KEYS = frozenset(RuleBCExample.__dataclass_fields__)
_SHARD_METADATA_KEYS = frozenset(
    {
        "record_id",
        "episode_id",
        "near_duplicate_id",
        "near_duplicate_ubiquitous",
        "split",
        "selection_type",
        "selection_context",
        "candidate_outcome",
        "source_sample_weight",
        "catalog_sha256",
        "snapshot_sha256",
        "source_record_sha256",
        "teacher_policy_sha256",
        "teacher_deck_sha256",
        "teacher_manifest_sha256",
        "action_digests",
    }
)
_BRIDGE_SOURCE_KEYS = frozenset(
    {
        "teacher_id", "archetype", "policy_sha256", "deck_sha256",
        "source_kind", "permission_manifest_id", "permission_trusted_bytes_sha256",
        "teacher_manifest_sha256", "dataset_snapshot_sha256",
        "snapshot_index_sha256", "source_records", "source_episodes",
        "trainable_decisions", "trainable_episodes", "dataset_chunks",
        "snapshot_shards", "sealed_split_audit", "native_code_bundled",
        "native_deck_bundled",
    }
)
_BRIDGE_DATASET_CHUNK_KEYS = frozenset(
    {"position", "sha256", "manifest_id", "manifest_content_hash"}
)
_BRIDGE_SNAPSHOT_SHARD_KEYS = frozenset({"snapshot_id", "sha256", "examples"})
_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version", "purpose", "objective_kind", "dataset_manifest_sha256",
        "catalog_sha256", "weight_sidecar_sha256", "model_config",
        "model_config_sha256", "training_config_sha256", "epoch", "model",
        "optimizer", "best_validation_exact_set_fidelity", "best_epoch", "metrics",
        "python_random_state", "torch_rng_state", "cuda_rng_state_all",
    }
)
_MODEL_CONFIG_KEYS = frozenset(
    {
        "schema_version", "feature_schema_version", "state_dimension",
        "action_dimension", "hidden", "blocks", "dropout", "max_count",
    }
)
_TRAINING_CONFIG_KEYS = frozenset(
    {
        "batch_size", "workers", "learning_rate", "count_loss_weight", "seed",
        "device", "compute_dtype", "objective_kind", "weight_sidecar_sha256",
        "optimizer", "optimizer_betas", "optimizer_eps", "optimizer_weight_decay",
        "checkpoint_journal", "runtime_evaluation_device", "initialization_kind",
        "initial_checkpoint_sha256", "initial_training_summary_sha256",
    }
)
_TRAINING_SUMMARY_KEYS = frozenset(
    {
        "schema_version", "purpose", "objective_kind", "dataset_manifest_sha256",
        "catalog_sha256", "weight_sidecar_sha256", "model_config",
        "model_config_sha256", "training_config", "training_config_sha256",
        "resumed_from_checkpoint", "recovered_interrupted_epoch", "epochs_completed",
        "best_validation_exact_set_fidelity", "effective_weight_mass",
        "effective_weight_ess", "external_weight_mass", "external_weight_ess",
        "external_weight_min", "external_weight_max", "joined_train_records",
        "metrics", "best_checkpoint_sha256", "last_checkpoint_path",
        "last_checkpoint_sha256", "authority",
    }
)
_VALIDATION_METRIC_KEYS = frozenset(
    {
        "examples", "loss", "set_loss", "count_loss", "exact_set_fidelity",
        "count_fidelity", "legal_action_rate", "fallback_rate",
    }
)
_EPOCH_METRIC_KEYS = frozenset(
    {
        "epoch", "train_loss", "train_set_loss", "train_count_loss",
        "epoch_seconds", "examples_per_second", "validation",
        "accelerator_validation", "best_selection_device",
    }
)


class GPUStudentV3SetError(ValueError):
    """Raised when the V3 dataset/model contract cannot be verified."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GPUStudentV3SetError("value is not finite canonical JSON") from exc


def _load_json_object_snapshot(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    """Load one regular JSON file from exactly the bytes that were inspected."""

    if path.is_symlink() or not path.is_file():
        raise GPUStudentV3SetError(f"{label} is not a regular file")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")

        def closed_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise GPUStudentV3SetError(f"{label} contains a duplicate key: {key}")
                value[key] = item
            return value

        payload = json.loads(text, object_pairs_hook=closed_pairs)
    except GPUStudentV3SetError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GPUStudentV3SetError(f"{label} is unreadable") from exc
    if type(payload) is not dict:
        raise GPUStudentV3SetError(f"{label} must be a JSON object")
    return raw, payload


def _require_finite_json_numbers(value: Any, *, label: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise GPUStudentV3SetError(f"{label} contains a non-finite number")
        return
    if type(value) is list:
        for item in value:
            _require_finite_json_numbers(item, label=label)
        return
    if type(value) is dict:
        for item in value.values():
            _require_finite_json_numbers(item, label=label)
        return
    raise GPUStudentV3SetError(f"{label} contains an invalid JSON value")


def _verify_validation_metric_v1(value: Any, *, label: str) -> None:
    if type(value) is not dict or set(value) != _VALIDATION_METRIC_KEYS:
        raise GPUStudentV3SetError(f"{label} has an invalid closed schema")
    if type(value["examples"]) is not int or value["examples"] <= 0:
        raise GPUStudentV3SetError(f"{label} examples is invalid")
    for key in ("exact_set_fidelity", "count_fidelity", "legal_action_rate", "fallback_rate"):
        metric = value[key]
        if type(metric) not in (int, float) or not math.isfinite(float(metric)) or not 0 <= metric <= 1:
            raise GPUStudentV3SetError(f"{label} {key} is invalid")


def _verify_epoch_metrics_v1(metrics: Any, *, expected_epochs: int) -> None:
    if type(metrics) is not list or len(metrics) != expected_epochs:
        raise GPUStudentV3SetError("training summary metric lineage mismatch")
    for expected_epoch, metric in enumerate(metrics):
        if type(metric) is not dict or set(metric) not in (
            _EPOCH_METRIC_KEYS,
            _EPOCH_METRIC_KEYS | {"peak_allocated_vram_bytes"},
        ):
            raise GPUStudentV3SetError("training summary epoch metric has an invalid closed schema")
        if metric.get("epoch") != expected_epoch or metric.get("best_selection_device") != "cpu":
            raise GPUStudentV3SetError("training summary epoch metric lineage mismatch")
        _verify_validation_metric_v1(
            metric.get("validation"), label="training summary CPU validation metric"
        )
        _verify_validation_metric_v1(
            metric.get("accelerator_validation"),
            label="training summary accelerator validation metric",
        )
    _require_finite_json_numbers(metrics, label="training summary metrics")


def _verify_training_summary_v1(summary: dict[str, Any]) -> None:
    if set(summary) != _TRAINING_SUMMARY_KEYS:
        raise GPUStudentV3SetError("training summary has an invalid closed schema")
    if (
        summary.get("schema_version") != STUDENT_V3_SET_SCHEMA
        or summary.get("purpose") != PURPOSE
        or summary.get("authority") != _AUTHORITY
    ):
        raise GPUStudentV3SetError("training summary schema/purpose/authority mismatch")
    if summary.get("objective_kind") not in {"THETA0_PRETRAIN", "AWR_FINE_TUNE"}:
        raise GPUStudentV3SetError("training summary objective kind is invalid")
    for key in (
        "dataset_manifest_sha256", "catalog_sha256", "model_config_sha256",
        "training_config_sha256", "best_checkpoint_sha256", "last_checkpoint_sha256",
    ):
        if not isinstance(summary.get(key), str) or _SHA256.fullmatch(summary[key]) is None:
            raise GPUStudentV3SetError(f"training summary {key} is invalid")
    sidecar_sha = summary.get("weight_sidecar_sha256")
    if sidecar_sha is not None and (
        not isinstance(sidecar_sha, str) or _SHA256.fullmatch(sidecar_sha) is None
    ):
        raise GPUStudentV3SetError("training summary weight sidecar SHA-256 is invalid")
    model_config = summary.get("model_config")
    if type(model_config) is not dict or set(model_config) != _MODEL_CONFIG_KEYS:
        raise GPUStudentV3SetError("training summary model config has an invalid closed schema")
    if _config_sha(
        model_config, domain="offline-scaleup-student-v3-set-model-config-v1"
    ) != summary.get("model_config_sha256"):
        raise GPUStudentV3SetError("training summary model config SHA-256 mismatch")
    training_config = summary.get("training_config")
    if type(training_config) is not dict or set(training_config) != _TRAINING_CONFIG_KEYS:
        raise GPUStudentV3SetError("training summary training config has an invalid closed schema")
    if _config_sha(
        training_config, domain="offline-scaleup-student-v3-set-training-config-v1"
    ) != summary.get("training_config_sha256"):
        raise GPUStudentV3SetError("training summary training config SHA-256 mismatch")
    if training_config.get("objective_kind") != summary.get("objective_kind"):
        raise GPUStudentV3SetError("training summary objective/config mismatch")
    if training_config.get("weight_sidecar_sha256") != sidecar_sha:
        raise GPUStudentV3SetError("training summary weight sidecar/config mismatch")
    if training_config.get("runtime_evaluation_device") != "cpu":
        raise GPUStudentV3SetError("training summary runtime evaluation device mismatch")
    initialization_kind = training_config.get("initialization_kind")
    initial_checkpoint_sha = training_config.get("initial_checkpoint_sha256")
    initial_summary_sha = training_config.get("initial_training_summary_sha256")
    if initialization_kind == "RANDOM_SEEDED":
        if initial_checkpoint_sha is not None or initial_summary_sha is not None:
            raise GPUStudentV3SetError("training summary random initialization lineage is invalid")
    elif initialization_kind == "THETA0_BEST_CHECKPOINT":
        if summary.get("objective_kind") != "AWR_FINE_TUNE" or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in (initial_checkpoint_sha, initial_summary_sha)
        ):
            raise GPUStudentV3SetError("training summary theta0 initialization lineage is invalid")
    else:
        raise GPUStudentV3SetError("training summary initialization kind is invalid")
    epochs = summary.get("epochs_completed")
    if type(epochs) is not int or epochs <= 0:
        raise GPUStudentV3SetError("training summary epochs completed is invalid")
    if type(summary.get("resumed_from_checkpoint")) is not bool or type(
        summary.get("recovered_interrupted_epoch")
    ) is not bool:
        raise GPUStudentV3SetError("training summary recovery flags are invalid")
    _verify_epoch_metrics_v1(summary.get("metrics"), expected_epochs=epochs)
    best_exact = summary.get("best_validation_exact_set_fidelity")
    if type(best_exact) not in (int, float) or not math.isfinite(float(best_exact)) or not 0 <= best_exact <= 1:
        raise GPUStudentV3SetError("training summary best validation metric is invalid")
    if best_exact != max(
        metric["validation"]["exact_set_fidelity"] for metric in summary["metrics"]
    ):
        raise GPUStudentV3SetError("training summary best validation metric lineage mismatch")
    expected_last = f"checkpoints/epoch-{epochs - 1:04d}.pt"
    if summary.get("last_checkpoint_path") != expected_last:
        raise GPUStudentV3SetError("training summary last checkpoint path mismatch")
    if type(summary.get("joined_train_records")) is not int or summary["joined_train_records"] < 0:
        raise GPUStudentV3SetError("training summary joined train records is invalid")
    for key in ("effective_weight_mass", "effective_weight_ess"):
        value = summary.get(key)
        if type(value) not in (int, float) or not math.isfinite(float(value)) or value <= 0:
            raise GPUStudentV3SetError(f"training summary {key} is invalid")
    _require_finite_json_numbers(summary, label="training summary")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_sha(value: object, *, domain: str) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _strict_json_line(line: str, *, line_number: int) -> dict[str, Any]:
    if not line.endswith("\n") or line.endswith("\r\n") or line == "\n":
        raise GPUStudentV3SetError(
            f"source line {line_number} is blank, CRLF, or unterminated"
        )
    body = line[:-1]

    def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise GPUStudentV3SetError(f"duplicate JSON key at source line {line_number}")
            value[key] = item
        return value

    def reject_constant(value: str) -> object:
        raise GPUStudentV3SetError(
            f"non-finite JSON constant {value!r} at source line {line_number}"
        )

    try:
        value = json.loads(
            body,
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise GPUStudentV3SetError(f"invalid JSON at source line {line_number}") from exc
    if type(value) is not dict:
        raise GPUStudentV3SetError(f"source line {line_number} is not an object")
    if _canonical_bytes(value).decode("utf-8") != body:
        raise GPUStudentV3SetError(f"source line {line_number} is not canonical JSON")
    return value


def _torch():
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as functional
        from torch.utils.data import DataLoader, Dataset
    except ImportError as exc:  # pragma: no cover - environment guard
        raise GPUStudentV3SetError("PyTorch is required for Student v3 set") from exc
    return torch, nn, functional, DataLoader, Dataset


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise GPUStudentV3SetError(f"{field} must be a lowercase SHA-256")
    return value


def _sample_from_source_row(
    row: dict[str, Any],
) -> tuple[
    list[float],
    list[list[float]],
    list[bool],
    int,
    int,
    int,
    dict[str, Any],
]:
    """Convert exactly one unordered source decision without silent drops."""
    if set(row) != _SOURCE_KEYS:
        raise GPUStudentV3SetError("source row has an invalid closed schema")
    if row.get("schema_version") != SOURCE_SCHEMA or row.get("purpose") != PURPOSE:
        raise GPUStudentV3SetError("source row schema or purpose mismatch")
    if row.get("authority") != _AUTHORITY:
        raise GPUStudentV3SetError("source row authority must remain false")
    record_id = _require_sha(row.get("record_id"), field="record_id")
    episode_id = _require_sha(row.get("episode_id"), field="episode_id")
    near_duplicate_id = _require_sha(
        row.get("near_duplicate_id"), field="near_duplicate_id"
    )
    near_duplicate_ubiquitous = row.get("near_duplicate_ubiquitous")
    if type(near_duplicate_ubiquitous) is not bool:
        raise GPUStudentV3SetError("near_duplicate_ubiquitous must be boolean")
    split = row.get("split")
    if split not in SPLITS:
        raise GPUStudentV3SetError("source row split is outside the closed split set")
    weight = row.get("sample_weight")
    if type(weight) not in (int, float) or not math.isfinite(float(weight)) or float(weight) <= 0:
        raise GPUStudentV3SetError("source sample_weight must be finite and positive")
    provenance = row.get("provenance")
    if type(provenance) is not dict or set(provenance) != _PROVENANCE_KEYS:
        raise GPUStudentV3SetError("source row provenance has an invalid closed schema")
    catalog_sha = _require_sha(provenance.get("catalog_sha256"), field="catalog SHA-256")
    snapshot_sha = _require_sha(provenance.get("snapshot_sha256"), field="snapshot SHA-256")
    source_record_sha = _require_sha(
        provenance.get("source_record_sha256"), field="source record SHA-256"
    )
    policy_sha = _require_sha(
        provenance.get("teacher_policy_sha256"), field="teacher policy SHA-256"
    )
    deck_sha = _require_sha(
        provenance.get("teacher_deck_sha256"), field="teacher deck SHA-256"
    )
    teacher_manifest_sha = _require_sha(
        provenance.get("teacher_manifest_sha256"), field="teacher manifest SHA-256"
    )
    if (
        provenance.get("native_code_bundled") is not False
        or provenance.get("native_deck_bundled") is not False
    ):
        raise GPUStudentV3SetError("source row may not bundle native code or deck")
    if row.get("candidate_outcome") not in {"WIN", "LOSS", "DRAW", "UNKNOWN"}:
        raise GPUStudentV3SetError("source row outcome is outside the closed vocabulary")

    raw_example = row.get("rule_bc_example")
    if type(raw_example) is not dict or set(raw_example) != _RULE_BC_EXAMPLE_KEYS:
        raise GPUStudentV3SetError("RuleBCExample has an invalid closed schema")
    example = RuleBCExample.from_dict(raw_example)
    if example.example_id != record_id or example.source_id != episode_id:
        raise GPUStudentV3SetError("record/episode identity does not bind RuleBCExample")
    if example.source_revision != policy_sha or example.deck_fingerprint != deck_sha:
        raise GPUStudentV3SetError("RuleBCExample does not bind teacher policy/deck SHA-256")
    if example.metadata.get("source_record_sha256") != source_record_sha:
        raise GPUStudentV3SetError("RuleBCExample does not bind source record SHA-256")
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise GPUStudentV3SetError("source row has an unknown CABT selection schema") from exc
    if ordered:
        raise GPUStudentV3SetError("ordered selection requires an ordered pointer head")
    if not example.legal_actions:
        raise GPUStudentV3SetError("set dataset cannot pool an empty legal action set")

    state = state_features_payload(
        example.public_state,
        example.own_private_state,
        example.visible_history,
    )
    actions = [_action_feature_vector(action) for action in example.legal_actions]
    legal_digests = [str(action["digest"]) for action in example.legal_actions]
    target_digests = tuple(example.target_action_digests)
    if any(legal_digests.count(digest) != 1 for digest in target_digests):
        raise GPUStudentV3SetError("selected target has an ActionKey alias collision")
    target_lookup = set(target_digests)
    target_set = [digest in target_lookup for digest in legal_digests]
    target_count = sum(target_set)
    if target_count != len(target_digests):
        raise GPUStudentV3SetError("target set cardinality changed during feature conversion")
    if not example.min_count <= target_count <= example.max_count:
        raise GPUStudentV3SetError("target count violates source bounds")
    if example.max_count > len(actions):
        raise GPUStudentV3SetError("maximum count exceeds legal action count")

    metadata = {
        "record_id": record_id,
        "episode_id": episode_id,
        "near_duplicate_id": near_duplicate_id,
        "near_duplicate_ubiquitous": near_duplicate_ubiquitous,
        "split": split,
        "selection_type": str(example.selection_type),
        "selection_context": str(example.selection_context),
        "candidate_outcome": row.get("candidate_outcome"),
        "source_sample_weight": float(weight),
        "catalog_sha256": catalog_sha,
        "snapshot_sha256": snapshot_sha,
        "source_record_sha256": source_record_sha,
        "teacher_policy_sha256": policy_sha,
        "teacher_deck_sha256": deck_sha,
        "teacher_manifest_sha256": teacher_manifest_sha,
        "action_digests": legal_digests,
    }
    return (
        state,
        actions,
        target_set,
        target_count,
        example.min_count,
        example.max_count,
        metadata,
    )


def _write_shard(
    path: Path,
    samples: list[
        tuple[list[float], list[list[float]], list[bool], int, int, int, dict[str, Any]]
    ],
) -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    states: list[list[float]] = []
    actions: list[list[float]] = []
    targets: list[bool] = []
    target_count: list[int] = []
    minimum: list[int] = []
    maximum: list[int] = []
    metadata: list[dict[str, Any]] = []
    offsets = [0]
    for state, candidates, target_set, count, lower, upper, meta in samples:
        states.append(state)
        actions.extend(candidates)
        targets.extend(target_set)
        target_count.append(count)
        minimum.append(lower)
        maximum.append(upper)
        metadata.append(meta)
        offsets.append(offsets[-1] + len(candidates))
    payload = {
        "schema_version": GPU_SET_DATASET_SCHEMA,
        "purpose": PURPOSE,
        "state": torch.tensor(states, dtype=torch.float32),
        "actions": torch.tensor(actions, dtype=torch.float32),
        "offsets": torch.tensor(offsets, dtype=torch.int64),
        "target_set": torch.tensor(targets, dtype=torch.bool),
        "target_count": torch.tensor(target_count, dtype=torch.int64),
        "min_count": torch.tensor(minimum, dtype=torch.int64),
        "max_count": torch.tensor(maximum, dtype=torch.int64),
        "metadata": metadata,
    }
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        torch.save(payload, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": path.name,
        "examples": len(samples),
        "candidates": len(actions),
        "sha256": _sha256(path),
    }


def _verify_dataset_manifest(
    output_dir: Path, manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if set(manifest) != _GPU_MANIFEST_KEYS:
        raise GPUStudentV3SetError("GPU set manifest has an invalid closed schema")
    if (
        manifest.get("schema_version") != GPU_SET_DATASET_SCHEMA
        or manifest.get("purpose") != PURPOSE
        or manifest.get("authority") != _AUTHORITY
    ):
        raise GPUStudentV3SetError("GPU set manifest schema/purpose/authority mismatch")
    supplied_semantic_sha = _require_sha(
        manifest.get("dataset_sha256"), field="GPU set dataset semantic SHA-256"
    )
    semantic_payload = {
        key: value for key, value in manifest.items() if key != "dataset_sha256"
    }
    if _semantic_sha(
        semantic_payload, domain="offline-scaleup-gpu-set-dataset-v1"
    ) != supplied_semantic_sha:
        raise GPUStudentV3SetError("GPU set manifest semantic SHA-256 mismatch")
    if (
        manifest.get("feature_schema_version") != FEATURE_VERSION
        or manifest.get("state_dimension") != STATE_FEATURE_DIM
        or manifest.get("action_dimension") != ACTION_FEATURE_DIM
        or manifest.get("record_id_unique") is not True
        or manifest.get("episode_leakage") != 0
        or manifest.get("non_ubiquitous_near_duplicate_leakage") != 0
        or type(manifest.get("synthetic_test_only")) is not bool
        or type(manifest.get("ubiquitous_near_duplicate_ids")) is not list
        or any(
            type(value) is not str or _SHA256.fullmatch(value) is None
            for value in manifest.get("ubiquitous_near_duplicate_ids", [])
        )
        or len(set(manifest.get("ubiquitous_near_duplicate_ids", [])))
        != len(manifest.get("ubiquitous_near_duplicate_ids", []))
    ):
        raise GPUStudentV3SetError("GPU set manifest feature/integrity contract mismatch")
    source_value = manifest.get("source_dataset")
    if type(source_value) is not str or not source_value:
        raise GPUStudentV3SetError("GPU set source dataset path is invalid")
    source_path = Path(source_value).resolve()
    if source_path.is_symlink() or not source_path.is_file():
        raise GPUStudentV3SetError("GPU set source dataset is not a regular file")
    source_raw = source_path.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != manifest.get("source_dataset_sha256"):
        raise GPUStudentV3SetError("GPU set source dataset SHA-256 mismatch")
    if manifest.get("synthetic_test_only") is False:
        bridge_value = manifest.get("bridge_manifest_path")
        if type(bridge_value) is not str or not bridge_value:
            raise GPUStudentV3SetError("performance provenance bridge path is invalid")
        bridge_path = Path(bridge_value).resolve()
        if bridge_path.is_symlink() or not bridge_path.is_file():
            raise GPUStudentV3SetError("performance provenance bridge is not a regular file")
        bridge_raw = bridge_path.read_bytes()
        if hashlib.sha256(bridge_raw).hexdigest() != manifest.get("bridge_manifest_sha256"):
            raise GPUStudentV3SetError("performance provenance bridge SHA-256 mismatch")
        from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
            TeacherSnapshotStudentV3BridgeError,
            verify_teacher_snapshot_student_v3_bridge_manifest_v1,
        )

        repo_root = Path(__file__).resolve().parents[3]
        try:
            bridge = verify_teacher_snapshot_student_v3_bridge_manifest_v1(
                bridge_path, repo_root
            )
        except (TeacherSnapshotStudentV3BridgeError, OSError, ValueError) as exc:
            raise GPUStudentV3SetError("performance provenance bridge verification failed") from exc
        if bridge_path.read_bytes() != bridge_raw:
            raise GPUStudentV3SetError("performance provenance bridge changed during verification")
        if (
            bridge.get("bridge_sha256") != manifest.get("bridge_sha256")
            or bridge.get("catalog_sha256") != manifest.get("catalog_sha256")
            or bridge.get("selected_teacher_ids") != manifest.get("selected_teacher_ids")
            or Path(str(bridge.get("output_dataset"))).resolve() != source_path
            or bridge.get("output_dataset_sha256")
            != manifest.get("source_dataset_sha256")
        ):
            raise GPUStudentV3SetError("performance provenance bridge binding mismatch")
    elif (
        manifest.get("bridge_manifest_path") is not None
        or manifest.get("bridge_manifest_sha256") is not None
        or manifest.get("bridge_sha256") is not None
        or manifest.get("selected_teacher_ids") != ["SYNTHETIC_TEST_ONLY"]
    ):
        raise GPUStudentV3SetError("synthetic dataset provenance marker is invalid")
    if (
        type(manifest.get("records")) is not dict
        or set(manifest["records"]) != set(SPLITS)
        or type(manifest.get("episodes")) is not dict
        or set(manifest["episodes"]) != set(SPLITS)
        or any(type(value) is not int or value <= 0 for value in manifest["records"].values())
        or any(type(value) is not int or value <= 0 for value in manifest["episodes"].values())
    ):
        raise GPUStudentV3SetError("GPU set manifest split counts are invalid")
    shards = manifest.get("shards")
    if type(shards) is not list:
        raise GPUStudentV3SetError("GPU set manifest shards are invalid")
    seen_paths: set[str] = set()
    loaded: dict[str, dict[str, Any]] = {}
    record_ids: set[str] = set()
    episode_splits: dict[str, set[str]] = defaultdict(set)
    near_splits: dict[str, set[str]] = defaultdict(set)
    near_ubiquity: dict[str, bool] = {}
    observed_records: Counter[str] = Counter()
    observed_episodes: dict[str, set[str]] = defaultdict(set)
    observed_max_count = 0
    torch, _nn, _functional, _loader, _dataset = _torch()
    for shard in shards:
        if type(shard) is not dict or set(shard) != {
            "split", "path", "examples", "candidates", "sha256"
        }:
            raise GPUStudentV3SetError("GPU set shard binding is invalid")
        if shard.get("split") not in SPLITS:
            raise GPUStudentV3SetError("GPU set shard split is invalid")
        relative = shard.get("path")
        if (
            type(relative) is not str
            or not relative
            or Path(relative).is_absolute()
            or len(Path(relative).parts) != 1
            or relative in seen_paths
        ):
            raise GPUStudentV3SetError("GPU set shard paths must be unique direct children")
        seen_paths.add(relative)
        path = output_dir / relative
        if path.is_symlink() or not path.is_file():
            raise GPUStudentV3SetError(f"shard is not a regular direct child: {relative}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != shard.get("sha256"):
            raise GPUStudentV3SetError(f"shard digest mismatch: {path.name}")
        try:
            payload = _verify_shard_payload(
                torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
            )
        except (RuntimeError, EOFError, ValueError) as exc:
            raise GPUStudentV3SetError(f"shard payload is unreadable: {relative}") from exc
        if (
            shard.get("examples") != len(payload["metadata"])
            or shard.get("candidates") != len(payload["actions"])
        ):
            raise GPUStudentV3SetError("GPU set shard declared counts mismatch payload")
        split = shard["split"]
        for index, meta in enumerate(payload["metadata"]):
            if meta["split"] != split:
                raise GPUStudentV3SetError("GPU set shard metadata split mismatch")
            record_id = meta["record_id"]
            if record_id in record_ids:
                raise GPUStudentV3SetError("GPU set record IDs are not unique")
            record_ids.add(record_id)
            observed_records[split] += 1
            observed_episodes[split].add(meta["episode_id"])
            episode_splits[meta["episode_id"]].add(split)
            near_id = meta["near_duplicate_id"]
            ubiquitous = meta["near_duplicate_ubiquitous"]
            if type(ubiquitous) is not bool:
                raise GPUStudentV3SetError("GPU set near-duplicate ubiquity is invalid")
            if near_id in near_ubiquity and near_ubiquity[near_id] is not ubiquitous:
                raise GPUStudentV3SetError("GPU set near-duplicate ubiquity disagrees")
            near_ubiquity[near_id] = ubiquitous
            near_splits[near_id].add(split)
            observed_max_count = max(observed_max_count, int(payload["max_count"][index]))
        loaded[relative] = payload
    if dict(observed_records) != manifest["records"] or {
        split: len(observed_episodes[split]) for split in SPLITS
    } != manifest["episodes"]:
        raise GPUStudentV3SetError("GPU set manifest aggregate counts mismatch payloads")
    if any(len(values) > 1 for values in episode_splits.values()):
        raise GPUStudentV3SetError("GPU set episode identity crosses payload splits")
    if any(
        len(values) > 1 and not near_ubiquity[near_id]
        for near_id, values in near_splits.items()
    ):
        raise GPUStudentV3SetError("GPU set non-ubiquitous identity crosses payload splits")
    if observed_max_count != manifest.get("max_count_class"):
        raise GPUStudentV3SetError("GPU set maximum count aggregate mismatch")
    if sorted(key for key, value in near_ubiquity.items() if value) != manifest.get(
        "ubiquitous_near_duplicate_ids"
    ):
        raise GPUStudentV3SetError("GPU set ubiquitous identity aggregate mismatch")
    return loaded


def _verify_shard_payload(payload: object) -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    if type(payload) is not dict or set(payload) != _GPU_SHARD_KEYS:
        raise GPUStudentV3SetError("GPU set shard has an invalid closed schema")
    if (
        payload.get("schema_version") != GPU_SET_DATASET_SCHEMA
        or payload.get("purpose") != PURPOSE
    ):
        raise GPUStudentV3SetError("GPU set shard schema/purpose mismatch")
    state = payload["state"]
    actions = payload["actions"]
    offsets = payload["offsets"]
    target_set = payload["target_set"]
    target_count = payload["target_count"]
    minimum = payload["min_count"]
    maximum = payload["max_count"]
    metadata = payload["metadata"]
    count = int(state.shape[0]) if isinstance(state, torch.Tensor) and state.ndim == 2 else -1
    candidates = (
        int(actions.shape[0])
        if isinstance(actions, torch.Tensor) and actions.ndim == 2
        else -1
    )
    if (
        count <= 0
        or candidates <= 0
        or state.shape[1] != STATE_FEATURE_DIM
        or actions.shape[1] != ACTION_FEATURE_DIM
        or not isinstance(offsets, torch.Tensor)
        or offsets.ndim != 1
        or len(offsets) != count + 1
        or not isinstance(target_set, torch.Tensor)
        or target_set.ndim != 1
        or len(target_set) != candidates
        or target_set.dtype != torch.bool
        or any(
            not isinstance(value, torch.Tensor)
            or value.ndim != 1
            or len(value) != count
            for value in (target_count, minimum, maximum)
        )
        or type(metadata) is not list
        or len(metadata) != count
    ):
        raise GPUStudentV3SetError("GPU set shard tensor dimensions are invalid")
    if (
        state.dtype != torch.float32
        or actions.dtype != torch.float32
        or offsets.dtype != torch.int64
        or target_count.dtype != torch.int64
        or minimum.dtype != torch.int64
        or maximum.dtype != torch.int64
    ):
        raise GPUStudentV3SetError("GPU set shard tensor dtype is invalid")
    if not bool(torch.isfinite(state).all().item()) or not bool(
        torch.isfinite(actions).all().item()
    ):
        raise GPUStudentV3SetError("GPU set shard feature tensor is not finite")
    offset_values = [int(value) for value in offsets.tolist()]
    if (
        offset_values[0] != 0
        or offset_values[-1] != candidates
        or any(left >= right for left, right in zip(offset_values, offset_values[1:]))
    ):
        raise GPUStudentV3SetError("GPU set shard offsets are invalid")
    for index, (start, end) in enumerate(zip(offset_values, offset_values[1:])):
        observed = int(target_set[start:end].sum().item())
        lower = int(minimum[index].item())
        upper = int(maximum[index].item())
        target = int(target_count[index].item())
        if observed != target or not 0 <= lower <= target <= upper <= end - start:
            raise GPUStudentV3SetError("GPU set shard target/count bounds disagree")
        meta = metadata[index]
        if type(meta) is not dict or set(meta) != _SHARD_METADATA_KEYS:
            raise GPUStudentV3SetError("GPU set shard metadata is invalid")
        _require_sha(meta.get("record_id"), field="shard metadata record_id")
        _require_sha(meta.get("episode_id"), field="shard metadata episode_id")
        digests = meta.get("action_digests")
        if (
            type(digests) is not list
            or len(digests) != end - start
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
        ):
            raise GPUStudentV3SetError("GPU set shard action digest metadata is invalid")
    return payload


def build_set_dataset(
    *,
    source: Path,
    output_dir: Path,
    shard_size: int = 4096,
    bridge_manifest: Path | None = None,
    synthetic_test_only: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a lossless, hash-bound ragged GPU dataset."""
    source = Path(source).resolve()
    output_dir = Path(output_dir).resolve()
    if not source.is_file():
        raise GPUStudentV3SetError("source dataset is not a regular file")
    if type(shard_size) is not int or shard_size < 1:
        raise GPUStudentV3SetError("shard size must be positive")
    source_sha = _sha256(source)
    bridge_binding: dict[str, Any]
    bridge_catalog_sha: str | None = None
    bridge_teacher_identities: set[tuple[str, str, str]] | None = None
    if bridge_manifest is None:
        if synthetic_test_only is not True:
            raise GPUStudentV3SetError(
                "bridge manifest is required for a performance dataset"
            )
        bridge_binding = {
            "bridge_manifest_path": None,
            "bridge_manifest_sha256": None,
            "bridge_sha256": None,
            "selected_teacher_ids": ["SYNTHETIC_TEST_ONLY"],
            "synthetic_test_only": True,
        }
    else:
        if synthetic_test_only is not False:
            raise GPUStudentV3SetError(
                "synthetic_test_only cannot accompany a bridge manifest"
            )
        bridge_path = Path(bridge_manifest).resolve()
        try:
            raw_bridge = bridge_path.read_bytes()
            bridge = json.loads(raw_bridge.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GPUStudentV3SetError("bridge manifest is unreadable") from exc
        if type(bridge) is not dict or _canonical_bytes(bridge) != raw_bridge:
            raise GPUStudentV3SetError("bridge manifest must be canonical JSON")
        from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
            BRIDGE_SCHEMA_V1,
            TeacherSnapshotStudentV3BridgeError,
            verify_teacher_snapshot_student_v3_bridge_manifest_v1,
        )

        verification_root = (
            Path(repo_root).resolve()
            if repo_root is not None
            else Path(__file__).resolve().parents[3]
        )
        try:
            formally_verified = verify_teacher_snapshot_student_v3_bridge_manifest_v1(
                bridge_path, verification_root
            )
        except (TeacherSnapshotStudentV3BridgeError, OSError, ValueError) as exc:
            raise GPUStudentV3SetError("formal bridge verification failed") from exc
        if formally_verified != bridge or bridge_path.read_bytes() != raw_bridge:
            raise GPUStudentV3SetError("bridge manifest changed during formal verification")
        required_bridge_keys = {
            "schema_version", "purpose", "catalog_path", "catalog_file_sha256", "catalog_sha256",
            "decision_sha256", "selected_teacher_ids", "sources", "trainer_contract",
            "feature_boundary", "compatibility", "split",
            "performance_training_ready", "blocked_reasons", "output_dataset",
            "output_dataset_sha256", "output_rows", "partial_dataset_published",
            "authority", "bridge_sha256",
        }
        if set(bridge) != required_bridge_keys:
            raise GPUStudentV3SetError("bridge manifest has an invalid closed schema")
        selected_teacher_ids = bridge.get("selected_teacher_ids")
        sources = bridge.get("sources")
        compatibility = bridge.get("compatibility")
        authority = bridge.get("authority")
        if (
            BRIDGE_SCHEMA_V1 != TEACHER_BRIDGE_SCHEMA
            or bridge.get("schema_version") != TEACHER_BRIDGE_SCHEMA
            or bridge.get("purpose") != PURPOSE
            or bridge.get("performance_training_ready") is not True
            or bridge.get("blocked_reasons") != []
            or bridge.get("partial_dataset_published") is not False
            or type(selected_teacher_ids) is not list
            or not selected_teacher_ids
            or len(selected_teacher_ids) != len(set(selected_teacher_ids))
            or any(type(value) is not str or not value for value in selected_teacher_ids)
            or type(sources) is not list
            or len(sources) != len(selected_teacher_ids)
            or type(compatibility) is not dict
            or compatibility.get("unsupported_total") != 0
            or type(authority) is not dict
            or any(value is not False for value in authority.values())
        ):
            raise GPUStudentV3SetError("bridge manifest is not performance-training ready")
        expected_bridge_sha = hashlib.sha256(
            TEACHER_BRIDGE_SCHEMA.encode("ascii")
            + b"\0"
            + _canonical_bytes(
                {key: value for key, value in bridge.items() if key != "bridge_sha256"}
            )
        ).hexdigest()
        if bridge.get("bridge_sha256") != expected_bridge_sha:
            raise GPUStudentV3SetError("bridge semantic SHA-256 mismatch")
        source_teacher_ids: list[str] = []
        bridge_teacher_identities = set()
        source_rows = 0
        for source_binding in sources:
            if type(source_binding) is not dict or set(source_binding) != _BRIDGE_SOURCE_KEYS:
                raise GPUStudentV3SetError("bridge source binding has an invalid closed schema")
            teacher_id = source_binding.get("teacher_id")
            if type(teacher_id) is not str or not teacher_id:
                raise GPUStudentV3SetError("bridge source teacher_id is invalid")
            source_teacher_ids.append(teacher_id)
            policy_sha = _require_sha(
                source_binding.get("policy_sha256"), field="bridge source policy SHA-256"
            )
            deck_sha = _require_sha(
                source_binding.get("deck_sha256"), field="bridge source deck SHA-256"
            )
            teacher_manifest_sha = _require_sha(
                source_binding.get("teacher_manifest_sha256"),
                field="bridge source teacher manifest SHA-256",
            )
            for field in (
                "permission_manifest_id", "permission_trusted_bytes_sha256",
                "dataset_snapshot_sha256", "snapshot_index_sha256",
            ):
                _require_sha(source_binding.get(field), field=f"bridge source {field}")
            if (
                source_binding.get("native_code_bundled") is not False
                or source_binding.get("native_deck_bundled") is not False
            ):
                raise GPUStudentV3SetError("bridge source may not bundle native assets")
            records = source_binding.get("source_records")
            decisions = source_binding.get("trainable_decisions")
            episodes = source_binding.get("source_episodes")
            trainable_episodes = source_binding.get("trainable_episodes")
            if (
                type(records) is not int or records < 1
                or decisions != records
                or type(episodes) is not int or episodes < 1
                or trainable_episodes != episodes
            ):
                raise GPUStudentV3SetError("bridge source count binding is invalid")
            chunks = source_binding.get("dataset_chunks")
            shards = source_binding.get("snapshot_shards")
            if (
                type(chunks) is not list or not chunks
                or any(type(row) is not dict or set(row) != _BRIDGE_DATASET_CHUNK_KEYS for row in chunks)
                or [row.get("position") for row in chunks] != list(range(len(chunks)))
                or any(
                    _SHA256.fullmatch(str(row.get(field))) is None
                    for row in chunks for field in ("sha256", "manifest_id", "manifest_content_hash")
                )
                or type(shards) is not list or not shards
                or any(type(row) is not dict or set(row) != _BRIDGE_SNAPSHOT_SHARD_KEYS for row in shards)
                or any(
                    _SHA256.fullmatch(str(row.get(field))) is None
                    for row in shards for field in ("snapshot_id", "sha256")
                )
                or any(type(row.get("examples")) is not int or row["examples"] < 1 for row in shards)
                or sum(row["examples"] for row in shards) != records
            ):
                raise GPUStudentV3SetError("bridge source chunk/shard binding is invalid")
            split_audit = source_binding.get("sealed_split_audit")
            if (
                type(split_audit) is not dict
                or split_audit.get("episode_split_intersection_count") != 0
                or split_audit.get(
                    "non_ubiquitous_near_duplicate_split_intersection_count"
                ) != 0
            ):
                raise GPUStudentV3SetError("bridge source split audit is unsafe")
            source_rows += records
            bridge_teacher_identities.add((policy_sha, deck_sha, teacher_manifest_sha))
        if source_teacher_ids != selected_teacher_ids or source_rows != bridge.get("output_rows"):
            raise GPUStudentV3SetError("bridge sources do not bind selected teachers/output rows")
        bridge_catalog_sha = _require_sha(
            bridge.get("catalog_sha256"), field="bridge catalog SHA-256"
        )
        if Path(str(bridge.get("output_dataset"))).resolve() != source:
            raise GPUStudentV3SetError("bridge output dataset path mismatch")
        if bridge.get("output_dataset_sha256") != source_sha:
            raise GPUStudentV3SetError("bridge output dataset SHA-256 mismatch")
        if bridge.get("output_rows") != sum(1 for _line in source.open("rb")):
            raise GPUStudentV3SetError("bridge output row count mismatch")
        bridge_binding = {
            "bridge_manifest_path": str(bridge_path),
            "bridge_manifest_sha256": hashlib.sha256(raw_bridge).hexdigest(),
            "bridge_sha256": _require_sha(
                bridge.get("bridge_sha256"), field="bridge semantic SHA-256"
            ),
            "selected_teacher_ids": list(selected_teacher_ids),
            "synthetic_test_only": False,
        }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GPUStudentV3SetError("existing GPU set manifest is unreadable") from exc
        if manifest.get("source_dataset_sha256") != source_sha:
            raise GPUStudentV3SetError("existing GPU set dataset has a different source SHA-256")
        _verify_dataset_manifest(output_dir, manifest)
        return manifest
    if output_dir.exists() and any(output_dir.iterdir()):
        raise GPUStudentV3SetError("output directory contains an uncommitted partial dataset")

    by_split: dict[
        str,
        list[
            tuple[
                list[float],
                list[list[float]],
                list[bool],
                int,
                int,
                int,
                dict[str, Any],
            ]
        ],
    ] = {split: [] for split in SPLITS}
    seen_records: set[str] = set()
    episode_splits: dict[str, set[str]] = defaultdict(set)
    near_duplicate_splits: dict[str, set[str]] = defaultdict(set)
    near_duplicate_ubiquity: dict[str, bool] = {}
    catalog_shas: set[str] = set()
    source_teacher_identities: set[tuple[str, str, str]] = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise GPUStudentV3SetError(f"blank source line {line_number}")
            row = _strict_json_line(line, line_number=line_number)
            sample = _sample_from_source_row(row)
            record_id = sample[6]["record_id"]
            if record_id in seen_records:
                raise GPUStudentV3SetError("source record_id is duplicated")
            seen_records.add(record_id)
            split = sample[6]["split"]
            by_split[split].append(sample)
            episode_splits[sample[6]["episode_id"]].add(split)
            near_id = sample[6]["near_duplicate_id"]
            declared_ubiquitous = sample[6]["near_duplicate_ubiquitous"]
            prior_ubiquitous = near_duplicate_ubiquity.setdefault(
                near_id, declared_ubiquitous
            )
            if prior_ubiquitous is not declared_ubiquitous:
                raise GPUStudentV3SetError(
                    "near-duplicate ubiquity declaration is inconsistent"
                )
            near_duplicate_splits[near_id].add(split)
            catalog_shas.add(sample[6]["catalog_sha256"])
            source_teacher_identities.add(
                (
                    sample[6]["teacher_policy_sha256"],
                    sample[6]["teacher_deck_sha256"],
                    sample[6]["teacher_manifest_sha256"],
                )
            )
    if not seen_records:
        raise GPUStudentV3SetError("source dataset is empty")
    if len(catalog_shas) != 1:
        raise GPUStudentV3SetError("source rows disagree on catalog SHA-256")
    if bridge_catalog_sha is not None and next(iter(catalog_shas)) != bridge_catalog_sha:
        raise GPUStudentV3SetError("source rows do not bind the bridge catalog SHA-256")
    if (
        bridge_teacher_identities is not None
        and source_teacher_identities != bridge_teacher_identities
    ):
        raise GPUStudentV3SetError("source rows do not bind the bridge teacher identities")
    if any(not values for values in by_split.values()):
        missing = sorted(split for split, values in by_split.items() if not values)
        raise GPUStudentV3SetError(f"GPU set dataset has empty splits: {missing}")
    leakage = sum(len(splits) > 1 for splits in episode_splits.values())
    if leakage:
        raise GPUStudentV3SetError("episode identity crosses GPU set dataset splits")
    non_ubiquitous_near_leakage = sum(
        len(splits) > 1 and not near_duplicate_ubiquity[near_id]
        for near_id, splits in near_duplicate_splits.items()
    )
    if non_ubiquitous_near_leakage:
        raise GPUStudentV3SetError(
            "non-ubiquitous near-duplicate identity crosses GPU set dataset splits"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    shards: list[dict[str, Any]] = []
    created: list[Path] = []
    try:
        for split in SPLITS:
            values = by_split[split]
            for offset in range(0, len(values), shard_size):
                path = output_dir / f"{split}-{offset // shard_size:05d}.pt"
                binding = _write_shard(path, values[offset : offset + shard_size])
                created.append(path)
                shards.append({"split": split, **binding})
        manifest: dict[str, Any] = {
            "schema_version": GPU_SET_DATASET_SCHEMA,
            "purpose": PURPOSE,
            "source_dataset": str(source),
            "source_dataset_sha256": source_sha,
            "catalog_sha256": next(iter(catalog_shas)),
            **bridge_binding,
            "feature_schema_version": FEATURE_VERSION,
            "state_dimension": STATE_FEATURE_DIM,
            "action_dimension": ACTION_FEATURE_DIM,
            "max_count_class": max(sample[5] for values in by_split.values() for sample in values),
            "records": {split: len(by_split[split]) for split in SPLITS},
            "episodes": {
                split: len({sample[6]["episode_id"] for sample in by_split[split]})
                for split in SPLITS
            },
            "record_id_unique": True,
            "episode_leakage": 0,
            "non_ubiquitous_near_duplicate_leakage": 0,
            "ubiquitous_near_duplicate_ids": sorted(
                near_id
                for near_id, ubiquitous in near_duplicate_ubiquity.items()
                if ubiquitous
            ),
            "shards": shards,
            "deterministic_order": "source-jsonl-order",
            "feature_boundary": {
                "model_inputs": [
                    "rule_bc_example.public_state",
                    "rule_bc_example.own_private_state",
                    "rule_bc_example.visible_history",
                    "rule_bc_example.legal_actions",
                ],
                "metadata_excluded_from_features": [
                    "teacher_identity",
                    "opponent_id",
                    "candidate_side",
                    "record_id",
                ],
            },
            "authority": dict(_AUTHORITY),
        }
        manifest["dataset_sha256"] = _semantic_sha(
            manifest, domain="offline-scaleup-gpu-set-dataset-v1"
        )
        _atomic_json(manifest_path, manifest)
        return manifest
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _load_split(dataset_dir: Path, split: str) -> list[dict[str, Any]]:
    if split not in SPLITS:
        raise GPUStudentV3SetError("unknown GPU set split")
    manifest_path = Path(dataset_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GPUStudentV3SetError("GPU set manifest is unreadable") from exc
    loaded = _verify_dataset_manifest(Path(dataset_dir), manifest)
    values: list[dict[str, Any]] = []
    for shard in manifest["shards"]:
        if shard["split"] != split:
            continue
        values.append(loaded[shard["path"]])
    if not values:
        raise GPUStudentV3SetError(f"GPU set dataset split is empty: {split}")
    return values


def _verified_split_payloads(
    manifest: Mapping[str, Any],
    loaded: Mapping[str, dict[str, Any]],
    split: str,
) -> list[dict[str, Any]]:
    """Select one split from an already verified immutable payload snapshot."""
    if split not in SPLITS:
        raise GPUStudentV3SetError("unknown GPU set split")
    values = [
        loaded[shard["path"]]
        for shard in manifest["shards"]
        if shard["split"] == split
    ]
    if not values:
        raise GPUStudentV3SetError(f"GPU set dataset split is empty: {split}")
    return values


def _examples(
    shards: Iterable[dict[str, Any]],
) -> Iterator[tuple[Any, Any, Any, int, int, int, dict[str, Any]]]:
    for shard in shards:
        for index, metadata in enumerate(shard["metadata"]):
            start = int(shard["offsets"][index])
            end = int(shard["offsets"][index + 1])
            yield (
                shard["state"][index],
                shard["actions"][start:end],
                shard["target_set"][start:end],
                int(shard["target_count"][index]),
                int(shard["min_count"][index]),
                int(shard["max_count"][index]),
                metadata,
            )


def _collate_set(
    batch: list[tuple[Any, Any, Any, int, int, int, dict[str, Any]]],
) -> dict[str, Any]:
    if not batch:
        raise GPUStudentV3SetError("cannot collate an empty batch")
    torch, _nn, _functional, _loader, _dataset = _torch()
    maximum = max(int(actions.shape[0]) for _state, actions, *_rest in batch)
    actions = torch.zeros((len(batch), maximum, ACTION_FEATURE_DIM), dtype=torch.float32)
    legal_mask = torch.zeros((len(batch), maximum), dtype=torch.bool)
    target_set = torch.zeros((len(batch), maximum), dtype=torch.bool)
    for index, (_state, candidate, target, _count, _minimum, _maximum, _meta) in enumerate(batch):
        count = int(candidate.shape[0])
        actions[index, :count] = candidate
        legal_mask[index, :count] = True
        target_set[index, :count] = target
    return {
        "state": torch.stack([item[0] for item in batch]),
        "actions": actions,
        "legal_mask": legal_mask,
        "target_set": target_set,
        "target_count": torch.tensor([item[3] for item in batch], dtype=torch.long),
        "min_count": torch.tensor([item[4] for item in batch], dtype=torch.long),
        "max_count": torch.tensor([item[5] for item in batch], dtype=torch.long),
        "metadata": [item[6] for item in batch],
    }


def make_set_cardinality_model(
    *, hidden: int, blocks: int, dropout: float, max_count: int
) -> Any:
    """Construct a permutation-equivariant action scorer and invariant count head."""
    if type(hidden) is not int or hidden < 4:
        raise GPUStudentV3SetError("hidden dimension must be at least four")
    if type(blocks) is not int or blocks < 0:
        raise GPUStudentV3SetError("block count must be non-negative")
    if type(dropout) not in (int, float) or not 0.0 <= float(dropout) < 1.0:
        raise GPUStudentV3SetError("dropout must be in [0, 1)")
    if type(max_count) is not int or max_count < 0:
        raise GPUStudentV3SetError("max_count must be non-negative")
    _torch_module, nn, _functional, _loader, _dataset = _torch()

    class Residual(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.LayerNorm(hidden),
                nn.Linear(hidden, hidden * 2),
                nn.GELU(),
                nn.Dropout(float(dropout)),
                nn.Linear(hidden * 2, hidden),
            )

        def forward(self, value: Any) -> Any:
            return value + self.net(value)

    class SetCardinalityRanker(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.max_count = max_count
            self.state_encoder = nn.Sequential(
                nn.Linear(STATE_FEATURE_DIM, hidden),
                nn.LayerNorm(hidden),
                nn.GELU(),
            )
            self.action_encoder = nn.Sequential(
                nn.Linear(ACTION_FEATURE_DIM, hidden),
                nn.LayerNorm(hidden),
                nn.GELU(),
            )
            self.blocks = nn.Sequential(*(Residual() for _ in range(blocks)))
            self.action_head = nn.Sequential(
                nn.LayerNorm(hidden),
                nn.Linear(hidden, hidden),
                nn.GELU(),
                nn.Linear(hidden, 1),
            )
            self.count_head = nn.Sequential(
                nn.LayerNorm(hidden * 3),
                nn.Linear(hidden * 3, hidden),
                nn.GELU(),
                nn.Dropout(float(dropout)),
                nn.Linear(hidden, max_count + 1),
            )

        def forward(self, state: Any, actions: Any, legal_mask: Any) -> tuple[Any, Any]:
            if state.ndim != 2 or actions.ndim != 3 or legal_mask.ndim != 2:
                raise GPUStudentV3SetError("set model received tensors with invalid ranks")
            if actions.shape[:2] != legal_mask.shape or state.shape[0] != actions.shape[0]:
                raise GPUStudentV3SetError("set model batch dimensions do not align")
            if not bool(legal_mask.any(dim=1).all().item()):
                raise GPUStudentV3SetError("set model requires at least one legal action per row")
            encoded_state = self.state_encoder(state)
            encoded_action = self.action_encoder(actions)
            joint = self.blocks(
                encoded_state.unsqueeze(1)
                + encoded_action
                + encoded_state.unsqueeze(1) * encoded_action
            )
            action_logits = self.action_head(joint).squeeze(-1)
            action_logits = action_logits.masked_fill(~legal_mask, float("-inf"))
            mask = legal_mask.unsqueeze(-1)
            denominator = legal_mask.sum(dim=1, keepdim=True).to(joint.dtype)
            pooled_mean = joint.masked_fill(~mask, 0.0).sum(dim=1) / denominator
            pooled_max = joint.masked_fill(~mask, float("-inf")).amax(dim=1)
            count_logits = self.count_head(
                _torch_module.cat((encoded_state, pooled_mean, pooled_max), dim=-1)
            )
            return action_logits, count_logits

    return SetCardinalityRanker()


def _masked_count_logits(
    count_logits: Any,
    minimum: Any,
    maximum: Any,
    legal_count: Any,
) -> Any:
    torch, _nn, _functional, _loader, _dataset = _torch()
    if count_logits.ndim != 2:
        raise GPUStudentV3SetError("count logits must have rank two")
    if any(value.ndim != 1 for value in (minimum, maximum, legal_count)):
        raise GPUStudentV3SetError("count bounds must have rank one")
    if not (
        count_logits.shape[0] == minimum.shape[0] == maximum.shape[0] == legal_count.shape[0]
    ):
        raise GPUStudentV3SetError("count bounds do not align with count logits")
    max_class = count_logits.shape[1] - 1
    if bool(
        (
            (minimum < 0)
            | (maximum < minimum)
            | (maximum > legal_count)
            | (maximum > max_class)
        ).any().item()
    ):
        raise GPUStudentV3SetError("count bounds are outside the legal count classes")
    classes = torch.arange(count_logits.shape[1], device=count_logits.device).unsqueeze(0)
    legal = (classes >= minimum.unsqueeze(1)) & (classes <= maximum.unsqueeze(1))
    if not bool(torch.isfinite(count_logits[legal]).all().item()):
        raise GPUStudentV3SetError("model produced non-finite legal count logits")
    return count_logits.masked_fill(~legal, float("-inf"))


def set_cardinality_loss(
    action_logits: Any,
    count_logits: Any,
    batch: Mapping[str, Any],
    *,
    count_loss_weight: float = 1.0,
    reduction: str = "mean",
) -> dict[str, Any]:
    """Compute legal-normalised set BCE plus legal-count-masked CE."""
    torch, _nn, functional, _loader, _dataset = _torch()
    if reduction not in {"none", "mean"}:
        raise GPUStudentV3SetError("loss reduction must be none or mean")
    if type(count_loss_weight) not in (int, float) or not math.isfinite(
        float(count_loss_weight)
    ) or float(count_loss_weight) < 0:
        raise GPUStudentV3SetError("count loss weight must be finite and non-negative")
    try:
        legal = batch["legal_mask"].bool()
        target_set = batch["target_set"].bool()
        target_count = batch["target_count"].long()
        minimum = batch["min_count"].long()
        maximum = batch["max_count"].long()
    except (KeyError, AttributeError) as exc:
        raise GPUStudentV3SetError("set loss batch is incomplete") from exc
    if action_logits.shape != legal.shape or target_set.shape != legal.shape:
        raise GPUStudentV3SetError("action/set/mask tensor shapes do not align")
    if not bool(legal.any(dim=1).all().item()):
        raise GPUStudentV3SetError("set loss received an empty legal action row")
    if bool((target_set & ~legal).any().item()):
        raise GPUStudentV3SetError("target set includes a non-legal action")
    if not bool(torch.isfinite(action_logits[legal]).all().item()):
        raise GPUStudentV3SetError("model produced non-finite legal action logits")
    observed_count = target_set.sum(dim=1).long()
    if bool((observed_count != target_count).any().item()):
        raise GPUStudentV3SetError("target set and target count disagree")
    legal_count = legal.sum(dim=1).long()
    masked_counts = _masked_count_logits(count_logits, minimum, maximum, legal_count)
    if bool(((target_count < minimum) | (target_count > maximum)).any().item()):
        raise GPUStudentV3SetError("target count violates its legal bounds")

    safe_logits = action_logits.masked_fill(~legal, 0.0)
    per_candidate = functional.binary_cross_entropy_with_logits(
        safe_logits,
        target_set.to(safe_logits.dtype),
        reduction="none",
    )
    set_per_example = (per_candidate * legal.to(per_candidate.dtype)).sum(dim=1) / legal_count
    count_per_example = functional.cross_entropy(
        masked_counts,
        target_count,
        reduction="none",
    )
    total_per_example = set_per_example + float(count_loss_weight) * count_per_example
    if not bool(torch.isfinite(total_per_example).all().item()):
        raise GPUStudentV3SetError("set/cardinality loss is non-finite")
    if reduction == "none":
        return {
            "total": total_per_example,
            "set": set_per_example,
            "count": count_per_example,
        }
    return {
        "total": total_per_example.mean(),
        "set": set_per_example.mean(),
        "count": count_per_example.mean(),
    }


def decode_set_predictions(
    action_logits: Any,
    count_logits: Any,
    legal_mask: Any,
    minimum: Any,
    maximum: Any,
    action_digests: Sequence[Sequence[str]],
) -> list[list[int]]:
    """Decode with the same Stable ActionKey tie break as the live runtime."""
    torch, _nn, _functional, _loader, _dataset = _torch()
    if action_logits.shape != legal_mask.shape:
        raise GPUStudentV3SetError("decode action logits and legal mask do not align")
    legal_count = legal_mask.sum(dim=1).long()
    masked_counts = _masked_count_logits(count_logits, minimum, maximum, legal_count)
    counts = masked_counts.argmax(dim=1)
    if (
        len(action_digests) != action_logits.shape[0]
        or any(type(row) not in (list, tuple) for row in action_digests)
    ):
        raise GPUStudentV3SetError("decode action digest rows do not align")
    results: list[list[int]] = []
    for row in range(action_logits.shape[0]):
        indices = torch.nonzero(legal_mask[row], as_tuple=False).flatten().tolist()
        digests = action_digests[row]
        if (
            len(digests) != len(indices)
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
        ):
            raise GPUStudentV3SetError("decode action digests are invalid")
        if any(not math.isfinite(float(action_logits[row, index].item())) for index in indices):
            raise GPUStudentV3SetError("decode received non-finite legal action score")
        digest_by_index = dict(zip(indices, digests, strict=True))
        ranked = sorted(
            indices,
            key=lambda index: (
                -float(action_logits[row, index]),
                digest_by_index[index],
                index,
            ),
        )
        results.append(ranked[: int(counts[row].item())])
    return results


def _strict_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()

        def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise GPUStudentV3SetError("duplicate JSON key in weight sidecar")
                value[key] = item
            return value

        def reject_constant(value: str) -> object:
            raise GPUStudentV3SetError(f"non-finite weight sidecar JSON value: {value}")

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GPUStudentV3SetError("weight sidecar is unreadable strict JSON") from exc
    if type(payload) is not dict:
        raise GPUStudentV3SetError("weight sidecar must be a JSON object")
    if _canonical_bytes(payload) != raw:
        raise GPUStudentV3SetError("weight sidecar must use exact canonical JSON bytes")
    return payload


def load_training_weight_sidecar(
    path: Path,
    *,
    dataset_manifest_sha256: str,
    catalog_sha256: str,
    train_record_ids: Sequence[str],
) -> tuple[dict[str, float], dict[str, Any]]:
    """Strictly join one positive external AWR weight to every train record."""
    payload = _strict_json_file(Path(path))
    expected_keys = {
        "schema_version",
        "objective_kind",
        "dataset_manifest_sha256",
        "catalog_sha256",
        "weights",
        "authority",
    }
    if set(payload) != expected_keys:
        raise GPUStudentV3SetError("weight sidecar has an invalid closed schema")
    if (
        payload.get("schema_version") != WEIGHT_SIDECAR_SCHEMA
        or payload.get("objective_kind") != "AWR_FINE_TUNE"
        or payload.get("authority") != _AUTHORITY
    ):
        raise GPUStudentV3SetError("weight sidecar schema/objective/authority mismatch")
    if payload.get("dataset_manifest_sha256") != _require_sha(
        dataset_manifest_sha256, field="dataset manifest SHA-256"
    ):
        raise GPUStudentV3SetError("weight sidecar dataset manifest SHA-256 mismatch")
    if payload.get("catalog_sha256") != _require_sha(
        catalog_sha256, field="catalog SHA-256"
    ):
        raise GPUStudentV3SetError("weight sidecar catalog SHA-256 mismatch")
    rows = payload.get("weights")
    if type(rows) is not list:
        raise GPUStudentV3SetError("weight sidecar weights must be a list")
    joined: dict[str, float] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"record_id", "weight"}:
            raise GPUStudentV3SetError("weight row has an invalid closed schema")
        record_id = _require_sha(row.get("record_id"), field="weight record_id")
        if record_id in joined:
            raise GPUStudentV3SetError("weight sidecar record_id is duplicated")
        raw_weight = row.get("weight")
        if (
            type(raw_weight) not in (int, float)
            or not math.isfinite(float(raw_weight))
            or float(raw_weight) <= 0
        ):
            raise GPUStudentV3SetError("weight must be finite and positive")
        joined[record_id] = float(raw_weight)
    expected = set(train_record_ids)
    if len(expected) != len(train_record_ids):
        raise GPUStudentV3SetError("train dataset record_id is duplicated")
    missing = expected - set(joined)
    extra = set(joined) - expected
    if missing:
        raise GPUStudentV3SetError(f"weight sidecar is missing {len(missing)} train records")
    if extra:
        raise GPUStudentV3SetError(
            f"weight sidecar has {len(extra)} extra or non-train records"
        )
    values = list(joined.values())
    mass = sum(values)
    square_mass = sum(value * value for value in values)
    return joined, {
        "weight_sidecar_sha256": _sha256(Path(path)),
        "external_weight_mass": mass,
        "external_weight_ess": mass * mass / square_mass,
        "external_weight_min": min(values),
        "external_weight_max": max(values),
        "joined_train_records": len(values),
    }


def _device(requested: str) -> tuple[Any, str]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    if requested.startswith("cuda"):
        if not torch.cuda.is_available():
            raise GPUStudentV3SetError("CUDA requested but unavailable")
        device = torch.device(requested)
        return device, "bf16" if torch.cuda.is_bf16_supported() else "fp16"
    if requested != "cpu":
        raise GPUStudentV3SetError("device must be cpu or cuda[:index]")
    return torch.device("cpu"), "fp32"


def _model_config(*, hidden: int, blocks: int, dropout: float, max_count: int) -> dict[str, Any]:
    return {
        "schema_version": STUDENT_V3_SET_SCHEMA,
        "feature_schema_version": FEATURE_VERSION,
        "state_dimension": STATE_FEATURE_DIM,
        "action_dimension": ACTION_FEATURE_DIM,
        "hidden": hidden,
        "blocks": blocks,
        "dropout": float(dropout),
        "max_count": max_count,
    }


def _config_sha(config: Mapping[str, Any], *, domain: str) -> str:
    return _semantic_sha(dict(config), domain=domain)


def _evaluate_set(
    model: Any,
    values: list[tuple[Any, Any, Any, int, int, int, dict[str, Any]]],
    *,
    device: Any,
    batch_size: int,
    count_loss_weight: float,
) -> dict[str, Any]:
    torch, _nn, _functional, DataLoader, Dataset = _torch()

    class Values(Dataset):
        def __len__(self) -> int:
            return len(values)

        def __getitem__(self, index: int) -> Any:
            return values[index]

    loader = DataLoader(
        Values(), batch_size=batch_size, shuffle=False, collate_fn=_collate_set
    )
    total = 0
    exact = 0
    correct_count = 0
    total_loss = 0.0
    total_set_loss = 0.0
    total_count_loss = 0.0
    model.eval()
    with torch.no_grad():
        for cpu_batch in loader:
            batch = {
                key: value.to(device) if hasattr(value, "to") else value
                for key, value in cpu_batch.items()
                if key != "metadata"
            }
            action_logits, count_logits = model(
                batch["state"], batch["actions"], batch["legal_mask"]
            )
            losses = set_cardinality_loss(
                action_logits,
                count_logits,
                batch,
                count_loss_weight=count_loss_weight,
                reduction="none",
            )
            predicted = decode_set_predictions(
                action_logits,
                count_logits,
                batch["legal_mask"],
                batch["min_count"],
                batch["max_count"],
                [item["action_digests"] for item in cpu_batch["metadata"]],
            )
            for row, indices in enumerate(predicted):
                target = set(
                    torch.nonzero(batch["target_set"][row], as_tuple=False)
                    .flatten()
                    .tolist()
                )
                selected = set(indices)
                exact += int(selected == target)
                correct_count += int(len(selected) == int(batch["target_count"][row].item()))
            count = len(predicted)
            total += count
            total_loss += float(losses["total"].sum().item())
            total_set_loss += float(losses["set"].sum().item())
            total_count_loss += float(losses["count"].sum().item())
    if total == 0:
        raise GPUStudentV3SetError("evaluation split is empty")
    return {
        "examples": total,
        "loss": total_loss / total,
        "set_loss": total_set_loss / total,
        "count_loss": total_count_loss / total,
        "exact_set_fidelity": exact / total,
        "count_fidelity": correct_count / total,
        "legal_action_rate": 1.0,
        "fallback_rate": 0.0,
    }


def _atomic_torch_save(path: Path, payload: Mapping[str, Any]) -> None:
    torch, _nn, _functional, _loader, _dataset = _torch()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        torch.save(dict(payload), temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _torch_load_snapshot(path: Path, *, map_location: Any) -> tuple[bytes, dict[str, Any]]:
    """Read, hash, and deserialize one regular checkpoint byte snapshot."""
    torch, _nn, _functional, _loader, _dataset = _torch()
    if path.is_symlink() or not path.is_file():
        raise GPUStudentV3SetError(f"checkpoint is not a regular file: {path.name}")
    try:
        raw = path.read_bytes()
        payload = torch.load(io.BytesIO(raw), map_location=map_location, weights_only=True)
    except (OSError, RuntimeError, EOFError, ValueError, pickle.UnpicklingError) as exc:
        raise GPUStudentV3SetError(f"checkpoint is unreadable: {path.name}") from exc
    if type(payload) is not dict or set(payload) != _CHECKPOINT_KEYS:
        raise GPUStudentV3SetError(f"checkpoint has an invalid closed schema: {path.name}")
    return raw, payload


def weighted_empirical_batch_loss_v1(
    losses: Any,
    weights: Any,
    *,
    global_effective_weight_mean: float,
) -> Any:
    """Return an unbiased minibatch estimate of the fixed weighted objective.

    The empirical objective is ``sum_i(w_i * loss_i) / sum_i(w_i)`` over the
    complete training split.  Dividing by the minibatch weight sum would erase
    all weights for a one-row batch and make the objective depend on how rows
    are partitioned.  Uniform shuffled minibatches instead use the fixed global
    mean weight in the denominator.
    """

    torch, _nn, _functional, _loader, _dataset = _torch()
    if (
        not isinstance(losses, torch.Tensor)
        or not isinstance(weights, torch.Tensor)
        or losses.ndim != 1
        or weights.ndim != 1
        or losses.shape != weights.shape
        or losses.numel() < 1
    ):
        raise GPUStudentV3SetError("weighted losses and weights must be equal nonempty vectors")
    if (
        type(global_effective_weight_mean) not in (int, float)
        or type(global_effective_weight_mean) is bool
        or not math.isfinite(float(global_effective_weight_mean))
        or float(global_effective_weight_mean) <= 0.0
    ):
        raise GPUStudentV3SetError("global effective weight mean must be finite and positive")
    if not bool(torch.isfinite(losses).all().item()):
        raise GPUStudentV3SetError("weighted losses must be finite")
    if not bool(torch.isfinite(weights).all().item()) or not bool((weights > 0).all().item()):
        raise GPUStudentV3SetError("training weights must be finite and positive")
    return (losses * weights).mean() / float(global_effective_weight_mean)


def train_set_student(
    *,
    dataset_dir: Path,
    output_dir: Path,
    device_name: str,
    epochs: int,
    batch_size: int,
    workers: int = 0,
    hidden: int = 128,
    blocks: int = 2,
    dropout: float = 0.0,
    learning_rate: float = 3e-4,
    count_loss_weight: float = 1.0,
    seed: int = 71003,
    resume: bool = False,
    weight_sidecar: Path | None = None,
    initial_model_dir: Path | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Train θ0 or strict record-weighted AWR without evaluation authority."""
    torch, _nn, _functional, DataLoader, Dataset = _torch()
    dataset_dir = Path(dataset_dir).resolve()
    output_dir = Path(output_dir).resolve()
    manifest_path = dataset_dir / "manifest.json"
    manifest_raw, manifest = _load_json_object_snapshot(
        manifest_path, label="GPU set manifest"
    )
    loaded_dataset = _verify_dataset_manifest(dataset_dir, manifest)
    if manifest_path.read_bytes() != manifest_raw:
        raise GPUStudentV3SetError("GPU set manifest changed during verification")
    dataset_manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    catalog_sha = _require_sha(manifest.get("catalog_sha256"), field="catalog SHA-256")
    if type(epochs) is not int or epochs < 1:
        raise GPUStudentV3SetError("epochs must be positive")
    if type(batch_size) is not int or batch_size < 1:
        raise GPUStudentV3SetError("batch size must be positive")
    if type(workers) is not int or workers < 0:
        raise GPUStudentV3SetError("workers must be non-negative")
    if type(learning_rate) not in (int, float) or not math.isfinite(
        float(learning_rate)
    ) or float(learning_rate) <= 0:
        raise GPUStudentV3SetError("learning rate must be finite and positive")
    device, compute_dtype = _device(device_name)
    random.seed(seed)
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)

    train_values = list(
        _examples(_verified_split_payloads(manifest, loaded_dataset, "train"))
    )
    validation_values = list(
        _examples(_verified_split_payloads(manifest, loaded_dataset, "validation"))
    )
    train_ids = [value[6]["record_id"] for value in train_values]
    external_weights: dict[str, float] | None = None
    weight_stats: dict[str, Any]
    objective_kind: str
    if weight_sidecar is None:
        objective_kind = "THETA0_PRETRAIN"
        weight_stats = {
            "weight_sidecar_sha256": None,
            "external_weight_mass": None,
            "external_weight_ess": None,
            "external_weight_min": None,
            "external_weight_max": None,
            "joined_train_records": 0,
        }
    else:
        objective_kind = "AWR_FINE_TUNE"
        external_weights, weight_stats = load_training_weight_sidecar(
            Path(weight_sidecar),
            dataset_manifest_sha256=dataset_manifest_sha,
            catalog_sha256=catalog_sha,
            train_record_ids=train_ids,
        )
    effective_weights = {
        value[6]["record_id"]: float(value[6]["source_sample_weight"])
        * (1.0 if external_weights is None else external_weights[value[6]["record_id"]])
        for value in train_values
    }
    if any(not math.isfinite(value) or value <= 0 for value in effective_weights.values()):
        raise GPUStudentV3SetError("effective training weight is non-finite or non-positive")
    effective_mass = sum(effective_weights.values())
    effective_square_mass = sum(value * value for value in effective_weights.values())
    global_effective_weight_mean = effective_mass / len(train_values)

    max_count = manifest.get("max_count_class")
    if type(max_count) is not int or max_count < 0:
        raise GPUStudentV3SetError("GPU set manifest max_count_class is invalid")
    model_config = _model_config(
        hidden=hidden, blocks=blocks, dropout=dropout, max_count=max_count
    )
    model_config_sha = _config_sha(
        model_config, domain="offline-scaleup-student-v3-set-model-config-v1"
    )
    initial_checkpoint_sha: str | None = None
    initial_summary_sha: str | None = None
    if initial_model_dir is None:
        initialization_kind = "RANDOM_SEEDED"
        model = make_set_cardinality_model(
            hidden=hidden, blocks=blocks, dropout=dropout, max_count=max_count
        ).to(device)
    else:
        if weight_sidecar is None:
            raise GPUStudentV3SetError("initial model transfer is only valid for AWR fine-tuning")
        initial_dir = Path(initial_model_dir).resolve()
        if initial_dir == output_dir:
            raise GPUStudentV3SetError("initial model directory must differ from output")
        model, initial_summary = load_set_checkpoint(initial_dir, device)
        initial_summary_raw, initial_summary_snapshot = _load_json_object_snapshot(
            initial_dir / "training_summary.json", label="initial training summary"
        )
        if initial_summary_snapshot != initial_summary:
            raise GPUStudentV3SetError("initial training summary changed during verification")
        if initial_summary.get("objective_kind") != "THETA0_PRETRAIN":
            raise GPUStudentV3SetError("initial model must be a theta0 pretrain checkpoint")
        if (
            initial_summary.get("dataset_manifest_sha256") != dataset_manifest_sha
            or initial_summary.get("catalog_sha256") != catalog_sha
        ):
            raise GPUStudentV3SetError("initial model dataset/catalog identity mismatch")
        if initial_summary.get("model_config") != model_config:
            raise GPUStudentV3SetError("initial model config mismatch")
        initialization_kind = "THETA0_BEST_CHECKPOINT"
        initial_checkpoint_sha = initial_summary["best_checkpoint_sha256"]
        initial_summary_sha = hashlib.sha256(initial_summary_raw).hexdigest()
    training_config = {
        "batch_size": batch_size,
        "workers": workers,
        "learning_rate": float(learning_rate),
        "count_loss_weight": float(count_loss_weight),
        "seed": seed,
        "device": str(device),
        "compute_dtype": compute_dtype,
        "objective_kind": objective_kind,
        "weight_sidecar_sha256": weight_stats["weight_sidecar_sha256"],
        "optimizer": "AdamW",
        "optimizer_betas": [0.9, 0.999],
        "optimizer_eps": 1e-8,
        "optimizer_weight_decay": 0.0,
        "checkpoint_journal": "immutable_epoch_v1",
        "runtime_evaluation_device": "cpu",
        "initialization_kind": initialization_kind,
        "initial_checkpoint_sha256": initial_checkpoint_sha,
        "initial_training_summary_sha256": initial_summary_sha,
    }
    training_config_sha = _config_sha(
        training_config, domain="offline-scaleup-student-v3-set-training-config-v1"
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(learning_rate),
        betas=(0.9, 0.999),
        eps=1e-8,
        weight_decay=0.0,
    )

    class Values(Dataset):
        def __len__(self) -> int:
            return len(train_values)

        def __getitem__(self, index: int) -> Any:
            return train_values[index]

    output_dir.mkdir(parents=True, exist_ok=True)
    last_path = output_dir / "last.pt"
    best_path = output_dir / "best.pt"
    summary_path = output_dir / "training_summary.json"
    checkpoint_dir = output_dir / "checkpoints"
    start_epoch = 0
    best_exact = -1.0
    best_epoch = -1
    metrics: list[dict[str, Any]] = []
    resumed = False
    recovered_interrupted_epoch = False
    expected_checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA,
        "purpose": PURPOSE,
        "objective_kind": objective_kind,
        "dataset_manifest_sha256": dataset_manifest_sha,
        "catalog_sha256": catalog_sha,
        "weight_sidecar_sha256": weight_stats["weight_sidecar_sha256"],
        "model_config_sha256": model_config_sha,
        "training_config_sha256": training_config_sha,
    }

    def verify_checkpoint(checkpoint: Mapping[str, Any], *, label: str) -> None:
        for key, value in expected_checkpoint.items():
            if checkpoint.get(key) != value:
                raise GPUStudentV3SetError(f"{label} checkpoint {key} mismatch")

    def restore_checkpoint(checkpoint: Mapping[str, Any]) -> None:
        nonlocal start_epoch, best_exact, best_epoch, metrics
        verify_checkpoint(checkpoint, label="resume")
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        start_epoch = int(checkpoint["epoch"]) + 1
        best_exact = float(checkpoint["best_validation_exact_set_fidelity"])
        best_epoch = int(checkpoint["best_epoch"])
        old_metrics = checkpoint.get("metrics")
        if type(old_metrics) is not list or len(old_metrics) != start_epoch:
            raise GPUStudentV3SetError("resume checkpoint metric lineage mismatch")
        metrics = list(old_metrics)
        random.setstate(checkpoint["python_random_state"])
        torch.set_rng_state(checkpoint["torch_rng_state"].cpu())
        if device.type == "cuda":
            states = checkpoint["cuda_rng_state_all"]
            if type(states) is not list or not states:
                raise GPUStudentV3SetError("resume checkpoint CUDA RNG state is invalid")
            torch.cuda.set_rng_state_all([value.cpu() for value in states])

    if resume:
        epoch_paths = sorted(checkpoint_dir.glob("epoch-*.pt"))
        if not epoch_paths:
            raise GPUStudentV3SetError("resume requires an immutable epoch checkpoint")
        latest_path = epoch_paths[-1]
        latest_raw, latest = _torch_load_snapshot(latest_path, map_location=device)
        if summary_path.is_file():
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
            latest_sha = hashlib.sha256(latest_raw).hexdigest()
            latest_relative = str(latest_path.relative_to(output_dir))
            summary_matches_latest = (
                previous.get("last_checkpoint_sha256") == latest_sha
                and previous.get("last_checkpoint_path") == latest_relative
            )
            if not summary_matches_latest:
                previous_epoch = int(latest.get("epoch")) - 1
                previous_path = checkpoint_dir / f"epoch-{previous_epoch:04d}.pt"
                if (
                    previous_epoch < 0
                    or not previous_path.is_file()
                    or previous.get("last_checkpoint_path")
                    != str(previous_path.relative_to(output_dir))
                    or previous.get("last_checkpoint_sha256") != _sha256(previous_path)
                    or previous.get("epochs_completed") != int(latest.get("epoch"))
                    or len(latest.get("metrics", [])) != int(latest.get("epoch")) + 1
                ):
                    raise GPUStudentV3SetError("last checkpoint SHA-256 does not match summary")
                recovered_interrupted_epoch = True
                _atomic_bytes(last_path, latest_raw)
            if not best_path.is_file() or previous.get("best_checkpoint_sha256") != _sha256(best_path):
                if not recovered_interrupted_epoch:
                    raise GPUStudentV3SetError("best checkpoint SHA-256 does not match summary")
            if recovered_interrupted_epoch:
                best_epoch_value = latest.get("best_epoch")
                if type(best_epoch_value) is not int or not 0 <= best_epoch_value <= latest["epoch"]:
                    raise GPUStudentV3SetError("orphan checkpoint best epoch is invalid")
                best_epoch_path = checkpoint_dir / f"epoch-{best_epoch_value:04d}.pt"
                best_raw, best_checkpoint = _torch_load_snapshot(
                    best_epoch_path, map_location=device
                )
                verify_checkpoint(best_checkpoint, label="best")
                if best_checkpoint.get("epoch") != best_epoch_value:
                    raise GPUStudentV3SetError("best checkpoint epoch lineage mismatch")
                _atomic_bytes(best_path, best_raw)
            else:
                _best_raw, best_checkpoint = _torch_load_snapshot(best_path, map_location=device)
                verify_checkpoint(best_checkpoint, label="best")
                if best_checkpoint.get("epoch") != latest.get("best_epoch"):
                    raise GPUStudentV3SetError("best checkpoint epoch lineage mismatch")
        else:
            recovered_interrupted_epoch = True
            best_epoch_value = latest.get("best_epoch")
            if type(best_epoch_value) is not int or best_epoch_value < 0:
                raise GPUStudentV3SetError("orphan checkpoint best epoch is invalid")
            best_epoch_path = checkpoint_dir / f"epoch-{best_epoch_value:04d}.pt"
            best_raw, best_checkpoint = _torch_load_snapshot(
                best_epoch_path, map_location=device
            )
            verify_checkpoint(best_checkpoint, label="best")
            if best_checkpoint.get("epoch") != best_epoch_value:
                raise GPUStudentV3SetError("best checkpoint epoch lineage mismatch")
            _atomic_bytes(best_path, best_raw)
            _atomic_bytes(last_path, latest_raw)
        restore_checkpoint(latest)
        resumed = True
    elif (
        last_path.exists() or best_path.exists() or summary_path.exists()
        or (checkpoint_dir.exists() and any(checkpoint_dir.iterdir()))
    ):
        raise GPUStudentV3SetError("training outputs exist; explicit compatible resume is required")
    if start_epoch >= epochs:
        raise GPUStudentV3SetError("requested epochs do not advance the checkpoint")

    def autocast_context() -> Any:
        if device.type != "cuda":
            return torch.autocast(device_type="cpu", enabled=False)
        dtype = torch.bfloat16 if compute_dtype == "bf16" else torch.float16
        return torch.autocast(device_type="cuda", dtype=dtype)

    for epoch in range(start_epoch, epochs):
        generator = torch.Generator()
        generator.manual_seed(seed + epoch)
        loader = DataLoader(
            Values(),
            batch_size=batch_size,
            shuffle=True,
            num_workers=workers,
            collate_fn=_collate_set,
            pin_memory=device.type == "cuda",
            persistent_workers=workers > 0,
            generator=generator,
        )
        model.train()
        started = time.perf_counter()
        totals = Counter()
        optimizer.zero_grad(set_to_none=True)
        for cpu_batch in loader:
            metadata = cpu_batch.pop("metadata")
            batch = {key: value.to(device, non_blocking=True) for key, value in cpu_batch.items()}
            with autocast_context():
                action_logits, count_logits = model(
                    batch["state"], batch["actions"], batch["legal_mask"]
                )
                losses = set_cardinality_loss(
                    action_logits,
                    count_logits,
                    batch,
                    count_loss_weight=count_loss_weight,
                    reduction="none",
                )
                weights = torch.tensor(
                    [effective_weights[item["record_id"]] for item in metadata],
                    device=device,
                    dtype=losses["total"].dtype,
                )
                loss = weighted_empirical_batch_loss_v1(
                    losses["total"],
                    weights,
                    global_effective_weight_mean=global_effective_weight_mean,
                )
            if not bool(torch.isfinite(loss).item()):
                raise GPUStudentV3SetError("training loss became non-finite")
            loss.backward()
            if any(
                parameter.grad is not None
                and not bool(torch.isfinite(parameter.grad).all().item())
                for parameter in model.parameters()
            ):
                raise GPUStudentV3SetError("training gradient became non-finite")
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            count = len(metadata)
            totals["examples"] += count
            totals["weight_sum"] += float(weights.sum().detach().cpu().item())
            totals["loss_weighted_sum"] += float(
                (losses["total"] * weights).sum().detach().cpu().item()
            )
            totals["set_weighted_sum"] += float(
                (losses["set"] * weights).sum().detach().cpu().item()
            )
            totals["count_weighted_sum"] += float(
                (losses["count"] * weights).sum().detach().cpu().item()
            )
        accelerator_validation = _evaluate_set(
            model,
            validation_values,
            device=device,
            batch_size=batch_size,
            count_loss_weight=count_loss_weight,
        )
        if device.type == "cpu":
            validation = accelerator_validation
        else:
            cpu_model = make_set_cardinality_model(
                hidden=hidden, blocks=blocks, dropout=dropout, max_count=max_count
            )
            cpu_model.load_state_dict(
                {key: value.detach().cpu() for key, value in model.state_dict().items()}
            )
            cpu_model.eval()
            validation = _evaluate_set(
                cpu_model,
                validation_values,
                device=torch.device("cpu"),
                batch_size=batch_size,
                count_loss_weight=count_loss_weight,
            )
        elapsed = time.perf_counter() - started
        epoch_metric = {
            "epoch": epoch,
            "train_loss": totals["loss_weighted_sum"] / totals["weight_sum"],
            "train_set_loss": totals["set_weighted_sum"] / totals["weight_sum"],
            "train_count_loss": totals["count_weighted_sum"] / totals["weight_sum"],
            "epoch_seconds": elapsed,
            "examples_per_second": totals["examples"] / elapsed,
            "validation": validation,
            "accelerator_validation": accelerator_validation,
            "best_selection_device": "cpu",
        }
        if device.type == "cuda":
            epoch_metric["peak_allocated_vram_bytes"] = torch.cuda.max_memory_allocated(device)
            torch.cuda.reset_peak_memory_stats(device)
        metrics.append(epoch_metric)
        improved = validation["exact_set_fidelity"] > best_exact
        if improved:
            best_exact = validation["exact_set_fidelity"]
            best_epoch = epoch
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA,
            "purpose": PURPOSE,
            "objective_kind": objective_kind,
            "dataset_manifest_sha256": dataset_manifest_sha,
            "catalog_sha256": catalog_sha,
            "weight_sidecar_sha256": weight_stats["weight_sidecar_sha256"],
            "model_config": model_config,
            "model_config_sha256": model_config_sha,
            "training_config_sha256": training_config_sha,
            "epoch": epoch,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "best_validation_exact_set_fidelity": best_exact,
            "best_epoch": best_epoch,
            "metrics": list(metrics),
            "python_random_state": random.getstate(),
            "torch_rng_state": torch.get_rng_state().cpu(),
            "cuda_rng_state_all": (
                [value.cpu() for value in torch.cuda.get_rng_state_all()]
                if device.type == "cuda"
                else []
            ),
        }
        epoch_path = checkpoint_dir / f"epoch-{epoch:04d}.pt"
        if epoch_path.exists():
            raise GPUStudentV3SetError("immutable epoch checkpoint already exists")
        _atomic_torch_save(epoch_path, checkpoint)
        epoch_raw = epoch_path.read_bytes()
        _atomic_bytes(last_path, epoch_raw)
        if improved:
            _atomic_bytes(best_path, epoch_raw)
        summary: dict[str, Any] = {
            "schema_version": STUDENT_V3_SET_SCHEMA,
            "purpose": PURPOSE,
            "objective_kind": objective_kind,
            "dataset_manifest_sha256": dataset_manifest_sha,
            "catalog_sha256": catalog_sha,
            "weight_sidecar_sha256": weight_stats["weight_sidecar_sha256"],
            "model_config": model_config,
            "model_config_sha256": model_config_sha,
            "training_config": training_config,
            "training_config_sha256": training_config_sha,
            "resumed_from_checkpoint": resumed,
            "recovered_interrupted_epoch": recovered_interrupted_epoch,
            "epochs_completed": len(metrics),
            "best_validation_exact_set_fidelity": best_exact,
            "effective_weight_mass": effective_mass,
            "effective_weight_ess": effective_mass * effective_mass / effective_square_mass,
            **weight_stats,
            "metrics": metrics,
            "best_checkpoint_sha256": _sha256(best_path),
            "last_checkpoint_path": str(epoch_path.relative_to(output_dir)),
            "last_checkpoint_sha256": hashlib.sha256(epoch_raw).hexdigest(),
            "authority": dict(_AUTHORITY),
        }
        _atomic_json(summary_path, summary)
        if progress:
            print(
                f"PROGRESS phase=train-student-v3-set epoch={epoch + 1}/{epochs} "
                f"validation_exact={validation['exact_set_fidelity']:.4f}",
                flush=True,
            )

    return summary


def load_set_checkpoint(model_dir: Path, device: Any) -> tuple[Any, dict[str, Any]]:
    """Load the best checkpoint only after summary/config/SHA verification."""
    torch, _nn, _functional, _loader, _dataset = _torch()
    model_dir = Path(model_dir)
    summary_path = model_dir / "training_summary.json"
    _summary_raw, summary = _load_json_object_snapshot(
        summary_path, label="training summary"
    )
    _verify_training_summary_v1(summary)
    model_config = summary.get("model_config")
    best_path = model_dir / "best.pt"
    best_raw, checkpoint = _torch_load_snapshot(best_path, map_location=device)
    if hashlib.sha256(best_raw).hexdigest() != summary.get("best_checkpoint_sha256"):
        raise GPUStudentV3SetError("best checkpoint SHA-256 mismatch")
    for key in (
        "schema_version",
        "purpose",
        "objective_kind",
        "dataset_manifest_sha256",
        "catalog_sha256",
        "weight_sidecar_sha256",
        "model_config_sha256",
        "training_config_sha256",
    ):
        expected = CHECKPOINT_SCHEMA if key == "schema_version" else summary.get(key)
        if checkpoint.get(key) != expected:
            raise GPUStudentV3SetError(f"best checkpoint {key} mismatch")
    if checkpoint.get("model_config") != model_config:
        raise GPUStudentV3SetError("best checkpoint model config mismatch")
    best_epoch = checkpoint.get("best_epoch")
    checkpoint_epoch = checkpoint.get("epoch")
    checkpoint_metrics = checkpoint.get("metrics")
    if (
        type(checkpoint_epoch) is not int
        or type(best_epoch) is not int
        or checkpoint_epoch < 0
        or best_epoch != checkpoint_epoch
    ):
        raise GPUStudentV3SetError("best checkpoint epoch lineage mismatch")
    _verify_epoch_metrics_v1(checkpoint_metrics, expected_epochs=checkpoint_epoch + 1)
    if (
        checkpoint.get("best_validation_exact_set_fidelity")
        != summary.get("best_validation_exact_set_fidelity")
        or checkpoint_metrics[-1]["validation"]["exact_set_fidelity"]
        != summary.get("best_validation_exact_set_fidelity")
    ):
        raise GPUStudentV3SetError("best checkpoint validation lineage mismatch")
    model = make_set_cardinality_model(
        hidden=int(model_config["hidden"]),
        blocks=int(model_config["blocks"]),
        dropout=float(model_config["dropout"]),
        max_count=int(model_config["max_count"]),
    ).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    return model, summary


def evaluate_set_student(
    *,
    dataset_dir: Path,
    model_dir: Path,
    output: Path,
    device_name: str,
    batch_size: int,
) -> dict[str, Any]:
    """Evaluate fidelity only; this report grants no promotion authority."""
    torch, _nn, _functional, _loader, _dataset = _torch()
    device, compute_dtype = _device(device_name)
    model, summary = load_set_checkpoint(Path(model_dir), device)
    manifest_path = Path(dataset_dir) / "manifest.json"
    manifest_raw, manifest = _load_json_object_snapshot(
        manifest_path, label="GPU set manifest"
    )
    if hashlib.sha256(manifest_raw).hexdigest() != summary.get("dataset_manifest_sha256"):
        raise GPUStudentV3SetError("evaluation dataset manifest SHA-256 mismatch")
    loaded_dataset = _verify_dataset_manifest(Path(dataset_dir), manifest)
    if manifest_path.read_bytes() != manifest_raw:
        raise GPUStudentV3SetError("GPU set manifest changed during verification")
    config = summary["training_config"]
    count_loss_weight = float(config["count_loss_weight"])
    split_values = {
        split: list(_examples(_verified_split_payloads(manifest, loaded_dataset, split)))
        for split in SPLITS
    }
    results = {
        split: _evaluate_set(
            model,
            values,
            device=device,
            batch_size=batch_size,
            count_loss_weight=count_loss_weight,
        )
        for split, values in split_values.items()
    }
    parity: object = "cpu_only"
    if device.type == "cuda":
        cpu_model, _cpu_summary = load_set_checkpoint(Path(model_dir), torch.device("cpu"))
        sample = split_values["validation"][:32]
        batch = _collate_set(sample)
        with torch.no_grad():
            gpu_action, gpu_count = model(
                batch["state"].to(device),
                batch["actions"].to(device),
                batch["legal_mask"].to(device),
            )
            cpu_action, cpu_count = cpu_model(
                batch["state"], batch["actions"], batch["legal_mask"]
            )
        gpu_decode = decode_set_predictions(
            gpu_action,
            gpu_count,
            batch["legal_mask"].to(device),
            batch["min_count"].to(device),
            batch["max_count"].to(device),
            [item["action_digests"] for item in batch["metadata"]],
        )
        cpu_decode = decode_set_predictions(
            cpu_action,
            cpu_count,
            batch["legal_mask"],
            batch["min_count"],
            batch["max_count"],
            [item["action_digests"] for item in batch["metadata"]],
        )
        parity = {
            "sample_examples": len(sample),
            "exact_decode_agreement": sum(
                gpu == cpu for gpu, cpu in zip(gpu_decode, cpu_decode, strict=True)
            )
            / len(sample),
        }
    report = {
        "schema_version": "offline-scaleup-student-v3-set-evaluation-v1",
        "purpose": PURPOSE,
        "objective_kind": summary["objective_kind"],
        "dataset_manifest_sha256": summary["dataset_manifest_sha256"],
        "catalog_sha256": summary["catalog_sha256"],
        "weight_sidecar_sha256": summary["weight_sidecar_sha256"],
        "best_checkpoint_sha256": summary["best_checkpoint_sha256"],
        "device": str(device),
        "compute_dtype": compute_dtype,
        "splits": results,
        "gpu_cpu_decode_parity": parity,
        "authority": dict(_AUTHORITY),
    }
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite evaluation report: {output}")
    _atomic_json(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu-student-v3-set")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-dataset")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--bridge-manifest", type=Path, required=True)
    build.add_argument("--shard-size", type=int, default=4096)
    train_command = commands.add_parser("train")
    train_command.add_argument("--dataset-dir", type=Path, required=True)
    train_command.add_argument("--output-dir", type=Path, required=True)
    train_command.add_argument("--device", default="cuda")
    train_command.add_argument("--epochs", type=int, default=40)
    train_command.add_argument("--batch-size", type=int, default=256)
    train_command.add_argument("--workers", type=int, default=4)
    train_command.add_argument("--hidden", type=int, default=128)
    train_command.add_argument("--blocks", type=int, default=2)
    train_command.add_argument("--dropout", type=float, default=0.0)
    train_command.add_argument("--learning-rate", type=float, default=3e-4)
    train_command.add_argument("--count-loss-weight", type=float, default=1.0)
    train_command.add_argument("--seed", type=int, default=71003)
    train_command.add_argument("--resume", action="store_true")
    train_command.add_argument("--weight-sidecar", type=Path)
    train_command.add_argument("--initial-model-dir", type=Path)
    train_command.add_argument("--progress", action="store_true")
    evaluate_command = commands.add_parser("evaluate")
    evaluate_command.add_argument("--dataset-dir", type=Path, required=True)
    evaluate_command.add_argument("--model-dir", type=Path, required=True)
    evaluate_command.add_argument("--output", type=Path, required=True)
    evaluate_command.add_argument("--device", default="cuda")
    evaluate_command.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        if args.command == "build-dataset":
            result = build_set_dataset(
                source=args.source,
                output_dir=args.output_dir,
                shard_size=args.shard_size,
                bridge_manifest=args.bridge_manifest,
            )
        elif args.command == "train":
            result = train_set_student(
                dataset_dir=args.dataset_dir,
                output_dir=args.output_dir,
                device_name=args.device,
                epochs=args.epochs,
                batch_size=args.batch_size,
                workers=args.workers,
                hidden=args.hidden,
                blocks=args.blocks,
                dropout=args.dropout,
                learning_rate=args.learning_rate,
                count_loss_weight=args.count_loss_weight,
                seed=args.seed,
                resume=args.resume,
                weight_sidecar=args.weight_sidecar,
                initial_model_dir=args.initial_model_dir,
                progress=args.progress,
            )
        else:
            result = evaluate_set_student(
                dataset_dir=args.dataset_dir,
                model_dir=args.model_dir,
                output=args.output,
                device_name=args.device,
                batch_size=args.batch_size,
            )
        print(_canonical_bytes(result).decode("utf-8"))
        return 0
    except (GPUStudentV3SetError, OSError, ValueError, RuntimeError) as exc:
        print(
            _canonical_bytes(
                {"error": type(exc).__name__, "message": str(exc)}
            ).decode("utf-8")
        )
        return 2


__all__ = [
    "CHECKPOINT_SCHEMA",
    "GPU_SET_DATASET_SCHEMA",
    "GPUStudentV3SetError",
    "PURPOSE",
    "SOURCE_SCHEMA",
    "SPLITS",
    "STUDENT_V3_SET_SCHEMA",
    "WEIGHT_SIDECAR_SCHEMA",
    "_collate_set",
    "_examples",
    "_load_split",
    "build_set_dataset",
    "decode_set_predictions",
    "evaluate_set_student",
    "load_set_checkpoint",
    "load_training_weight_sidecar",
    "make_set_cardinality_model",
    "set_cardinality_loss",
    "train_set_student",
    "weighted_empirical_batch_loss_v1",
]


if __name__ == "__main__":
    raise SystemExit(main())
