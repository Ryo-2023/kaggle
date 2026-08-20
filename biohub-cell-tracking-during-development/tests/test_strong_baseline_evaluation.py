from pathlib import Path
from unittest.mock import Mock

import polars as pl
import pytest
import tracksdata as td
import zarr

from biohub.strong_baseline import evaluation
from biohub.strong_baseline.evaluation import evaluate_prediction
from biohub.strong_baseline.manifest import prediction_directory_manifest, write_prediction_manifest


def _two_node_graph() -> td.graph.IndexedRXGraph:
    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, dtype=pl.Float64, default_value=0.0)
    graph.add_node({"t": 0, "z": 0.0, "y": 0.0, "x": 0.0})
    graph.add_node({"t": 1, "z": 0.0, "y": 0.0, "x": 0.0})
    graph.add_edge(0, 1, {})
    return graph


def _write_two_node_graph(path: Path) -> None:
    _two_node_graph().to_geff(path)


def test_evaluate_prediction_reports_official_metrics(tmp_path: Path) -> None:
    pred_path = tmp_path / "prediction.geff"
    gt_path = tmp_path / "ground_truth.geff"
    _write_two_node_graph(pred_path)
    _write_two_node_graph(gt_path)
    write_prediction_manifest(pred_path, prediction_directory_manifest(pred_path))

    gt_root = zarr.open_group(gt_path)
    attrs = gt_root.attrs.asdict()
    attrs["geff"]["extra"]["estimated_number_of_nodes"] = 2
    gt_root.attrs.put(attrs)

    result = evaluate_prediction(
        pred_path,
        gt_path,
        scale=(1.625, 0.40625, 0.40625),
    )

    assert result["prediction_node_count"] == 2
    assert result["prediction_edge_count"] == 1
    assert result["edge_tp"] == 1
    assert result["edge_fp"] == 0
    assert result["edge_fn"] == 0
    assert result["edge_jaccard"] == 1.0
    assert result["adjusted_edge_jaccard"] == 1.0
    assert result["final_score"] == 1.0
    assert result["division_jaccard"] is None
    assert result["prediction_manifest_validated_before_gt"] is True
    assert result["prediction_manifest_validation_action"].endswith("before opening ground truth")


def test_evaluate_prediction_rejects_missing_manifest_before_opening_gt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction = tmp_path / "prediction.geff"
    ground_truth = tmp_path / "ground_truth.geff"
    _write_two_node_graph(prediction)
    _write_two_node_graph(ground_truth)
    gt_open = Mock(side_effect=AssertionError("GT must not be opened"))
    monkeypatch.setattr(
        evaluation,
        "_load_graph",
        lambda path: gt_open(path) if path == ground_truth else _two_node_graph(),
    )

    with pytest.raises(ValueError, match="prediction manifest"):
        evaluate_prediction(prediction, ground_truth, scale=(1.625, 0.40625, 0.40625))

    gt_open.assert_not_called()


def test_evaluate_prediction_rejects_mismatched_manifest_before_opening_gt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction = tmp_path / "prediction.geff"
    ground_truth = tmp_path / "ground_truth.geff"
    _write_two_node_graph(prediction)
    _write_two_node_graph(ground_truth)
    (tmp_path / "prediction_manifest.json").write_text(
        '{"prediction_path": "prediction.geff", "directory_sha256": "bad", "files": 1, '
        '"total_bytes": 1, "nodes": 2, "edges": 1}\n'
    )
    gt_open = Mock(side_effect=AssertionError("GT must not be opened"))
    monkeypatch.setattr(
        evaluation,
        "_load_graph",
        lambda path: gt_open(path) if path == ground_truth else _two_node_graph(),
    )

    with pytest.raises(ValueError, match="manifest"):
        evaluate_prediction(prediction, ground_truth, scale=(1.625, 0.40625, 0.40625))

    gt_open.assert_not_called()
