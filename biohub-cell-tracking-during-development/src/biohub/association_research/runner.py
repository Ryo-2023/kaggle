"""Replay a research scoring rule against an existing detector-fixed cache.

Nothing here runs the detector.  The only inputs are a materialised
``DetectorCache`` and one :class:`~biohub.association_research.scoring.ScoringRule`;
the graph construction, ILP solve, GEFF write and official metric all reuse
the pinned upstream path so that a research rule is comparable, edge for edge,
with ``official_ilp`` and ``harmonic_v1``.

Candidate ordering is reproduced exactly as
``biohub.detector_fixed_race.association._candidate_rows`` produces it (frame
pairs in ascending ``(source_t, target_t)`` order, rows within a pair sorted
descending by ``(score, source_id, target_id)``), because that order becomes
the tracksdata edge id order and therefore drives ILP tie-breaking and the
official metric's out-degree dedup.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from biohub.association_research.scoring import RESEARCH_RULES, PairInputs, ScoringRule

# Reusing Codex's receipt helper keeps the selected-edge contract identical to
# the published baselines rather than re-deriving it here.
from biohub.detector_fixed_race.association import (
    OFFICIAL_ILP_CONFIG,
    AssociationResult,
    _selected_receipt,
)
from biohub.detector_fixed_race.schema import DetectorCache

_TIME_SHIFT = 12
"""Bits reserved for the target frame index inside the packed group key."""


@dataclass(frozen=True, slots=True)
class PairSlice:
    """One frame pair's rows, node ids and dense matrices."""

    source_time: int
    target_time: int
    row_indices: np.ndarray
    source_ids: np.ndarray
    target_ids: np.ndarray
    inputs: PairInputs


def _packed_group_key(cache: DetectorCache) -> np.ndarray:
    nodes = cache.nodes
    edges = cache.edges
    source_time = nodes.tzyx[edges.source_node_id, 0].astype(np.int32, copy=False)
    target_time = nodes.tzyx[edges.target_node_id, 0].astype(np.int32, copy=False)
    if source_time.size and int(target_time.max()) >= (1 << _TIME_SHIFT):
        raise ValueError("frame index is too large for the packed group key")
    if np.any(target_time <= source_time):
        raise ValueError("association candidate edges must point to a later frame")
    return (source_time << _TIME_SHIFT) | target_time


def iter_pair_slices(cache: DetectorCache) -> Iterator[PairSlice]:
    """Yield frame pairs in ascending ``(source_t, target_t)`` order."""

    edges = cache.edges
    if edges.length == 0:
        return
    keys = _packed_group_key(cache)
    unique_keys, inverse = np.unique(keys, return_inverse=True)
    del keys
    order = np.argsort(inverse, kind="stable")
    counts = np.bincount(inverse, minlength=unique_keys.size)
    del inverse
    starts = np.concatenate(([0], np.cumsum(counts)))
    source_node_id = edges.source_node_id
    target_node_id = edges.target_node_id
    for group_index, key in enumerate(unique_keys.tolist()):
        row_indices = order[starts[group_index] : starts[group_index + 1]]
        if row_indices.size == 0:
            continue
        row_indices = np.sort(row_indices)
        source_ids = np.unique(source_node_id[row_indices])
        target_ids = np.unique(target_node_id[row_indices])
        matrices = _dense_matrices(cache, row_indices, source_ids, target_ids)
        yield PairSlice(
            source_time=int(key >> _TIME_SHIFT),
            target_time=int(key & ((1 << _TIME_SHIFT) - 1)),
            row_indices=row_indices,
            source_ids=source_ids,
            target_ids=target_ids,
            inputs=matrices,
        )


def _dense_matrices(
    cache: DetectorCache,
    row_indices: np.ndarray,
    source_ids: np.ndarray,
    target_ids: np.ndarray,
) -> PairInputs:
    edges = cache.edges
    shape = (source_ids.size, target_ids.size)
    if row_indices.size != shape[0] * shape[1]:
        raise ValueError(
            "detector cache frame pair is not a complete source-by-target block: "
            f"{row_indices.size} rows for shape {shape}"
        )
    row_slot = np.searchsorted(source_ids, edges.source_node_id[row_indices])
    column_slot = np.searchsorted(target_ids, edges.target_node_id[row_indices])
    flat_slot = row_slot.astype(np.int64) * shape[1] + column_slot
    if np.unique(flat_slot).size != flat_slot.size:
        raise ValueError("detector cache contains a duplicate candidate pair")

    def scatter(values: np.ndarray) -> np.ndarray:
        destination = np.empty(shape[0] * shape[1], dtype=np.float64)
        destination[flat_slot] = values[row_indices]
        return destination.reshape(shape)

    return PairInputs(
        forward_logit=scatter(edges.forward_logit),
        reverse_logit=scatter(edges.reverse_logit),
        physical_distance=scatter(edges.physical_distance),
        forward_probability=scatter(edges.forward_probability),
    )


def build_candidate_rows(
    cache: DetectorCache,
    rule: ScoringRule,
) -> tuple[list[tuple[int, int, float, float]], dict[str, Any]]:
    """Score every frame pair and return upstream-ordered candidate rows.

    The second return value is a GT-free diagnostic bundle: it records how
    many candidates each admission path contributed, which is what separates
    "this rule changed the ranking" from "this rule changed the threshold".
    """

    if not isinstance(rule, ScoringRule):
        raise TypeError("rule must be a ScoringRule")
    rows: list[tuple[int, int, float, float]] = []
    pair_count = 0
    accepted_total = 0
    threshold_accepted_total = 0
    gate_only_total = 0
    scored_total = 0
    for pair_slice in iter_pair_slices(cache):
        pair_count += 1
        inputs = pair_slice.inputs
        scores, accepted = rule.evaluate(inputs)
        scores32 = scores.astype(np.float32, copy=False)
        above_threshold = scores32 > np.float32(rule.threshold)
        accepted = accepted & np.isfinite(scores)
        scored_total += scores.size
        threshold_accepted_total += int(above_threshold.sum())
        gate_only_total += int((accepted & ~above_threshold).sum())
        flat = np.flatnonzero(accepted.ravel())
        if flat.size == 0:
            continue
        accepted_total += int(flat.size)
        column_count = scores.shape[1]
        source_slot, target_slot = np.divmod(flat, column_count)
        source_id = pair_slice.source_ids[source_slot].astype(np.int64, copy=False)
        target_id = pair_slice.target_ids[target_slot].astype(np.int64, copy=False)
        selected_score = scores32.ravel()[flat]
        distance = inputs.physical_distance.ravel()[flat].astype(np.float32, copy=False)
        # Upstream sorts candidate tuples descending by (prob, source, target).
        order = np.lexsort((-target_id, -source_id, -selected_score.astype(np.float64)))
        rows.extend(
            zip(
                source_id[order].tolist(),
                target_id[order].tolist(),
                selected_score[order].astype(np.float64).tolist(),
                distance[order].astype(np.float64).tolist(),
                strict=True,
            )
        )
    diagnostics = {
        "frame_pair_count": pair_count,
        "scored_candidate_count": scored_total,
        "candidate_edge_count": accepted_total,
        "threshold_admitted_count": threshold_accepted_total,
        "gate_only_admitted_count": gate_only_total,
    }
    return rows, diagnostics


def associate_research_rule(
    cache: DetectorCache,
    rule_id: str,
    *,
    graph_builder: Callable[..., Any],
    ilp_solver: Callable[[Any], Any],
    rules: Mapping[str, ScoringRule] | None = None,
) -> tuple[AssociationResult, dict[str, Any]]:
    """Build and solve one research association graph from a cache only."""

    if not isinstance(cache, DetectorCache):
        raise TypeError("cache must be a DetectorCache")
    if not callable(graph_builder) or not callable(ilp_solver):
        raise TypeError("graph_builder and ilp_solver must be callable")
    registry = RESEARCH_RULES if rules is None else rules
    try:
        rule = registry[rule_id]
    except KeyError as exc:
        raise ValueError(f"unknown research rule_id: {rule_id!r}") from exc

    cache_hash = cache.manifest.get("cache_hash")
    if not isinstance(cache_hash, str) or not cache_hash:
        raise ValueError("detector cache manifest must contain a non-empty cache_hash")
    if cache.manifest.get("ground_truth_included") is not False:
        raise ValueError("association requires a ground-truth-free detector cache")

    started = time.monotonic()
    candidate_rows, diagnostics = build_candidate_rows(cache, rule)
    scoring_seconds = time.monotonic() - started

    coords = cache.nodes.tzyx.copy()
    graph = graph_builder(coords, candidate_rows)
    solve_started = time.monotonic()
    solved_graph = ilp_solver(graph) if candidate_rows else graph
    solve_seconds = time.monotonic() - solve_started
    if solved_graph is None:
        raise RuntimeError("ILP solver returned None for a non-empty candidate graph")
    selected_edges = _selected_receipt(solved_graph, candidate_rows)

    config: dict[str, Any] = {
        "method_id": rule_id,
        "lane": "claude/f-association",
        "rule": rule.describe(),
        "edge_threshold": float(rule.threshold),
        "ilp": dict(OFFICIAL_ILP_CONFIG),
        "candidate_edge_count": len(candidate_rows),
        "selected_edge_count": int(selected_edges.shape[0]),
    }
    config.update(diagnostics)
    diagnostics = dict(diagnostics)
    diagnostics.update(
        {
            "scoring_seconds": scoring_seconds,
            "ilp_seconds": solve_seconds,
            "selected_edge_count": int(selected_edges.shape[0]),
        }
    )
    return (
        AssociationResult(
            method_id=rule_id,
            cache_hash=cache_hash,
            selected_edges=selected_edges,
            graph=solved_graph,
            config=config,
        ),
        diagnostics,
    )


def score_distribution(cache: DetectorCache, rule_id: str) -> dict[str, Any]:
    """GT-free score diagnostics for one rule; no graph is built.

    Useful for answering "did this rule move the ranking or only the
    threshold?" without paying for an ILP solve.
    """

    rule = RESEARCH_RULES[rule_id]
    column_top: list[float] = []
    column_runner_up: list[float] = []
    admitted = 0
    scored = 0
    for pair_slice in iter_pair_slices(cache):
        scores, accepted = rule.evaluate(pair_slice.inputs)
        scored += scores.size
        admitted += int(accepted.sum())
        if scores.shape[0] >= 2:
            partitioned = np.partition(scores, -2, axis=0)
            column_top.append(float(partitioned[-1].sum()))
            column_runner_up.append(float(partitioned[-2].sum()))
    return {
        "rule_id": rule_id,
        "scored_candidate_count": scored,
        "admitted_candidate_count": admitted,
        "summed_column_top1": float(np.sum(column_top)),
        "summed_column_top2": float(np.sum(column_runner_up)),
    }


def available_rules() -> Sequence[str]:
    return tuple(RESEARCH_RULES)


__all__ = [
    "PairSlice",
    "associate_research_rule",
    "available_rules",
    "build_candidate_rows",
    "iter_pair_slices",
    "score_distribution",
]
