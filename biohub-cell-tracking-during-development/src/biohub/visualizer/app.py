"""Local browser application for inspecting Biohub image inputs and graph outputs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser

import numpy as np

from .core import (
    EdgeRecord,
    NodeRecord,
    classify_edge_records,
    encode_grayscale_png,
    normalize_to_uint8,
    select_overlay,
)
from .html import VIEWER_HTML


class _SingletonChannelTZYX:
    """Expose a ``(T, 1, Z, Y, X)`` source as lazy ``(T, Z, Y, X)``."""

    def __init__(self, source: Any) -> None:
        self._source = source
        self.shape = (source.shape[0], source.shape[2], source.shape[3], source.shape[4])

    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, tuple):
            return self._source[key, 0]
        if len(key) == 2:
            t, z = key
            return self._source[t, 0, z]
        if len(key) == 4:
            t, z, y, x = key
            return self._source[t, 0, z, y, x]
        raise IndexError(f"Expected 1, 2, or 4 indices, got {len(key)}")


def ensure_tzyx(image: Any) -> Any:
    """Validate a lazy image array and normalize it to ``(T, Z, Y, X)``."""

    shape = tuple(int(value) for value in getattr(image, "shape", ()))
    if len(shape) == 4:
        return image
    if len(shape) == 5:
        if shape[1] != 1:
            raise ValueError(
                f"Expected a singleton channel axis for a 5-D image, got shape {shape!r}"
            )
        return _SingletonChannelTZYX(image)
    raise ValueError(f"Expected image shape (T,Z,Y,X) or (T,1,Z,Y,X), got {shape!r}")


@dataclass(slots=True)
class ViewerState:
    """Data made available by the local HTTP viewer."""

    image: Any
    dataset: str
    nodes: list[NodeRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    metrics: dict[str, int | float | None] = field(default_factory=dict)
    contrast_low: float | None = None
    contrast_high: float | None = None

    def __post_init__(self) -> None:
        self.image = ensure_tzyx(self.image)

    @property
    def shape(self) -> tuple[int, int, int, int]:
        return tuple(int(value) for value in self.image.shape)

    def _validated_indices(self, t: int, z: int) -> tuple[int, int]:
        t_count, z_count, _, _ = self.shape
        if not 0 <= t < t_count:
            raise ValueError(f"t must be in [0, {t_count - 1}], got {t}")
        if not 0 <= z < z_count:
            raise ValueError("z must be in [0, {z_count - 1}], got {z}")
        return t, z

    def frame_png(self, *, t: int, z: int) -> bytes:
        t, z = self._validated_indices(t, z)
        slice_2d = np.asarray(self.image[t, z])
        normalized, _, _ = normalize_to_uint8(
            slice_2d,
            low=self.contrast_low,
            high=self.contrast_high,
        )
        return encode_grayscale_png(normalized)

    def overlay(self, *, t: int, z: float, z_radius: float) -> dict[str, list[dict[str, Any]]]:
        self._validated_indices(t, int(z))
        return select_overlay(self.nodes, self.edges, t=t, z=z, z_radius=z_radius)

    def meta(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "shape": list(self.shape),
            "metrics": self.metrics,
            "contrast": {"low": self.contrast_low, "high": self.contrast_high},
        }


class ViewerServer(ThreadingHTTPServer):
    """Threading server carrying one immutable-ish :class:`ViewerState`."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ViewerState) -> None:
        self.state = state
        super().__init__(address, ViewerRequestHandler)


class ViewerRequestHandler(BaseHTTPRequestHandler):
    server: ViewerServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep inference logs readable; endpoint failures still return details.
        return

    def _send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status)

    @staticmethod
    def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
        return int(query.get(name, [str(default)])[0])

    @staticmethod
    def _query_float(query: dict[str, list[str]], name: str, default: float) -> float:
        return float(query.get(name, [str(default)])[0])

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._send_bytes(VIEWER_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/meta":
                self._send_json(self.server.state.meta())
                return
            if parsed.path == "/api/frame":
                t = self._query_int(query, "t", 0)
                z = self._query_int(query, "z", 0)
                self._send_bytes(self.server.state.frame_png(t=t, z=z), "image/png")
                return
            if parsed.path == "/api/overlay":
                t = self._query_int(query, "t", 0)
                z = self._query_float(query, "z", 0.0)
                z_radius = self._query_float(query, "z_radius", 1.0)
                self._send_json(self.server.state.overlay(t=t, z=z, z_radius=z_radius))
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, IndexError, TypeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # pragma: no cover - final HTTP safety net
            self._send_json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def create_server(state: ViewerState, *, host: str, port: int) -> ViewerServer:
    """Create, but do not start, a local viewer server."""

    return ViewerServer((host, port), state)


def _unwrap_geff_result(result: Any) -> Any:
    return result[0] if isinstance(result, tuple) else result


def _load_geff(path: Path) -> Any:
    import tracksdata as td

    return _unwrap_geff_result(td.graph.IndexedRXGraph.from_geff(path))


def _open_zarr_image(path: Path, array_key: str) -> tuple[Any, Any]:
    import zarr

    root = zarr.open(str(path), mode="r")
    if hasattr(root, "shape"):
        return ensure_tzyx(root), root
    if array_key not in root:
        available = ", ".join(str(key) for key in root.keys())
        raise KeyError(f"Array key {array_key!r} not found in {path}; available: {available}")
    array = root[array_key]
    return ensure_tzyx(array), root


def _rows(frame: Any) -> list[dict[str, Any]]:
    return list(frame.iter_rows(named=True))


def _graph_nodes(graph: Any, kind: str) -> list[NodeRecord]:
    frame = graph.node_attrs(attr_keys=["node_id", "t", "z", "y", "x"])
    return [
        NodeRecord(
            node_id=int(row["node_id"]),
            t=int(row["t"]),
            z=float(row["z"]),
            y=float(row["y"]),
            x=float(row["x"]),
            kind=kind,  # type: ignore[arg-type]
        )
        for row in _rows(frame)
    ]


def _graph_edges(graph: Any) -> list[tuple[int, int]]:
    frame = graph.edge_attrs(attr_keys=["source_id", "target_id"])
    return [(int(row["source_id"]), int(row["target_id"])) for row in _rows(frame)]


def _safe_jaccard(tp: int, fp: int, fn: int) -> float | None:
    denominator = tp + fp + fn
    return tp / denominator if denominator > 0 else None


def _evaluate_graphs(
    prediction: Any,
    ground_truth: Any,
    *,
    scale: tuple[float, float, float],
    max_distance: float,
) -> tuple[list[EdgeRecord], dict[str, int | float | None]]:
    # These files are vendored byte-for-byte from the official repository.
    from biohub.official_metrics.metrics import _evaluate_matched_graph, evaluate

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
    matched_frame = prediction.node_attrs(attr_keys=["node_id", "matched_node_id"])
    pred_to_gt = {
        int(row["node_id"]): int(row["matched_node_id"])
        for row in _rows(matched_frame)
        if row["matched_node_id"] is not None and int(row["matched_node_id"]) >= 0
    }
    edge_records = classify_edge_records(
        prediction_edges=_graph_edges(prediction),
        official_rows=official_rows,
        prediction_to_ground_truth=pred_to_gt,
        ground_truth_edges=_graph_edges(ground_truth),
    )
    metrics: dict[str, int | float | None] = {
        "edge_tp": int(result.edge_tp),
        "edge_fp": int(result.edge_fp),
        "edge_fn": int(result.edge_fn),
        "division_tp": int(result.division_tp),
        "division_fp": int(result.division_fp),
        "division_fn": int(result.division_fn),
        "num_pred_nodes": int(result.num_pred_nodes),
        "edge_jaccard": _safe_jaccard(result.edge_tp, result.edge_fp, result.edge_fn),
        "division_jaccard": _safe_jaccard(result.division_tp, result.division_fp, result.division_fn),
    }
    return edge_records, metrics


def _unscored_prediction_edges(graph: Any) -> list[EdgeRecord]:
    return [EdgeRecord(source_id=source, target_id=target, category="prediction") for source, target in _graph_edges(graph)]


def _find_nested(mapping: Any, *path: str) -> Any:
    value = mapping
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _contrast_from_attrs(*objects: Any) -> tuple[float | None, float | None]:
    """Read common Biohub quantile metadata without assuming one Zarr layout."""

    for obj in objects:
        attrs = getattr(obj, "attrs", None)
        if attrs is None:
            continue
        try:
            attrs_dict = dict(attrs)
        except Exception:
            continue
        quantiles = _find_nested(attrs_dict, "image_statistics", "quantiles")
        if not isinstance(quantiles, dict):
            quantiles = attrs_dict.get("quantiles")
        if not isinstance(quantiles, dict):
            continue
        low = quantiles.get("0.001", quantiles.get("0.01"))
        high = quantiles.get("0.999", quantiles.get("0.99"))
        if low is not None and high is not None and float(high) > float(low):
            return float(low), float(high)
    return None, None


def build_state(
    *,
    image_path: Path,
    prediction_path: Path | None,
    ground_truth_path: Path | None,
    array_key: str,
    scale: tuple[float, float, float],
    max_distance: float,
    contrast_low: float | None,
    contrast_high: float | None,
) -> ViewerState:
    image, root = _open_zarr_image(image_path, array_key)
    source_array = getattr(image, "_source", image)
    inferred_low, inferred_high = _contrast_from_attrs(root, source_array)
    resolved_low = contrast_low if contrast_low is not None else inferred_low
    resolved_high = contrast_high if contrast_high is not None else inferred_high

    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    metrics: dict[str, int | float | None] = {}

    prediction = _load_geff(prediction_path) if prediction_path is not None else None
    ground_truth = _load_geff(ground_truth_path) if ground_truth_path is not None else None

    if prediction is not None:
        nodes.extend(_graph_nodes(prediction, "prediction"))
    if ground_truth is not None:
        nodes.extend(_graph_nodes(ground_truth, "ground_truth"))

    if prediction is not None and ground_truth is not None:
        edges, metrics = _evaluate_graphs(
            prediction,
            ground_truth,
            scale=scale,
            max_distance=max_distance,
         )
    elif prediction is not None:
        edges = _unscored_prediction_edges(prediction)
        metrics = {"num_pred_nodes": int(prediction.num_nodes())}

    return ViewerState(
        image=image,
        dataset=image_path.name,
        nodes=nodes,
        edges=edges,
        metrics=metrics,
        contrast_low=resolved_low,
        contrast_high=resolved_high,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open a local browser viewer showing raw Biohub image slices next to "
            "predicted nodes/tracks and official TP/FP/FN overlays."
        )
    )
    parser.add_argument("--image", type=Path, required=True, help="OME-Zarr image path")
    parser.add_argument("--prediction", type=Path, default=None, help="Predicted .geff graph")
    parser.add_argument("--ground-truth", type=Path, default=None, help="Ground-truth .geff graph")
    parser.add_argument("--array-key", default="0", help="Zarr array key when --image is a group (default: 0)")
    parser.add_argument(
        "--scale",
        nargs=3,
        type=float,
        metavar=("Z_UM", "Y_UM", "X_UM"),
        default=(1.625, 0.40625, 0.40625),
        help="Physical voxel scale used by the official matcher",
    )
    parser.add_argument("--max-distance", type=float, default=7.0, help="Official node-matching radius in µm")
    parser.add_argument("--contrast-low", type=float, default=None)
    parser.add_argument("--contrast-high", type=float, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not try to open a browser tab")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for path in (args.image, args.prediction, args.ground_truth):
        if path is not None and not path.exists():
            print(f"Path does not exist: {path}", file=sys.stderr)
            return 2
    if (args.contrast_low is None) != (args.contrast_high is None):
        print("--contrast-low and --contrast-high must be supplied together", file=sys.stderr)
        return 2
    if args.contrast_low is not None and args.contrast_high <= args.contrast_low:
        print("--contrast-high must be greater than --contrast-low", file=sys.stderr)
        return 2

    state = build_state(
        image_path=args.image,
        prediction_path=args.prediction,
        ground_truth_path=args.ground_truth,
        array_key=args.array_key,
        scale=tuple(args.scale),
        max_distance=args.max_distance,
        contrast_low=args.contrast_low,
        contrast_high=args.contrast_high,
    )
    server = create_server(state, host=args.host, port=args.port)
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{browser_host}:{server.server_address[1]}/"
    print(f"Biohub Visual Inspector: {url}")
    print(f"Image shape: {state.shape}")
    if state.metrics:
        print(json.dumps(state.metrics, ensure_ascii=False, indent=2))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
