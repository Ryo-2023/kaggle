"""End-to-end checks for the viewer's real Zarr + geff loading path.

The unit tests build :class:`ViewerState` from NumPy arrays and hand-written
records, so they cannot catch attribute-name drift against tracksdata or Zarr.
These tests drive :func:`build_state` on a real (tiny) OME-Zarr image and real
``.geff`` graphs instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import numpy as np
import pytest

from biohub.visualizer.app import ViewerState, build_state, create_server

pytest.importorskip("zarr")
pytest.importorskip("tracksdata")

T, Z, Y, X = 4, 5, 24, 32

# Two cells drifting along x; cell 0 sits on z=2, cell 1 on z=3.
def _positions(t: int) -> list[tuple[float, float, float]]:
    return [(2.0, 10.0, 5.0 + 3 * t), (3.0, 16.0, 24.0 - 2 * t)]


def _write_image(path: Path) -> None:
    import zarr

    rng = np.random.default_rng(0)
    image = rng.normal(100.0, 5.0, size=(T, Z, Y, X)).astype(np.float32)
    for t in range(T):
        for z, y, x in _positions(t):
            image[t, int(z), int(y) - 1 : int(y) + 2, int(x) - 1 : int(x) + 2] += 800.0

    root = zarr.open_group(str(path), mode="w")
    array = root.create_array("0", shape=image.shape, dtype="float32", chunks=(1, 1, Y, X))
    array[:] = image
    root.attrs["image_statistics"] = {"quantiles": {"0.001": 80.0, "0.999": 900.0}}


def _write_geff(path: Path, *, jitter: float, drop_edge: tuple[int, int] | None) -> None:
    import polars as pl
    import tracksdata as td

    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, pl.Float64, 0.0)

    ids: dict[tuple[int, int], int] = {}
    for t in range(T):
        for cell, (z, y, x) in enumerate(_positions(t)):
            ids[(t, cell)] = graph.add_node({"t": t, "z": z, "y": y + jitter, "x": x + jitter})
    for t in range(T - 1):
        for cell in range(2):
            if drop_edge == (t, cell):
                continue
            graph.add_edge(ids[(t, cell)], ids[(t + 1, cell)], {})

    graph.to_geff(str(path), overwrite=True)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("biohub-viewer")
    image = root / "image.zarr"
    prediction = root / "pred.geff"
    ground_truth = root / "gt.geff"
    _write_image(image)
    _write_geff(ground_truth, jitter=0.0, drop_edge=None)
    # The prediction is slightly offset and misses the (t=2, cell=1) link.
    _write_geff(prediction, jitter=0.3, drop_edge=(2, 1))
    return {"image": image, "prediction": prediction, "ground_truth": ground_truth}


def _build(dataset: dict[str, Path], **overrides: object) -> ViewerState:
    kwargs: dict[str, object] = {
        "image_path": dataset["image"],
        "prediction_path": None,
        "ground_truth_path": None,
        "array_key": "0",
        "scale": (1.0, 1.0, 1.0),
        "max_distance": 7.0,
        "contrast_low": None,
        "contrast_high": None,
    }
    kwargs.update(overrides)
    return build_state(**kwargs)  # type: ignore[arg-type]


def test_build_state_reads_zarr_shape_and_quantile_contrast(dataset: dict[str, Path]) -> None:
    state = _build(dataset)

    assert state.shape == (T, Z, Y, X)
    assert (state.contrast_low, state.contrast_high) == (80.0, 900.0)
    assert state.nodes == [] and state.edges == []


def test_build_state_scores_prediction_against_ground_truth(dataset: dict[str, Path]) -> None:
    """Regression guard for tracksdata attribute names used by the matcher."""

    state = _build(
        dataset,
        prediction_path=dataset["prediction"],
        ground_truth_path=dataset["ground_truth"],
    )

    # 6 ground-truth links, one of which the prediction never proposes.
    assert state.metrics["edge_tp"] == 5
    assert state.metrics["edge_fp"] == 0
    assert state.metrics["edge_fn"] == 1
    assert state.metrics["num_pred_nodes"] == 2 * T
    assert state.metrics["edge_jaccard"] == pytest.approx(5 / 6)

    categories = sorted(edge.category for edge in state.edges)
    assert categories == ["fn", "tp", "tp", "tp", "tp", "tp"]

    # The missed link must be reported as FN on the ground-truth nodes.
    overlay = state.overlay(t=2, z=3.0, z_radius=1.0)
    assert [edge["category"] for edge in overlay["edges"] if edge["category"] == "fn"] == ["fn"]


def test_build_state_without_ground_truth_keeps_edges_unscored(dataset: dict[str, Path]) -> None:
    state = _build(dataset, prediction_path=dataset["prediction"])

    assert state.metrics == {"num_pred_nodes": 2 * T}
    assert {edge.category for edge in state.edges} == {"prediction"}


def test_http_endpoints_serve_real_dataset(dataset: dict[str, Path]) -> None:
    state = _build(
        dataset,
        prediction_path=dataset["prediction"],
        ground_truth_path=dataset["ground_truth"],
    )
    server = create_server(state, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    try:
        with urlopen(f"{base}/api/meta") as response:
            meta = json.load(response)
        assert meta["shape"] == [T, Z, Y, X]
        assert meta["contrast"] == {"low": 80.0, "high": 900.0}

        with urlopen(f"{base}/api/frame?t=1&z=2") as response:
            assert response.read().startswith(b"\x89PNG\r\n\x1a\n")

        with urlopen(f"{base}/api/overlay?t=0&z=2&z_radius=1") as response:
            overlay = json.load(response)
        assert overlay["nodes"], "expected nodes on the first timepoint"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_out_of_range_z_reports_the_actual_bounds() -> None:
    state = ViewerState(image=np.zeros((2, 3, 4, 5), dtype=np.float32), dataset="tiny.zarr")

    with pytest.raises(ValueError, match=r"z must be in \[0, 2\], got 9"):
        state.frame_png(t=0, z=9)
