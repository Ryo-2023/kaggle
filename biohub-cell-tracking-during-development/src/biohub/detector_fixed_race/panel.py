"""Score-free validation-panel selection and detector-fixed race orchestration."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import tracksdata as td
import zarr

from biohub.detector_fixed_race.association import (
    ASSOCIATION_METHODS,
    OFFICIAL_ILP_CONFIG,
    AssociationSpec,
    associate_from_cache,
)
from biohub.detector_fixed_race.cache import load_detector_cache
from biohub.detector_fixed_race.prediction import evaluate_prediction, write_prediction

PANEL_SCHEMA_VERSION = "detector_fixed.panel.v1"
DEFAULT_PANEL_METHODS = ASSOCIATION_METHODS


def _array_and_attrs(image_path: Path) -> tuple[Any, Mapping[str, Any]]:
    root = zarr.open_group(image_path, mode="r")
    try:
        array = root["0"]
    except (KeyError, TypeError):
        array = root
    attrs = root.attrs.asdict() if hasattr(root.attrs, "asdict") else dict(root.attrs)
    return array, attrs


def _image_scale(attrs: Mapping[str, Any]) -> tuple[float, float, float]:
    try:
        scale = attrs["multiscales"][0]["datasets"][0]["coordinateTransformations"][0]["scale"]
        if len(scale) == 4:
            return tuple(float(value) for value in scale[1:])
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return (1.625, 0.40625, 0.40625)


def _image_quantiles(attrs: Mapping[str, Any]) -> dict[str, float]:
    try:
        values = attrs["image_statistics"]["quantiles"]
        low = float(values["0.001"])
        high = float(values["0.999"])
        return {"0.001": low, "0.999": high}
    except (KeyError, TypeError, ValueError):
        return {"0.001": 0.0, "0.999": 1.0}


def _load_gt_graph(path: Path) -> td.graph.BaseGraph:
    loaded = td.graph.IndexedRXGraph.from_geff(path)
    return loaded[0] if isinstance(loaded, tuple) else loaded


def _gt_summary(path: Path) -> tuple[int, int, int]:
    graph = _load_gt_graph(path)
    edges = graph.edge_list()
    outgoing = Counter(int(source) for source, _target in edges)
    divisions = sum(count >= 2 for count in outgoing.values())
    return int(graph.num_nodes()), int(graph.num_edges()), int(divisions)


def _sample_record(image_path: Path, gt_path: Path) -> dict[str, Any]:
    array, attrs = _array_and_attrs(image_path)
    shape = tuple(int(value) for value in array.shape)
    if len(shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X): {image_path} -> {shape!r}")
    gt_nodes, gt_edges, divisions = _gt_summary(gt_path)
    return {
        "sample_id": image_path.stem.removesuffix(".zarr"),
        "image_path": str(image_path),
        "ground_truth_path": str(gt_path),
        "shape": list(shape),
        "scale": list(_image_scale(attrs)),
        "quantiles": _image_quantiles(attrs),
        "ground_truth_nodes": gt_nodes,
        "ground_truth_edges": gt_edges,
        "division_source_count": divisions,
    }


def freeze_validation_panel(
    train_root: Path,
    gt_root: Path,
    development_sample: str,
    minimum: int = 3,
    maximum: int = 5,
    require_division_if_available: bool = True,
) -> dict[str, Any]:
    """Freeze deterministic sample metadata without reading any metric score."""

    train_root = Path(train_root)
    gt_root = Path(gt_root)
    if minimum < 1 or maximum < minimum:
        raise ValueError("panel requires 1 <= minimum <= maximum")
    candidates: list[dict[str, Any]] = []
    for image_path in sorted(train_root.glob("*.zarr"), key=lambda path: path.name):
        sample_id = image_path.stem.removesuffix(".zarr")
        gt_path = gt_root / f"{sample_id}.geff"
        if not gt_path.exists():
            continue
        candidates.append(_sample_record(image_path, gt_path))
    by_id = {record["sample_id"]: record for record in candidates}
    if development_sample not in by_id:
        raise ValueError(f"development sample is not a train image with GT: {development_sample}")

    selected = candidates[:maximum]
    if development_sample not in {record["sample_id"] for record in selected}:
        replacement_index = next(
            (index for index, record in enumerate(selected) if record["sample_id"] != development_sample),
            None,
        )
        if replacement_index is None:
            selected.append(by_id[development_sample])
        else:
            selected[replacement_index] = by_id[development_sample]

    if require_division_if_available:
        division_candidates = [record for record in candidates if record["division_source_count"] > 0]
        if division_candidates and not any(record["division_source_count"] > 0 for record in selected):
            division_record = division_candidates[0]
            replacement_index = next(
                (index for index, record in enumerate(selected) if record["sample_id"] != development_sample),
                None,
            )
            if replacement_index is not None:
                selected[replacement_index] = division_record

    unique_selected = {record["sample_id"] for record in selected}
    if len(unique_selected) < minimum:
        raise ValueError(f"only {len(unique_selected)} GT-backed images are available; minimum is {minimum}")
    selected = sorted(
        [by_id[sample_id] for sample_id in unique_selected],
        key=lambda item: item["sample_id"],
    )
    return {
        "schema_version": PANEL_SCHEMA_VERSION,
        "development_sample": development_sample,
        "minimum": minimum,
        "maximum": maximum,
        "require_division_if_available": bool(require_division_if_available),
        "selection_rule": (
            "lexicographic image filename; development sample required; "
            "division metadata included when available"
        ),
        "samples": selected,
    }


def _cache_path(cache_root: Path, sample_id: str) -> Path:
    cache_root = Path(cache_root)
    return cache_root if (cache_root / "READY").is_file() else cache_root / sample_id


def _association_components(predictor_module: ModuleType) -> tuple[Any, Any]:
    if not hasattr(predictor_module, "build_graph"):
        raise TypeError("predictor_module must expose build_graph")

    def graph_builder(coords: Any, edges: Any) -> Any:
        return predictor_module.build_graph(coords, edges)

    def ilp_solver(graph: Any) -> Any:
        if graph.num_edges() <= 0:
            return graph
        solver = td.solvers.ILPSolver(
            edge_weight=OFFICIAL_ILP_CONFIG["edge_weight"] * td.EdgeAttr("edge_prob"),
            appearance_weight=OFFICIAL_ILP_CONFIG["appearance_weight"],
            disappearance_weight=OFFICIAL_ILP_CONFIG["disappearance_weight"],
            division_weight=OFFICIAL_ILP_CONFIG["division_weight"],
        )
        solved = solver.solve(graph)
        if solved is None:
            raise RuntimeError("tracksdata ILP solver returned None")
        return solved

    return graph_builder, ilp_solver


def run_dev_race(
    *,
    sample_id: str,
    cache_root: Path,
    output_root: Path,
    methods: Sequence[str],
    gt_path: Path,
    predictor_module: ModuleType,
) -> list[dict[str, Any]]:
    """Replay all association methods from one detector cache and score them."""

    unknown = set(methods) - set(ASSOCIATION_METHODS)
    if unknown:
        raise ValueError(f"unknown association methods: {sorted(unknown)}")
    cache = load_detector_cache(_cache_path(Path(cache_root), sample_id))
    graph_builder, ilp_solver = _association_components(predictor_module)
    output_root = Path(output_root)
    records: list[dict[str, Any]] = []
    scale = tuple(float(value) for value in cache.manifest["scale"])
    for method_id in methods:
        association = associate_from_cache(
            cache,
            AssociationSpec(method_id),
            graph_builder=graph_builder,
            ilp_solver=ilp_solver,
        )
        prediction_path = output_root / sample_id / f"{method_id}.geff"
        write_prediction(cache, association, predictor_module, prediction_path)
        metrics = evaluate_prediction(
            prediction_path,
            Path(gt_path),
            {"scale": scale, "max_distance": 7.0},
        )
        records.append(
            {
                "sample_id": sample_id,
                "method_id": method_id,
                "cache_hash": association.cache_hash,
                "prediction_path": str(prediction_path),
                "prediction_manifest_path": metrics["prediction_manifest_path"],
                "prediction_node_count": metrics["prediction_node_count"],
                "prediction_edge_count": metrics["prediction_edge_count"],
                "selected_edge_count": association.config["selected_edge_count"],
                "config": dict(association.config),
                "metrics": metrics,
            }
        )
    receipt_path = output_root / sample_id / "race_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return records


def _aggregate_panel(records: list[dict[str, Any]], methods: Sequence[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    by_method: dict[str, list[float]] = {method: [] for method in methods}
    for record in records:
        by_method.setdefault(record["method_id"], []).append(float(record["metrics"]["final_score"]))
    official_scores = by_method.get("official_ilp", [])
    official_mean = statistics.fmean(official_scores) if official_scores else None
    for method, scores in by_method.items():
        if not scores:
            summary[method] = {"n": 0, "mean_final_score": None, "median_final_score": None, "delta_vs_official": None}
            continue
        mean_score = statistics.fmean(scores)
        summary[method] = {
            "n": len(scores),
            "mean_final_score": mean_score,
            "median_final_score": statistics.median(scores),
            "delta_vs_official": None if official_mean is None else mean_score - official_mean,
            "improve_count": sum(
                score > official for score, official in zip(scores, official_scores, strict=True)
            ),
            "harm_count": sum(
                score < official for score, official in zip(scores, official_scores, strict=True)
            ),
        }
    return summary


def run_panel(
    *,
    panel_path: Path,
    methods: Sequence[str],
    output_root: Path,
    train_root: Path,
    gt_root: Path,
    cache_root: Path | None = None,
    predictor_module: ModuleType,
) -> dict[str, Any]:
    """Run all methods for every frozen panel sample; never select by score."""

    panel = json.loads(Path(panel_path).read_text())
    if panel.get("schema_version") != PANEL_SCHEMA_VERSION:
        raise ValueError("unsupported validation panel schema")
    samples = panel.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("validation panel has no samples")
    train_root = Path(train_root)
    gt_root = Path(gt_root)
    cache_root = Path(cache_root) if cache_root is not None else Path(output_root).parent / "cache"
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        gt_path = gt_root / f"{sample_id}.geff"
        try:
            records.extend(
                run_dev_race(
                    sample_id=sample_id,
                    cache_root=cache_root,
                    output_root=output_root,
                    methods=methods,
                    gt_path=gt_path,
                    predictor_module=predictor_module,
                )
            )
        except Exception as exc:
            failures.append({"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"})
    result = {
        "schema_version": "detector_fixed.panel_run.v1",
        "panel_path": str(panel_path),
        "methods": list(methods),
        "samples": [sample["sample_id"] for sample in samples],
        "records": records,
        "failed_samples": failures,
        "summary": _aggregate_panel(records, methods),
    }
    output_path = Path(output_root) / "panel_receipt.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return result


__all__ = [
    "DEFAULT_PANEL_METHODS",
    "PANEL_SCHEMA_VERSION",
    "freeze_validation_panel",
    "run_dev_race",
    "run_panel",
]
