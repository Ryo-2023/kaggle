"""Array and receipt schemas for the detector-fixed association cache.

The cache boundary deliberately contains detector outputs only.  Ground-truth
graphs, annotation paths, and label-derived columns are not represented by
any of these schemas.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

NODE_ARRAY_SCHEMA: dict[str, Any] = {
    "node_id": {"dtype": "int64", "shape": ["N"]},
    "tzyx": {"dtype": "int16|int32", "shape": ["N", 4], "axes": "t,z,y,x"},
    "physical_zyx": {"dtype": "float32", "shape": ["N", 3], "axes": "z,y,x"},
    "detector_peak_logit": {"dtype": "float32", "shape": ["N"]},
    "detector_peak_probability": {"dtype": "float32", "shape": ["N"]},
    "node_features": {"dtype": "float32", "shape": ["N", "C"]},
}

EDGE_ARRAY_SCHEMA: dict[str, Any] = {
    "source_node_id": {"dtype": "int64", "shape": ["E"]},
    "target_node_id": {"dtype": "int64", "shape": ["E"]},
    "delta_t": {"dtype": "int16", "shape": ["E"]},
    "voxel_delta": {"dtype": "float32", "shape": ["E", 3], "axes": "z,y,x"},
    "physical_delta": {"dtype": "float32", "shape": ["E", 3], "axes": "z,y,x"},
    "voxel_distance": {"dtype": "float32", "shape": ["E"]},
    "physical_distance": {"dtype": "float32", "shape": ["E"]},
    "forward_logit": {"dtype": "float32", "shape": ["E"]},
    "reverse_logit": {"dtype": "float32", "shape": ["E"]},
    "forward_probability": {"dtype": "float32", "shape": ["E"]},
    "reverse_probability": {"dtype": "float32", "shape": ["E"]},
}

NODE_ARRAY_NAMES = tuple(NODE_ARRAY_SCHEMA)
EDGE_ARRAY_NAMES = tuple(EDGE_ARRAY_SCHEMA)


def _require_array(
    name: str,
    value: object,
    *,
    dtype: np.dtype[Any] | tuple[np.dtype[Any], ...],
    ndim: int,
) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise TypeError(f"{name} must be a numpy.ndarray")
    expected = dtype if isinstance(dtype, tuple) else (dtype,)
    if value.dtype not in expected:
        names = "|".join(item.name for item in expected)
        raise ValueError(f"{name} must have dtype {names}, got {value.dtype}")
    if value.ndim != ndim:
        raise ValueError(f"{name} must have {ndim} dimensions, got shape {value.shape}")
    return value


def _require_float_array(name: str, value: object, *, ndim: int, trailing: int | None = None) -> np.ndarray:
    array = _require_array(name, value, dtype=np.dtype(np.float32), ndim=ndim)
    if trailing is not None and array.shape[-1] != trailing:
        raise ValueError(f"{name} must have shape (N, {trailing}), got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite float32 values")
    return array


def _check_probability(name: str, array: np.ndarray) -> None:
    if np.any((array < 0.0) | (array > 1.0)):
        raise ValueError(f"{name} must contain probabilities in [0, 1]")


@dataclass(frozen=True, slots=True)
class NodeArrays:
    """Detector node outputs in ``(t, z, y, x)`` order.

    Semantic ordering checks that require all arrays are exposed through
    :meth:`validate`.  They are intentionally repeated at the cache write and
    load boundaries so a caller cannot persist an invalid replacement created
    with :func:`dataclasses.replace`.
    """

    node_id: np.ndarray
    tzyx: np.ndarray
    physical_zyx: np.ndarray
    detector_peak_logit: np.ndarray
    detector_peak_probability: np.ndarray
    node_features: np.ndarray

    def __post_init__(self) -> None:
        node_id = _require_array("node_id", self.node_id, dtype=np.dtype(np.int64), ndim=1)
        tzyx = _require_array(
            "tzyx",
            self.tzyx,
            dtype=(np.dtype(np.int16), np.dtype(np.int32)),
            ndim=2,
        )
        if tzyx.shape[1] != 4:
            raise ValueError(f"tzyx must have shape (N, 4) in t,z,y,x order, got {tzyx.shape}")
        physical_zyx = _require_float_array("physical_zyx", self.physical_zyx, ndim=2, trailing=3)
        detector_peak_logit = _require_float_array("detector_peak_logit", self.detector_peak_logit, ndim=1)
        detector_peak_probability = _require_float_array(
            "detector_peak_probability", self.detector_peak_probability, ndim=1
        )
        _check_probability("detector_peak_probability", detector_peak_probability)
        node_features = _require_float_array("node_features", self.node_features, ndim=2)

        length = node_id.shape[0]
        for name, array in (
            ("tzyx", tzyx),
            ("physical_zyx", physical_zyx),
            ("detector_peak_logit", detector_peak_logit),
            ("detector_peak_probability", detector_peak_probability),
            ("node_features", node_features),
        ):
            if array.shape[0] != length:
                raise ValueError(
                    f"all node arrays must have the same first dimension; {name} has {array.shape[0]}, "
                    f"expected {length}"
                )

        # Keep the input arrays and their dtypes intact.  The cache writer makes
        # a serialized snapshot, while this object remains convenient for
        # callers that use numpy views during detector capture.
        object.__setattr__(self, "node_id", node_id)
        object.__setattr__(self, "tzyx", tzyx)
        object.__setattr__(self, "physical_zyx", physical_zyx)
        object.__setattr__(self, "detector_peak_logit", detector_peak_logit)
        object.__setattr__(self, "detector_peak_probability", detector_peak_probability)
        object.__setattr__(self, "node_features", node_features)

    def validate(self) -> None:
        """Validate node IDs and temporal ordering before persistence."""

        expected_ids = np.arange(self.node_id.shape[0], dtype=np.int64)
        if not np.array_equal(self.node_id, expected_ids):
            raise ValueError("node_id must be a consecutive sequence starting at zero")
        times = self.tzyx[:, 0]
        if times.size and np.any(times < 0):
            raise ValueError("node t coordinates must be non-negative")
        if times.size > 1 and np.any(np.diff(times) < 0):
            raise ValueError("node t coordinates must be monotonically non-decreasing")

    @property
    def length(self) -> int:
        return int(self.node_id.shape[0])


@dataclass(frozen=True, slots=True)
class CandidateEdgeArrays:
    """All detector candidate source/target pairs and their bidirectional scores."""

    source_node_id: np.ndarray
    target_node_id: np.ndarray
    delta_t: np.ndarray
    voxel_delta: np.ndarray
    physical_delta: np.ndarray
    voxel_distance: np.ndarray
    physical_distance: np.ndarray
    forward_logit: np.ndarray
    reverse_logit: np.ndarray
    forward_probability: np.ndarray
    reverse_probability: np.ndarray

    def __post_init__(self) -> None:
        source_node_id = _require_array("source_node_id", self.source_node_id, dtype=np.dtype(np.int64), ndim=1)
        target_node_id = _require_array("target_node_id", self.target_node_id, dtype=np.dtype(np.int64), ndim=1)
        delta_t = _require_array("delta_t", self.delta_t, dtype=np.dtype(np.int16), ndim=1)
        voxel_delta = _require_float_array("voxel_delta", self.voxel_delta, ndim=2, trailing=3)
        physical_delta = _require_float_array("physical_delta", self.physical_delta, ndim=2, trailing=3)
        voxel_distance = _require_float_array("voxel_distance", self.voxel_distance, ndim=1)
        physical_distance = _require_float_array("physical_distance", self.physical_distance, ndim=1)
        forward_logit = _require_float_array("forward_logit", self.forward_logit, ndim=1)
        reverse_logit = _require_float_array("reverse_logit", self.reverse_logit, ndim=1)
        forward_probability = _require_float_array("forward_probability", self.forward_probability, ndim=1)
        reverse_probability = _require_float_array("reverse_probability", self.reverse_probability, ndim=1)
        _check_probability("forward_probability", forward_probability)
        _check_probability("reverse_probability", reverse_probability)

        length = source_node_id.shape[0]
        for name, array in (
            ("target_node_id", target_node_id),
            ("delta_t", delta_t),
            ("voxel_delta", voxel_delta),
            ("physical_delta", physical_delta),
            ("voxel_distance", voxel_distance),
            ("physical_distance", physical_distance),
            ("forward_logit", forward_logit),
            ("reverse_logit", reverse_logit),
            ("forward_probability", forward_probability),
            ("reverse_probability", reverse_probability),
        ):
            if array.shape[0] != length:
                raise ValueError(
                    f"all edge arrays must have the same first dimension; {name} has {array.shape[0]}, "
                    f"expected {length}"
                )

        object.__setattr__(self, "source_node_id", source_node_id)
        object.__setattr__(self, "target_node_id", target_node_id)
        object.__setattr__(self, "delta_t", delta_t)
        object.__setattr__(self, "voxel_delta", voxel_delta)
        object.__setattr__(self, "physical_delta", physical_delta)
        object.__setattr__(self, "voxel_distance", voxel_distance)
        object.__setattr__(self, "physical_distance", physical_distance)
        object.__setattr__(self, "forward_logit", forward_logit)
        object.__setattr__(self, "reverse_logit", reverse_logit)
        object.__setattr__(self, "forward_probability", forward_probability)
        object.__setattr__(self, "reverse_probability", reverse_probability)

    def validate(self, nodes: NodeArrays | None = None) -> None:
        """Validate edge direction, time deltas, and coordinate orientation."""

        length = self.length
        chunk_size = 1_000_000
        for start in range(0, length, chunk_size):
            end = min(start + chunk_size, length)
            if np.any(self.delta_t[start:end] <= 0):
                raise ValueError("candidate edge delta_t/time must be positive")
            if np.any(self.source_node_id[start:end] >= self.target_node_id[start:end]):
                raise ValueError("candidate edge source_node_id must be smaller than target_node_id")
        if nodes is None:
            return

        node_count = nodes.length
        for start in range(0, length, chunk_size):
            end = min(start + chunk_size, length)
            if np.any(self.source_node_id[start:end] < 0) or np.any(self.target_node_id[start:end] < 0):
                raise ValueError("candidate edge node IDs must be non-negative")
            if np.any(self.source_node_id[start:end] >= node_count) or np.any(
                self.target_node_id[start:end] >= node_count
            ):
                raise ValueError("candidate edge node IDs must refer to existing nodes")

        source = self.source_node_id
        target = self.target_node_id
        length = self.length
        # Validate dense edge columns in bounded chunks.  Detector-fixed
        # caches can exceed tens of millions of rows, for which constructing a
        # full expected-delta array would duplicate hundreds of MB at once.
        for start in range(0, length, chunk_size):
            end = min(start + chunk_size, length)
            source_chunk = source[start:end]
            target_chunk = target[start:end]
            source_t = nodes.tzyx[source_chunk, 0].astype(np.int64, copy=False)
            target_t = nodes.tzyx[target_chunk, 0].astype(np.int64, copy=False)
            if np.any(target_t <= source_t):
                raise ValueError("candidate edge target time must be greater than source time")
            expected_delta_t = target_t - source_t
            if not np.array_equal(self.delta_t[start:end].astype(np.int64), expected_delta_t):
                raise ValueError("candidate edge delta_t does not match source/target time")

            expected_voxel_delta = (
                nodes.tzyx[target_chunk, 1:].astype(np.float32)
                - nodes.tzyx[source_chunk, 1:].astype(np.float32)
            )
            if not np.allclose(
                self.voxel_delta[start:end], expected_voxel_delta, rtol=1e-5, atol=1e-5
            ):
                raise ValueError("candidate edge voxel_delta has the wrong source/target orientation")
            expected_physical_delta = nodes.physical_zyx[target_chunk] - nodes.physical_zyx[source_chunk]
            if not np.allclose(
                self.physical_delta[start:end], expected_physical_delta, rtol=1e-5, atol=1e-5
            ):
                raise ValueError("candidate edge physical_delta has the wrong source/target orientation")
            expected_voxel_distance = np.linalg.norm(expected_voxel_delta, axis=1).astype(np.float32)
            expected_physical_distance = np.linalg.norm(expected_physical_delta, axis=1).astype(np.float32)
            if not np.allclose(
                self.voxel_distance[start:end], expected_voxel_distance, rtol=1e-5, atol=1e-5
            ):
                raise ValueError("candidate edge voxel_distance does not match voxel_delta")
            if not np.allclose(
                self.physical_distance[start:end], expected_physical_distance, rtol=1e-5, atol=1e-5
            ):
                raise ValueError("candidate edge physical_distance does not match physical_delta")

    @property
    def length(self) -> int:
        return int(self.source_node_id.shape[0])


@dataclass(frozen=True, slots=True)
class DetectorCache:
    """Loaded, validated detector cache and its manifest."""

    root: Path
    manifest: Mapping[str, Any]
    nodes: NodeArrays
    edges: CandidateEdgeArrays


@dataclass(frozen=True, slots=True)
class CacheReceipt:
    """Receipt emitted after an atomic detector-cache publish."""

    root: Path
    cache_hash: str
    manifest_path: Path
    nodes_path: Path
    candidate_edges_path: Path


__all__ = [
    "EDGE_ARRAY_NAMES",
    "EDGE_ARRAY_SCHEMA",
    "NODE_ARRAY_NAMES",
    "NODE_ARRAY_SCHEMA",
    "CacheReceipt",
    "CandidateEdgeArrays",
    "DetectorCache",
    "NodeArrays",
]
