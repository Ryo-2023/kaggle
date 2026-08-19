from __future__ import annotations

import struct
import zlib

import numpy as np

from biohub.visualizer.core import (
    EdgeRecord,
    NodeRecord,
    encode_grayscale_png,
    normalize_to_uint8,
    select_overlay,
)


def _decode_grayscale_png(payload: bytes) -> tuple[int, int, bytes]:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = 0
    compressed = bytearray()
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        data = payload[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", data)
            assert bit_depth == 8
            assert color_type == 0
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
    return width, height, zlib.decompress(bytes(compressed))


def test_normalize_to_uint8_uses_explicit_limits_and_clips() -> None:
    image = np.array([[-1.0, 0.0, 1.0], [2.0, 3.0, 4.0]], dtype=np.float32)

    normalized, low, high = normalize_to_uint8(image, low=0.0, high=2.0)

    assert low == 0.0
    assert high == 2.0
    assert normalized.dtype == np.uint8
    assert normalized.tolist() == [[0, 0, 128], [255, 255, 255]]


def test_normalize_to_uint8_handles_flat_slice() -> None:
    image = np.full((2, 3), 7.0, dtype=np.float32)

    normalized, low, high = normalize_to_uint8(image)

    assert low == 7.0
    assert high > low
    assert normalized.tolist() == [[0, 0, 0], [0, 0, 0]]


def test_encode_grayscale_png_writes_valid_scanlines() -> None:
    image = np.array([[0, 127], [128, 255]], dtype=np.uint8)

    payload = encode_grayscale_png(image)

    width, height, scanlines = _decode_grayscale_png(payload)
    assert (width, height) == (2, 2)
    assert scanlines == b"\x00\x00\x7f\x00\x80\xff"


def test_select_overlay_filters_current_slice_and_keeps_outgoing_motion_vectors() -> None:
    nodes = [
        NodeRecord(node_id=1, t=2, z=4.2, y=10.0, x=20.0, kind="prediction"),
        NodeRecord(node_id=2, t=3, z=4.8, y=12.0, x=25.0, kind="prediction"),
        NodeRecord(node_id=3, t=2, z=8.0, y=30.0, x=40.0, kind="prediction"),
        NodeRecord(node_id=10, t=2, z=4.0, y=9.0, x=19.0, kind="ground_truth"),
    ]
    edges = [
        EdgeRecord(source_id=1, target_id=2, category="tp"),
        EdgeRecord(source_id=3, target_id=2, category="fp"),
    ]

    payload = select_overlay(nodes, edges, t=2, z=4.0, z_radius=0.5)

    assert [node["node_id"] for node in payload["nodes"]] == [1, 10]
    assert payload["edges"] == [
        {
            "source_id": 1,
            "target_id": 2,
            "category": "tp",
            "x1": 20.0,
            "y1": 10.0,
            "z1": 4.2,
            "x2": 25.0,
            "y2": 12.0,
            "z2": 4.8,
        }
    ]


def test_select_overlay_uses_ground_truth_coordinates_for_fn_edges() -> None:
    nodes = [
        NodeRecord(node_id=7, t=0, z=1.0, y=4.0, x=5.0, kind="ground_truth"),
        NodeRecord(node_id=8, t=1, z=1.0, y=6.0, x=7.0, kind="ground_truth"),
    ]
    edges = [EdgeRecord(source_id=7, target_id=8, category="fn")]

    payload = select_overlay(nodes, edges, t=0, z=1.0, z_radius=0.1)

    assert payload["edges"][0]["x1"] == 5.0
    assert payload["edges"][0]["x2"] == 7.0


def test_classify_edge_records_matches_official_tp_fp_fn_sets() -> None:
    from biohub.visualizer.core import classify_edge_records

    records = classify_edge_records(
        prediction_edges=[(1, 2), (2, 3), (3, 4)],
        official_rows=[
            {"source_id": 1, "target_id": 2, "matched_edge_mask": True, "pred_valid": True},
            {"source_id": 2, "target_id": 3, "matched_edge_mask": False, "pred_valid": True},
        ],
        prediction_to_ground_truth={1: 10, 2: 11, 3: 12},
        ground_truth_edges=[(10, 11), (11, 12), (12, 13)],
    )

    assert [(edge.source_id, edge.target_id, edge.category) for edge in records] == [
        (1, 2, "tp"),
        (2, 3, "fp"),
        (3, 4, "prediction"),
        (11, 12, "fn"),
        (12, 13, "fn"),
    ]
