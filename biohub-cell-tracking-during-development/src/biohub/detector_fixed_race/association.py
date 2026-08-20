"""Cache-only association methods for the detector-fixed race.

The detector cache is the only inference input accepted by this module.  Each
method turns the same bidirectional candidate rows into a scored edge list,
then delegates graph construction and ILP solving to injected callables.  This
keeps detector output, association policy, and GEFF serialization separate and
allows a fair comparison without rerunning the image detector.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from biohub.detector_fixed_race.schema import DetectorCache
from biohub.strong_baseline.harmonic import fuse_harmonic_logits

ASSOCIATION_METHODS = (
    "official_ilp",
    "harmonic_v1",
    "mutual_confidence",
    "motion_gated",
)
OFFICIAL_EDGE_THRESHOLD = 0.50
OFFICIAL_ILP_CONFIG = {
    "edge_weight": -1.0,
    "appearance_weight": 0.1,
    "disappearance_weight": 0.1,
    "division_weight": 1.0,
}


@dataclass(frozen=True, slots=True)
class AssociationSpec:
    """Frozen, score-only settings used by one association race method."""

    method_id: str
    reverse_weight: float = 0.20
    mutual_threshold: float = 0.50
    motion_gate_um: float = 12.0
    motion_alpha: float = 0.05

    def __post_init__(self) -> None:
        if self.method_id not in ASSOCIATION_METHODS:
            raise ValueError(f"unknown association method_id: {self.method_id!r}")
        values = {
            "reverse_weight": self.reverse_weight,
            "mutual_threshold": self.mutual_threshold,
            "motion_gate_um": self.motion_gate_um,
            "motion_alpha": self.motion_alpha,
        }
        for name, value in values.items():
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number")
        if not 0.0 < float(self.reverse_weight) <= 0.35:
            raise ValueError("reverse_weight must be in (0, 0.35]")
        if not 0.0 <= float(self.mutual_threshold) <= 1.0:
            raise ValueError("mutual_threshold must be in [0, 1]")
        if float(self.motion_gate_um) <= 0.0:
            raise ValueError("motion_gate_um must be positive")
        if float(self.motion_alpha) < 0.0:
            raise ValueError("motion_alpha must be non-negative")


@dataclass(frozen=True, slots=True)
class AssociationResult:
    """Solved graph and a compact selected-edge receipt.

    ``selected_edges`` has shape ``(E, 4)`` and columns
    ``source_node_id, target_node_id, score, physical_distance``.  Node IDs
    are stored as float32 in this mixed numeric array; consumers must cast the
    first two columns to integer IDs before graph construction.
    """

    method_id: str
    cache_hash: str
    selected_edges: np.ndarray
    graph: Any
    config: Mapping[str, Any]


def _cache_hash(cache: DetectorCache) -> str:
    value = cache.manifest.get("cache_hash")
    if not isinstance(value, str) or not value:
        raise ValueError("detector cache manifest must contain a non-empty cache_hash")
    if cache.manifest.get("ground_truth_included") is not False:
        raise ValueError("association requires a ground-truth-free detector cache")
    return value


def _group_rows(cache: DetectorCache) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Return cache rows grouped by their adjacent source/target frames."""

    edges = cache.edges
    nodes = cache.nodes
    if edges.length == 0:
        return []
    source_times = nodes.tzyx[edges.source_node_id, 0].astype(np.int64, copy=False)
    target_times = nodes.tzyx[edges.target_node_id, 0].astype(np.int64, copy=False)
    keys = np.stack((source_times, target_times), axis=1)
    unique_keys, inverse = np.unique(keys, axis=0, return_inverse=True)
    groups: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for group_index, key in enumerate(unique_keys):
        row_indices = np.flatnonzero(inverse == group_index)
        if row_indices.size == 0:
            continue
        if int(key[1]) <= int(key[0]):
            raise ValueError("association candidate edges must point to a later frame")
        groups.append(
            (
                row_indices,
                np.unique(edges.source_node_id[row_indices]),
                np.unique(edges.target_node_id[row_indices]),
            )
        )
    return groups


def _pair_matrices(
    cache: DetectorCache,
    row_indices: np.ndarray,
    source_ids: np.ndarray,
    target_ids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Place flattened pair rows into source-by-target matrices.

    The pinned upstream model uses ``softmax(axis=0)`` on a matrix shaped
    ``(N_source, N_target)``.  Reconstructing that matrix here prevents a
    flattened cache from accidentally changing the normalization axis.
    """

    edges = cache.edges
    source_lookup = {int(node_id): index for index, node_id in enumerate(source_ids.tolist())}
    target_lookup = {int(node_id): index for index, node_id in enumerate(target_ids.tolist())}
    shape = (source_ids.size, target_ids.size)
    forward = np.full(shape, np.nan, dtype=np.float32)
    reverse = np.full(shape, np.nan, dtype=np.float32)
    forward_probability = np.full(shape, np.nan, dtype=np.float32)
    reverse_probability = np.full(shape, np.nan, dtype=np.float32)
    for row_index in row_indices.tolist():
        source = int(edges.source_node_id[row_index])
        target = int(edges.target_node_id[row_index])
        try:
            position = (source_lookup[source], target_lookup[target])
        except KeyError as exc:
            raise ValueError("candidate row refers to an unknown pair node") from exc
        if not np.isnan(forward[position]):
            raise ValueError("detector cache contains a duplicate candidate pair")
        forward[position] = edges.forward_logit[row_index]
        reverse[position] = edges.reverse_logit[row_index]
        forward_probability[position] = edges.forward_probability[row_index]
        reverse_probability[position] = edges.reverse_probability[row_index]
    if not np.isfinite(forward).all() or not np.isfinite(reverse).all():
        raise ValueError("detector cache has an incomplete raw-logit candidate matrix")
    if not np.isfinite(forward_probability).all() or not np.isfinite(reverse_probability).all():
        raise ValueError("detector cache has an incomplete probability candidate matrix")
    return forward, reverse, forward_probability, reverse_probability


def _harmonic_probability(forward_logits: np.ndarray, reverse_logits: np.ndarray, weight: float) -> np.ndarray:
    forward_tensor = torch.from_numpy(forward_logits).unsqueeze(0)
    # Cache reverse logits are stored in forward source-by-target orientation;
    # the published helper accepts the model's native target-by-source output.
    reverse_native_tensor = torch.from_numpy(reverse_logits.T).unsqueeze(0)
    fused_logits = fuse_harmonic_logits(
        forward_tensor,
        reverse_native_tensor,
        reverse_weight=weight,
    )[0]
    return torch.softmax(fused_logits.float(), dim=0).numpy().astype(np.float32, copy=False)


def _score_matrix(
    cache: DetectorCache,
    spec: AssociationSpec,
    row_indices: np.ndarray,
    source_ids: np.ndarray,
    target_ids: np.ndarray,
) -> np.ndarray:
    forward_logits, reverse_logits, forward_probability, reverse_probability = _pair_matrices(
        cache,
        row_indices,
        source_ids,
        target_ids,
    )
    if spec.method_id == "official_ilp":
        return forward_probability
    if spec.method_id == "harmonic_v1":
        return _harmonic_probability(forward_logits, reverse_logits, spec.reverse_weight)
    if spec.method_id == "mutual_confidence":
        return np.sqrt(np.maximum(forward_probability * reverse_probability, 0.0)).astype(np.float32)
    if spec.method_id == "motion_gated":
        # Physical distance is indexed from the flattened cache rows below;
        # the gate is applied while converting the matrix to edge rows.
        return forward_probability
    raise AssertionError(f"unsupported association method: {spec.method_id}")


def _candidate_rows(cache: DetectorCache, spec: AssociationSpec) -> list[tuple[int, int, float, float]]:
    edges = cache.edges
    rows: list[tuple[int, int, float, float]] = []
    for row_indices, source_ids, target_ids in _group_rows(cache):
        scores = _score_matrix(cache, spec, row_indices, source_ids, target_ids)
        source_lookup = {int(node_id): index for index, node_id in enumerate(source_ids.tolist())}
        target_lookup = {int(node_id): index for index, node_id in enumerate(target_ids.tolist())}
        for row_index in row_indices.tolist():
            source = int(edges.source_node_id[row_index])
            target = int(edges.target_node_id[row_index])
            score = float(scores[source_lookup[source], target_lookup[target]])
            distance = float(edges.physical_distance[row_index])
            if spec.method_id == "motion_gated":
                if distance > float(spec.motion_gate_um):
                    continue
                score *= math.exp(-float(spec.motion_alpha) * distance)
            threshold = (
                float(spec.mutual_threshold)
                if spec.method_id == "mutual_confidence"
                else OFFICIAL_EDGE_THRESHOLD
            )
            if score > threshold:
                rows.append((source, target, score, distance))
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _edge_pairs(graph: Any) -> np.ndarray:
    """Read solver-selected source/target pairs from a tracksdata graph view."""

    if isinstance(graph, Mapping) and "selected_edges" in graph:
        value = graph["selected_edges"]
    else:
        edge_list = getattr(graph, "edge_list", None)
        if not callable(edge_list):
            raise TypeError("graph/solver result must expose edge_list() or selected_edges")
        value = edge_list()
    array = np.asarray(value, dtype=np.int64)
    if array.size == 0:
        return np.empty((0, 2), dtype=np.int64)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError(f"solver-selected edges must have shape (E, 2), got {array.shape}")
    return array


def _selected_receipt(
    graph: Any,
    candidate_rows: list[tuple[int, int, float, float]],
) -> np.ndarray:
    pairs = _edge_pairs(graph)
    candidate_by_pair = {(source, target): (score, distance) for source, target, score, distance in candidate_rows}
    selected: list[tuple[int, int, float, float]] = []
    for source, target in pairs.tolist():
        key = (int(source), int(target))
        try:
            score, distance = candidate_by_pair[key]
        except KeyError as exc:
            raise ValueError(f"solver returned an edge that was not a candidate: {key}") from exc
        selected.append((key[0], key[1], score, distance))
    selected.sort(key=lambda row: (row[0], row[1]))
    return np.asarray(selected, dtype=np.float32).reshape((-1, 4))


def associate_from_cache(
    cache: DetectorCache,
    spec: AssociationSpec,
    *,
    graph_builder: Callable[..., Any],
    ilp_solver: Callable[[Any], Any],
) -> AssociationResult:
    """Build and solve one association graph from a detector cache only."""

    if not isinstance(cache, DetectorCache):
        raise TypeError("cache must be a DetectorCache")
    if not isinstance(spec, AssociationSpec):
        raise TypeError("spec must be an AssociationSpec")
    if not callable(graph_builder) or not callable(ilp_solver):
        raise TypeError("graph_builder and ilp_solver must be callable")
    cache_hash = _cache_hash(cache)
    cache.nodes.validate()
    cache.edges.validate(cache.nodes)
    candidate_rows = _candidate_rows(cache, spec)
    coords = cache.nodes.tzyx.copy()
    graph = graph_builder(coords, candidate_rows)
    solved_graph = ilp_solver(graph) if candidate_rows else graph
    if solved_graph is None:
        raise RuntimeError("ILP solver returned None for a non-empty candidate graph")
    selected_edges = _selected_receipt(solved_graph, candidate_rows)
    config: dict[str, Any] = {
        "method_id": spec.method_id,
        "reverse_weight": float(spec.reverse_weight),
        "mutual_threshold": float(spec.mutual_threshold),
        "motion_gate_um": float(spec.motion_gate_um),
        "motion_alpha": float(spec.motion_alpha),
        "edge_threshold": (
            float(spec.mutual_threshold)
            if spec.method_id == "mutual_confidence"
            else OFFICIAL_EDGE_THRESHOLD
        ),
        "ilp": dict(OFFICIAL_ILP_CONFIG),
        "candidate_edge_count": len(candidate_rows),
        "selected_edge_count": int(selected_edges.shape[0]),
    }
    return AssociationResult(
        method_id=spec.method_id,
        cache_hash=cache_hash,
        selected_edges=selected_edges,
        graph=solved_graph,
        config=config,
    )


__all__ = [
    "ASSOCIATION_METHODS",
    "OFFICIAL_EDGE_THRESHOLD",
    "OFFICIAL_ILP_CONFIG",
    "AssociationResult",
    "AssociationSpec",
    "associate_from_cache",
]
