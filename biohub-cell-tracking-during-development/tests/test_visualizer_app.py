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


def test_evaluate_graphs_never_hardcodes_the_matched_node_id_string() -> None:
    """Regression guard for a real, previously-shipped bug.

    The last-committed `feat/biohub-bootstrap` version of ``_evaluate_graphs`` called
    ``prediction.node_attrs(attr_keys=["node_id", "matched_node_id"])`` with a hardcoded
    key name. tracksdata's actual attribute key
    (``td.DEFAULT_ATTR_KEYS.MATCHED_NODE_ID``) is ``"match_node_id"`` (no "ed"), so that
    line raised, for every real prediction/ground-truth pair:

        KeyError: "node attribute key(s) ['matched_node_id'] not found. Available node
        attribute keys: ['match_node_id', 'match_score', 'node_id', 't', 'x', 'y', 'z']"

    Reproduced against real CODEX prediction GEFFs by temporarily restoring the old
    file content; see docs/results/claude_lane_c_error_analysis.md and
    tests/test_visualizer_integration.py::test_build_state_scores_prediction_against_ground_truth
    for the behavioral (tracksdata-backed) regression coverage. This test pins the
    narrower, dependency-light source-level contract: the lookup key must always be
    resolved through ``td.DEFAULT_ATTR_KEYS``, never a literal string, so a future
    rename in tracksdata cannot silently reintroduce the same crash.
    """
    import inspect

    from biohub.visualizer import app

    source = inspect.getsource(app._evaluate_graphs)
    assert '"matched_node_id"' not in source
    assert "'matched_node_id'" not in source
    assert "DEFAULT_ATTR_KEYS.MATCHED_NODE_ID" in source
    assert "DEFAULT_ATTR_KEYS.NODE_ID" in source
