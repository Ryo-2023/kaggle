"""Strict, ground-truth-free Recipe C submission-to-GEFF bridge.

The source recipe writes a deliberately small CSV.  This module is the boundary
that turns that CSV into one independently reloadable GEFF per sample and writes
the per-GEFF persistence manifest used by the existing GT guard.  It does not
open, enumerate, or import any ground-truth data.
"""

from __future__ import annotations

import csv
import ctypes
import ctypes.util
import errno
import json
import os
import secrets
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.gt_guard import prediction_manifest_path

CSV_HEADER = (
    "id",
    "dataset",
    "row_type",
    "node_id",
    "t",
    "z",
    "y",
    "x",
    "source_id",
    "target_id",
)

_RENAME_NOREPLACE = 1
_AT_FDCWD = -100
_MAX_PROVENANCE_KEY_LENGTH = 64


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    text = str(value).strip()
    if not text or text[0] == "+" or (text[0] == "-" and len(text) == 1):
        raise ValueError(f"{field} must be an integer")
    if text[0] == "-":
        digits = text[1:]
    else:
        digits = text
    if not digits.isdigit():
        raise ValueError(f"{field} must be an integer")
    return int(text)


def _safe_provenance(provenance: Mapping[str, object]) -> dict[str, object]:
    """Keep only scalar, non-sensitive provenance labels in the manifest."""

    safe: dict[str, object] = {}
    forbidden = ("ground_truth", "groundtruth", "credential", "password", "secret", "token")
    forbidden_path = ("path", "metric", "score")
    for raw_key, value in provenance.items():
        key = str(raw_key)
        lowered = key.lower()
        if not key or len(key) > _MAX_PROVENANCE_KEY_LENGTH:
            continue
        if any(part in lowered for part in forbidden + forbidden_path):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _parse_submission(
    csv_path: Path,
    sample_ids: Sequence[str],
    expected_volume_shape_tzyx: Mapping[str, Sequence[int]] | Sequence[int] | None,
) -> dict[str, dict[str, Any]]:
    expected = tuple(str(item) for item in sample_ids)
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("sample_ids must be non-empty and unique")
    by_dataset: dict[str, dict[str, Any]] = {
        sample: {"nodes": {}, "edges": []} for sample in expected
    }
    with Path(csv_path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_HEADER:
            raise ValueError(f"submission header must be exactly {CSV_HEADER!r}")
        rows = list(reader)
    if not rows:
        raise ValueError("submission is empty")
    for expected_id, row in enumerate(rows):
        if None in row:
            raise ValueError("submission contains too many columns")
        row_id = _strict_int(row.get("id"), field="id")
        if row_id != expected_id:
            raise ValueError("row id must be contiguous from zero")
        dataset = row.get("dataset")
        if dataset not in by_dataset:
            raise ValueError(f"unknown dataset {dataset!r}")
        row_type = row.get("row_type")
        bucket = by_dataset[dataset]
        if row_type == "node":
            node_id = _strict_int(row.get("node_id"), field="node_id")
            if node_id < 0 or node_id in bucket["nodes"]:
                raise ValueError("node_id must be unique and non-negative")
            if any(_strict_int(row.get(field), field=field) != -1 for field in ("source_id", "target_id")):
                raise ValueError("node edge fields must use -1 sentinel")
            values = {
                field: _strict_int(row.get(field), field=field)
                for field in ("t", "z", "y", "x")
            }
            if any(value < 0 for value in values.values()):
                raise ValueError("node coordinates must be non-negative")
            shape: Sequence[int] | None
            if isinstance(expected_volume_shape_tzyx, Mapping):
                shape = expected_volume_shape_tzyx.get(dataset)
            else:
                shape = expected_volume_shape_tzyx
            if shape is not None and len(shape) == 4 and any(
                values[field] >= int(limit) for field, limit in zip(("t", "z", "y", "x"), shape, strict=True)
            ):
                raise ValueError("node coordinate is outside the expected volume")
            bucket["nodes"][node_id] = values
        elif row_type == "edge":
            if any(_strict_int(row.get(field), field=field) != -1 for field in ("node_id", "t", "z", "y", "x")):
                raise ValueError("edge node fields must use -1 sentinel")
            source_id = _strict_int(row.get("source_id"), field="source_id")
            target_id = _strict_int(row.get("target_id"), field="target_id")
            if source_id < 0 or target_id < 0 or source_id == target_id:
                raise ValueError("edge endpoints must be distinct non-negative node IDs")
            bucket["edges"].append((source_id, target_id))
        else:
            raise ValueError(f"unknown row_type {row_type!r}")
    parsed: dict[str, dict[str, Any]] = {}
    for dataset in expected:
        bucket = by_dataset[dataset]
        node_ids = sorted(bucket["nodes"])
        if node_ids != list(range(len(node_ids))):
            raise ValueError("node IDs must be contiguous from zero")
        if not node_ids:
            raise ValueError(f"dataset {dataset!r} has no nodes")
        frame_by_id = {node_id: int(attrs["t"]) for node_id, attrs in bucket["nodes"].items()}
        incoming: defaultdict[int, int] = defaultdict(int)
        outgoing: defaultdict[int, int] = defaultdict(int)
        seen_edges: set[tuple[int, int]] = set()
        for source_id, target_id in bucket["edges"]:
            if source_id not in frame_by_id or target_id not in frame_by_id:
                raise ValueError("edge endpoint does not refer to a node")
            if (source_id, target_id) in seen_edges:
                raise ValueError("duplicate edge")
            seen_edges.add((source_id, target_id))
            if frame_by_id[target_id] != frame_by_id[source_id] + 1:
                raise ValueError("edges must connect adjacent frames")
            incoming[target_id] += 1
            outgoing[source_id] += 1
            if incoming[target_id] > 1:
                raise ValueError("indegree must be at most one")
            if outgoing[source_id] > 2:
                raise ValueError("outdegree must be at most two")
        parsed[dataset] = bucket
    return parsed


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Publish a fresh directory without ever replacing a final entry."""

    source = Path(source)
    destination = Path(destination)
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(str(source)),
        _AT_FDCWD,
        os.fsencode(str(destination)),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_geff(destination: Path, bucket: Mapping[str, Any]) -> None:
    import polars as pl
    import tracksdata as td

    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, dtype=pl.Int64, default_value=0)
    for node_id in sorted(bucket["nodes"]):
        attrs = bucket["nodes"][node_id]
        assigned = graph.add_node(
            {"t": int(attrs["t"]), "z": int(attrs["z"]), "y": int(attrs["y"]), "x": int(attrs["x"])},
            index=int(node_id),
        )
        if assigned != node_id:
            raise RuntimeError("tracksdata assigned a non-deterministic node ID")
    for source_id, target_id in bucket["edges"]:
        graph.add_edge(int(source_id), int(target_id), {})
    graph.to_geff(destination, overwrite=False)


def postprocessed_csv_to_geffs(
    csv_path: Path,
    output_root: Path,
    *,
    sample_ids: Sequence[str],
    provenance: Mapping[str, object],
) -> dict[str, Path]:
    """Convert one strict CSV into fresh, no-clobber GEFF directories."""

    csv_path = Path(csv_path)
    output_root = Path(output_root)
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"prediction output root must be fresh: {output_root.name}")
    output_root.mkdir(parents=True, exist_ok=False)
    parsed = _parse_submission(csv_path, sample_ids, None)
    written: dict[str, Path] = {}
    try:
        for sample_id in sample_ids:
            final = output_root / f"{sample_id}.geff"
            if final.exists() or final.is_symlink():
                raise FileExistsError(final)
            temporary = output_root / f".{sample_id}.geff.{secrets.token_hex(8)}.tmp"
            published = False
            try:
                _build_geff(temporary, parsed[sample_id])
                _rename_noreplace(temporary, final)
                published = True
                _fsync_directory(output_root)
                signature = _read_prediction_signature(final)
                expected_signature = {
                    "nodes": {
                        int(node_id): (
                            int(attrs["t"]),
                            int(attrs["z"]),
                            int(attrs["y"]),
                            int(attrs["x"]),
                        )
                        for node_id, attrs in parsed[sample_id]["nodes"].items()
                    },
                    "edges": sorted(tuple(map(int, edge)) for edge in parsed[sample_id]["edges"]),
                }
                signature["edges"] = sorted(signature["edges"])
                if signature != expected_signature:
                    raise ValueError("GEFF roundtrip is not lossless for CSV topology or coordinates")
            except BaseException:
                if temporary.exists() and not temporary.is_symlink():
                    shutil.rmtree(temporary)
                if published and final.is_dir() and not final.is_symlink():
                    shutil.rmtree(final)
                raise
            written[sample_id] = final
    except BaseException:
        # Only remove directories created by this invocation; never touch an
        # existing output root or a sibling prediction.
        for path in written.values():
            if path.exists() and path.is_dir():
                shutil.rmtree(path)
        raise
    return written


def _read_prediction_signature(path: Path) -> dict[str, object]:
    import tracksdata as td

    loaded = td.graph.IndexedRXGraph.from_geff(path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    nodes = list(graph.node_attrs().iter_rows(named=True))
    if not nodes:
        raise ValueError("prediction GEFF contains no nodes")
    node_signature: dict[int, tuple[int, int, int, int]] = {}
    for row in nodes:
        node_id = int(row["node_id"])
        if node_id in node_signature:
            raise ValueError("prediction node IDs are duplicated")
        node_signature[node_id] = tuple(int(row[field]) for field in ("t", "z", "y", "x"))
    if sorted(node_signature) != list(range(len(node_signature))):
        raise ValueError("prediction node IDs must be contiguous from zero")
    edge_signature: list[tuple[int, int]] = []
    seen_edges: set[tuple[int, int]] = set()
    for row in list(graph.edge_attrs().iter_rows(named=True)):
        edge = (int(row["source_id"]), int(row["target_id"]))
        if edge in seen_edges:
            raise ValueError("prediction edges are duplicated")
        seen_edges.add(edge)
        edge_signature.append(edge)
    return {"nodes": node_signature, "edges": edge_signature}


def validate_prediction_geff(
    path: Path,
    sample_id: str,
    *,
    expected_volume_shape_tzyx: Sequence[int] | None,
) -> dict[str, int]:
    """Reload and structurally validate a prediction GEFF without GT access."""

    path = Path(path)
    if path.name != f"{sample_id}.geff" or not path.is_dir() or path.is_symlink():
        raise ValueError(f"prediction GEFF path is not the expected regular directory: {path}")
    signature = _read_prediction_signature(path)
    nodes = signature["nodes"]
    edges = signature["edges"]
    node_frames: dict[int, int] = {}
    for node_id, values in nodes.items():
        node_frames[int(node_id)] = int(values[0])
        for field in ("t", "z", "y", "x"):
            value = int(values[("t", "z", "y", "x").index(field)])
            if value < 0:
                raise ValueError("prediction coordinates must be non-negative")
        if expected_volume_shape_tzyx is not None and any(
            value >= int(limit)
            for value, limit in zip(values, expected_volume_shape_tzyx, strict=True)
        ):
            raise ValueError("prediction coordinate is outside the expected volume")
    incoming: defaultdict[int, int] = defaultdict(int)
    outgoing: defaultdict[int, int] = defaultdict(int)
    for source, target in edges:
        if source not in node_frames or target not in node_frames:
            raise ValueError("prediction edge endpoint is missing")
        if node_frames[target] != node_frames[source] + 1:
            raise ValueError("prediction edges must connect adjacent frames")
        incoming[target] += 1
        outgoing[source] += 1
        if incoming[target] > 1 or outgoing[source] > 2:
            raise ValueError("prediction graph degree constraint is violated")
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "forks": sum(1 for count in outgoing.values() if count == 2),
    }


def _write_manifest_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError("short manifest write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_prediction_manifest(
    prediction_path: Path,
    *,
    selection_lock_id: str,
    provenance: Mapping[str, object],
) -> Path:
    """Write the final per-GEFF manifest with only role-relative references."""

    prediction_path = Path(prediction_path)
    manifest_path = prediction_manifest_path(prediction_path)
    counts = validate_prediction_geff(prediction_path, prediction_path.stem, expected_volume_shape_tzyx=None)
    report = directory_digest_report(prediction_path)
    payload: dict[str, object] = {
        "schema_version": 1,
        "prediction_role": "recipe_c_prediction",
        "prediction_path": prediction_path.name,
        "prediction_name": prediction_path.name,
        "selection_lock_id": selection_lock_id,
        "ground_truth_included": False,
        "ground_truth_inputs": [],
        "manifest_created_at": datetime.now(UTC).isoformat(),
        "directory_sha256": report["directory_sha256"],
        "files": report["files"],
        "total_bytes": report["total_bytes"],
        "hash_algorithm": report["hash_algorithm"],
        **counts,
    }
    payload.update(_safe_provenance(provenance))
    _write_manifest_exclusive(manifest_path, payload)
    return manifest_path


__all__ = [
    "CSV_HEADER",
    "postprocessed_csv_to_geffs",
    "validate_prediction_geff",
    "write_prediction_manifest",
]
