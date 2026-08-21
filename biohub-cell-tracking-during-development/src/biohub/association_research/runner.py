"""Replay a research scoring rule against an existing detector-fixed cache.

Nothing here runs the detector.  The only inputs are a materialised cache and
one :class:`~biohub.association_research.scoring.ScoringRule`; graph
construction, ILP solve, GEFF write and official metric all reuse the pinned
upstream path so a research rule is comparable, edge for edge, with
``official_ilp`` and ``harmonic_v1``.

Candidate ordering reproduces
``biohub.detector_fixed_race.association._candidate_rows`` exactly: frame
pairs in ascending ``(source_t, target_t)`` order, rows within a pair sorted
descending by ``(score, source_id, target_id)``.  That order becomes the
tracksdata edge id order, which drives ILP tie-breaking and the official
metric's out-degree-2 dedup, so changing it would make the comparison
dishonest.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from biohub.association_research.cache_view import LeanCache
from biohub.association_research.scoring import RESEARCH_RULES, PairInputs, ScoringRule

# Reusing Codex's receipt and compaction helpers keeps the selected-edge and
# GEFF node contracts identical to the published baselines.
from biohub.detector_fixed_race.association import (
    OFFICIAL_ILP_CONFIG,
    AssociationResult,
    _selected_receipt,
)
from biohub.detector_fixed_race.prediction import _compact_prediction_inputs
from biohub.strong_baseline.manifest import (
    prediction_directory_manifest,
    write_prediction_manifest,
)

_TIME_SHIFT = 12
"""Bits reserved for the target frame index inside the packed group key."""


@dataclass(frozen=True, slots=True)
class PairSlice:
    """One frame pair's node ids and dense source-by-target matrices."""

    source_time: int
    target_time: int
    source_ids: np.ndarray
    target_ids: np.ndarray
    inputs: PairInputs
    contiguous: bool


def _frame_pair_runs(cache: LeanCache) -> tuple[np.ndarray, np.ndarray]:
    """Return the packed key and the start offset of every contiguous run."""

    tzyx = cache.nodes.tzyx
    source_time = np.asarray(tzyx[np.asarray(cache.column("source_node_id")), 0], dtype=np.int32)
    target_time = np.asarray(tzyx[np.asarray(cache.column("target_node_id")), 0], dtype=np.int32)
    if source_time.size == 0:
        return np.empty((0,), dtype=np.int64), np.empty((0,), dtype=np.int64)
    if np.any(target_time <= source_time):
        raise ValueError("association candidate edges must point to a later frame")
    if int(target_time.max()) >= (1 << _TIME_SHIFT):
        raise ValueError("frame index is too large for the packed group key")
    keys = (source_time.astype(np.int64) << _TIME_SHIFT) | target_time.astype(np.int64)
    del source_time, target_time
    boundaries = np.flatnonzero(np.diff(keys) != 0) + 1
    starts = np.concatenate(([0], boundaries, [keys.size])).astype(np.int64)
    run_keys = keys[starts[:-1]]
    if run_keys.size != np.unique(run_keys).size:
        raise ValueError(
            "detector cache frame pairs are interleaved; the contiguous-run reader cannot be used"
        )
    if run_keys.size > 1 and np.any(np.diff(run_keys) <= 0):
        raise ValueError(
            "detector cache frame pairs are not in ascending (source_t, target_t) order"
        )
    return run_keys, starts


def iter_pair_slices(cache: LeanCache) -> Iterator[PairSlice]:
    """Yield frame pairs in ascending ``(source_t, target_t)`` order.

    The upstream capture writes each frame pair as ``np.repeat(source_ids, T)``
    against ``np.tile(target_ids, S)``, so a pair's rows are one contiguous,
    source-major block.  That is asserted rather than assumed: the layout is
    what allows a plain memmap slice and a ``reshape`` instead of a
    7-million-element scatter.
    """

    run_keys, starts = _frame_pair_runs(cache)
    if run_keys.size == 0:
        return
    source_node_id = cache.column("source_node_id")
    target_node_id = cache.column("target_node_id")
    for index in range(run_keys.size):
        start = int(starts[index])
        stop = int(starts[index + 1])
        source_block = np.asarray(source_node_id[start:stop], dtype=np.int64)
        target_block = np.asarray(target_node_id[start:stop], dtype=np.int64)
        source_ids = np.unique(source_block)
        target_ids = np.unique(target_block)
        rows, columns = source_ids.size, target_ids.size
        if rows * columns != source_block.size:
            raise ValueError(
                "detector cache frame pair is not a complete source-by-target block: "
                f"{source_block.size} rows for shape {(rows, columns)}"
            )
        expected_source = np.repeat(source_ids, columns)
        expected_target = np.tile(target_ids, rows)
        if not (
            np.array_equal(source_block, expected_source)
            and np.array_equal(target_block, expected_target)
        ):
            raise ValueError(
                "detector cache frame pair is not in source-major repeat/tile order"
            )
        shape = (rows, columns)

        def block(name: str) -> np.ndarray:
            return np.asarray(cache.column(name)[start:stop], dtype=np.float64).reshape(shape)

        yield PairSlice(
            source_time=int(run_keys[index] >> _TIME_SHIFT),
            target_time=int(run_keys[index] & ((1 << _TIME_SHIFT) - 1)),
            source_ids=source_ids,
            target_ids=target_ids,
            inputs=PairInputs(
                forward_logit=block("forward_logit"),
                reverse_logit=block("reverse_logit"),
                physical_distance=block("physical_distance"),
                forward_probability=block("forward_probability"),
            ),
            contiguous=True,
        )


def build_candidate_rows(
    cache: LeanCache,
    rule: ScoringRule,
) -> tuple[list[tuple[int, int, float, float]], dict[str, Any]]:
    """Score every frame pair and return upstream-ordered candidate rows.

    The second return value is a ground-truth-free diagnostic bundle that
    separates "this rule re-ranked the candidates" from "this rule only moved
    the threshold", which is the distinction the harmonic result turns on.
    """

    if not isinstance(rule, ScoringRule):
        raise TypeError("rule must be a ScoringRule")
    rows: list[tuple[int, int, float, float]] = []
    pair_count = 0
    accepted_total = 0
    threshold_total = 0
    gate_only_total = 0
    scored_total = 0
    argmax_admitted = 0
    for pair_slice in iter_pair_slices(cache):
        pair_count += 1
        inputs = pair_slice.inputs
        scores, accepted = rule.evaluate(inputs)
        scores32 = scores.astype(np.float32, copy=False)
        above_threshold = scores32 > np.float32(rule.threshold)
        accepted = accepted & np.isfinite(scores)
        scored_total += int(scores.size)
        threshold_total += int(above_threshold.sum())
        gate_only_total += int((accepted & ~above_threshold).sum())
        argmax_admitted += int(accepted.sum(axis=0).clip(max=1).sum())
        flat = np.flatnonzero(accepted.ravel())
        if flat.size == 0:
            continue
        accepted_total += int(flat.size)
        source_slot, target_slot = np.divmod(flat, scores.shape[1])
        source_id = pair_slice.source_ids[source_slot]
        target_id = pair_slice.target_ids[target_slot]
        selected_score = scores32.ravel()[flat].astype(np.float64)
        distance = inputs.physical_distance.ravel()[flat]
        order = np.lexsort((-target_id, -source_id, -selected_score))
        rows.extend(
            zip(
                source_id[order].tolist(),
                target_id[order].tolist(),
                selected_score[order].tolist(),
                distance[order].tolist(),
                strict=True,
            )
        )
    diagnostics = {
        "frame_pair_count": pair_count,
        "scored_candidate_count": scored_total,
        "candidate_edge_count": accepted_total,
        "threshold_admitted_count": threshold_total,
        "gate_only_admitted_count": gate_only_total,
        "targets_with_a_candidate": argmax_admitted,
    }
    return rows, diagnostics


def associate_research_rule(
    cache: LeanCache,
    rule_id: str,
    *,
    graph_builder: Callable[..., Any],
    ilp_solver: Callable[[Any], Any],
    rules: Mapping[str, ScoringRule] | None = None,
) -> tuple[AssociationResult, dict[str, Any]]:
    """Build and solve one research association graph from a cache only."""

    if not callable(graph_builder) or not callable(ilp_solver):
        raise TypeError("graph_builder and ilp_solver must be callable")
    registry = RESEARCH_RULES if rules is None else rules
    try:
        rule = registry[rule_id]
    except KeyError as exc:
        raise ValueError(f"unknown research rule_id: {rule_id!r}") from exc
    if cache.manifest.get("ground_truth_included") is not False:
        raise ValueError("association requires a ground-truth-free detector cache")

    started = time.monotonic()
    candidate_rows, diagnostics = build_candidate_rows(cache, rule)
    scoring_seconds = time.monotonic() - started

    coords = np.asarray(cache.nodes.tzyx).copy()
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
        "selected_edge_count": int(selected_edges.shape[0]),
    }
    config.update(diagnostics)
    receipt = dict(diagnostics)
    receipt.update(
        {
            "scoring_seconds": scoring_seconds,
            "ilp_seconds": solve_seconds,
            "selected_edge_count": int(selected_edges.shape[0]),
        }
    )
    return (
        AssociationResult(
            method_id=rule_id,
            cache_hash=cache.cache_hash,
            selected_edges=selected_edges,
            graph=solved_graph,
            config=config,
        ),
        receipt,
    )


def write_research_prediction(
    cache: LeanCache,
    result: AssociationResult,
    predictor_module: Any,
    output_path: Path,
) -> Path:
    """Serialize with the pinned upstream GEFF writer and a GT-free manifest.

    Mirrors ``biohub.detector_fixed_race.prediction.write_prediction`` but
    takes a :class:`LeanCache`, whose schema validation was already performed
    at open time against the manifest digests.  Re-running the full
    ``CandidateEdgeArrays.validate`` here would rebuild several ``(E, 3)``
    float arrays and defeat the point of the memmap view.
    """

    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"prediction destination already exists: {output_path}")
    if result.cache_hash != cache.cache_hash:
        raise ValueError("association result cache_hash does not match detector cache")
    if not hasattr(predictor_module, "build_graph") or not hasattr(predictor_module, "save_graph"):
        raise TypeError("predictor_module must expose build_graph and save_graph")
    selected = result.selected_edges
    if selected.ndim != 2 or selected.shape[1] != 4:
        raise ValueError("association selected_edges must be an (E, 4) array")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    coords, edge_rows = _compact_prediction_inputs(cache, selected)
    graph = predictor_module.build_graph(coords, edge_rows)
    predictor_module.save_graph(graph, output_path)
    if not output_path.exists():
        raise RuntimeError(f"predictor_module.save_graph did not create {output_path}")
    manifest = prediction_directory_manifest(output_path)
    manifest.update(
        {
            "method_id": result.method_id,
            "cache_hash": result.cache_hash,
            "config": dict(result.config),
            "prediction_sha256": manifest["directory_sha256"],
            "ground_truth_included": False,
        }
    )
    write_prediction_manifest(output_path, manifest)
    return output_path


def available_rules() -> Sequence[str]:
    return tuple(RESEARCH_RULES)


__all__ = [
    "PairSlice",
    "associate_research_rule",
    "available_rules",
    "build_candidate_rows",
    "iter_pair_slices",
    "write_research_prediction",
]
