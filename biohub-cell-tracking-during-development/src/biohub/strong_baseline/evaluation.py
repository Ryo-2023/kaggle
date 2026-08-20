"""Post-prediction boundary for the vendored official graph metrics."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import tracksdata as td
import zarr

from biohub.official_metrics.metrics import (
    evaluate,
    node_recall,
    per_sample_metrics,
    summarise,
)
from biohub.strong_baseline.manifest import validate_prediction_manifest

MetricValue = int | float | None | str | bool


def _load_graph(path: Path) -> td.graph.BaseGraph:
    loaded = td.graph.IndexedRXGraph.from_geff(path)
    if isinstance(loaded, tuple):
        return loaded[0]
    return loaded


def _estimated_node_count(path: Path) -> float:
    attrs = zarr.open_group(path).attrs
    try:
        value: Any = attrs["geff"]["extra"]["estimated_number_of_nodes"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Ground-truth GEFF is missing geff.extra.estimated_number_of_nodes: {path}",
        ) from exc
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(
            "Ground-truth geff.extra.estimated_number_of_nodes must be numeric: "
            f"{value!r}",
        )
    return float(value)


def _json_value(value: Any) -> MetricValue:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def evaluate_prediction(
    prediction_path: Path,
    gt_path: Path,
    scale: tuple[float, float, float],
    max_distance: float = 7.0,
) -> dict[str, MetricValue]:
    """Evaluate a serialized prediction against a serialized ground truth.

    The prediction is loaded from disk within this post-prediction phase.  The
    official evaluator writes matching attributes onto the loaded graph, never
    onto the serialized prediction artifact.
    """

    prediction_path = Path(prediction_path)
    gt_path = Path(gt_path)
    manifest_receipt = validate_prediction_manifest(prediction_path)
    prediction = _load_graph(prediction_path)
    ground_truth = _load_graph(gt_path)
    prediction_node_count = prediction.num_nodes()
    prediction_edge_count = prediction.num_edges()

    evaluation_result = evaluate(
        prediction,
        ground_truth,
        scale=scale,
        max_distance=max_distance,
    )
    recall = node_recall(prediction, ground_truth)
    row = per_sample_metrics(
        evaluation_result,
        n_total=_estimated_node_count(gt_path),
        node_recall=recall,
    )
    summary = summarise([row])

    values: dict[str, Any] = {
        "prediction_node_count": prediction_node_count,
        "prediction_edge_count": prediction_edge_count,
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
    result = {key: _json_value(value) for key, value in values.items()}
    result.update(
        {
            "prediction_manifest_path": manifest_receipt["manifest_path"],
            "prediction_manifest_directory_sha256": manifest_receipt["directory_sha256"],
            "prediction_manifest_validated_at": manifest_receipt["validated_at"],
            "prediction_manifest_validation_action": manifest_receipt["validation_action"],
            "prediction_manifest_validated_before_gt": True,
        },
    )
    return result
