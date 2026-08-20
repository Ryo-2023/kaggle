"""Prediction GEFF and official-metric boundaries for detector-fixed runs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import tracksdata as td
import zarr

from biohub.detector_fixed_race.association import AssociationResult
from biohub.detector_fixed_race.schema import DetectorCache
from biohub.official_metrics.metrics import evaluate, node_recall, per_sample_metrics, summarise
from biohub.strong_baseline.manifest import (
    prediction_directory_manifest,
    validate_prediction_manifest,
    write_prediction_manifest,
)

DEFAULT_METRIC_SCALE = (1.625, 0.40625, 0.40625)


def _compact_prediction_inputs(
    cache: DetectorCache,
    selected_edges: np.ndarray,
) -> tuple[np.ndarray, list[tuple[int, int, float, float]]]:
    """Drop detector nodes that are not present in the solved graph.

    The pinned upstream ILP writer serializes the solver graph, which retains
    only nodes participating in a selected edge.  The detector cache also
    contains isolated detections, so remap selected edge IDs before calling
    the same upstream graph builder to preserve official GEFF semantics.
    """

    if selected_edges.shape[0] == 0:
        return np.empty((0, 4), dtype=cache.nodes.tzyx.dtype), []
    raw_ids = selected_edges[:, :2]
    if not np.equal(raw_ids, np.floor(raw_ids)).all():
        raise ValueError("selected edge node IDs must be integer-valued")
    node_ids = raw_ids.astype(np.int64, copy=False).reshape(-1)
    if np.any(node_ids < 0) or np.any(node_ids >= cache.nodes.length):
        raise ValueError("selected edge node IDs must refer to detector cache nodes")
    used_ids = np.unique(node_ids)
    remap = {int(old): index for index, old in enumerate(used_ids.tolist())}
    coords = cache.nodes.tzyx[used_ids].copy()
    edge_rows = [
        (remap[int(row[0])], remap[int(row[1])], float(row[2]), float(row[3]))
        for row in selected_edges.tolist()
    ]
    return coords, edge_rows


def _load_graph(path: Path) -> td.graph.BaseGraph:
    loaded = td.graph.IndexedRXGraph.from_geff(path)
    if isinstance(loaded, tuple):
        return loaded[0]
    return loaded


def _open_ground_truth(path: Path) -> td.graph.BaseGraph:
    """Open GT only after prediction manifest validation has succeeded."""

    return _load_graph(path)


def _estimated_node_count(path: Path) -> float:
    attrs = zarr.open_group(path).attrs
    try:
        value: Any = attrs["geff"]["extra"]["estimated_number_of_nodes"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Ground-truth GEFF is missing geff.extra.estimated_number_of_nodes: {path}"
        ) from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"ground-truth estimated_number_of_nodes must be finite numeric: {value!r}")
    return float(value)


def _metric_settings(metric_config: Mapping[str, Any]) -> tuple[tuple[float, float, float], float]:
    if not isinstance(metric_config, Mapping):
        raise TypeError("metric_config must be a mapping")
    scale_value = metric_config.get("scale", DEFAULT_METRIC_SCALE)
    if not isinstance(scale_value, (tuple, list)) or len(scale_value) != 3:
        raise ValueError("metric_config.scale must contain three physical voxel scales")
    scale = tuple(float(value) for value in scale_value)
    if not all(math.isfinite(value) and value > 0.0 for value in scale):
        raise ValueError("metric_config.scale must contain finite positive values")
    max_distance = float(metric_config.get("max_distance", 7.0))
    if not math.isfinite(max_distance) or max_distance <= 0.0:
        raise ValueError("metric_config.max_distance must be finite and positive")
    return scale, max_distance


def _validate_detector_prediction_manifest(path: Path) -> dict[str, Any]:
    receipt = validate_prediction_manifest(path)
    manifest_path = Path(receipt["manifest_path"])
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"prediction manifest is unreadable: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("ground_truth_included") is not False:
        raise ValueError("prediction manifest must explicitly set ground_truth_included=false")
    return receipt


def write_prediction(
    cache: DetectorCache,
    result: AssociationResult,
    predictor_module: ModuleType,
    output_path: Path,
) -> Path:
    """Serialize an association result with the pinned upstream GEFF writer."""

    if not isinstance(cache, DetectorCache):
        raise TypeError("cache must be a DetectorCache")
    if not isinstance(result, AssociationResult):
        raise TypeError("result must be an AssociationResult")
    cache_hash = cache.manifest.get("cache_hash")
    if not isinstance(cache_hash, str) or result.cache_hash != cache_hash:
        raise ValueError("association result cache_hash does not match detector cache")
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"prediction destination already exists: {output_path}")
    if not hasattr(predictor_module, "build_graph") or not hasattr(predictor_module, "save_graph"):
        raise TypeError("predictor_module must expose build_graph and save_graph")

    selected = result.selected_edges
    if selected.ndim != 2 or selected.shape[1] != 4 or not math.isfinite(float(selected.sum())):
        raise ValueError("association selected_edges must be a finite (E, 4) array")
    cache.nodes.validate()
    cache.edges.validate(cache.nodes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    coords, edge_rows = _compact_prediction_inputs(cache, selected)
    graph = predictor_module.build_graph(coords, edge_rows)
    predictor_module.save_graph(graph, output_path)
    if not output_path.exists():
        raise RuntimeError(f"predictor_module.save_graph did not create {output_path}")

    manifest = prediction_directory_manifest(output_path)
    manifest.update(
        {
            "method_id": result.method_id,
            "cache_hash": result.cache_hash,
            "config": dict(result.config),
            "prediction_sha256": manifest["directory_sha256"],
            "ground_truth_included": False,
        }
    )
    write_prediction_manifest(output_path, manifest)
    return output_path


def evaluate_prediction(
    prediction_path: Path,
    ground_truth_path: Path,
    metric_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one prediction after validating its manifest and GT boundary."""

    prediction_path = Path(prediction_path)
    ground_truth_path = Path(ground_truth_path)
    scale, max_distance = _metric_settings(metric_config)
    manifest_receipt = _validate_detector_prediction_manifest(prediction_path)
    prediction = _load_graph(prediction_path)
    ground_truth = _open_ground_truth(ground_truth_path)

    evaluation_result = evaluate(
        prediction,
        ground_truth,
        scale=scale,
        max_distance=max_distance,
    )
    recall = node_recall(prediction, ground_truth)
    row = per_sample_metrics(
        evaluation_result,
        n_total=_estimated_node_count(ground_truth_path),
        node_recall=recall,
    )
    summary = summarise([row])
    values: dict[str, Any] = {
        "prediction_node_count": prediction.num_nodes(),
        "prediction_edge_count": prediction.num_edges(),
        "edge_tp": evaluation_result.edge_tp,
        "edge_fp": evaluation_result.edge_fp,
        "edge_fn": evaluation_result.edge_fn,
        "division_tp": evaluation_result.division_tp,
        "division_fp": evaluation_result.division_fp,
        "division_fn": evaluation_result.division_fn,
        "node_recall": row["node_recall"],
        "total_node_ratio": row["total_node_ratio"],
        "edge_jaccard": summary["edge_jaccard"],
        "adjusted_edge_jaccard": summary["adj_edge_jaccard"],
        "division_jaccard": summary["division_jaccard"],
        "final_score": summary["score"],
    }
    result: dict[str, Any] = {
        key: (value if not isinstance(value, float) or math.isfinite(value) else None)
        for key, value in values.items()
    }
    result.update(
        {
            "prediction_manifest_path": manifest_receipt["manifest_path"],
            "prediction_manifest_directory_sha256": manifest_receipt["directory_sha256"],
            "prediction_manifest_validated_at": manifest_receipt["validated_at"],
            "prediction_manifest_validation_action": manifest_receipt["validation_action"],
            "prediction_manifest_validated_before_gt": True,
        }
    )
    return result


__all__ = [
    "DEFAULT_METRIC_SCALE",
    "evaluate_prediction",
    "write_prediction",
]
