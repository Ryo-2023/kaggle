"""Fail-closed boundary for the locked Recipe C official metric.

This module is intentionally the only Task 5 code that can cross from a
persisted prediction into ground-truth evaluation.  Prediction GEFFs and their
per-GEFF manifests are fully revalidated before a persistence token is minted;
the token is consumed by :func:`biohub.reproducibility.gt_guard.open_ground_truth`
and only the graph returned by that opener is passed to the vendored RoyerLab
metric.  No ground-truth path is opened while the prediction preflight is still
in progress.

The module does not alter the vendored evaluator or Recipe C inference runner.
It also keeps the fixed five-sample macro separate from the official
size-weighted summary returned by ``summarise``.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tracksdata as td
import zarr

from biohub.official_metrics import UPSTREAM_BLOBS, UPSTREAM_COMMIT, UPSTREAM_REPOSITORY
from biohub.official_metrics.metrics import (
    ADJUSTMENT_ALPHA,
    SCORE_DIVISION_WEIGHT,
    evaluate,
    node_recall,
    per_sample_metrics,
    summarise,
)
from biohub.recipe_c.geff_bridge import validate_prediction_geff, write_json_exclusive
from biohub.recipe_c.protocol import PANEL_V1, validate_selection_lock, validate_selection_lock_payload
from biohub.reproducibility import gt_guard
from biohub.reproducibility.digest import PREDICTION_DIRECTORY_HASH_ALGORITHM, directory_digest_report
from biohub.reproducibility.gt_guard import (
    GroundTruthOrderingError,
    PredictionPersistedToken,
    mint_prediction_token,
    parse_timestamp,
    prediction_manifest_path,
)

MetricValue = int | float | str | bool | None

METRIC_SCHEMA_VERSION = "biohub_095.metric_boundary.v1"
PANEL_SCHEMA_VERSION = "biohub_095.panel_metric_receipt.v1"
PANEL_ID = "PANEL_V1"
TARGET_SCORE = 0.95
DEFAULT_SCALE: tuple[float, float, float] = (1.625, 0.40625, 0.40625)
DEFAULT_MAX_DISTANCE = 7.0

_TASK4_MANIFEST_BASE_KEYS = frozenset(
    {
        "schema_version",
        "prediction_role",
        "prediction_path",
        "prediction_name",
        "selection_lock_id",
        "ground_truth_included",
        "ground_truth_inputs",
        "manifest_created_at",
        "directory_sha256",
        "files",
        "total_bytes",
        "hash_algorithm",
        "nodes",
        "edges",
        "forks",
    }
)
_TASK4_MANIFEST_PROVENANCE_KEYS = frozenset(
    {
        "source_commit",
        "config_sha256",
        "predictor_sha256_before",
        "predictor_sha256",
        "predictor_sha256_after",
        "d4_predictor_sha256_after",
        "stage_predictor_sha256_after",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_sha256",
        "resolved_device",
        "device_candidates",
        "patch_spatial_d4",
        "patch_builder",
        "runtime_role",
        "command_sha256",
        "execution_argv_sha256",
        "child_device",
        "child_stdout_sha256",
        "child_stderr_sha256",
        "predictor_diagnostic_sha256",
        "trace_module_sha256",
        "trace_module_derived_sha256",
        "cuda_equivalence_validated",
    }
)
_TASK4_MANIFEST_KEYS = _TASK4_MANIFEST_BASE_KEYS | _TASK4_MANIFEST_PROVENANCE_KEYS

# Keep this object JSON-safe and immutable-by-convention.  A copy is returned in
# receipts so a caller cannot mutate the provenance of a later sample.
OFFICIAL_METRIC_PROVENANCE: dict[str, object] = {
    "repo": UPSTREAM_REPOSITORY,
    "commit": UPSTREAM_COMMIT,
    "metrics_blob": UPSTREAM_BLOBS["metrics.py"],
    "division_metrics_blob": UPSTREAM_BLOBS["division_metrics.py"],
}


class MetricBoundaryError(ValueError):
    """Raised when the Task 5 boundary cannot prove a safe evaluation order."""

    def __init__(self, message: str, *, phase: str = "preflight", sample_id: str | None = None) -> None:
        self.phase = phase
        self.sample_id = sample_id
        prefix = f"{sample_id} [{phase}] " if sample_id else f"[{phase}] "
        super().__init__(prefix + message)


@dataclass(frozen=True, slots=True)
class GroundTruthOpened:
    """GT graph and metadata returned by one sanctioned opener callback."""

    graph: td.graph.BaseGraph
    estimated_number_of_nodes: float


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _now().isoformat()


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise MetricBoundaryError(f"{field} must be finite numeric")
    return float(value)


def _json_value(value: Any) -> MetricValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}  # type: ignore[return-value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]  # type: ignore[return-value]
    return str(value)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    payload = Path(path).read_bytes()
    return hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    path = Path(path)
    _reject_symlink_components(path, label=label)
    if path.is_symlink() or not path.is_file():
        raise MetricBoundaryError(f"{label} must be a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetricBoundaryError(f"{label} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise MetricBoundaryError(f"{label} must contain a JSON object: {path}")
    return payload


def _reject_symlink_components(path: Path, *, label: str) -> None:
    """Reject symlinks anywhere in an explicitly supplied filesystem path."""

    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise MetricBoundaryError(f"{label} could not be inspected: {current}") from exc
        if stat.S_ISLNK(mode):
            raise MetricBoundaryError(f"{label} contains a symlink: {current}")


def _safe_write_target(path: Path) -> Path:
    """Prepare a receipt target without following symlinked parents."""

    target = Path(path)
    if any(part == ".." for part in target.parts):
        raise MetricBoundaryError("receipt path must not contain '..'", phase="receipt_write")
    _reject_symlink_components(target.parent, label="receipt parent")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MetricBoundaryError(
            f"receipt parent could not be created: {target.parent}", phase="receipt_write"
        ) from exc
    _reject_symlink_components(target.parent, label="receipt parent")
    try:
        if target.is_symlink():
            raise MetricBoundaryError(f"receipt target is a symlink: {target}", phase="receipt_write")
    except OSError as exc:
        raise MetricBoundaryError(f"receipt target could not be inspected: {target}", phase="receipt_write") from exc
    return target


def _validate_panel_lock(selection_lock: Path | Mapping[str, object]) -> dict[str, object]:
    """Validate a persisted selection lock and freeze its panel identity.

    Both persisted paths and in-memory mappings use the same canonical lock
    validator.  There is deliberately no reduced test-only mapping contract:
    the lock is the authority for the panel and all of its provenance fields.
    """

    if isinstance(selection_lock, Path):
        value = validate_selection_lock(selection_lock)
    elif isinstance(selection_lock, Mapping):
        try:
            value = validate_selection_lock_payload(selection_lock)
        except (TypeError, ValueError) as exc:
            raise MetricBoundaryError(f"selection lock payload is invalid: {exc}", phase="lock") from exc
    else:
        raise TypeError("selection_lock must be a persisted Path or mapping")

    panel = value.get("panel")
    if not isinstance(panel, Mapping) or panel.get("panel_id") != PANEL_ID:
        raise MetricBoundaryError("selection lock does not contain PANEL_V1", phase="lock")
    if tuple(panel.get("sample_ids", ())) != PANEL_V1:
        raise MetricBoundaryError("selection lock PANEL_V1 order is not exact", phase="lock")
    return value


def _metric_config(metric_config: Mapping[str, object] | None) -> dict[str, object]:
    """Return the one fixed metric configuration, rejecting overrides."""

    expected = {
        "scale": list(DEFAULT_SCALE),
        "max_distance": DEFAULT_MAX_DISTANCE,
        "alpha": ADJUSTMENT_ALPHA,
        "division_weight": SCORE_DIVISION_WEIGHT,
    }
    if metric_config is None:
        return expected
    supplied = dict(metric_config)
    if set(supplied) != set(expected):
        raise MetricBoundaryError("metric config keys are not the fixed Task 5 set", phase="config")
    scale = supplied.get("scale")
    if not isinstance(scale, (tuple, list)) or len(scale) != 3:
        raise MetricBoundaryError("metric_config.scale must contain three values", phase="config")
    try:
        normalized_scale = [float(value) for value in scale]
        normalized_distance = float(supplied["max_distance"])
        normalized_alpha = float(supplied["alpha"])
        normalized_division_weight = float(supplied["division_weight"])
    except (TypeError, ValueError) as exc:
        raise MetricBoundaryError("metric config values must be numeric", phase="config") from exc
    normalized = {
        "scale": normalized_scale,
        "max_distance": normalized_distance,
        "alpha": normalized_alpha,
        "division_weight": normalized_division_weight,
    }
    if normalized != expected:
        raise MetricBoundaryError("sample metric config must equal the fixed Task 5 config", phase="config")
    if not all(math.isfinite(value) and value > 0.0 for value in normalized_scale):
        raise MetricBoundaryError("metric_config.scale must be finite and positive", phase="config")
    if not math.isfinite(normalized_distance) or normalized_distance <= 0.0:
        raise MetricBoundaryError("metric_config.max_distance must be finite and positive", phase="config")
    return expected


def _assert_official_metric_pin() -> None:
    """Fail closed if the vendored evaluator provenance is changed."""

    from biohub import official_metrics

    if official_metrics.UPSTREAM_REPOSITORY != OFFICIAL_METRIC_PROVENANCE["repo"]:
        raise MetricBoundaryError("official metric repository provenance is not pinned", phase="metric_pin")
    if official_metrics.UPSTREAM_COMMIT != OFFICIAL_METRIC_PROVENANCE["commit"]:
        raise MetricBoundaryError("official metric commit provenance is not pinned", phase="metric_pin")
    if official_metrics.UPSTREAM_BLOBS != {
        "metrics.py": OFFICIAL_METRIC_PROVENANCE["metrics_blob"],
        "division_metrics.py": OFFICIAL_METRIC_PROVENANCE["division_metrics_blob"],
    }:
        raise MetricBoundaryError("official metric blob provenance is not pinned", phase="metric_pin")
    from biohub.official_metrics import division_metrics as division_metrics_module
    from biohub.official_metrics import metrics as metrics_module

    for filename, module, expected in (
        ("metrics.py", metrics_module, OFFICIAL_METRIC_PROVENANCE["metrics_blob"]),
        ("division_metrics.py", division_metrics_module, OFFICIAL_METRIC_PROVENANCE["division_metrics_blob"]),
    ):
        module_path = getattr(module, "__file__", None)
        if not isinstance(module_path, str) or _git_blob_sha1(Path(module_path)) != expected:
            raise MetricBoundaryError(f"vendored {filename} bytes are not pinned", phase="metric_pin")


def _load_graph(path: Path) -> td.graph.BaseGraph:
    loaded = td.graph.IndexedRXGraph.from_geff(path)
    if isinstance(loaded, tuple):
        return loaded[0]
    return loaded


def _load_ground_truth(path: Path) -> GroundTruthOpened:
    """Load the graph and its node-count metadata in the sanctioned opener."""

    path = Path(path)
    _reject_symlink_components(path, label="ground-truth path")
    if not path.is_dir():
        raise MetricBoundaryError(f"ground-truth GEFF must be a directory: {path}", phase="gt_open")
    graph = _load_graph(path)
    attrs = zarr.open_group(path).attrs
    try:
        value: Any = attrs["geff"]["extra"]["estimated_number_of_nodes"]
    except (KeyError, TypeError) as exc:
        raise MetricBoundaryError(
            f"ground-truth GEFF is missing estimated_number_of_nodes: {path}", phase="metric"
        ) from exc
    result = _finite(value, field="ground-truth estimated_number_of_nodes")
    if result <= 0.0:
        raise MetricBoundaryError("ground-truth estimated_number_of_nodes must be positive", phase="metric")
    return GroundTruthOpened(graph=graph, estimated_number_of_nodes=result)


def _open_gt(gt_path: Path, token: PredictionPersistedToken) -> tuple[GroundTruthOpened, dict[str, Any]]:
    """The sole ground-truth opener used by this module."""

    return gt_guard.open_ground_truth(gt_path, token, _load_ground_truth)


def _validate_prediction_artifact(
    prediction_path: Path,
    sample_id: str,
    *,
    selection_lock_id: str,
    expected_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Revalidate one final GEFF and its sibling per-prediction manifest."""

    path = Path(prediction_path)
    _reject_symlink_components(path, label="prediction GEFF path")
    if path.name != f"{sample_id}.geff" or path.is_symlink() or not path.is_dir():
        raise MetricBoundaryError(
            f"prediction GEFF must be a non-symlink directory named {sample_id}.geff: {path}",
            sample_id=sample_id,
        )
    if any(child.is_symlink() for child in path.rglob("*")):
        raise MetricBoundaryError("prediction GEFF contains a symlink", sample_id=sample_id)

    manifest_path = prediction_manifest_path(path)
    if expected_manifest_path is not None and Path(expected_manifest_path) != manifest_path:
        raise MetricBoundaryError("receipt manifest path is not the GEFF sibling manifest", sample_id=sample_id)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise MetricBoundaryError(
            "per-prediction manifest is missing; shared prediction_manifest.json is not accepted",
            sample_id=sample_id,
        )
    payload = _read_json(manifest_path, label="per-prediction manifest")
    unknown_keys = set(payload) - _TASK4_MANIFEST_KEYS
    if unknown_keys:
        names = ", ".join(sorted(str(key) for key in unknown_keys))
        raise MetricBoundaryError(
            f"prediction manifest contains unknown Task 4 fields: {names}",
            sample_id=sample_id,
        )
    if payload.get("schema_version") != 1:
        raise MetricBoundaryError("prediction manifest schema_version is not 1", sample_id=sample_id)
    if payload.get("prediction_role") != "recipe_c_prediction":
        raise MetricBoundaryError("prediction manifest prediction_role is not recipe_c_prediction", sample_id=sample_id)
    recorded_path = payload.get("prediction_path")
    recorded_name = Path(recorded_path).name if isinstance(recorded_path, str) else None
    if recorded_name != path.name or payload.get("prediction_name") != path.name:
        raise MetricBoundaryError("prediction manifest names a different GEFF", sample_id=sample_id)
    if payload.get("selection_lock_id") != selection_lock_id:
        raise MetricBoundaryError("prediction manifest selection lock does not match", sample_id=sample_id)
    if payload.get("ground_truth_included") is not False:
        raise MetricBoundaryError("prediction manifest must set ground_truth_included=false", sample_id=sample_id)
    if payload.get("ground_truth_inputs") != []:
        raise MetricBoundaryError("prediction manifest ground_truth_inputs must be empty", sample_id=sample_id)
    try:
        created_at = parse_timestamp(payload.get("manifest_created_at"), field="manifest_created_at")
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), sample_id=sample_id) from exc
    if created_at > _now():
        raise MetricBoundaryError("prediction manifest claims a future creation time", sample_id=sample_id)

    digest = directory_digest_report(path)
    directory_sha256 = payload.get("directory_sha256")
    if (
        not isinstance(directory_sha256, str)
        or len(directory_sha256) != 64
        or any(char not in "0123456789abcdef" for char in directory_sha256)
    ):
        raise MetricBoundaryError(
            "prediction manifest directory_sha256 is not a lowercase SHA-256", sample_id=sample_id
        )
    for key in ("files", "total_bytes"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MetricBoundaryError(f"prediction manifest {key} is not a non-negative integer", sample_id=sample_id)
    if payload.get("hash_algorithm") != PREDICTION_DIRECTORY_HASH_ALGORITHM:
        raise MetricBoundaryError(
            "prediction manifest hash_algorithm is not the pinned digest algorithm", sample_id=sample_id
        )
    expected_hashes = {
        "directory_sha256": digest["directory_sha256"],
        "files": digest["files"],
        "total_bytes": digest["total_bytes"],
        "hash_algorithm": PREDICTION_DIRECTORY_HASH_ALGORITHM,
    }
    for key, expected in expected_hashes.items():
        if payload.get(key) != expected:
            raise MetricBoundaryError(
                f"prediction manifest {key} mismatch: expected {expected!r}, got {payload.get(key)!r}",
                sample_id=sample_id,
            )

    try:
        counts = validate_prediction_geff(path, sample_id, expected_volume_shape_tzyx=None)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise MetricBoundaryError(f"prediction GEFF structural validation failed: {exc}", sample_id=sample_id) from exc
    for key in ("nodes", "edges", "forks"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value != counts[key]:
            raise MetricBoundaryError(
                f"prediction manifest {key} does not match the reloaded GEFF", sample_id=sample_id
            )

    return {
        "prediction_path": path,
        "manifest_path": manifest_path,
        "manifest_payload": payload,
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_created_at": created_at.isoformat(),
        "directory_sha256": digest["directory_sha256"],
        "files": int(digest["files"]),
        "total_bytes": int(digest["total_bytes"]),
        "counts": {key: int(counts[key]) for key in ("nodes", "edges", "forks")},
    }


def _mint_for_artifact(artifact: Mapping[str, Any], *, sample_id: str) -> PredictionPersistedToken:
    try:
        token = mint_prediction_token(Path(artifact["prediction_path"]))
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), sample_id=sample_id) from exc
    if token.manifest_path != Path(artifact["manifest_path"]) or token.directory_sha256 != artifact["directory_sha256"]:
        raise MetricBoundaryError("minted token does not match the preflight digest", sample_id=sample_id)
    if _sha256_file(Path(artifact["manifest_path"])) != artifact["manifest_sha256"]:
        raise MetricBoundaryError("prediction manifest changed during token mint", sample_id=sample_id)
    return token


def _assert_artifact_manifest_unchanged(artifact: Mapping[str, Any], *, sample_id: str) -> None:
    try:
        current = _sha256_file(Path(artifact["manifest_path"]))
    except OSError as exc:
        raise MetricBoundaryError("prediction manifest became unreadable before GT open", sample_id=sample_id) from exc
    if current != artifact["manifest_sha256"]:
        raise MetricBoundaryError("prediction manifest changed after token mint", sample_id=sample_id)


def _validate_guard_receipt(receipt: Mapping[str, Any], artifact: Mapping[str, Any], sample_id: str) -> dict[str, Any]:
    if not receipt:
        raise MetricBoundaryError("ground-truth opener returned no guard receipt", phase="gt_open", sample_id=sample_id)
    result = dict(receipt)
    authority = result.get("ordering_enforced_by")
    if authority != "biohub.reproducibility.gt_guard.open_ground_truth":
        raise MetricBoundaryError(
            "GT receipt is missing gt_guard ordering authority",
            phase="gt_open",
            sample_id=sample_id,
        )
    expected_prediction = Path(artifact["prediction_path"]).absolute()
    expected_manifest = Path(artifact["manifest_path"]).absolute()
    for field, expected in (
        ("prediction_directory_sha256", artifact["directory_sha256"]),
        ("prediction_files", artifact["files"]),
        ("prediction_total_bytes", artifact["total_bytes"]),
    ):
        if result.get(field) != expected:
            raise MetricBoundaryError(
                f"GT receipt {field} does not match preflight",
                phase="gt_open",
                sample_id=sample_id,
            )
    for field, expected in (
        ("prediction_path", expected_prediction),
        ("prediction_manifest_path", expected_manifest),
    ):
        recorded = result.get(field)
        if not isinstance(recorded, str) or Path(recorded).absolute() != expected:
            raise MetricBoundaryError(
                f"GT receipt {field} does not match preflight",
                phase="gt_open",
                sample_id=sample_id,
            )
    try:
        created = parse_timestamp(result.get("prediction_manifest_created_at"), field="manifest_created_at")
        persisted = parse_timestamp(result.get("prediction_persisted_at"), field="prediction_persisted_at")
        opened = parse_timestamp(result.get("ground_truth_opened_at"), field="ground_truth_opened_at")
        expected_created = parse_timestamp(artifact["manifest_created_at"], field="manifest_created_at")
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), phase="gt_open", sample_id=sample_id) from exc
    if created != expected_created:
        raise MetricBoundaryError(
            "GT receipt manifest creation time does not match the validated manifest",
            phase="gt_open",
            sample_id=sample_id,
        )
    if not created < persisted < opened:
        raise MetricBoundaryError(
            "GT receipt timestamps do not prove prediction persistence before GT open",
            phase="gt_open",
            sample_id=sample_id,
        )
    return result


def _evaluate_prevalidated(
    artifact: Mapping[str, Any],
    token: PredictionPersistedToken,
    gt_path: Path,
    *,
    sample_id: str,
    selection_lock: Mapping[str, object],
    metric_config: Mapping[str, object],
    reproduction_command: str,
) -> dict[str, object]:
    """Open GT and execute official functions after all preflight checks."""

    _assert_artifact_manifest_unchanged(artifact, sample_id=sample_id)
    try:
        opened = _open_gt(Path(gt_path), token)
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), phase="gt_open", sample_id=sample_id) from exc
    if not isinstance(opened, tuple) or len(opened) != 2:
        raise MetricBoundaryError(
            "GT opener must return (GroundTruthOpened, guard receipt)", phase="gt_open", sample_id=sample_id
        )
    ground_truth_opened, guard_receipt = opened
    if not isinstance(ground_truth_opened, GroundTruthOpened):
        raise MetricBoundaryError(
            "GT opener must return GroundTruthOpened from one sanctioned callback",
            phase="gt_open",
            sample_id=sample_id,
        )
    ground_truth = ground_truth_opened.graph
    estimated_nodes = ground_truth_opened.estimated_number_of_nodes
    if not isinstance(guard_receipt, Mapping):
        raise MetricBoundaryError("GT opener returned an invalid guard receipt", phase="gt_open", sample_id=sample_id)
    guard = _validate_guard_receipt(guard_receipt, artifact, sample_id)
    metric_started = _iso_now()
    prediction = _load_graph(Path(artifact["prediction_path"]))
    scale = tuple(float(value) for value in metric_config["scale"])
    max_distance = float(metric_config["max_distance"])
    try:
        evaluation_result = evaluate(prediction, ground_truth, scale=scale, max_distance=max_distance)
        recall = node_recall(prediction, ground_truth)
        row = per_sample_metrics(evaluation_result, n_total=estimated_nodes, node_recall=recall)
        summary = summarise([row])
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise MetricBoundaryError(
            f"official metric failed: {type(exc).__name__}: {exc}", phase="metric", sample_id=sample_id
        ) from exc
    metric_finished = _iso_now()

    counts = artifact["counts"]
    division_total = int(evaluation_result.division_tp + evaluation_result.division_fp + evaluation_result.division_fn)
    division_jaccard = _json_value(summary.get("division_jaccard"))
    final_score = summary.get("score")
    adjusted = summary.get("adj_edge_jaccard")
    _finite(final_score, field="final_score")
    _finite(adjusted, field="adjusted_edge_jaccard")
    if division_total == 0:
        division_jaccard = None
    else:
        _finite(division_jaccard, field="division_jaccard")
    values: dict[str, object] = {
        "schema_version": METRIC_SCHEMA_VERSION,
        "status": "READY",
        "panel_id": PANEL_ID,
        "selection_lock_id": selection_lock.get("selection_lock_id"),
        "sample_id": sample_id,
        "sample_index": list(PANEL_V1).index(sample_id),
        "prediction_path": str(artifact["prediction_path"]),
        "prediction_manifest_path": str(artifact["manifest_path"]),
        "prediction_manifest_sha256": artifact["manifest_sha256"],
        "prediction_directory_sha256": artifact["directory_sha256"],
        "prediction_files": artifact["files"],
        "prediction_total_bytes": artifact["total_bytes"],
        "prediction_node_count": counts["nodes"],
        "prediction_edge_count": counts["edges"],
        "prediction_fork_count": counts["forks"],
        "metric_config": dict(metric_config),
        "official_metric_provenance": dict(OFFICIAL_METRIC_PROVENANCE),
        "edge_tp": int(evaluation_result.edge_tp),
        "edge_fp": int(evaluation_result.edge_fp),
        "edge_fn": int(evaluation_result.edge_fn),
        "division_tp": int(evaluation_result.division_tp),
        "division_fp": int(evaluation_result.division_fp),
        "division_fn": int(evaluation_result.division_fn),
        "node_recall": _json_value(row["node_recall"]),
        "total_node_ratio": _json_value(row["total_node_ratio"]),
        "edge_jaccard": _json_value(summary.get("edge_jaccard")),
        "adjusted_edge_jaccard": _json_value(adjusted),
        "division_jaccard": division_jaccard,
        "final_score": _json_value(final_score),
        "division_term_live": division_total > 0,
        "metric_started_at": metric_started,
        "metric_finished_at": metric_finished,
        "reproduction_command": reproduction_command,
        "gt_open_receipt": guard,
        "official_metric_row": _json_value(row),
        "prediction_manifest_validated_before_gt": True,
        "selection_lock_identity": {
            key: selection_lock[key]
            for key in (
                "config_sha256",
                "source_commit",
                "primary_checkpoint_sha256",
                "secondary_checkpoint_sha256",
            )
            if key in selection_lock
        },
    }
    return values


def evaluate_locked_prediction(
    prediction: Path,
    gt: Path,
    selection_lock: Path | Mapping[str, object],
    output: Path | None = None,
    *,
    metric_config: Mapping[str, object] | None = None,
    sample_id: str | None = None,
    reproduction_command: str = "",
) -> dict[str, object]:
    """Evaluate one persisted prediction behind the GT guard.

    ``gt`` is an explicit path supplied by a fixed panel map.  This function
    never searches for, globs, or opens GT before the prediction preflight and
    fresh token mint have completed.
    """

    lock = _validate_panel_lock(selection_lock)
    config = _metric_config(metric_config)
    _assert_official_metric_pin()
    prediction_path = Path(prediction)
    resolved_sample = sample_id or prediction_path.name.removesuffix(".geff")
    if resolved_sample not in PANEL_V1:
        raise MetricBoundaryError("sample is not in fixed PANEL_V1", sample_id=resolved_sample)
    artifact = _validate_prediction_artifact(
        prediction_path,
        resolved_sample,
        selection_lock_id=str(lock["selection_lock_id"]),
    )
    token = _mint_for_artifact(artifact, sample_id=resolved_sample)
    receipt = _evaluate_prevalidated(
        artifact,
        token,
        Path(gt),
        sample_id=resolved_sample,
        selection_lock=lock,
        metric_config=config,
        reproduction_command=reproduction_command,
    )
    if output is not None:
        _write_json(Path(output), receipt)
    return receipt


def _receipt_mapping(value: Mapping[str, object] | Path, *, label: str) -> tuple[dict[str, object], str]:
    if isinstance(value, Path):
        payload = _read_json(value, label=label)
        return payload, _sha256_file(value)
    if isinstance(value, Mapping):
        payload = dict(value)
        return payload, hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    raise TypeError(f"{label} must be a path or mapping")


def _validate_sample_receipt(
    receipt: Mapping[str, object],
    sample_id: str,
    *,
    sample_index: int,
    selection_lock: Mapping[str, object],
    metric_config: Mapping[str, object],
) -> dict[str, object]:
    value = dict(receipt)
    if value.get("schema_version") != METRIC_SCHEMA_VERSION or value.get("status") != "READY":
        raise MetricBoundaryError("sample receipt is not a READY Task 5 receipt", sample_id=sample_id)
    if value.get("panel_id") != PANEL_ID or value.get("selection_lock_id") != selection_lock.get("selection_lock_id"):
        raise MetricBoundaryError("sample receipt panel/lock identity mismatch", sample_id=sample_id)
    if value.get("sample_id") != sample_id or value.get("sample_index") != sample_index:
        raise MetricBoundaryError("sample receipt order is not PANEL_V1", sample_id=sample_id)
    if value.get("metric_config") != dict(metric_config):
        raise MetricBoundaryError("sample receipt metric config differs from fixed config", sample_id=sample_id)
    if value.get("official_metric_provenance") != OFFICIAL_METRIC_PROVENANCE:
        raise MetricBoundaryError("sample receipt official metric provenance differs", sample_id=sample_id)
    for field in ("prediction_path", "prediction_manifest_path"):
        if not isinstance(value.get(field), str) or not str(value[field]).strip():
            raise MetricBoundaryError(f"sample receipt {field} is missing", sample_id=sample_id)
    for field in ("prediction_manifest_sha256", "prediction_directory_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise MetricBoundaryError(f"sample receipt {field} is not a lowercase SHA-256", sample_id=sample_id)
    for field in (
        "edge_tp",
        "edge_fp",
        "edge_fn",
        "division_tp",
        "division_fp",
        "division_fn",
        "prediction_node_count",
        "prediction_edge_count",
        "prediction_fork_count",
    ):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise MetricBoundaryError(f"sample receipt {field} is not a non-negative integer", sample_id=sample_id)
    for field in ("node_recall", "total_node_ratio", "edge_jaccard", "adjusted_edge_jaccard", "final_score"):
        _finite(value.get(field), field=f"sample receipt {field}")
    try:
        metric_started = parse_timestamp(value.get("metric_started_at"), field="metric_started_at")
        metric_finished = parse_timestamp(value.get("metric_finished_at"), field="metric_finished_at")
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), sample_id=sample_id) from exc
    if metric_started > _now() or metric_finished > _now() or not metric_started <= metric_finished:
        raise MetricBoundaryError("sample metric timestamps are invalid", sample_id=sample_id)
    division_total = int(value["division_tp"]) + int(value["division_fp"]) + int(value["division_fn"])
    if division_total == 0:
        if value.get("division_jaccard") is not None or value.get("division_term_live") is not False:
            raise MetricBoundaryError("zero-division sample must carry null division_jaccard", sample_id=sample_id)
    else:
        _finite(value.get("division_jaccard"), field="sample receipt division_jaccard")
        if value.get("division_term_live") is not True:
            raise MetricBoundaryError(
                "division term must be live when its denominator is positive",
                sample_id=sample_id,
            )
    guard = value.get("gt_open_receipt")
    if not isinstance(guard, Mapping):
        raise MetricBoundaryError("sample receipt is missing gt_open_receipt", sample_id=sample_id)
    if guard.get("ordering_enforced_by") != "biohub.reproducibility.gt_guard.open_ground_truth":
        raise MetricBoundaryError("sample receipt lacks gt_guard ordering evidence", sample_id=sample_id)
    for field in ("prediction_files", "prediction_total_bytes"):
        number = guard.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise MetricBoundaryError(f"sample receipt guard {field} is invalid", sample_id=sample_id)
        if field in value and value[field] != number:
            raise MetricBoundaryError(f"sample receipt guard {field} disagrees", sample_id=sample_id)
    for field in ("prediction_path", "prediction_manifest_path", "prediction_directory_sha256"):
        if guard.get(field) != value.get(field):
            raise MetricBoundaryError(f"sample receipt guard {field} disagrees", sample_id=sample_id)
    try:
        created = parse_timestamp(guard.get("prediction_manifest_created_at"), field="manifest_created_at")
        persisted = parse_timestamp(guard.get("prediction_persisted_at"), field="prediction_persisted_at")
        opened = parse_timestamp(guard.get("ground_truth_opened_at"), field="ground_truth_opened_at")
    except GroundTruthOrderingError as exc:
        raise MetricBoundaryError(str(exc), sample_id=sample_id) from exc
    if not created < persisted < opened:
        raise MetricBoundaryError("sample receipt guard timestamps are out of order", sample_id=sample_id)
    if opened > _now():
        raise MetricBoundaryError("sample receipt guard claims a future GT open", sample_id=sample_id)
    row = _receipt_row(value)
    expected_row_fields = {
        "edge_tp",
        "edge_fp",
        "edge_fn",
        "division_tp",
        "division_fp",
        "division_fn",
        "num_pred_nodes",
        "node_recall",
        "total_node_ratio",
        "edge_jaccard",
        "adj_edge_jaccard",
    }
    if set(row) != expected_row_fields:
        raise MetricBoundaryError("sample receipt official metric row schema is not exact", sample_id=sample_id)
    for field in (
        "edge_tp",
        "edge_fp",
        "edge_fn",
        "division_tp",
        "division_fp",
        "division_fn",
        "num_pred_nodes",
    ):
        number = row.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise MetricBoundaryError(f"sample receipt metric row {field} is invalid", sample_id=sample_id)
        receipt_field = "prediction_node_count" if field == "num_pred_nodes" else field
        if field != "num_pred_nodes" and number != value[receipt_field]:
            raise MetricBoundaryError(f"sample receipt metric row {field} disagrees", sample_id=sample_id)
        if field == "num_pred_nodes" and number != value["prediction_node_count"]:
            raise MetricBoundaryError("sample receipt metric row num_pred_nodes disagrees", sample_id=sample_id)
    for row_field, receipt_field in (
        ("node_recall", "node_recall"),
        ("total_node_ratio", "total_node_ratio"),
        ("edge_jaccard", "edge_jaccard"),
        ("adj_edge_jaccard", "adjusted_edge_jaccard"),
    ):
        row_value = _finite(row[row_field], field=f"sample receipt metric row {row_field}")
        receipt_value = _finite(value[receipt_field], field=f"sample receipt {receipt_field}")
        if not math.isclose(row_value, receipt_value, rel_tol=1e-12, abs_tol=1e-12):
            raise MetricBoundaryError(f"sample receipt metric row {row_field} disagrees", sample_id=sample_id)
    return value


def _receipt_row(receipt: Mapping[str, object]) -> dict[str, object]:
    row = receipt.get("official_metric_row")
    if not isinstance(row, Mapping):
        raise MetricBoundaryError("sample receipt lacks the official metric row", phase="aggregate")
    return dict(row)


def _assert_summary_matches_receipt(
    receipt: Mapping[str, object],
    row: Mapping[str, object],
    summary: Mapping[str, object],
) -> None:
    """Reject score fields that disagree with a fresh official-row summary."""

    def matches(actual: object, expected: object, *, field: str) -> None:
        expected_value = float(expected) if isinstance(expected, (int, float)) else expected
        if isinstance(expected_value, float) and not math.isfinite(expected_value):
            expected_value = None
        if expected_value is None:
            if actual is not None:
                raise MetricBoundaryError(f"sample receipt {field} disagrees with summarise([row])", phase="aggregate")
            return
        try:
            actual_value = _finite(actual, field=f"sample receipt {field}")
        except MetricBoundaryError as exc:
            raise MetricBoundaryError(
                f"sample receipt {field} disagrees with summarise([row])", phase="aggregate"
            ) from exc
        if not math.isclose(actual_value, expected_value, rel_tol=1e-12, abs_tol=1e-12):
            raise MetricBoundaryError(f"sample receipt {field} disagrees with summarise([row])", phase="aggregate")

    matches(receipt["edge_jaccard"], summary.get("edge_jaccard"), field="edge_jaccard")
    matches(receipt["adjusted_edge_jaccard"], summary.get("adj_edge_jaccard"), field="adjusted_edge_jaccard")
    matches(receipt["final_score"], summary.get("score"), field="final_score")
    matches(receipt.get("division_jaccard"), summary.get("division_jaccard"), field="division_jaccard")
    matches(receipt["node_recall"], summary.get("node_recall"), field="node_recall")
    matches(row["node_recall"], summary.get("node_recall"), field="official_metric_row.node_recall")
    matches(receipt["total_node_ratio"], row["total_node_ratio"], field="total_node_ratio")
    division_live = (
        int(row["division_tp"]) + int(row["division_fp"]) + int(row["division_fn"]) > 0
    )
    if receipt.get("division_term_live") is not division_live:
        raise MetricBoundaryError("sample receipt division_term_live disagrees with official row", phase="aggregate")


def aggregate_panel_receipts(
    receipts: Sequence[Mapping[str, object] | Path],
    selection_lock: Path | Mapping[str, object],
    control_receipt: Mapping[str, object] | Path | None = None,
) -> dict[str, object]:
    """Aggregate exactly five READY sample receipts in fixed PANEL_V1 order."""

    lock = _validate_panel_lock(selection_lock)
    config = _metric_config(None)
    _assert_official_metric_pin()
    if len(receipts) != len(PANEL_V1):
        raise MetricBoundaryError("PANEL_V1 aggregation requires exactly five sample receipts", phase="aggregate")
    loaded: list[dict[str, object]] = []
    receipt_refs: list[dict[str, object]] = []
    for index, item in enumerate(receipts):
        value, receipt_hash = _receipt_mapping(item, label="sample metric receipt")
        sample_id = value.get("sample_id")
        if sample_id != PANEL_V1[index]:
            raise MetricBoundaryError("sample receipts are missing, duplicated, or out of order", phase="aggregate")
        checked = _validate_sample_receipt(
            value,
            str(sample_id),
            sample_index=index,
            selection_lock=lock,
            metric_config=config,
        )
        loaded.append(checked)
        receipt_refs.append(
            {
                "sample_id": sample_id,
                "receipt_sha256": receipt_hash,
                "final_score": checked["final_score"],
                "prediction_path": checked.get("prediction_path"),
            }
        )

    rows = [_receipt_row(item) for item in loaded]
    for receipt, row in zip(loaded, rows, strict=True):
        try:
            sample_summary = summarise([row])
        except Exception as exc:
            raise MetricBoundaryError(
                f"official metric row could not be summarised: {type(exc).__name__}: {exc}",
                phase="aggregate",
            ) from exc
        _assert_summary_matches_receipt(receipt, row, sample_summary)
    summary = summarise(rows)
    scores = [float(item["final_score"]) for item in loaded]
    macro = math.fsum(scores) / len(scores)
    total_division = sum(
        int(item["division_tp"]) + int(item["division_fp"]) + int(item["division_fn"])
        for item in loaded
    )
    total_division_tp = sum(int(item["division_tp"]) for item in loaded)
    total_division_fp = sum(int(item["division_fp"]) for item in loaded)
    total_division_fn = sum(int(item["division_fn"]) for item in loaded)
    panel_division_jaccard = (
        total_division_tp / total_division if total_division > 0 else None
    )
    official_score = _json_value(summary.get("score"))
    _finite(official_score, field="official size-weighted score")
    result: dict[str, object] = {
        "schema_version": PANEL_SCHEMA_VERSION,
        "status": "READY",
        "panel_status": "READY",
        "panel_id": PANEL_ID,
        "panel_version": PANEL_ID,
        "sample_order": list(PANEL_V1),
        "selection_lock_id": lock.get("selection_lock_id"),
        "selection_lock_digest": lock.get("selection_lock_id"),
        "metric_config": dict(config),
        "official_metric_provenance": dict(OFFICIAL_METRIC_PROVENANCE),
        "sample_receipts": receipt_refs,
        "macro_final_score": macro,
        "official_size_weighted_adjusted_edge_jaccard": _json_value(summary.get("adj_edge_jaccard")),
        "official_size_weighted_score": official_score,
        "micro_edge_jaccard": _json_value(summary.get("edge_jaccard")),
        "division_counts_total": {
            "tp": total_division_tp,
            "fp": total_division_fp,
            "fn": total_division_fn,
        },
        "panel_division_jaccard": panel_division_jaccard,
        "division_term_live": total_division > 0,
        "median_final_score": statistics.median(scores),
        "target": TARGET_SCORE,
        "gap_to_target": macro - TARGET_SCORE,
        "gate_passed": macro >= TARGET_SCORE,
        "reproduction_command": "",
        "created_at": _iso_now(),
        "finished_at": _iso_now(),
    }
    if control_receipt is not None:
        result["control_receipt"] = _receipt_mapping(control_receipt, label="control receipt")[0]
    return result


def _validate_inference_receipt(
    receipt: Mapping[str, object],
    *,
    selection_lock: Mapping[str, object],
) -> tuple[dict[str, Path], dict[str, Path]]:
    if receipt.get("status") != "READY":
        raise MetricBoundaryError("Task 4 inference receipt is not READY", phase="inference_receipt")
    if receipt.get("selection_lock_id") != selection_lock.get("selection_lock_id"):
        raise MetricBoundaryError("Task 4 receipt selection lock mismatch", phase="inference_receipt")
    sample_ids = receipt.get("sample_ids")
    if tuple(sample_ids or ()) != PANEL_V1:
        raise MetricBoundaryError("Task 4 receipt does not contain exact PANEL_V1", phase="inference_receipt")
    final_raw = receipt.get("final_geffs")
    manifests_raw = receipt.get("manifests")
    if not isinstance(final_raw, Mapping) or not isinstance(manifests_raw, Mapping):
        raise MetricBoundaryError("Task 4 receipt lacks final_geffs/manifests", phase="inference_receipt")
    if tuple(final_raw) != PANEL_V1 or tuple(manifests_raw) != PANEL_V1:
        raise MetricBoundaryError(
            "Task 4 receipt final GEFF/manifest set is not exact PANEL_V1",
            phase="inference_receipt",
        )
    final: dict[str, Path] = {}
    manifests: dict[str, Path] = {}
    for sample_id in PANEL_V1:
        raw_prediction = final_raw[sample_id]
        raw_manifest = manifests_raw[sample_id]
        if not isinstance(raw_prediction, str) or not isinstance(raw_manifest, str):
            raise MetricBoundaryError("Task 4 receipt paths must be strings", phase="inference_receipt")
        final[sample_id] = Path(raw_prediction)
        manifests[sample_id] = Path(raw_manifest)
    return final, manifests


def _resolve_role_path(path: Path, *, root: Path) -> Path:
    """Resolve one Task 4 role only within a non-symlink root."""

    role = Path(path)
    if (
        role.is_absolute()
        or not role.parts
        or any(part in ("", ".", "..") for part in role.parts)
        or any("\\" in part for part in role.parts)
    ):
        raise MetricBoundaryError(
            "role path must be relative, normalized, and contain no traversal",
            phase="inference_receipt",
        )
    root = Path(root).absolute()
    _reject_symlink_components(root, label="prediction root")
    if root.is_symlink() or not root.is_dir():
        raise MetricBoundaryError("prediction root must be a non-symlink directory", phase="inference_receipt")
    candidate = root.joinpath(*role.parts)
    _reject_symlink_components(candidate, label="role path")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MetricBoundaryError("role path escapes prediction root", phase="inference_receipt") from exc
    if candidate.is_symlink():
        raise MetricBoundaryError("role path is a symlink", phase="inference_receipt")
    return candidate


def _failed_panel_receipt(*, lock: Mapping[str, object], failure: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": PANEL_SCHEMA_VERSION,
        "status": "FAILED",
        "panel_status": "INCOMPLETE",
        "panel_id": PANEL_ID,
        "panel_version": PANEL_ID,
        "sample_order": list(PANEL_V1),
        "selection_lock_id": lock.get("selection_lock_id"),
        "metric_config": _metric_config(None),
        "official_metric_provenance": dict(OFFICIAL_METRIC_PROVENANCE),
        "failure": dict(failure),
        "reproduction_command": "",
        "created_at": _iso_now(),
        "finished_at": _iso_now(),
    }


def evaluate_panel(
    selection_lock: Path | Mapping[str, object],
    prediction_root: Path,
    output: Path | None = None,
    *,
    inference_receipt: Path | Mapping[str, object],
    ground_truth_map: Mapping[str, Path],
    metric_config: Mapping[str, object] | None = None,
    reproduction_command: str = "",
) -> dict[str, object]:
    """Preflight and evaluate the locked five-sample panel.

    All five prediction artifacts and persistence tokens are prepared before the
    first GT opener call.  A later sample failure therefore cannot produce a
    partial macro or a ``READY`` panel receipt.
    """

    lock = _validate_panel_lock(selection_lock)
    config = _metric_config(metric_config)
    _assert_official_metric_pin()
    root = Path(prediction_root)
    _reject_symlink_components(root, label="prediction root")
    if root.is_symlink() or not root.is_dir():
        raise MetricBoundaryError("prediction root must be a non-symlink directory", phase="inference_receipt")
    if isinstance(inference_receipt, Mapping):
        inference_payload = dict(inference_receipt)
    elif isinstance(inference_receipt, Path):
        inference_payload = _read_json(Path(inference_receipt), label="Task 4 inference receipt")
    else:
        raise TypeError("inference_receipt must be an explicit path or mapping")
    try:
        final_raw, manifests_raw = _validate_inference_receipt(inference_payload, selection_lock=lock)
        final_paths = {
            sample: _resolve_role_path(path, root=root)
            for sample, path in final_raw.items()
        }
        manifest_paths = {
            sample: _resolve_role_path(path, root=root)
            for sample, path in manifests_raw.items()
        }
        artifacts: dict[str, dict[str, Any]] = {}
        tokens: dict[str, PredictionPersistedToken] = {}
        for sample_id in PANEL_V1:
            artifacts[sample_id] = _validate_prediction_artifact(
                final_paths[sample_id],
                sample_id,
                selection_lock_id=str(lock["selection_lock_id"]),
                expected_manifest_path=manifest_paths[sample_id],
            )
            tokens[sample_id] = _mint_for_artifact(artifacts[sample_id], sample_id=sample_id)
    except MetricBoundaryError as exc:
        if output is not None:
            _write_json(
                Path(output),
                _failed_panel_receipt(
                    lock=lock,
                    failure={
                        "phase": exc.phase,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "reproduction_command": reproduction_command,
                    },
                ),
            )
        raise
    except Exception as exc:
        if output is not None:
            _write_json(
                Path(output),
                _failed_panel_receipt(
                    lock=lock,
                    failure={
                        "phase": "preflight",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "reproduction_command": reproduction_command,
                    },
                ),
            )
        raise MetricBoundaryError(f"panel preflight failed: {type(exc).__name__}: {exc}", phase="preflight") from exc

    if tuple(ground_truth_map) != PANEL_V1:
        raise MetricBoundaryError("ground_truth_map must contain exact PANEL_V1 order", phase="gt_map")
    gt_paths: dict[str, Path] = {}
    for sample in PANEL_V1:
        raw_path = ground_truth_map[sample]
        if not isinstance(raw_path, (Path, str)):
            raise MetricBoundaryError("ground_truth_map values must be explicit paths", phase="gt_map")
        gt_path = Path(raw_path)
        if any(part in ("", ".", "..") for part in gt_path.parts):
            raise MetricBoundaryError("ground_truth_map paths must not contain traversal", phase="gt_map")
        gt_paths[sample] = gt_path

    sample_receipts: list[dict[str, object]] = []
    try:
        for sample_id in PANEL_V1:
            sample_receipts.append(
                _evaluate_prevalidated(
                    artifacts[sample_id],
                    tokens[sample_id],
                    gt_paths[sample_id],
                    sample_id=sample_id,
                    selection_lock=lock,
                    metric_config=config,
                    reproduction_command=reproduction_command,
                )
            )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        failure = {
            "sample_id": getattr(exc, "sample_id", None),
            "phase": getattr(exc, "phase", "metric"),
            "error_type": type(exc).__name__,
            "message": str(exc),
            "reproduction_command": reproduction_command,
        }
        result = _failed_panel_receipt(lock=lock, failure=failure)
        if output is not None:
            _write_json(Path(output), result)
        return result

    try:
        result = aggregate_panel_receipts(sample_receipts, lock)
    except Exception as exc:
        failure = {
            "phase": "aggregate",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "reproduction_command": reproduction_command,
        }
        result = _failed_panel_receipt(lock=lock, failure=failure)
        if output is not None:
            _write_json(Path(output), result)
        return result

    result["reproduction_command"] = reproduction_command
    if output is not None:
        output_path = Path(output)
        persisted_refs: list[dict[str, object]] = []
        for receipt in sample_receipts:
            sample_id = str(receipt["sample_id"])
            sample_path = output_path.parent / f"{sample_id}.metric_receipt.json"
            _write_json(sample_path, receipt)
            persisted_refs.append(
                {
                    "sample_id": sample_id,
                    "receipt_path": str(sample_path),
                    "receipt_sha256": _sha256_file(sample_path),
                    "final_score": receipt["final_score"],
                }
            )
        result["sample_receipts"] = persisted_refs
        artifact_payload = {key: value for key, value in result.items() if key != "artifact_sha256"}
        result["artifact_sha256"] = hashlib.sha256(_canonical_json(artifact_payload).encode("utf-8")).hexdigest()
        _write_json(output_path, result)
    return result


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    target = _safe_write_target(Path(path))
    write_json_exclusive(target, payload, mode=0o600)


__all__ = [
    "DEFAULT_MAX_DISTANCE",
    "DEFAULT_SCALE",
    "METRIC_SCHEMA_VERSION",
    "OFFICIAL_METRIC_PROVENANCE",
    "PANEL_ID",
    "PANEL_SCHEMA_VERSION",
    "TARGET_SCORE",
    "GroundTruthOpened",
    "MetricBoundaryError",
    "aggregate_panel_receipts",
    "evaluate_locked_prediction",
    "evaluate_panel",
]
