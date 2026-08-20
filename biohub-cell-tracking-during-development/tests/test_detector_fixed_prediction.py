import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import tracksdata as td
import zarr

import biohub.detector_fixed_race.prediction as prediction_module
from biohub.detector_fixed_race.association import AssociationResult
from biohub.detector_fixed_race.prediction import evaluate_prediction, write_prediction
from biohub.detector_fixed_race.schema import CandidateEdgeArrays, DetectorCache, NodeArrays


def _graph(coords: np.ndarray, edges: list[tuple[int, int, float, float]]) -> td.graph.IndexedRXGraph:
    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, dtype=pl.Float64, default_value=0.0)
    graph.add_edge_attr_key("edge_prob", dtype=pl.Float64, default_value=0.0)
    graph.add_edge_attr_key("edge_dist", dtype=pl.Float64, default_value=0.0)
    for t, z, y, x in coords.tolist():
        graph.add_node({"t": int(t), "z": float(z), "y": float(y), "x": float(x)})
    for source, target, score, distance in edges:
        graph.add_edge(
            int(source),
            int(target),
            {"edge_prob": float(score), "edge_dist": float(distance)},
        )
    return graph


def _predictor() -> SimpleNamespace:
    return SimpleNamespace(
        build_graph=_graph,
        save_graph=lambda graph, path: graph.to_geff(path),
    )


def _cache() -> DetectorCache:
    nodes = NodeArrays(
        node_id=np.array([0, 1, 2], dtype=np.int64),
        tzyx=np.array([[0, 0, 0, 0], [1, 0, 0, 0], [2, 0, 0, 0]], dtype=np.int32),
        physical_zyx=np.zeros((3, 3), dtype=np.float32),
        detector_peak_logit=np.ones(3, dtype=np.float32),
        detector_peak_probability=np.full(3, 0.75, dtype=np.float32),
        node_features=np.ones((3, 1), dtype=np.float32),
    )
    edges = CandidateEdgeArrays(
        source_node_id=np.array([0], dtype=np.int64),
        target_node_id=np.array([1], dtype=np.int64),
        delta_t=np.array([1], dtype=np.int16),
        voxel_delta=np.zeros((1, 3), dtype=np.float32),
        physical_delta=np.zeros((1, 3), dtype=np.float32),
        voxel_distance=np.zeros(1, dtype=np.float32),
        physical_distance=np.zeros(1, dtype=np.float32),
        forward_logit=np.ones(1, dtype=np.float32),
        reverse_logit=np.ones(1, dtype=np.float32),
        forward_probability=np.full(1, 0.9, dtype=np.float32),
        reverse_probability=np.full(1, 0.8, dtype=np.float32),
    )
    return DetectorCache(
        root=Path("cache/test"),
        manifest={"cache_hash": "cache-hash", "ground_truth_included": False},
        nodes=nodes,
        edges=edges,
    )


def _result() -> AssociationResult:
    return AssociationResult(
        method_id="official_ilp",
        cache_hash="cache-hash",
        selected_edges=np.array([[0, 1, 0.9, 0.0]], dtype=np.float32),
        graph=object(),
        config={"edge_threshold": 0.5},
    )


def _write_gt(path: Path) -> None:
    _graph(
        np.array([[0, 0, 0, 0], [1, 0, 0, 0]], dtype=np.int32),
        [(0, 1, 1.0, 0.0)],
    ).to_geff(path)
    root = zarr.open_group(path)
    attrs = root.attrs.asdict()
    attrs["geff"]["extra"]["estimated_number_of_nodes"] = 2
    root.attrs.put(attrs)


def test_write_prediction_records_cache_hash_and_ground_truth_boundary(tmp_path: Path) -> None:
    path = write_prediction(_cache(), _result(), _predictor(), tmp_path / "prediction.geff")
    payload = json.loads((tmp_path / "prediction_manifest.json").read_text())
    assert path.is_dir()
    assert payload["method_id"] == "official_ilp"
    assert payload["cache_hash"] == "cache-hash"
    assert payload["prediction_sha256"] == payload["directory_sha256"]
    assert payload["ground_truth_included"] is False
    assert payload["nodes"] == 2
    assert payload["edges"] == 1


def test_metric_opens_ground_truth_only_after_prediction_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction = write_prediction(_cache(), _result(), _predictor(), tmp_path / "prediction.geff")
    ground_truth = tmp_path / "ground_truth.geff"
    _write_gt(ground_truth)
    events: list[str] = []
    original_open = prediction_module._open_ground_truth

    def record_open(path: Path) -> td.graph.BaseGraph:
        events.append("gt")
        return original_open(path)

    monkeypatch.setattr("biohub.detector_fixed_race.prediction._open_ground_truth", record_open)
    result = evaluate_prediction(
        prediction,
        ground_truth,
        {"scale": (1.625, 0.40625, 0.40625), "max_distance": 7.0},
    )
    assert events == ["gt"]
    assert result["final_score"] == 1.0
    assert result["prediction_manifest_validated_before_gt"] is True


def test_invalid_prediction_manifest_rejects_metric_before_ground_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction = write_prediction(_cache(), _result(), _predictor(), tmp_path / "prediction.geff")
    ground_truth = tmp_path / "ground_truth.geff"
    _write_gt(ground_truth)
    manifest_path = tmp_path / "prediction_manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["directory_sha256"] = "invalid"
    manifest_path.write_text(json.dumps(payload))
    events: list[str] = []
    monkeypatch.setattr("biohub.detector_fixed_race.prediction._open_ground_truth", lambda path: events.append("gt"))
    with pytest.raises(ValueError, match="prediction manifest"):
        evaluate_prediction(
            prediction,
            ground_truth,
            {"scale": (1.625, 0.40625, 0.40625)},
        )
    assert events == []
