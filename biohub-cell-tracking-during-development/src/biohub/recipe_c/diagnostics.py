"""GT-free Recipe C stage diagnostics and derived instrumentation.

This module owns only observation helpers.  It never opens ground truth,
imports official metrics, or reimplements source postprocessing.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import py_compile
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

DIAGNOSTIC_STDOUT_PREFIX = "BIOHUB_RECIPE_C_DIAGNOSTIC "
_SCHEMA_VERSION = 1
_FIELDS = (
    "candidate_edges",
    "combined_detector_nodes",
    "ilp_post_edges",
    "ilp_post_nodes",
    "ilp_pre_edges",
    "ilp_pre_nodes",
    "sample_id",
    "schema_version",
)
_CSV_HEADER = ("id", "dataset", "row_type", "node_id", "t", "z", "y", "x", "source_id", "target_id")
_TRACE_STAGES = (
    "distance_next_frame_filter",
    "motion_relink",
    "single_parent_repair",
    "single_child_repair",
    "gap_close",
    "gap2_recovery",
    "safe_divisions",
    "division_geometry_filter",
    "prune_isolated",
    "short_track_filter",
    "linefit_smooth",
)


@dataclass(slots=True)
class DiagnosticRecorder:
    """Mutable, GT-free observation state for one fresh inference run."""

    sample_ids: tuple[str, ...]
    resolved_device: str
    state: dict[str, object]
    child_device: str = ""
    child_records: dict[str, dict[str, int | str]] = field(default_factory=dict)
    predictor_diagnostic_instrumented: bool = False
    predictor_diagnostic_sha256: str | None = None
    trace_module_sha256: str | None = None
    trace_module_derived_sha256: str | None = None
    trace_publish: dict[str, object] | None = None
    trace_results: dict[
        str,
        tuple[dict[int, dict[str, object]], list[dict[str, object]], dict[str, object]],
    ] = field(default_factory=dict)

    @classmethod
    def create(cls, sample_ids: Sequence[str], resolved_device: str) -> DiagnosticRecorder:
        selected = tuple(str(sample) for sample in sample_ids)
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("diagnostic samples must be non-empty and unique")
        return cls(
            sample_ids=selected,
            resolved_device=resolved_device,
            state={
                "schema_version": _SCHEMA_VERSION,
                "status": "RUNNING",
                "ground_truth_used_for_prediction": False,
                "metric_status": "not_run_gt_guard",
                "sample_ids": list(selected),
                "device": {
                    "resolved_device": resolved_device,
                    "child_device": "",
                    "cuda_equivalence_validated": False,
                },
                "samples": {},
            },
        )

    def set_child_device(self, child_device: str, *, resolved_device: str | None = None) -> None:
        if resolved_device is not None:
            self.resolved_device = resolved_device
        self.child_device = str(child_device)
        self.state["device"] = {
            "resolved_device": self.resolved_device,
            "child_device": self.child_device,
            "cuda_equivalence_validated": False,
        }

    def record_image_input(self, shape_tzyx: Sequence[int]) -> None:
        shape = tuple(int(value) for value in shape_tzyx)
        if len(shape) != 4 or any(value <= 0 for value in shape):
            raise ValueError("image input shape must be a finite positive TZYX tuple")
        self.state["image_input"] = {
            "shape_tzyx": list(shape),
            "sample_ids": list(self.sample_ids),
            "device": dict(self.state["device"]),
        }

    def prepare_trace(
        self,
        source_api: Any,
        destination: Path,
        relative: PurePosixPath,
        publish: Callable[[PurePosixPath, bytes], dict[str, object]],
    ) -> Any:
        trace_function, source_hash, derived_hash, payload = _prepare_trace_adapter(
            source_api, destination
        )
        self.trace_module_sha256 = source_hash
        self.trace_module_derived_sha256 = derived_hash
        if payload is not None:
            self.trace_publish = publish(relative, payload)
        return trace_function

    def instrument_predictor(
        self, path: Path, compile_predictor: Callable[[Path], object]
    ) -> bytes:
        payload, instrumented = _apply_predictor_diagnostic_instrumentation(path.read_bytes())
        self.predictor_diagnostic_instrumented = instrumented
        if instrumented:
            path.write_bytes(payload)
        compile_predictor(path)
        self.predictor_diagnostic_sha256 = _sha256(payload)
        return payload

    def parse_child_stdout(self, stdout: str) -> None:
        if self.predictor_diagnostic_instrumented or DIAGNOSTIC_STDOUT_PREFIX in stdout:
            self.child_records = _parse_child_diagnostic_stdout(stdout, self.sample_ids)

    def finalize(
        self,
        path: Path,
        provenance: Mapping[str, object],
        write_json: Callable[..., object],
    ) -> str:
        self.state["status"] = "READY"
        self.state["provenance"] = dict(provenance)
        write_json(path, _json_safe(self.state, label="diagnostics"), mode=0o644)
        return _sha256_file(path)

    def provenance(
        self,
        *,
        source_commit: str,
        predictor_sha256_before: str,
        predictor_sha256_after: str,
        postprocessing_module_sha256: str | None,
        child_stdout_sha256: str,
    ) -> dict[str, object]:
        return {
            "source_commit": source_commit,
            "trace_module_sha256": self.trace_module_sha256,
            "trace_module_derived_sha256": self.trace_module_derived_sha256,
            "postprocessing_module_sha256": postprocessing_module_sha256,
            "predictor_sha256_before": predictor_sha256_before,
            "predictor_sha256_after": predictor_sha256_after,
            "predictor_diagnostic_instrumented": self.predictor_diagnostic_instrumented,
            "predictor_diagnostic_sha256": self.predictor_diagnostic_sha256,
            "child_stdout_sha256": child_stdout_sha256,
            "cuda_equivalence_validated": False,
        }

    def receipt_counts(self, postprocessing_module_sha256: str | None) -> dict[str, object]:
        return {
            "child": self.child_records,
            "trace_module_sha256": self.trace_module_sha256,
            "trace_module_derived_sha256": self.trace_module_derived_sha256,
            "postprocessing_module_sha256": postprocessing_module_sha256,
            "predictor_sha256": self.predictor_diagnostic_sha256,
            "metric_status": self.state["metric_status"],
            "cuda_equivalence_validated": False,
        }

    def mark_failed(self) -> None:
        self.state["status"] = "FAILED"
        self.state["device"] = {
            "resolved_device": self.resolved_device,
            "child_device": self.child_device,
            "cuda_equivalence_validated": False,
        }

    def record_raw_sample(
        self,
        sample_id: str,
        path: Path,
        structural_counts: Mapping[str, object],
        digest: Mapping[str, object],
        *,
        strict: bool,
    ) -> tuple[dict[str, object], tuple[dict[int, dict[str, object]], list[dict[str, object]]]]:
        graph_nodes, graph_edges = _graph_records_or_empty(path, strict=strict)
        graph_counts = (
            _graph_counts(graph_nodes, graph_edges)
            if graph_nodes
            else _fallback_graph_counts(structural_counts)
        )
        raw_counts = {
            **dict(structural_counts),
            **graph_counts,
            **dict(digest),
        }
        record = self.child_records.get(sample_id)
        if record is None:
            if self.predictor_diagnostic_instrumented:
                raise ValueError(f"child diagnostic record is missing: {sample_id}")
            record = {
                "sample_id": sample_id,
                "schema_version": _SCHEMA_VERSION,
                "combined_detector_nodes": int(graph_counts["nodes"]),
                "candidate_edges": int(graph_counts["edges"]),
                "ilp_pre_nodes": int(graph_counts["nodes"]),
                "ilp_pre_edges": int(graph_counts["edges"]),
                "ilp_post_nodes": int(graph_counts["nodes"]),
                "ilp_post_edges": int(graph_counts["edges"]),
            }
        elif int(record["ilp_post_nodes"]) != int(graph_counts["nodes"]) or int(record["ilp_post_edges"]) != int(
            graph_counts["edges"]
        ):
            raise ValueError("child ILP post topology disagrees with raw GEFF")
        samples = self._samples()
        samples[sample_id] = {
            "device": dict(self.state["device"]),
            "combined_detector": {"nodes": int(record["combined_detector_nodes"])},
            "candidate_graph": {
                "nodes": int(record["combined_detector_nodes"]),
                "edges": int(record["candidate_edges"]),
            },
            "ilp": {
                "pre": {
                    "nodes": int(record["ilp_pre_nodes"]),
                    "edges": int(record["ilp_pre_edges"]),
                },
                "post": {
                    "nodes": int(record["ilp_post_nodes"]),
                    "edges": int(record["ilp_post_edges"]),
                },
            },
            "raw_geff": dict(raw_counts),
        }
        return raw_counts, (graph_nodes, graph_edges)

    def record_csv_and_trace(
        self,
        path: Path,
        raw_graphs: Mapping[str, tuple[dict[int, dict[str, object]], list[dict[str, object]]]],
        trace_function: Any,
        *,
        strict: bool,
        trace_module_sha256: str | None = None,
    ) -> dict[str, tuple[dict[int, dict[str, object]], list[dict[str, object]]]]:
        try:
            csv_graphs = _csv_signatures(path, self.sample_ids)
        except Exception:
            if strict:
                raise
            csv_graphs = {sample: ({}, []) for sample in self.sample_ids}
        for sample_id in self.sample_ids:
            csv_nodes, csv_edges = csv_graphs[sample_id]
            sample = self._samples().get(sample_id)
            if not isinstance(sample, dict):
                raise ValueError("diagnostic raw graph state is missing")
            sample["production_csv"] = _graph_counts(csv_nodes, csv_edges)
            if trace_function is None:
                if trace_module_sha256 is not None:
                    raise ValueError("pinned source trace was not loaded")
                continue
            raw_nodes, raw_edges = raw_graphs[sample_id]
            traced_nodes, traced_edges, trace_payload = _trace_graph(
                trace_function,
                {node_id: dict(node) for node_id, node in raw_nodes.items()},
                [dict(edge) for edge in raw_edges],
                dataset=sample_id,
            )
            self.trace_results[sample_id] = (traced_nodes, traced_edges, trace_payload)
            if _graph_signature(traced_nodes, traced_edges) != _graph_signature(csv_nodes, csv_edges):
                raise ValueError(f"source trace final graph disagrees with production CSV: {sample_id}")
            trace_payload["identity_matches_csv"] = True
            trace_payload["final_counts"] = _graph_counts(traced_nodes, traced_edges)
            sample["source_trace"] = trace_payload
        return csv_graphs

    def record_final_sample(
        self,
        sample_id: str,
        path: Path,
        structural_counts: Mapping[str, object],
        digest: Mapping[str, object],
        csv_graph: tuple[dict[int, dict[str, object]], list[dict[str, object]]],
        *,
        strict: bool,
    ) -> dict[str, object]:
        final_nodes, final_edges = _graph_records_or_empty(path, strict=strict)
        final_graph_counts = (
            _graph_counts(final_nodes, final_edges)
            if final_nodes
            else _fallback_graph_counts(structural_counts)
        )
        result = {**dict(structural_counts), **dict(digest), **final_graph_counts}
        csv_nodes, csv_edges = csv_graph
        if final_nodes and _graph_signature(final_nodes, final_edges) != _graph_signature(csv_nodes, csv_edges):
            raise ValueError(f"bridge GEFF changed production CSV identity: {sample_id}")
        sample = self._samples().get(sample_id)
        if not isinstance(sample, dict):
            raise ValueError("diagnostic sample state is missing before bridge")
        sample["bridge_final_geff"] = dict(final_graph_counts)
        sample["persisted_reload"] = {
            **dict(final_graph_counts),
            "identity_matches_csv": True,
        }
        trace_value = self.trace_results.get(sample_id)
        if trace_value is not None and final_nodes:
            traced_nodes, traced_edges, _ = trace_value
            if _graph_signature(traced_nodes, traced_edges) != _graph_signature(final_nodes, final_edges):
                raise ValueError(f"source trace final graph disagrees with persisted GEFF: {sample_id}")
        return result

    def assert_six_frame_contract(self) -> None:
        for sample_id in self.sample_ids:
            sample = self._samples().get(sample_id)
            if not isinstance(sample, Mapping):
                raise ValueError("diagnostic sample state is missing at smoke completion")
            detector = sample.get("combined_detector", {})
            ilp = sample.get("ilp", {})
            post = ilp.get("post", {}) if isinstance(ilp, Mapping) else {}
            final = sample.get("bridge_final_geff", {})
            if (
                not isinstance(detector, Mapping)
                or int(detector.get("nodes", 0)) <= 0
                or not isinstance(post, Mapping)
                or int(post.get("nodes", 0)) <= 0
                or not isinstance(final, Mapping)
                or int(final.get("nodes", 0)) <= 0
            ):
                raise ValueError("six-frame diagnostic done contract requires positive node counts")

    def _samples(self) -> dict[str, object]:
        samples = self.state.get("samples")
        if not isinstance(samples, dict):
            raise ValueError("diagnostic samples state is invalid")
        return samples


def _graph_records_or_empty(
    path: Path, *, strict: bool
) -> tuple[dict[int, dict[str, object]], list[dict[str, object]]]:
    try:
        return _graph_records(path)
    except Exception:
        if strict:
            raise
        return {}, []


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _apply_predictor_diagnostic_instrumentation(payload: bytes) -> tuple[bytes, bool]:
    """Insert a deterministic one-record-per-sample print at save boundary."""

    text = payload.decode("utf-8")
    marker = "_BIOHUB_RECIPE_C_DIAGNOSTIC_INSTRUMENTATION = 1"
    if marker in text:
        return payload, True
    graph_line = "graph = build_graph(coords, edges)"
    if text.count(graph_line) != 1:
        return payload, False
    save_match = re.search(r"(?m)^(?P<indent>[ \t]+)save_graph\(", text)
    if save_match is None:
        return payload, False
    graph_index = text.index(graph_line)
    line_start = text.rfind("\n", 0, graph_index) + 1
    graph_indent = text[line_start:graph_index] or save_match.group("indent")
    prefix = (
        f"{graph_indent}{marker}\n"
        f"{graph_indent}_biohub_ilp_pre_nodes = int(graph.num_nodes())\n"
        f"{graph_indent}_biohub_ilp_pre_edges = int(graph.num_edges())\n"
    )
    graph_end = graph_index + len(graph_line)
    text = text[:graph_end] + "\n" + prefix + text[graph_end:]
    save_match = re.search(r"(?m)^(?P<indent>[ \t]+)save_graph\(", text)
    if save_match is None:  # pragma: no cover
        raise ValueError("predictor save boundary disappeared")
    indent = save_match.group("indent")
    record = (
        f"{indent}_biohub_ilp_post_nodes = int(graph.num_nodes())\n"
        f"{indent}_biohub_ilp_post_edges = int(graph.num_edges())\n"
        f"{indent}print({DIAGNOSTIC_STDOUT_PREFIX!r} + json.dumps({{\n"
        f"{indent}    'candidate_edges': int(len(edges)),\n"
        f"{indent}    'combined_detector_nodes': int(len(coords)),\n"
        f"{indent}    'ilp_post_edges': _biohub_ilp_post_edges,\n"
        f"{indent}    'ilp_post_nodes': _biohub_ilp_post_nodes,\n"
        f"{indent}    'ilp_pre_edges': _biohub_ilp_pre_edges,\n"
        f"{indent}    'ilp_pre_nodes': _biohub_ilp_pre_nodes,\n"
        f"{indent}    'sample_id': str(name),\n"
        f"{indent}    'schema_version': 1,\n"
        f"{indent}}}, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)\n"
    )
    return (text[: save_match.start()] + record + text[save_match.start() :]).encode(), True


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"diagnostic stdout contains non-finite JSON constant: {value}")


def _parse_child_diagnostic_stdout(stdout: str, sample_ids: Sequence[str]) -> dict[str, dict[str, int | str]]:
    expected = tuple(str(sample) for sample in sample_ids)
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("diagnostic samples must be non-empty and unique")
    records: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if DIAGNOSTIC_STDOUT_PREFIX in line and not line.startswith(DIAGNOSTIC_STDOUT_PREFIX):
            raise ValueError("diagnostic stdout prefix is not exact")
        if not line.startswith(DIAGNOSTIC_STDOUT_PREFIX):
            continue
        encoded = line[len(DIAGNOSTIC_STDOUT_PREFIX) :]
        if not encoded or encoded.strip() != encoded:
            raise ValueError("diagnostic stdout record whitespace is not exact")
        try:
            value = json.loads(encoded, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("diagnostic stdout JSON is invalid or non-finite") from exc
        if not isinstance(value, dict) or set(value) != set(_FIELDS):
            raise ValueError("diagnostic stdout schema is not exact")
        records.append(value)
    if len(records) != len(expected):
        raise ValueError(
            f"diagnostic stdout has duplicate or missing records; expected exactly one per sample, got {len(records)}"
        )
    parsed: dict[str, dict[str, int | str]] = {}
    order: list[str] = []
    for value in records:
        sample = value.get("sample_id")
        if not isinstance(sample, str) or sample not in expected or sample in parsed:
            raise ValueError("diagnostic stdout sample is missing, unknown, or duplicated")
        schema = value.get("schema_version")
        if isinstance(schema, bool) or schema != _SCHEMA_VERSION:
            raise ValueError("diagnostic stdout schema version is unsupported")
        converted: dict[str, int | str] = {"sample_id": sample, "schema_version": int(schema)}
        for field_name in _FIELDS:
            if field_name in {"sample_id", "schema_version"}:
                continue
            item = value.get(field_name)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"diagnostic stdout field {field_name} must be finite and non-negative")
            converted[field_name] = int(item)
        parsed[sample] = converted
        order.append(sample)
    if tuple(order) != expected:
        raise ValueError("diagnostic stdout sample order does not match selected samples")
    return parsed


def _derive_trace_payload(payload: bytes) -> tuple[bytes, str, str]:
    """Add only per-frame checkpoint counts to the pinned trace source."""

    source_hash = _sha256(payload)
    text = payload.decode("utf-8")
    marker = "_BIOHUB_RECIPE_C_PER_FRAME_COUNTS"
    if marker in text:
        return payload, source_hash, source_hash
    helper = (
        "\n\n"
        "# _BIOHUB_RECIPE_C_PER_FRAME_COUNTS\n"
        "def _biohub_per_frame_node_counts(nodes):\n"
        "    counts = {}\n"
        "    for node in nodes.values():\n"
        "        frame = str(int(node[\"t\"]))\n"
        "        counts[frame] = counts.get(frame, 0) + 1\n"
        "    return {frame: counts[frame] for frame in sorted(counts, key=int)}\n"
        "\n"
    )
    function_marker = "def filter_output_graph_traced("
    if text.count(function_marker) != 1:
        raise ValueError("pinned stage trace function is missing or duplicated")
    text = text.replace(function_marker, helper + function_marker, 1)
    pattern = re.compile(r"(?m)^(?P<indent>[ \t]+)(?P<quote>[\"'])n_nodes(?P=quote): len\(nodes\),$")
    match = pattern.search(text)
    if match is not None:
        indent = match.group("indent")
        line = (
            f"{indent}{match.group('quote')}per_frame_node_counts{match.group('quote')}: "
            "_biohub_per_frame_node_counts(nodes),\n"
        )
        text = text[: match.end()] + "\n" + line + text[match.end() :]
    else:
        inline = re.search(r"(?P<quote>[\"'])n_nodes(?P=quote)\s*:\s*len\(nodes\)", text)
        if inline is None:
            raise ValueError("pinned stage trace has no checkpoint node count")
        quote = inline.group("quote")
        text = (
            text[: inline.end()]
            + f", {quote}per_frame_node_counts{quote}: _biohub_per_frame_node_counts(nodes)"
            + text[inline.end() :]
        )
    derived = text.encode("utf-8")
    return derived, source_hash, _sha256(derived)


def _json_safe(value: object, *, label: str = "value") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, label=f"{label}.{key}") for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, label=f"{label}[{index}]") for index, item in enumerate(value)]
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item(), label=label)
    raise ValueError(f"{label} is not JSON-safe")


def _per_frame_node_counts(nodes: Mapping[int, Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes.values():
        frame = str(int(node["t"]))
        counts[frame] = counts.get(frame, 0) + 1
    return {frame: counts[frame] for frame in sorted(counts, key=int)}


def _fallback_graph_counts(structural_counts: Mapping[str, object]) -> dict[str, object]:
    return {
        "nodes": int(structural_counts.get("nodes", 0)),
        "edges": int(structural_counts.get("edges", 0)),
        "forks": int(structural_counts.get("forks", 0)),
        "per_frame_node_counts": {},
        "canonical_sha256": None,
    }


def _graph_records(path: Path) -> tuple[dict[int, dict[str, object]], list[dict[str, object]]]:
    import tracksdata as td

    loaded = td.graph.IndexedRXGraph.from_geff(Path(path))
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    nodes: dict[int, dict[str, object]] = {}
    for row in graph.node_attrs().iter_rows(named=True):
        node_id = int(row["node_id"])
        nodes[node_id] = {
            "node_id": node_id,
            "t": int(row["t"]),
            "z": float(row["z"]),
            "y": float(row["y"]),
            "x": float(row["x"]),
        }
    edges: list[dict[str, object]] = []
    for row in graph.edge_attrs().iter_rows(named=True):
        probability = row.get("edge_prob") if hasattr(row, "get") else None
        edges.append(
            {
                "source_id": int(row["source_id"]),
                "target_id": int(row["target_id"]),
                "edge_prob": None if probability is None else float(probability),
            }
        )
    return nodes, edges


def _graph_signature(
    nodes: Mapping[int, Mapping[str, object]], edges: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    return {
        "nodes": {
            int(node_id): tuple(int(float(node[field])) for field in ("t", "z", "y", "x"))
            for node_id, node in nodes.items()
        },
        "edges": sorted((int(edge["source_id"]), int(edge["target_id"])) for edge in edges),
    }


def _graph_counts(
    nodes: Mapping[int, Mapping[str, object]], edges: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    outgoing: dict[int, int] = {}
    for edge in edges:
        source = int(edge["source_id"])
        outgoing[source] = outgoing.get(source, 0) + 1
    signature = _graph_signature(nodes, edges)
    canonical_sha256 = _sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":"), default=list).encode()
    )
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "forks": sum(count == 2 for count in outgoing.values()),
        "per_frame_node_counts": _per_frame_node_counts(nodes),
        "canonical_sha256": canonical_sha256,
    }


def _csv_signatures(
    path: Path, sample_ids: Sequence[str]
) -> dict[str, tuple[dict[int, dict[str, object]], list[dict[str, object]]]]:
    expected = tuple(sample_ids)
    by_sample = {sample: ({}, []) for sample in expected}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _CSV_HEADER:
            raise ValueError("production CSV header is not exact")
        rows = list(reader)
    for expected_id, row in enumerate(rows):
        if row.get("id") != str(expected_id):
            raise ValueError("production CSV row IDs are not contiguous")
        sample = row.get("dataset")
        if sample not in by_sample:
            raise ValueError("production CSV contains an unknown sample")
        nodes, edges = by_sample[sample]
        if row.get("row_type") == "node":
            node_id = int(row["node_id"])
            if node_id in nodes:
                raise ValueError("production CSV node IDs are duplicated")
            nodes[node_id] = {
                "node_id": node_id,
                "t": int(row["t"]),
                "z": int(row["z"]),
                "y": int(row["y"]),
                "x": int(row["x"]),
            }
        elif row.get("row_type") == "edge":
            edges.append({"source_id": int(row["source_id"]), "target_id": int(row["target_id"])})
        else:
            raise ValueError("production CSV row type is invalid")
    for sample, (nodes, edges) in by_sample.items():
        if sorted(nodes) != list(range(len(nodes))) or not nodes:
            raise ValueError(f"production CSV node IDs are not contiguous for {sample}")
        _graph_signature(nodes, edges)
    return by_sample


def _prepare_trace_adapter(source_api: Any, destination: Path) -> tuple[Any, str | None, str | None, bytes | None]:
    trace_function = getattr(source_api, "trace_filter_output_graph", None)
    trace_path = getattr(source_api, "trace_module_path", None)
    if not callable(trace_function) or trace_path is None:
        return None, None, None, None
    source_payload = Path(trace_path).read_bytes()
    derived_payload, source_hash, derived_hash = _derive_trace_payload(source_payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(derived_payload)
    py_compile.compile(str(destination), doraise=True)
    if derived_hash == source_hash:
        return trace_function, source_hash, derived_hash, derived_payload
    module_name = f"_biohub_recipe_c_trace_{time.monotonic_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, destination)
    if spec is None or spec.loader is None:
        raise ImportError("unable to load the derived stage trace module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    derived_function = getattr(module, "filter_output_graph_traced", None)
    if not callable(derived_function):
        raise ValueError("derived stage trace function is missing")
    return derived_function, source_hash, derived_hash, derived_payload


def _trace_graph(
    trace_function: Any, nodes: dict[int, dict[str, object]], edges: list[dict[str, object]], *, dataset: str
) -> tuple[dict[int, dict[str, object]], list[dict[str, object]], dict[str, object]]:
    traced_nodes, traced_edges, stats, trace = trace_function(nodes, edges, dataset=dataset)
    if (
        not isinstance(traced_nodes, dict)
        or not isinstance(traced_edges, list)
        or not isinstance(stats, Mapping)
        or not isinstance(trace, Mapping)
    ):
        raise ValueError("source stage trace returned invalid diagnostics")
    snapshots = trace.get("stage_snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError("source stage trace did not return stage snapshots")
    safe_snapshots: list[dict[str, object]] = []
    stage_names: list[str] = []
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, Mapping):
            raise ValueError(f"source stage trace snapshot {index} is invalid")
        safe = _json_safe(snapshot, label=f"stage_snapshots[{index}]")
        if not isinstance(safe, dict) or "per_frame_node_counts" not in safe:
            raise ValueError("source stage trace snapshot lacks per-frame node counts")
        stage = safe.get("stage")
        if not isinstance(stage, str):
            raise ValueError(f"source stage trace snapshot {index} has no stage name")
        per_frame = safe["per_frame_node_counts"]
        if not isinstance(per_frame, Mapping) or any(
            not isinstance(frame, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for frame, count in per_frame.items()
        ):
            raise ValueError(f"source stage trace snapshot {index} has invalid per-frame counts")
        stage_names.append(stage)
        safe_snapshots.append(safe)
    missing = [stage for stage in _TRACE_STAGES if stage not in stage_names]
    duplicate = [stage for stage in _TRACE_STAGES if stage_names.count(stage) != 1]
    if missing or duplicate:
        raise ValueError(
            "source stage trace boundaries are incomplete or duplicated: "
            f"missing={missing!r}, duplicate={duplicate!r}"
        )
    safe_trace = _json_safe(trace, label="trace")
    safe_stats = _json_safe(stats, label="stats")
    if not isinstance(safe_trace, dict) or not isinstance(safe_stats, dict):
        raise ValueError("source stage trace JSON conversion failed")
    safe_trace["stage_snapshots"] = safe_snapshots
    safe_trace["final_counts"] = _graph_counts(traced_nodes, traced_edges)
    return traced_nodes, traced_edges, {"stats": safe_stats, "trace": safe_trace}


__all__ = [
    "DIAGNOSTIC_STDOUT_PREFIX",
    "DiagnosticRecorder",
    "_apply_predictor_diagnostic_instrumentation",
    "_csv_signatures",
    "_derive_trace_payload",
    "_graph_counts",
    "_graph_records",
    "_graph_signature",
    "_json_safe",
    "_parse_child_diagnostic_stdout",
    "_prepare_trace_adapter",
    "_sha256_file",
    "_trace_graph",
]
