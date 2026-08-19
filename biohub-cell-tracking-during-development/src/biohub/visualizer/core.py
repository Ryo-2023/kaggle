"""Dependency-light helpers for the Biohub browser viewer.

This module deliberately depends only on NumPy and the Python standard
library, so its image conversion and overlay selection can be tested without a
GUI, Zarr, or tracksdata installation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import struct
from typing import Literal
import zlib

import numpy as np

NodeKind = Literal["prediction", "ground_truth"]
EdgeCategory = Literal["prediction", "tp", "fp", "fn"]


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """One graph node in image coordinates."""

    node_id: int
    t: int
    z: float
    y: float
    x: float
    kind: NodeKind


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    """One directed edge, classified with the official metric when possible."""

    source_id: int
    target_id: int
    category: EdgeCategory


def normalize_to_uint8(
    image: np.ndarray,
    *,
    low: float | None = None,
    high: float | None = None,
    lower_quantile: float = 0.005,
    upper_quantile: float = 0.995,
) -> tuple[np.ndarray, float, float]:
    """Contrast-normalize a two-dimensional image to ``uint8``.

    Explicit limits take precedence.  Otherwise finite-value quantiles are
    used.  Flat or non-finite slices are handled deterministically instead of
    emitting NaNs or divide-by-zero warnings.
    """

    values = np.asarray(image)
    if values.ndim != 2:
        raise ValueError(f"Expected a 2-D image slice, got shape {values.shape!r}")

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros(values.shape, dtype=np.uint8), 0.0, 1.0

    resolved_low = float(np.quantile(finite, lower_quantile)) if low is None else float(low)
    resolved_high = float(np.quantile(finite, upper_quantile)) if high is None else float(high)
    if not np.isfinite(resolved_low):
        resolved_low = float(finite.min())
    if not np.isfinite(resolved_high):
        resolved_high = float(finite.max())
    if resolved_high <= resolved_low:
        resolved_high = resolved_low + max(abs(resolved_low) * 1e-6, 1.0)

    safe = np.nan_to_num(
        values.astype(np.float64, copy=False),
        nan=resolved_low,
        posinf=resolved_high,
        neginf=resolved_low,
    )
    scaled = np.clip((safe - resolved_low) / (resolved_high - resolved_low), 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8), resolved_low, resolved_high


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def encode_grayscale_png(image: np.ndarray) -> bytes:
    """Encode a 2-D ``uint8`` array as an 8-bit grayscale PNG.

    Keeping this tiny encoder in-tree avoids adding Pillow or an image-server
    dependency solely for visual inspection.
    """

    pixels = np.asarray(image)
    if pixels.ndim != 2:
        raise ValueError(f"Expected a 2-D image, got shape {pixels.shape!r}")
    if pixels.dtype != np.uint8:
        raise TypeError(f"Expected uint8 pixels, got {pixels.dtype}")

    height, width = pixels.shape
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")

    # PNG filter type 0 for each row.
    scanlines = b"".join(b"\x00" + row.tobytes(order="C") for row in pixels)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=6))
        + _png_chunk(b"IEND", b"")
    )


def select_overlay(
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
    *,
    t: int,
    z: float,
    z_radius: float,
) -> dict[str, list[dict[str, int | float | str]]]:
    """Select points and outgoing motion vectors visible on one ``(t, z)`` slice.

    Nodes are shown only when they belong to the current timepoint and lie
    within ``z_radius`` of the selected plane.  Edges are drawn as motion
    vectors when their *source* is visible; their target may lie on the next
    timepoint or a neighboring z-plane, which is useful for spotting bad links.
    """

    if z_radius < 0:
        raise ValueError("z_radius must be non-negative")

    visible_nodes = [
        node
        for node in nodes
        if node.t == t and abs(node.z - z) <= z_radius
    ]
    nodes_by_kind = {
        kind: {node.node_id: node for node in nodes if node.kind == kind}
        for kind in ("prediction", "ground_truth")
    }

    visible_edges: list[dict[str, int | float | str]] = []
    for edge in edges:
        node_kind = "ground_truth" if edge.category == "fn" else "prediction"
        source = nodes_by_kind[node_kind].get(edge.source_id)
        target = nodes_by_kind[node_kind].get(edge.target_id)
        if source is None or target is None:
            continue
        if source.t != t or abs(source.z - z) > z_radius:
            continue
        visible_edges.append(
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "category": edge.category,
                "x1": source.x,
                "y1": source.y,
                "z1": source.z,
                "x2": target.x,
                "y2": target.y,
                "z2": target.z,
            }
        )

    return {
        "nodes": [asdict(node) for node in visible_nodes],
        "edges": visible_edges,
    }


def classify_edge_records(
    *,
    prediction_edges: list[tuple[int, int]],
    official_rows: list[dict[str, int | bool]],
    prediction_to_ground_truth: dict[int, int],
    ground_truth_edges: list[tuple[int, int]],
) -> list[EdgeRecord]:
    """Translate official metric rows into drawable TP/FP/FN edge records.

    ``official_rows`` should come from the vendored metric's
    ``_evaluate_matched_graph`` output.  Prediction edges omitted from that
    table remain visible as unscored context.  A ground-truth edge is FN unless
    it is covered by a TP prediction edge, matching the official visualizer's
    interpretation.
    """

    categories: dict[tuple[int, int], EdgeCategory] = {}
    for row in official_rows:
        pair = (int(row["source_id"]), int(row["target_id"]))
        if bool(row["matched_edge_mask"]):
            categories[pair] = "tp"
        elif bool(row["pred_valid"]):
            categories[pair] = "fp"

    result = [
        EdgeRecord(source_id=source, target_id=target, category=categories.get((source, target), "prediction"))
        for source, target in prediction_edges
    ]

    covered_gt_edges: set[tuple[int, int]] = set()
    for edge in result:
        if edge.category != "tp":
            continue
        gt_source = prediction_to_ground_truth.get(edge.source_id)
        gt_target = prediction_to_ground_truth.get(edge.target_id)
        if gt_source is not None and gt_target is not None and gt_source >= 0 and gt_target >= 0:
            covered_gt_edges.add((gt_source, gt_target))

    result.extend(
        EdgeRecord(source_id=source, target_id=target, category="fn")
        for source, target in ground_truth_edges
        if (source, target) not in covered_gt_edges
    )
    return result
