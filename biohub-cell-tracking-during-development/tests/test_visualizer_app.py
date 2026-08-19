from __future__ import annotations

import json
from threading import Thread
from urllib.request import urlopen

import numpy as np

from biohub.visualizer.app import ViewerState, create_server, ensure_tzyx
from biohub.visualizer.core import EdgeRecord, NodeRecord


def test_ensure_tzyx_accepts_four_dimensions_and_singleton_channel() -> None:
    four_d = np.zeros((2, 3, 4, 5), dtype=np.float32)
    five_d = np.zeros((2, 1, 3, 4, 5), dtype=np.float32)

    assert ensure_tzyx(four_d).shape == (2, 3, 4, 5)
    wrapped = ensure_tzyx(five_d)
    assert wrapped.shape == (2, 3, 4, 5)
    assert wrapped[1, 2].shape == (4, 5)


def test_ensure_tzyx_rejects_ambiguous_shape() -> None:
    image = np.zeros((2, 2, 3, 4, 5), dtype=np.float32)

    try:
        ensure_tzyx(image)
    except ValueError as error:
        assert "singleton channel" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_server_exposes_meta_frame_and_overlay_endpoints() -> None:
    image = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
    state = ViewerState(
        image=ensure_tzyx(image),
        dataset="tiny.zarr",
        nodes=[
            NodeRecord(node_id=1, t=0, z=1.0, y=2.0, x=3.0, kind="prediction"),
            NodeRecord(node_id=2, t=1, z=1.0, y=2.5, x=3.5, kind="prediction"),
        ],
        edges=[EdgeRecord(source_id=1, target_id=2, category="tp")],
        metrics={"edge_tp": 1, "edge_fp": 0, "edge_fn": 0},
    )
    server = create_server(state, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        with urlopen(f"http://{host}:{port}/api/meta") as response:
            meta = json.load(response)
        assert meta["dataset"] == "tiny.zarr"
        assert meta["shape"] == [2, 3, 4, 5]
        assert meta["metrics"]["edge_tp"] == 1

        with urlopen(f"http://{host}:{port}/api/frame?t=0&z=1") as response:
            assert response.headers.get_content_type() == "image/png"
            assert response.read().startswith(b"\x89PNG\r\n\x1a\n")

        with urlopen(f"http://{host}:{port}/api/overlay?t=0&z=1&z_radius=0.25") as response:
            overlay = json.load(response)
        assert [node["node_id"] for node in overlay["nodes"]] == [1]
        assert overlay["edges"][0]["category"] == "tp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
