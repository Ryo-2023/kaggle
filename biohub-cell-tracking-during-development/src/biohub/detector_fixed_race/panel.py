"""Score-free validation-panel selection and detector-fixed race orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import re
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
VALIDATION_RECEIPT_SCHEMA_VERSION = "detector_fixed.validation_receipt.v1"
DEFAULT_PANEL_METHODS = ASSOCIATION_METHODS
DEFAULT_HARMONIC_REVERSE_WEIGHT = 0.20
_CACHE_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _association_spec(method_id: str, *, harmonic_reverse_weight: float) -> AssociationSpec:
    """Build one association spec while exposing harmonic tuning explicitly."""

    reverse_weight = (
        harmonic_reverse_weight
        if method_id == "harmonic_v1"
        else DEFAULT_HARMONIC_REVERSE_WEIGHT
    )
    return AssociationSpec(method_id, reverse_weight=reverse_weight)


def _prediction_filename(method_id: str, *, harmonic_reverse_weight: float) -> str:
    """Keep canonical filenames at the default and isolate custom variants."""

    if method_id != "harmonic_v1" or float(harmonic_reverse_weight) == DEFAULT_HARMONIC_REVERSE_WEIGHT:
        return f"{method_id}.geff"
    tag = f"{float(harmonic_reverse_weight):.2f}".replace(".", "p")
    return f"harmonic_v1_rw_{tag}.geff"


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
    harmonic_reverse_weight: float = DEFAULT_HARMONIC_REVERSE_WEIGHT,
) -> list[dict[str, Any]]:
    """Replay all association methods from one detector cache and score them."""

    unknown = set(methods) - set(ASSOCIATION_METHODS)
    if unknown:
        raise ValueError(f"unknown association methods: {sorted(unknown)}")
    # Validate even when a caller requests only non-harmonic methods so an
    # invalid sweep setting cannot be silently accepted.
    AssociationSpec("harmonic_v1", reverse_weight=harmonic_reverse_weight)
    cache = load_detector_cache(_cache_path(Path(cache_root), sample_id))
    graph_builder, ilp_solver = _association_components(predictor_module)
    output_root = Path(output_root)
    records: list[dict[str, Any]] = []
    scale = tuple(float(value) for value in cache.manifest["scale"])
    for method_id in methods:
        association = associate_from_cache(
            cache,
            _association_spec(
                method_id,
                harmonic_reverse_weight=harmonic_reverse_weight,
            ),
            graph_builder=graph_builder,
            ilp_solver=ilp_solver,
        )
        prediction_path = output_root / sample_id / _prediction_filename(
            method_id,
            harmonic_reverse_weight=harmonic_reverse_weight,
        )
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


def _receipt_json(path: Path, kind: str) -> tuple[bytes, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{kind} is unreadable: {path}") from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{kind} is malformed JSON: {path}") from exc
    return raw, payload


def _receipt_evidence_path(value: str, receipt_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.is_file():
        return path
    relative_to_receipt = receipt_path.parent / path
    return relative_to_receipt if relative_to_receipt.is_file() else path


def _require_cache_hash(value: Any, *, sample_id: str, method_id: str) -> str:
    if not isinstance(value, str) or _CACHE_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"cache_hash for ({sample_id}, {method_id}) must be a 64-character lowercase hex string"
        )
    return value


def aggregate_validation_receipts(
    *,
    panel_path: Path,
    receipt_paths: Sequence[Path],
    methods: Sequence[str],
) -> dict[str, Any]:
    """Aggregate persisted detector-fixed race receipts without opening GT or images."""

    panel_path = Path(panel_path)
    panel_raw, panel = _receipt_json(panel_path, "validation panel")
    if not isinstance(panel, Mapping) or panel.get("schema_version") != PANEL_SCHEMA_VERSION:
        raise ValueError("validation panel schema must be detector_fixed.panel.v1")

    sample_entries = panel.get("samples")
    if not isinstance(sample_entries, list) or not sample_entries:
        raise ValueError("validation panel samples must be a non-empty list")
    sample_ids: list[str] = []
    for entry in sample_entries:
        if not isinstance(entry, Mapping):
            raise ValueError("validation panel samples must be objects")
        sample_id = entry.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError("validation panel sample IDs must be non-empty strings")
        if sample_id in sample_ids:
            raise ValueError(f"validation panel sample IDs must be unique: {sample_id}")
        sample_ids.append(sample_id)

    method_ids = tuple(methods)
    if not method_ids or any(not isinstance(method, str) or not method.strip() for method in method_ids):
        raise ValueError("methods must be a non-empty sequence of non-empty strings")
    if len(set(method_ids)) != len(method_ids):
        raise ValueError("methods must be unique")

    expected_pairs = {(sample_id, method_id) for sample_id in sample_ids for method_id in method_ids}
    records_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    receipt_metadata: dict[tuple[str, str], tuple[Path, str]] = {}
    receipt_paths = tuple(Path(path) for path in receipt_paths)
    if not receipt_paths:
        raise ValueError("receipt_paths must not be empty")
    for receipt_path in receipt_paths:
        receipt_raw, receipt_payload = _receipt_json(receipt_path, "race receipt")
        if not isinstance(receipt_payload, list):
            raise ValueError(f"race receipt must contain a list: {receipt_path}")
        receipt_hash = hashlib.sha256(receipt_raw).hexdigest()
        for index, record_value in enumerate(receipt_payload):
            if not isinstance(record_value, Mapping):
                raise ValueError(f"race receipt record {index} must be an object: {receipt_path}")
            record = dict(record_value)
            sample_id = record.get("sample_id")
            method_id = record.get("method_id")
            pair = (sample_id, method_id)
            if not isinstance(sample_id, str) or not isinstance(method_id, str):
                raise ValueError(f"race receipt record {index} is missing sample_id or method_id")
            if pair not in expected_pairs:
                raise ValueError(f"unexpected sample/method pair: {pair!r}")
            if pair in records_by_pair:
                raise ValueError(f"duplicate sample/method pair: {pair!r}")
            _require_cache_hash(record.get("cache_hash"), sample_id=sample_id, method_id=method_id)
            records_by_pair[pair] = record
            receipt_metadata[pair] = (receipt_path, receipt_hash)

    missing_pairs = expected_pairs - records_by_pair.keys()
    if missing_pairs:
        ordered_missing = [
            (sample_id, method_id)
            for sample_id in sample_ids
            for method_id in method_ids
            if (sample_id, method_id) in missing_pairs
        ]
        raise ValueError(f"missing expected sample/method pair(s): {ordered_missing!r}")

    for sample_id in sample_ids:
        sample_hashes = {
            records_by_pair[(sample_id, method_id)]["cache_hash"] for method_id in method_ids
        }
        if len(sample_hashes) != 1:
            raise ValueError(f"cache_hash mismatch across methods for sample {sample_id}")

    normalized_records: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        for method_id in method_ids:
            pair = (sample_id, method_id)
            record = records_by_pair[pair]
            receipt_path, receipt_hash = receipt_metadata[pair]
            manifest_value = record.get("prediction_manifest_path")
            if not isinstance(manifest_value, str) or not manifest_value.strip():
                raise ValueError(f"record {pair!r} is missing prediction_manifest_path")
            manifest_path = _receipt_evidence_path(manifest_value, receipt_path)
            manifest_raw, manifest = _receipt_json(manifest_path, "prediction manifest")
            del manifest_raw
            if not isinstance(manifest, Mapping):
                raise ValueError(f"prediction manifest must contain an object: {manifest_path}")
            if manifest.get("ground_truth_included") is not False:
                raise ValueError(
                    f"prediction manifest ground_truth_included must be false: {manifest_path}"
                )
            for field, expected in (
                ("sample_id", sample_id),
                ("method_id", method_id),
                ("cache_hash", record["cache_hash"]),
            ):
                if manifest.get(field) != expected:
                    raise ValueError(f"prediction manifest {field} mismatch: {manifest_path}")

            metrics = record.get("metrics")
            if not isinstance(metrics, Mapping):
                raise ValueError(f"record {pair!r} is missing metrics")
            if metrics.get("prediction_manifest_validated_before_gt") is not True:
                raise ValueError(
                    f"metrics prediction_manifest_validated_before_gt must be true: {pair!r}"
                )
            metrics_manifest_value = metrics.get("prediction_manifest_path")
            if not isinstance(metrics_manifest_value, str) or not metrics_manifest_value.strip():
                raise ValueError(f"metrics are missing prediction_manifest_path: {pair!r}")
            metrics_manifest_path = _receipt_evidence_path(metrics_manifest_value, receipt_path)
            if metrics_manifest_path.resolve() != manifest_path.resolve():
                raise ValueError(f"metrics prediction manifest path mismatch: {pair!r}")
            try:
                score = float(metrics["final_score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"metrics final_score is missing or non-numeric: {pair!r}") from exc
            if not math.isfinite(score):
                raise ValueError(f"metrics final_score must be finite: {pair!r}")

            normalized_record = dict(record)
            normalized_record["race_receipt_path"] = str(receipt_path)
            normalized_record["race_receipt_sha256"] = receipt_hash
            normalized_records.append(normalized_record)

    return {
        "schema_version": VALIDATION_RECEIPT_SCHEMA_VERSION,
        "panel_path": str(panel_path),
        "panel_sha256": hashlib.sha256(panel_raw).hexdigest(),
        "samples": sample_ids,
        "methods": list(method_ids),
        "records": normalized_records,
        "summary": _aggregate_panel(normalized_records, method_ids),
        "failed_samples": [],
        "ground_truth_usage": "official metric evaluation only",
    }


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
    "VALIDATION_RECEIPT_SCHEMA_VERSION",
    "aggregate_validation_receipts",
    "freeze_validation_panel",
    "run_dev_race",
    "run_panel",
]
