"""Headless, receipt-backed sanity check for the existing Biohub viewer path."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from biohub.official_metrics.metrics import _evaluate_matched_graph, evaluate
from biohub.visualizer import app
from biohub.visualizer.app import ViewerState, create_server
from biohub.visualizer.core import classify_edge_records

DEFAULT_SCALE = (1.625, 0.40625, 0.40625)
DEFAULT_MAX_DISTANCE = 7.0
EXPECTED_SAMPLE_STEM = "44b6_0113de3b"
WINDOW_Z_RADIUS = 0.75

EXPECTED_VISUAL_SUMMARY: dict[str, Any] = {
    "sample_stem": EXPECTED_SAMPLE_STEM,
    "image_shape": [100, 64, 256, 256],
    "scale": list(DEFAULT_SCALE),
    "max_distance_um": DEFAULT_MAX_DISTANCE,
    "prediction_node_count": 26301,
    "prediction_edge_count": 24205,
    "ground_truth_node_count": 52,
    "ground_truth_edge_count": 50,
    "overlay_totals": {"tp": 48, "fp": 2, "fn": 2, "prediction": 24155},
    "raw_slice_endpoint": {
        "path": "/api/frame",
        "bytes": 39490,
        "sha256": "69b6c5d2c322f092c8538f94c3aa2fffc672a1425857c9677597dcbb1a5b84e4",
    },
    "windows": {
        "matched": {
            "category": "tp",
            "t": 0,
            "z": 62.0,
            "z_radius": WINDOW_Z_RADIUS,
            "source": {"node_id": 219, "t": 0, "z": 62.0, "y": 224.0, "x": 248.0},
            "target": {"node_id": 441, "t": 1, "z": 62.0, "y": 228.0, "x": 248.0},
            "visible_node_kinds": {"prediction": 3},
            "visible_edge_categories": {"prediction": 2, "tp": 1},
        },
        "error": {
            "category": "fp",
            "t": 47,
            "z": 31.0,
            "z_radius": WINDOW_Z_RADIUS,
            "source": {"node_id": 11624, "t": 47, "z": 31.0, "y": 108.0, "x": 120.0},
            "target": {"node_id": 11886, "t": 48, "z": 28.0, "y": 108.0, "x": 116.0},
            "visible_node_kinds": {"ground_truth": 1, "prediction": 6},
            "visible_edge_categories": {"fn": 1, "fp": 1, "prediction": 3},
        },
        "sparse_unmatched": {
            "category": "prediction",
            "t": 0,
            "z": 1.0,
            "z_radius": WINDOW_Z_RADIUS,
            "source": {"node_id": 0, "t": 0, "z": 1.0, "y": 8.0, "x": 52.0},
            "target": {"node_id": 225, "t": 1, "z": 1.0, "y": 16.0, "x": 52.0},
            "visible_node_kinds": {"prediction": 12},
            "visible_edge_categories": {"prediction": 12},
        },
    },
}


def _assert_expected(path: str, actual: Any, expected: Any) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            raise ValueError(f"{path} must be a mapping")
        for key, value in expected.items():
            if key not in actual:
                raise ValueError(f"{path}.{key} is missing")
            _assert_expected(f"{path}.{key}", actual[key], value)
        return
    if actual != expected:
        raise ValueError(f"{path} mismatch: expected {expected!r}, got {actual!r}")


def validate_visual_summary(summary: Mapping[str, Any]) -> None:
    """Fail loudly unless the fixed sample's expected windows and totals match."""

    _assert_expected("summary", summary, EXPECTED_VISUAL_SUMMARY)
    for name, window in summary["windows"].items():
        categories = window.get("visible_edge_categories")
        nodes = window.get("visible_node_kinds")
        if not isinstance(categories, Mapping) or not isinstance(nodes, Mapping):
            raise ValueError(f"windows.{name} lacks overlay category counts")
    raw_slice = summary.get("raw_slice_endpoint")
    if not isinstance(raw_slice, Mapping):
        raise ValueError("raw_slice_endpoint is missing")
    if raw_slice.get("path") != "/api/frame":
        raise ValueError("raw_slice_endpoint.path must be /api/frame")
    if int(raw_slice.get("bytes", 0)) <= 0 or not raw_slice.get("sha256"):
        raise ValueError("raw_slice_endpoint must contain non-empty PNG evidence")


def _rows(frame: Any) -> list[dict[str, Any]]:
    return list(frame.iter_rows(named=True))


def _http_json(base_url: str, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
    query = urlencode({key: str(value) for key, value in params.items()})
    with urlopen(f"{base_url}{path}?{query}", timeout=30) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError(f"{path} returned a non-object response")
    return value


def _http_bytes(base_url: str, path: str, params: Mapping[str, Any]) -> bytes:
    query = urlencode({key: str(value) for key, value in params.items()})
    with urlopen(f"{base_url}{path}?{query}", timeout=30) as response:
        return response.read()


def _node_record(node: Any) -> dict[str, int | float]:
    return {
        "node_id": node.node_id,
        "t": node.t,
        "z": node.z,
        "y": node.y,
        "x": node.x,
    }


def _window(
    state: ViewerState,
    edge: Any,
    node_map: Mapping[int, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    source = node_map[edge.source_id]
    target = node_map[edge.target_id]
    overlay_edges = overlay["edges"]
    selected_edge = next(
        (
            item
            for item in overlay_edges
            if item["source_id"] == edge.source_id and item["target_id"] == edge.target_id
        ),
        None,
    )
    if selected_edge is None:
        raise ValueError(f"selected {edge.category} edge is absent from its overlay")
    direct_overlay = state.overlay(t=source.t, z=source.z, z_radius=WINDOW_Z_RADIUS)
    if direct_overlay != overlay:
        raise ValueError(f"HTTP overlay differs from direct select_overlay for {edge.category}")
    return {
        "category": edge.category,
        "t": source.t,
        "z": source.z,
        "z_radius": WINDOW_Z_RADIUS,
        "source": _node_record(source),
        "target": _node_record(target),
        "edge": selected_edge,
        "visible_node_kinds": dict(sorted(Counter(node["kind"] for node in overlay["nodes"]).items())),
        "visible_edge_categories": dict(
            sorted(Counter(item["category"] for item in overlay_edges).items())
        ),
        "frame_png_bytes": len(state.frame_png(t=source.t, z=int(source.z))),
    }


def _build_state(
    image_path: Path,
    prediction_path: Path,
    ground_truth_path: Path,
    scale: tuple[float, float, float],
    max_distance: float,
) -> tuple[ViewerState, Any, Any, dict[str, Any]]:
    image, image_root = app._open_zarr_image(image_path, "0")
    prediction = app._load_geff(prediction_path)
    ground_truth = app._load_geff(ground_truth_path)
    result = evaluate(prediction, ground_truth, scale=scale, max_distance=max_distance)
    official_frame = _evaluate_matched_graph(prediction, ground_truth)
    official_rows = [
        {
            "source_id": int(row["source_id"]),
            "target_id": int(row["target_id"]),
            "matched_edge_mask": bool(row["matched_edge_mask"]),
            "pred_valid": bool(row["pred_valid"]),
        }
        for row in _rows(official_frame)
    ]
    prediction_to_ground_truth = {
        int(row["node_id"]): int(row["match_node_id"])
        for row in _rows(prediction.node_attrs(attr_keys=["node_id", "match_node_id"]))
        if row["match_node_id"] is not None and int(row["match_node_id"]) >= 0
    }
    edge_records = classify_edge_records(
        prediction_edges=app._graph_edges(prediction),
        official_rows=official_rows,
        prediction_to_ground_truth=prediction_to_ground_truth,
        ground_truth_edges=app._graph_edges(ground_truth),
    )
    contrast_low, contrast_high = app._contrast_from_attrs(
        image_root,
        getattr(image, "_source", image),
    )
    state = ViewerState(
        image=image,
        dataset=image_path.name,
        nodes=app._graph_nodes(prediction, "prediction") + app._graph_nodes(ground_truth, "ground_truth"),
        edges=edge_records,
        metrics={
            "edge_tp": int(result.edge_tp),
            "edge_fp": int(result.edge_fp),
            "edge_fn": int(result.edge_fn),
            "division_tp": int(result.division_tp),
            "division_fp": int(result.division_fp),
            "division_fn": int(result.division_fn),
            "num_pred_nodes": int(result.num_pred_nodes),
        },
        contrast_low=contrast_low,
        contrast_high=contrast_high,
    )
    return state, prediction, ground_truth, {
        "edge_tp": int(result.edge_tp),
        "edge_fp": int(result.edge_fp),
        "edge_fn": int(result.edge_fn),
        "division_tp": int(result.division_tp),
        "division_fp": int(result.division_fp),
        "division_fn": int(result.division_fn),
    }


def run_visual_sanity(
    image_path: Path,
    prediction_path: Path,
    ground_truth_path: Path,
    *,
    scale: Sequence[float] = DEFAULT_SCALE,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> dict[str, Any]:
    """Load the fixed sample through the existing viewer path and validate it."""

    image_path = Path(image_path)
    prediction_path = Path(prediction_path)
    ground_truth_path = Path(ground_truth_path)
    resolved_scale = tuple(float(value) for value in scale)
    if resolved_scale != DEFAULT_SCALE:
        raise ValueError(f"scale is fixed to {DEFAULT_SCALE!r}, got {resolved_scale!r}")
    if float(max_distance) != DEFAULT_MAX_DISTANCE:
        raise ValueError(f"max_distance is fixed to {DEFAULT_MAX_DISTANCE}, got {max_distance}")
    if image_path.stem != EXPECTED_SAMPLE_STEM:
        raise ValueError(f"unexpected image sample stem: {image_path.stem!r}")
    for path in (image_path, prediction_path, ground_truth_path):
        if not path.exists():
            raise FileNotFoundError(f"visual sanity input does not exist: {path}")

    state, prediction, ground_truth, metrics = _build_state(
        image_path,
        prediction_path,
        ground_truth_path,
        resolved_scale,
        float(max_distance),
    )
    prediction_nodes = {node.node_id: node for node in state.nodes if node.kind == "prediction"}
    edge_by_category = {
        category: next((edge for edge in state.edges if edge.category == category), None)
        for category in ("tp", "fp", "prediction")
    }
    if any(edge is None for edge in edge_by_category.values()):
        raise ValueError("required TP, FP, and sparse-unmatched prediction edges are absent")

    server = create_server(state, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        raw_frame = _http_bytes(base_url, "/api/frame", {"t": 0, "z": 0})
        overlays = {
            "matched": _http_json(
                base_url,
                "/api/overlay",
                {"t": 0, "z": 62.0, "z_radius": WINDOW_Z_RADIUS},
            ),
            "error": _http_json(
                base_url,
                "/api/overlay",
                {"t": 47, "z": 31.0, "z_radius": WINDOW_Z_RADIUS},
            ),
            "sparse_unmatched": _http_json(
                base_url,
                "/api/overlay",
                {"t": 0, "z": 1.0, "z_radius": WINDOW_Z_RADIUS},
            ),
        }
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    if thread.is_alive():
        raise RuntimeError("viewer sanity HTTP server did not stop")

    windows = {
        "matched": _window(state, edge_by_category["tp"], prediction_nodes, overlays["matched"]),
        "error": _window(state, edge_by_category["fp"], prediction_nodes, overlays["error"]),
        "sparse_unmatched": _window(
            state,
            edge_by_category["prediction"],
            prediction_nodes,
            overlays["sparse_unmatched"],
        ),
    }
    summary: dict[str, Any] = {
        "schema_version": 1,
        "sample_stem": image_path.stem,
        "image_path": str(image_path),
        "prediction_path": str(prediction_path),
        "ground_truth_path": str(ground_truth_path),
        "image_shape": list(state.shape),
        "scale": list(resolved_scale),
        "max_distance_um": float(max_distance),
        "prediction_node_count": int(prediction.num_nodes()),
        "prediction_edge_count": int(prediction.num_edges()),
        "ground_truth_node_count": int(ground_truth.num_nodes()),
        "ground_truth_edge_count": int(ground_truth.num_edges()),
        "metrics": metrics,
        "overlay_totals": dict(
            sorted(Counter(edge.category for edge in state.edges).items())
        ),
        "raw_slice_endpoint": {
            "path": "/api/frame",
            "query": {"t": 0, "z": 0},
            "bytes": len(raw_frame),
            "sha256": hashlib.sha256(raw_frame).hexdigest(),
        },
        "windows": windows,
    }
    validate_visual_summary(summary)
    return summary


def render_visual_summary(summary: Mapping[str, Any]) -> str:
    """Render a concise, stable text receipt for the machine-readable summary."""

    lines = [
        f"sample_stem={summary['sample_stem']}",
        f"image_shape={tuple(summary['image_shape'])}",
        f"scale={tuple(summary['scale'])}",
        f"max_distance_um={summary['max_distance_um']}",
        f"prediction_nodes={summary['prediction_node_count']}",
        f"prediction_edges={summary['prediction_edge_count']}",
        f"ground_truth_nodes={summary['ground_truth_node_count']}",
        f"ground_truth_edges={summary['ground_truth_edge_count']}",
        f"overlay_totals={json.dumps(summary['overlay_totals'], sort_keys=True)}",
        f"raw_slice_endpoint={summary['raw_slice_endpoint']['path']}",
        f"raw_slice_bytes={summary['raw_slice_endpoint']['bytes']}",
        f"raw_slice_sha256={summary['raw_slice_endpoint']['sha256']}",
    ]
    for name in ("matched", "error", "sparse_unmatched"):
        window = summary["windows"][name]
        lines.extend(
            [
                f"window.{name}.category={window['category']}",
                f"window.{name}.source={json.dumps(window['source'], sort_keys=True)}",
                f"window.{name}.target={json.dumps(window['target'], sort_keys=True)}",
                f"window.{name}.visible_node_kinds={json.dumps(window['visible_node_kinds'], sort_keys=True)}",
                "window."
                f"{name}.visible_edge_categories={json.dumps(window['visible_edge_categories'], sort_keys=True)}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_visual_outputs(summary: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Persist the JSON and text receipts under the ignored artifact directory."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "visual_sanity.json"
    text_path = output_dir / "visual_sanity.txt"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    text_path.write_text(render_visual_summary(summary))
    return json_path, text_path


__all__ = [
    "DEFAULT_MAX_DISTANCE",
    "DEFAULT_SCALE",
    "EXPECTED_SAMPLE_STEM",
    "EXPECTED_VISUAL_SUMMARY",
    "render_visual_summary",
    "run_visual_sanity",
    "validate_visual_summary",
    "write_visual_outputs",
]
