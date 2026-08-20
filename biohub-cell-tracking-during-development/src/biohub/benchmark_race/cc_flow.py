"""Image-only connected-component detector with global min-cost-flow linking.

``cc_flow`` is intentionally independent of :mod:`blob_lap`: foreground is
formed from a fixed image quantile normalization, candidates are 3-D
connected components, and association is solved in one global flow problem
over all available frames.  No ground-truth graph is accepted or opened by
this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import find_objects, generate_binary_structure, label

from biohub.benchmark_race.blob_lap import EdgeTable, PredictionArtifact, voxel_to_physical
from biohub.benchmark_race.cache import build_cache_manifest
from biohub.benchmark_race.contracts import RaceRequest
from biohub.strong_baseline.manifest import prediction_directory_manifest, write_prediction_manifest

DEFAULT_QUANTILE_LOW = 0.001
DEFAULT_QUANTILE_HIGH = 0.999
DEFAULT_FOREGROUND_THRESHOLD = 0.25
DEFAULT_MIN_COMPONENT_VOXELS = 3
DEFAULT_MAX_COMPONENT_VOXELS = 250_000
DEFAULT_MAX_LINK_DISTANCE_UM = 7.0
DEFAULT_LINK_COST_PER_UM = 1.0
DEFAULT_GAP_COST_UM = 8.0
DEFAULT_SCALE = (1.625, 0.40625, 0.40625)
_FLOW_COST_SCALE = 1_000_000


def _three_values(name: str, value: Sequence[Any], *, cast: type) -> tuple[Any, Any, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        converted = cast(value)
        return converted, converted, converted  # type: ignore[return-value]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values in (Z, Y, X) order")
    converted = tuple(cast(item) for item in value)
    return converted  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class CCFlowConfig:
    """Fixed image-only configuration for the connected-component lane.

    ``gap_cost_um`` is the combined birth/death cost for a track path.  The
    flow graph assigns half of it to the source and half to the sink, so a
    link is selected globally when its physical link cost is cheaper than
    starting and ending two independent paths.  Division is intentionally
    disabled for this initial race lane.
    """

    q_low: float = DEFAULT_QUANTILE_LOW
    q_high: float = DEFAULT_QUANTILE_HIGH
    foreground_threshold: float = DEFAULT_FOREGROUND_THRESHOLD
    min_component_voxels: int = DEFAULT_MIN_COMPONENT_VOXELS
    max_component_voxels: int = DEFAULT_MAX_COMPONENT_VOXELS
    max_link_distance_um: float = DEFAULT_MAX_LINK_DISTANCE_UM
    link_cost_per_um: float = DEFAULT_LINK_COST_PER_UM
    gap_cost_um: float = DEFAULT_GAP_COST_UM
    scale: tuple[float, float, float] = DEFAULT_SCALE
    division_enabled: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        q_low = float(self.q_low)
        q_high = float(self.q_high)
        if not np.isfinite(q_low) or not np.isfinite(q_high) or not 0.0 <= q_low < q_high <= 1.0:
            raise ValueError("q_low and q_high must satisfy 0 <= q_low < q_high <= 1")
        threshold = float(self.foreground_threshold)
        if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("foreground_threshold must be finite and satisfy 0 <= value <= 1")
        min_voxels = int(self.min_component_voxels)
        max_voxels = int(self.max_component_voxels)
        if isinstance(self.min_component_voxels, bool) or min_voxels <= 0:
            raise ValueError("min_component_voxels must be a positive integer")
        if isinstance(self.max_component_voxels, bool) or max_voxels < min_voxels:
            raise ValueError("max_component_voxels must be >= min_component_voxels")
        scale = tuple(float(value) for value in _three_values("scale", self.scale, cast=float))
        if any(not np.isfinite(value) or value <= 0.0 for value in scale):
            raise ValueError("scale must contain positive finite values")
        for name in ("max_link_distance_um", "link_cost_per_um", "gap_cost_um"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if float(self.gap_cost_um) <= 0.0:
            raise ValueError("gap_cost_um must be positive so the flow can select links")
        object.__setattr__(self, "q_low", q_low)
        object.__setattr__(self, "q_high", q_high)
        object.__setattr__(self, "foreground_threshold", threshold)
        object.__setattr__(self, "min_component_voxels", min_voxels)
        object.__setattr__(self, "max_component_voxels", max_voxels)
        object.__setattr__(self, "max_link_distance_um", float(self.max_link_distance_um))
        object.__setattr__(self, "link_cost_per_um", float(self.link_cost_per_um))
        object.__setattr__(self, "gap_cost_um", float(self.gap_cost_um))
        object.__setattr__(self, "scale", scale)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None = None,
        *,
        scale: Sequence[float] = DEFAULT_SCALE,
    ) -> CCFlowConfig:
        """Build a fixed config from JSON-like values and reject unknown keys."""

        raw = dict(values or {})
        division_enabled = raw.pop("division_enabled", False)
        if division_enabled:
            raise ValueError("cc_flow division is fixed disabled")
        aliases = {
            "threshold": "foreground_threshold",
            "foreground_threshold_normalized": "foreground_threshold",
            "min_component_size": "min_component_voxels",
            "max_component_size": "max_component_voxels",
            "min_voxels": "min_component_voxels",
            "max_voxels": "max_component_voxels",
            "link_distance_um": "max_link_distance_um",
            "link_cost_weight": "link_cost_per_um",
            "gap_cost": "gap_cost_um",
            "gap_cost_per_track_um": "gap_cost_um",
        }
        for source, target in aliases.items():
            if source in raw:
                if target in raw:
                    raise ValueError(f"duplicate config options {source!r} and {target!r}")
                raw[target] = raw.pop(source)
        allowed = {
            "q_low",
            "q_high",
            "foreground_threshold",
            "min_component_voxels",
            "max_component_voxels",
            "max_link_distance_um",
            "link_cost_per_um",
            "gap_cost_um",
            "scale",
        }
        unknown = sorted(set(raw).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported cc_flow config option(s): {', '.join(unknown)}")
        raw.setdefault("scale", scale)
        return cls(**raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "q_low": self.q_low,
            "q_high": self.q_high,
            "foreground_threshold": self.foreground_threshold,
            "min_component_voxels": self.min_component_voxels,
            "max_component_voxels": self.max_component_voxels,
            "max_link_distance_um": self.max_link_distance_um,
            "link_cost_per_um": self.link_cost_per_um,
            "gap_cost_um": self.gap_cost_um,
            "scale": list(self.scale),
            "division_enabled": False,
        }

    @property
    def threshold(self) -> float:
        """Compatibility alias for callers that call the foreground cutoff a threshold."""

        return self.foreground_threshold

    @property
    def gap_cost(self) -> float:
        """Compatibility alias exposing the configured physical gap cost."""

        return self.gap_cost_um


@dataclass(frozen=True, slots=True)
class CandidateTable:
    """Connected-component rows and deterministic region features.

    ``coordinates`` stores the geometric centroid in ``(T, Z, Y, X)`` voxel
    coordinates.  ``physical_coordinates`` stores the corresponding
    ``(Z, Y, X)`` micrometre coordinates.  Intensities are normalized to the
    image quantile range before feature computation, so ``scores`` are also
    image-only and comparable between frames.
    """

    coordinates: np.ndarray
    physical_coordinates: np.ndarray
    areas: np.ndarray
    mean_intensities: np.ndarray
    max_intensities: np.ndarray
    scores: np.ndarray

    def __post_init__(self) -> None:
        coordinates = np.asarray(self.coordinates, dtype=np.float64)
        physical = np.asarray(self.physical_coordinates, dtype=np.float64)
        areas = np.asarray(self.areas, dtype=np.int64)
        means = np.asarray(self.mean_intensities, dtype=np.float64)
        maxima = np.asarray(self.max_intensities, dtype=np.float64)
        scores = np.asarray(self.scores, dtype=np.float64)
        if coordinates.ndim != 2 or coordinates.shape[1] != 4:
            raise ValueError("candidate coordinates must have shape (N, 4) in (T, Z, Y, X) order")
        count = len(coordinates)
        if physical.shape != (count, 3):
            raise ValueError("candidate physical_coordinates must have shape (N, 3)")
        for name, values in (
            ("areas", areas),
            ("mean_intensities", means),
            ("max_intensities", maxima),
            ("scores", scores),
        ):
            if values.shape != (count,):
                raise ValueError(f"candidate {name} must have shape (N,)")
        if (areas <= 0).any():
            raise ValueError("candidate areas must be positive")
        if (
            not np.isfinite(coordinates).all()
            or not np.isfinite(physical).all()
            or not np.isfinite(means).all()
            or not np.isfinite(maxima).all()
            or not np.isfinite(scores).all()
        ):
            raise ValueError("candidate coordinates and features must be finite")
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "physical_coordinates", physical)
        object.__setattr__(self, "areas", areas)
        object.__setattr__(self, "mean_intensities", means)
        object.__setattr__(self, "max_intensities", maxima)
        object.__setattr__(self, "scores", scores)

    def __len__(self) -> int:
        return int(self.coordinates.shape[0])

    @property
    def voxel_coordinates(self) -> np.ndarray:
        return self.coordinates[:, 1:]

    @property
    def physical_coords(self) -> np.ndarray:
        return self.physical_coordinates

    @property
    def positions(self) -> np.ndarray:
        return self.coordinates

    @property
    def tzyx(self) -> np.ndarray:
        return self.coordinates

    @property
    def node_ids(self) -> np.ndarray:
        return np.arange(len(self), dtype=np.int64)

    @property
    def area(self) -> np.ndarray:
        return self.areas

    @property
    def component_area(self) -> np.ndarray:
        return self.areas

    @property
    def component_voxels(self) -> np.ndarray:
        return self.areas

    @property
    def intensities(self) -> np.ndarray:
        return self.mean_intensities

    @property
    def intensity(self) -> np.ndarray:
        return self.mean_intensities

    @property
    def mean_intensity(self) -> np.ndarray:
        return self.mean_intensities

    @property
    def max_intensity(self) -> np.ndarray:
        return self.max_intensities


def _normalise_image(image: np.ndarray, config: CCFlowConfig) -> np.ndarray:
    values = np.asarray(image, dtype=np.float32)
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros(values.shape, dtype=np.float32)
    finite_values = values[finite]
    low = float(np.quantile(finite_values, config.q_low))
    high = float(np.quantile(finite_values, config.q_high))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(values.shape, dtype=np.float32)
    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    return np.where(finite, normalized, 0.0).astype(np.float32)


def _normalise_frame(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    values = np.asarray(frame, dtype=np.float32)
    finite = np.isfinite(values)
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(values.shape, dtype=np.float32)
    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    return np.where(finite, normalized, 0.0).astype(np.float32)


def _component_rows(
    frame: np.ndarray,
    frame_index: int,
    config: CCFlowConfig,
) -> list[tuple[int, float, float, float, float, float, float, int]]:
    structure = generate_binary_structure(rank=3, connectivity=3)
    foreground = np.isfinite(frame) & (frame >= config.foreground_threshold)
    labeled, component_count = label(foreground, structure=structure)
    slices = find_objects(labeled)
    rows: list[tuple[int, float, float, float, float, float, float, int]] = []
    for component_id in range(1, component_count + 1):
        component_slice = slices[component_id - 1]
        if component_slice is None:
            continue
        local_mask = labeled[component_slice] == component_id
        area = int(np.count_nonzero(local_mask))
        if area < config.min_component_voxels or area > config.max_component_voxels:
            continue
        starts = np.asarray([item.start for item in component_slice], dtype=np.float64)
        local_coordinates = np.argwhere(local_mask).astype(np.float64) + starts
        centroid = local_coordinates.mean(axis=0)
        intensities = frame[component_slice][local_mask].astype(np.float64)
        rows.append(
            (
                frame_index,
                float(centroid[0]),
                float(centroid[1]),
                float(centroid[2]),
                float(intensities.mean()),
                float(intensities.max()),
                float(intensities.mean()),
                area,
            ),
        )
    return rows


def detect_cc_candidates(image: np.ndarray, config: CCFlowConfig | Mapping[str, Any]) -> CandidateTable:
    """Detect 3-D quantile-foreground connected components per frame."""

    config = config if isinstance(config, CCFlowConfig) else CCFlowConfig.from_mapping(config)
    values = np.asarray(image)
    if values.ndim != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {values.shape!r}")
    normalized = _normalise_image(values, config)
    rows: list[tuple[int, float, float, float, float, float, float, int]] = []
    for frame_index in range(normalized.shape[0]):
        rows.extend(_component_rows(normalized[frame_index], frame_index, config))
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], -row[6], row[7]))
    if not rows:
        return CandidateTable(
            np.empty((0, 4), dtype=np.float64),
            np.empty((0, 3), dtype=np.float64),
            np.empty((0,), dtype=np.int64),
            np.empty((0,), dtype=np.float64),
            np.empty((0,), dtype=np.float64),
            np.empty((0,), dtype=np.float64),
        )
    coordinates = np.asarray([[frame, z, y, x] for frame, z, y, x, *_ in rows], dtype=np.float64)
    physical = voxel_to_physical(coordinates[:, 1:], config.scale)
    means = np.asarray([row[4] for row in rows], dtype=np.float64)
    maxima = np.asarray([row[5] for row in rows], dtype=np.float64)
    scores = np.asarray([row[6] for row in rows], dtype=np.float64)
    areas = np.asarray([row[7] for row in rows], dtype=np.int64)
    return CandidateTable(coordinates, physical, areas, means, maxima, scores)


def detect_cc_candidates_streaming(
    image: Any,
    config: CCFlowConfig | Mapping[str, Any],
    *,
    quantiles: Mapping[str, float],
    max_frames: int | None = None,
) -> CandidateTable:
    """Detect connected components one frame at a time from a Zarr array."""

    config = config if isinstance(config, CCFlowConfig) else CCFlowConfig.from_mapping(config)
    shape = tuple(int(value) for value in image.shape)
    if len(shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {shape!r}")
    frame_count = shape[0] if max_frames is None else int(max_frames)
    if frame_count <= 0 or frame_count > shape[0]:
        raise ValueError(f"max_frames must be in [1, {shape[0]}]")
    low_value = quantiles.get(str(config.q_low))
    high_value = quantiles.get(str(config.q_high))
    if low_value is None or high_value is None:
        if config.q_low == 0.0 and config.q_high == 1.0:
            lows: list[float] = []
            highs: list[float] = []
            for frame_index in range(frame_count):
                frame_values = np.asarray(image[frame_index], dtype=np.float32)
                finite = frame_values[np.isfinite(frame_values)]
                if finite.size:
                    lows.append(float(finite.min()))
                    highs.append(float(finite.max()))
            low_value = min(lows, default=0.0)
            high_value = max(highs, default=0.0)
        else:
            raise ValueError("sample quantiles are missing detector quantile bounds")
    low = float(low_value)
    high = float(high_value)
    rows: list[tuple[int, float, float, float, float, float, float, int]] = []
    for frame_index in range(frame_count):
        frame = _normalise_frame(np.asarray(image[frame_index]), low, high)
        rows.extend(_component_rows(frame, frame_index, config))
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], -row[6], row[7]))
    if not rows:
        return CandidateTable(
            np.empty((0, 4), dtype=np.float64),
            np.empty((0, 3), dtype=np.float64),
            np.empty((0,), dtype=np.int64),
            np.empty((0,), dtype=np.float64),
            np.empty((0,), dtype=np.float64),
            np.empty((0,), dtype=np.float64),
        )
    coordinates = np.asarray([[frame, z, y, x] for frame, z, y, x, *_ in rows], dtype=np.float64)
    physical = voxel_to_physical(coordinates[:, 1:], config.scale)
    means = np.asarray([row[4] for row in rows], dtype=np.float64)
    maxima = np.asarray([row[5] for row in rows], dtype=np.float64)
    scores = np.asarray([row[6] for row in rows], dtype=np.float64)
    areas = np.asarray([row[7] for row in rows], dtype=np.int64)
    return CandidateTable(coordinates, physical, areas, means, maxima, scores)


def _pairwise_physical_distances(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    first_array = np.asarray(first, dtype=np.float64)
    second_array = np.asarray(second, dtype=np.float64)
    if (
        first_array.ndim != 2
        or second_array.ndim != 2
        or first_array.shape[1:] != (3,)
        or second_array.shape[1:] != (3,)
    ):
        raise ValueError("distance tables must have shape (N, 3) and (M, 3)")
    return np.linalg.norm(first_array[:, None, :] - second_array[None, :, :], axis=2)


def _flow_weight(cost: float, *, tie_break: int = 0) -> int:
    return round(float(cost) * _FLOW_COST_SCALE) + tie_break


def link_cc_flow(candidates: CandidateTable, config: CCFlowConfig | Mapping[str, Any]) -> EdgeTable:
    """Link all frames with one deterministic network-simplex flow solve.

    A source-to-sink bypass carries unused flow.  Candidate nodes are
    capacity-one in/out pairs; start and end edges carry half the configured
    gap cost, and adjacent physical links carry distance cost.  Thus a chain
    competes globally with all alternative chains and isolated candidates,
    instead of solving one Hungarian assignment per frame pair.
    """

    if not isinstance(candidates, CandidateTable):
        raise TypeError("candidates must be a cc_flow CandidateTable")
    config = config if isinstance(config, CCFlowConfig) else CCFlowConfig.from_mapping(config)
    if len(candidates) == 0:
        return EdgeTable(np.empty((0, 2), dtype=np.int64), np.empty((0,), dtype=np.float64))

    import networkx as nx

    count = len(candidates)
    source = "__source__"
    sink = "__sink__"
    graph = nx.DiGraph()
    graph.add_node(source, demand=-count)
    graph.add_node(sink, demand=count)
    graph.add_edge(source, sink, capacity=count, weight=0)
    half_gap = config.gap_cost_um / 2.0
    for candidate_id in range(count):
        node_in = f"in:{candidate_id}"
        node_out = f"out:{candidate_id}"
        graph.add_edge(
            source,
            node_in,
            capacity=1,
            weight=_flow_weight(half_gap, tie_break=candidate_id),
        )
        # A selected node earns one gap-cost unit.  An isolated node therefore
        # ties the source-to-sink bypass, while adding a physical link replaces
        # one extra birth/death pair and is globally worthwhile when its link
        # cost is below ``gap_cost_um``.
        graph.add_edge(
            node_in,
            node_out,
            capacity=1,
            weight=_flow_weight(-config.gap_cost_um),
        )
        graph.add_edge(
            node_out,
            sink,
            capacity=1,
            weight=_flow_weight(half_gap, tie_break=count - candidate_id),
        )

    frame_values = candidates.coordinates[:, 0]
    pair_distances: dict[tuple[int, int], float] = {}
    for frame in range(int(frame_values.max())):
        source_ids = np.flatnonzero(frame_values == frame)
        target_ids = np.flatnonzero(frame_values == frame + 1)
        if len(source_ids) == 0 or len(target_ids) == 0:
            continue
        distances = _pairwise_physical_distances(
            candidates.physical_coordinates[source_ids],
            candidates.physical_coordinates[target_ids],
        )
        for source_position, source_id in enumerate(source_ids):
            for target_position, target_id in enumerate(target_ids):
                distance = float(distances[source_position, target_position])
                if distance > config.max_link_distance_um:
                    continue
                pair = (int(source_id), int(target_id))
                pair_distances[pair] = distance
                link_cost = distance * config.link_cost_per_um
                tie_break = int(source_id) * (count + 1) + int(target_id)
                graph.add_edge(
                    f"out:{source_id}",
                    f"in:{target_id}",
                    capacity=1,
                    weight=_flow_weight(link_cost, tie_break=tie_break),
                )

    flow = nx.min_cost_flow(graph)
    pairs: list[tuple[int, int]] = []
    distances: list[float] = []
    for pair, distance in pair_distances.items():
        source_id, target_id = pair
        if int(flow[f"out:{source_id}"].get(f"in:{target_id}", 0)) > 0:
            pairs.append(pair)
            distances.append(distance)
    order = sorted(range(len(pairs)), key=lambda index: pairs[index])
    return EdgeTable(
        np.asarray([pairs[index] for index in order], dtype=np.int64).reshape(-1, 2),
        np.asarray([distances[index] for index in order], dtype=np.float64),
    )


def build_prediction_graph(
    candidates: CandidateTable,
    edges: EdgeTable,
    config: CCFlowConfig | Mapping[str, Any] | None = None,
) -> Any:
    """Build a tracksdata GEFF graph preserving component features and costs."""

    import polars as pl
    import tracksdata as td

    config = CCFlowConfig() if config is None else (
        config if isinstance(config, CCFlowConfig) else CCFlowConfig.from_mapping(config)
    )
    graph = td.graph.IndexedRXGraph()
    for name in (
        "z",
        "y",
        "x",
        "physical_z",
        "physical_y",
        "physical_x",
        "mean_intensity",
        "max_intensity",
        "score",
    ):
        graph.add_node_attr_key(name, dtype=pl.Float64, default_value=0.0)
    graph.add_node_attr_key("component_area", dtype=pl.Int64, default_value=0)
    graph.add_edge_attr_key("distance_um", dtype=pl.Float64, default_value=0.0)
    graph.add_edge_attr_key("link_cost", dtype=pl.Float64, default_value=0.0)
    for index, (coordinate, physical, area, mean, maximum, score) in enumerate(
        zip(
            candidates.coordinates,
            candidates.physical_coordinates,
            candidates.areas,
            candidates.mean_intensities,
            candidates.max_intensities,
            candidates.scores,
            strict=True,
        ),
    ):
        frame, z, y, x = coordinate
        physical_z, physical_y, physical_x = physical
        node_id = graph.add_node(
            {
                "t": int(frame),
                "z": float(z),
                "y": float(y),
                "x": float(x),
                "physical_z": float(physical_z),
                "physical_y": float(physical_y),
                "physical_x": float(physical_x),
                "component_area": int(area),
                "mean_intensity": float(mean),
                "max_intensity": float(maximum),
                "score": float(score),
            },
            index=int(index),
        )
        if node_id != index:
            raise RuntimeError("tracksdata assigned a non-deterministic node ID")
    for pair, distance in zip(edges.pairs, edges.distances_um, strict=True):
        distance_value = float(distance)
        graph.add_edge(
            int(pair[0]),
            int(pair[1]),
            {
                "distance_um": distance_value,
                "link_cost": distance_value * config.link_cost_per_um,
            },
        )
    return graph


def _open_image(path: Path) -> Any:
    import zarr

    root = zarr.open(str(path), mode="r")
    if hasattr(root, "shape"):
        array = root
    elif "0" in root:
        array = root["0"]
    else:
        raise ValueError(f"image Zarr is missing array '0': {path}")
    if len(array.shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {array.shape!r}")
    return array


def _sample_path(request: RaceRequest) -> Path:
    path = Path(request.sample.image_stem)
    if path.suffix.casefold() != ".zarr":
        path = path.with_suffix(".zarr")
    if not path.exists():
        raise FileNotFoundError(f"image Zarr not found: {path}")
    return path


def _source_revision() -> str:
    """Return explicit provenance or an honest container-local sentinel.

    The container does not expose the host worktree's implementation commit.
    Resolving a parent repository with ``git rev-parse`` would therefore make
    a bootstrap commit look like the adapter revision.  A caller may inject a
    verified revision explicitly; inference otherwise records the sentinel.
    """

    explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_REVISION")
    if explicit is None:
        explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_COMMIT")
    if explicit and explicit.strip():
        return explicit.strip()
    return "unavailable-in-container"


def _image_digest(image: Any, *, max_frames: int | None = None) -> str:
    digest = hashlib.sha256()
    digest.update(str(image.dtype).encode("ascii"))
    shape = tuple(int(value) for value in image.shape)
    frame_count = shape[0] if max_frames is None else int(max_frames)
    digest.update(repr((frame_count, *shape[1:])).encode("ascii"))
    for frame_index in range(frame_count):
        digest.update(np.ascontiguousarray(np.asarray(image[frame_index])).tobytes())
    return digest.hexdigest()


def _source_file_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _save_candidate_cache(
    request: RaceRequest,
    candidates: CandidateTable,
    image: Any,
    config: CCFlowConfig,
    max_frames: int | None,
    source_revision: str,
) -> tuple[str, Path]:
    detector_config = config.as_dict()
    detector_config["max_frames"] = max_frames
    cache_manifest = build_cache_manifest(
        sample=request.sample,
        image_digest=_image_digest(image, max_frames=max_frames),
        detector_config=detector_config,
        source_commit=source_revision,
    )
    cache_dir = Path(request.cache_root).resolve() / cache_manifest["cache_key"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_dir / "detections.npz",
        coordinates=candidates.coordinates,
        physical_coordinates=candidates.physical_coordinates,
        areas=candidates.areas,
        mean_intensities=candidates.mean_intensities,
        max_intensities=candidates.max_intensities,
        scores=candidates.scores,
    )
    detections_digest = hashlib.sha256((cache_dir / "detections.npz").read_bytes()).hexdigest()
    cache_manifest["detections_sha256"] = detections_digest
    (cache_dir / "cache_manifest.json").write_text(json.dumps(cache_manifest, indent=2, sort_keys=True) + "\n")
    return str(cache_manifest["cache_key"]), cache_dir / "cache_manifest.json"


def run_cc_flow(request: RaceRequest) -> PredictionArtifact:
    """Run image-only connected-component/global-flow inference."""

    if not isinstance(request, RaceRequest):
        raise TypeError("request must be a RaceRequest")
    if request.expected_device.casefold() != "cpu":
        raise ValueError("cc_flow currently supports only the CPU device")
    started_at = _timestamp()
    started = time.monotonic()
    request_config = dict(request.config)
    max_frames_value = request_config.pop("max_frames", None)
    if max_frames_value is None:
        max_frames = None
    else:
        if isinstance(max_frames_value, bool):
            raise ValueError("max_frames must be a positive integer")
        max_frames = int(max_frames_value)
        if max_frames <= 0:
            raise ValueError("max_frames must be a positive integer")
    config = CCFlowConfig.from_mapping(request_config, scale=request.sample.scale)
    expected_scale = tuple(float(value) for value in request.sample.scale)
    if config.scale != expected_scale:
        raise ValueError(
            "cc_flow config scale must match request.sample.scale; "
            f"got {config.scale!r}, expected {expected_scale!r}",
        )
    image_path = _sample_path(request)
    image = _open_image(image_path)
    if tuple(image.shape[1:]) != tuple(request.sample.shape[1:]):
        raise ValueError(f"image spatial shape {image.shape[1:]} disagrees with sample {request.sample.shape[1:]}")
    if max_frames is not None:
        if max_frames > image.shape[0]:
            raise ValueError(f"max_frames {max_frames} exceeds image frame count {image.shape[0]}")
    candidates = detect_cc_candidates_streaming(
        image,
        config,
        quantiles=request.sample.quantiles,
        max_frames=max_frames,
    )
    edges = link_cc_flow(candidates, config)
    source_revision = _source_revision()
    source_file_sha256 = _source_file_digest()
    cache_key, cache_manifest_path = _save_candidate_cache(
        request,
        candidates,
        image,
        config,
        max_frames,
        source_revision,
    )

    output_root = Path(request.output_root).resolve()
    target = output_root / "methods" / "cc_flow" / f"{Path(request.sample.image_stem).stem}.geff"
    if target.exists():
        raise FileExistsError(f"prediction destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    graph = build_prediction_graph(candidates, edges, config=config)
    graph.to_geff(target)
    manifest_payload = prediction_directory_manifest(target)
    manifest_payload.update(
        {
            "method_id": "cc_flow",
            "candidate_count": len(candidates),
            "edge_count": len(edges),
            "division_enabled": False,
            "ground_truth_included": False,
            "cache_key": cache_key,
            "config": config.as_dict(),
            "source_revision": source_revision,
            "source_file_sha256": source_file_sha256,
        },
    )
    manifest_path = write_prediction_manifest(target, manifest_payload)
    elapsed = time.monotonic() - started
    run_payload = {
        "method_id": "cc_flow",
        "method_family": "classical_connected_component_and_global_flow",
        "detector_id": "quantile_foreground_3d_connected_components",
        "linker_id": "global_min_cost_flow",
        "version": "cc_flow.v1",
        "source_module": "biohub.benchmark_race.cc_flow",
        "source_revision": source_revision,
        "source_commit": source_revision,
        "source_file_sha256": source_file_sha256,
        "checkpoint_sha256": None,
        "sample_id": request.sample.sample_id,
        "image_stem": request.sample.image_stem.as_posix(),
        "image_shape": [max_frames or int(image.shape[0]), *[int(value) for value in image.shape[1:]]],
        "config": config.as_dict(),
        "expected_device": request.expected_device,
        "actual_device": "cpu",
        "runtime_seconds": float(elapsed),
        "started_at": started_at,
        "finished_at": _timestamp(),
        "candidate_count": len(candidates),
        "prediction_node_count": len(candidates),
        "edge_count": len(edges),
        "prediction_edge_count": len(edges),
        "division_enabled": False,
        "ground_truth_included": False,
        "solver": "networkx.network_simplex",
        "solver_status": "optimal",
        "graph_optimization": "global_min_cost_flow",
        "cache_key": cache_key,
        "cache_manifest": str(cache_manifest_path),
        "prediction_manifest": str(manifest_path),
    }
    run_json_path = target.parent / "run.json"
    run_json_path.write_text(json.dumps(run_payload, indent=2, sort_keys=True) + "\n")
    (target.parent / "inference.log").write_text(
        f"method=cc_flow candidates={len(candidates)} edges={len(edges)} "
        "solver=networkx.network_simplex device=cpu\n",
    )
    return PredictionArtifact(
        prediction_path=target,
        prediction_manifest_path=manifest_path,
        run_json_path=run_json_path,
        candidate_count=len(candidates),
        edge_count=len(edges),
        cache_manifest_path=cache_manifest_path,
    )


__all__ = [
    "CCFlowConfig",
    "CandidateTable",
    "EdgeTable",
    "PredictionArtifact",
    "build_prediction_graph",
    "detect_cc_candidates",
    "detect_cc_candidates_streaming",
    "link_cc_flow",
    "run_cc_flow",
]
