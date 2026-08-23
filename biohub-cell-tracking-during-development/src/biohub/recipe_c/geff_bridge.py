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
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
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
_RESERVED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "prediction_role",
        "prediction_path",
        "prediction_name",
        "selection_lock_id",
        "ground_truth_included",
        "ground_truth_inputs",
        "manifest_created_at",
        "directory_sha256",
        "files",
        "total_bytes",
        "hash_algorithm",
        "nodes",
        "edges",
        "forks",
    }
)


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    text = str(value)
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
        if not bucket["nodes"]:
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


def _rename_noreplace_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """Publish by directory descriptors without replacing a final entry."""

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
        source_parent_fd,
        os.fsencode(source_name),
        destination_parent_fd,
        os.fsencode(destination_name),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination_name)


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Publish a fresh directory without ever replacing a final entry."""

    source = Path(source)
    destination = Path(destination)
    _rename_noreplace_at(
        _AT_FDCWD,
        str(source),
        _AT_FDCWD,
        str(destination),
    )


def publish_directory_noreplace(source: Path, destination: Path) -> None:
    """Publish an already-fsynced directory without replacing a final entry."""

    _rename_noreplace(Path(source), Path(destination))


def publish_file_noreplace(source: Path, destination: Path) -> None:
    """Publish one regular file by exclusive hard-link, then remove its owner name."""

    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError("file publish source must be a regular file")
    source_stat = source.lstat()
    source_identity = (source_stat.st_dev, source_stat.st_ino)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.link(source, destination, follow_symlinks=False)
    try:
        _fsync_directory(destination.parent)
    except BaseException:
        try:
            destination_stat = destination.lstat()
            if (destination_stat.st_dev, destination_stat.st_ino) == source_identity:
                destination.unlink()
                _fsync_directory(destination.parent)
        except BaseException:
            pass
        raise
    try:
        source.unlink()
        _fsync_directory(source.parent)
    except BaseException:
        try:
            destination_stat = destination.lstat()
            if (destination_stat.st_dev, destination_stat.st_ino) == source_identity:
                destination.unlink()
                _fsync_directory(destination.parent)
        except BaseException:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def fsync_directory(path: Path) -> None:
    """Fsync one directory entry set for callers publishing sibling artifacts."""

    _fsync_directory(Path(path))


def _entry_identity(path: Path) -> tuple[int, int]:
    stat_result = Path(path).lstat()
    return stat_result.st_dev, stat_result.st_ino


def _open_directory_fd(
    path: Path, *, expected_identity: tuple[int, int] | None = None
) -> tuple[int, tuple[int, int]]:
    """Open a directory without following a symlink and retain its identity."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"directory anchor is not a directory: {path}")
        identity = metadata.st_dev, metadata.st_ino
        if expected_identity is not None and identity != expected_identity:
            raise OSError(f"directory anchor identity changed: {path}")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _entry_identity_at(parent_descriptor: int, name: str) -> tuple[int, int]:
    metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino


def _open_directory_fd_at(
    parent_descriptor: int,
    name: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, tuple[int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"directory anchor is not a directory: {name}")
        identity = metadata.st_dev, metadata.st_ino
        if expected_identity is not None and identity != expected_identity:
            raise OSError(f"directory anchor identity changed: {name}")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _fd_identity(descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("directory anchor descriptor is no longer a directory")
    return metadata.st_dev, metadata.st_ino


def _remove_owned_directory(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    try:
        current = Path(path).lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != identity:
        return
    if stat.S_ISDIR(current.st_mode):
        shutil.rmtree(path)


def _remove_owned_empty_directory(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    try:
        current = Path(path).lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != identity or not stat.S_ISDIR(current.st_mode):
        return
    try:
        Path(path).rmdir()
    except OSError:
        pass


def _assert_owned_final(path: Path, identity: tuple[int, int], *, stage: str) -> None:
    try:
        metadata = Path(path).lstat()
    except OSError as exc:
        raise OSError(f"final GEFF identity changed {stage}") from exc
    current = metadata.st_dev, metadata.st_ino
    if current != identity or not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"final GEFF identity changed {stage}")


def _assert_owned_final_at(
    parent_descriptor: int, name: str, identity: tuple[int, int], *, stage: str
) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise OSError(f"final GEFF identity changed {stage}") from exc
    current = metadata.st_dev, metadata.st_ino
    if current != identity or not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"final GEFF identity changed {stage}")


def _assert_owned_root(
    path: Path,
    descriptor: int,
    identity: tuple[int, int],
    *,
    stage: str,
) -> None:
    try:
        fd_identity = _fd_identity(descriptor)
        path_identity = _entry_identity(path)
    except OSError as exc:
        raise OSError(f"prediction output root identity changed {stage}") from exc
    if fd_identity != identity or path_identity != identity:
        raise OSError(f"prediction output root identity changed {stage}")


def _cleanup_owned_build(
    parent: Path,
    parent_identity: tuple[int, int] | None,
    child: Path,
    child_identity: tuple[int, int] | None,
) -> None:
    """Remove only a build child whose private parent is still ours.

    The parent identity is the ownership anchor.  If a caller or competitor
    replaced it, even a child with the expected name must not be removed.
    The child identity is captured at exclusive creation, so an unclaimed
    replacement is never restatted and deleted during cleanup.
    """

    if parent_identity is None:
        return
    try:
        current_parent = _entry_identity(parent)
    except OSError:
        return
    if current_parent != parent_identity:
        return
    if child_identity is not None:
        _remove_owned_directory(child, child_identity)
    _remove_owned_empty_directory(parent, parent_identity)


def _build_geff(destination: Any, bucket: Mapping[str, Any], *, overwrite: bool = False) -> None:
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
    graph.to_geff(destination, overwrite=overwrite, zarr_format=2)


def postprocessed_csv_to_geffs(
    csv_path: Path,
    output_root: Path,
    *,
    sample_ids: Sequence[str],
    provenance: Mapping[str, object],
    on_published: Callable[[Path, tuple[int, int]], None] | None = None,
) -> dict[str, Path]:
    """Convert one strict CSV into fresh, no-clobber GEFF directories."""

    csv_path = Path(csv_path)
    output_root = Path(output_root)
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"prediction output root must be fresh: {output_root.name}")
    output_root.mkdir(parents=True, exist_ok=False)
    output_root_identity = _entry_identity(output_root)
    output_root_descriptor: int | None = None
    output_root_fd_identity: tuple[int, int] | None = None
    written: dict[str, Path] = {}
    published: dict[Path, tuple[int, int]] = {}
    try:
        output_root_descriptor, output_root_fd_identity = _open_directory_fd(
            output_root, expected_identity=output_root_identity
        )
        if on_published is not None:
            on_published(output_root, output_root_identity)
        parsed = _parse_submission(csv_path, sample_ids, None)
        for sample_id in sample_ids:
            if output_root_descriptor is None or output_root_fd_identity is None:
                raise OSError("prediction output root descriptor is unavailable")
            _assert_owned_root(
                output_root,
                output_root_descriptor,
                output_root_fd_identity,
                stage="during publication",
            )
            final = output_root / f"{sample_id}.geff"
            if final.exists() or final.is_symlink():
                raise FileExistsError(final)
            build_parent = output_root / f".{sample_id}.geff.build-{secrets.token_hex(8)}.tmp"
            temporary = build_parent / f"{sample_id}.geff"
            build_parent_identity: tuple[int, int] | None = None
            temporary_identity: tuple[int, int] | None = None
            build_parent_descriptor: int | None = None
            temporary_descriptor: int | None = None
            final_identity: tuple[int, int] | None = None
            try:
                if output_root_descriptor is None:
                    raise OSError("prediction output root descriptor is unavailable")
                os.mkdir(build_parent.name, 0o700, dir_fd=output_root_descriptor)
                build_parent_identity = _entry_identity_at(output_root_descriptor, build_parent.name)
                build_parent_descriptor, _ = _open_directory_fd_at(
                    output_root_descriptor,
                    build_parent.name,
                    expected_identity=build_parent_identity,
                )
                os.fchmod(build_parent_descriptor, 0o700)
                os.mkdir(temporary.name, 0o700, dir_fd=build_parent_descriptor)
                temporary_identity = _entry_identity_at(build_parent_descriptor, temporary.name)
                temporary_descriptor, _ = _open_directory_fd_at(
                    build_parent_descriptor,
                    temporary.name,
                    expected_identity=temporary_identity,
                )
                from zarr.storage import LocalStore

                # Keep the pre-owned child open while tracksdata/geff writes.
                # LocalStore(/proc/self/fd/N) cannot follow a replacement of
                # the named child or its parent.
                _build_geff(
                    LocalStore(Path(f"/proc/self/fd/{temporary_descriptor}")),
                    parsed[sample_id],
                    overwrite=False,
                )
                if _fd_identity(temporary_descriptor) != temporary_identity:
                    raise OSError("temporary GEFF identity changed during serialization")
                temporary_stat = os.stat(
                    temporary.name,
                    dir_fd=build_parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    temporary_stat.st_dev,
                    temporary_stat.st_ino,
                ) != temporary_identity or not stat.S_ISDIR(temporary_stat.st_mode):
                    raise OSError("temporary GEFF identity changed before publication")
                if _fd_identity(build_parent_descriptor) != build_parent_identity:
                    raise OSError("GEFF build parent identity changed during serialization")
                if _entry_identity_at(build_parent_descriptor, ".") != build_parent_identity:
                    raise OSError("GEFF build parent identity changed before publication")
                if output_root_descriptor is None or output_root_fd_identity is None:
                    raise OSError("prediction output root descriptor is unavailable")
                _assert_owned_root(
                    output_root,
                    output_root_descriptor,
                    output_root_fd_identity,
                    stage="during publication",
                )
                _rename_noreplace_at(
                    build_parent_descriptor,
                    temporary.name,
                    output_root_descriptor,
                    final.name,
                )
                if _fd_identity(temporary_descriptor) != temporary_identity:
                    raise OSError("temporary GEFF identity changed during publish")
                published_identity = _entry_identity_at(output_root_descriptor, final.name)
                _assert_owned_final_at(
                    output_root_descriptor,
                    final.name,
                    temporary_identity,
                    stage="during publish",
                )
                final_identity = published_identity
                published[final] = final_identity
                if on_published is not None:
                    on_published(final, final_identity)
                _assert_owned_final_at(output_root_descriptor, final.name, final_identity, stage="after callback")
                _assert_owned_final(final, final_identity, stage="after callback")
                if output_root_descriptor is None or output_root_fd_identity is None:
                    raise OSError("prediction output root descriptor is unavailable")
                _assert_owned_root(
                    output_root,
                    output_root_descriptor,
                    output_root_fd_identity,
                    stage="after publication",
                )
                if temporary_descriptor is None:
                    raise OSError("temporary GEFF descriptor is unavailable")
                _fsync_directory_fd(output_root_descriptor)
                _assert_owned_final_at(output_root_descriptor, final.name, final_identity, stage="after fsync")
                _assert_owned_final(final, final_identity, stage="after fsync")
                _assert_owned_root(
                    output_root,
                    output_root_descriptor,
                    output_root_fd_identity,
                    stage="after fsync",
                )
                signature = _read_prediction_signature(
                    LocalStore(Path(f"/proc/self/fd/{temporary_descriptor}"))
                )
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
                _assert_owned_final_at(output_root_descriptor, final.name, final_identity, stage="after roundtrip")
                _assert_owned_final(final, final_identity, stage="after roundtrip")
                _assert_owned_root(
                    output_root,
                    output_root_descriptor,
                    output_root_fd_identity,
                    stage="after roundtrip",
                )
            except BaseException:
                _cleanup_owned_build(build_parent, build_parent_identity, temporary, temporary_identity)
                _remove_owned_directory(final, final_identity)
                published.pop(final, None)
                raise
            finally:
                if temporary_descriptor is not None:
                    os.close(temporary_descriptor)
                if build_parent_descriptor is not None:
                    os.close(build_parent_descriptor)
            _cleanup_owned_build(build_parent, build_parent_identity, temporary, temporary_identity)
            written[sample_id] = final
    except BaseException:
        # Only remove directories created by this invocation; never touch an
        # existing output root or a sibling prediction.
        for path, identity in published.items():
            _remove_owned_directory(path, identity)
        _remove_owned_empty_directory(output_root, output_root_identity)
        raise
    finally:
        if output_root_descriptor is not None:
            os.close(output_root_descriptor)
    return written


def _read_prediction_signature(path: Any) -> dict[str, object]:
    import tracksdata as td

    loaded = td.graph.IndexedRXGraph.from_geff(path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    nodes = list(graph.node_attrs().iter_rows(named=True))
    if not nodes:
        raise ValueError("prediction GEFF contains no nodes")
    node_signature: dict[int, tuple[int, int, int, int]] = {}
    for row in nodes:
        node_id = int(row["node_id"])
        if node_id < 0:
            raise ValueError("prediction node IDs must be non-negative")
        if node_id in node_signature:
            raise ValueError("prediction node IDs are duplicated")
        node_signature[node_id] = tuple(int(row[field]) for field in ("t", "z", "y", "x"))
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


def write_json_exclusive(path: Path, payload: Mapping[str, object], *, mode: int = 0o600) -> None:
    path = Path(path)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    published_identity: tuple[int, int] | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        created = os.fstat(descriptor)
        temporary_identity = (created.st_dev, created.st_ino)
        encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short manifest write")
            offset += written
        os.fsync(descriptor)
        stat_result = os.fstat(descriptor)
        if stat_result.st_size != len(encoded):
            raise OSError("short manifest write")
        if (stat_result.st_dev, stat_result.st_ino) != temporary_identity:
            raise OSError("receipt temporary identity changed")
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        published_identity = temporary_identity
        _fsync_directory(path.parent)
        temporary_stat = temporary.lstat()
        if temporary_identity is None or (
            temporary_stat.st_dev,
            temporary_stat.st_ino,
        ) != temporary_identity:
            raise OSError("receipt temporary identity changed")
        temporary.unlink()
        _fsync_directory(path.parent)
    except BaseException as exc:
        if descriptor is not None:
            os.close(descriptor)
        if published_identity is not None:
            try:
                final_stat = path.lstat()
                if (final_stat.st_dev, final_stat.st_ino) == published_identity:
                    path.unlink()
                    _fsync_directory(path.parent)
            except FileNotFoundError:
                pass
            except BaseException as cleanup_error:
                exc.add_note(f"receipt final cleanup failed: {type(cleanup_error).__name__}")
        try:
            temporary_stat = temporary.lstat()
            if temporary_identity is not None and (
                temporary_stat.st_dev,
                temporary_stat.st_ino,
            ) == temporary_identity:
                temporary.unlink()
        except FileNotFoundError:
            pass
        except BaseException as cleanup_error:
            exc.add_note(f"receipt temporary cleanup failed: {type(cleanup_error).__name__}")
        raise


def _write_manifest_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    write_json_exclusive(path, payload)


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
    collisions = sorted({str(key) for key in provenance if str(key) in _RESERVED_MANIFEST_KEYS})
    if collisions:
        raise ValueError(f"provenance contains reserved manifest keys: {', '.join(collisions)}")
    payload.update(_safe_provenance(provenance))
    _write_manifest_exclusive(manifest_path, payload)
    return manifest_path


__all__ = [
    "CSV_HEADER",
    "fsync_directory",
    "postprocessed_csv_to_geffs",
    "publish_directory_noreplace",
    "publish_file_noreplace",
    "validate_prediction_geff",
    "write_json_exclusive",
    "write_prediction_manifest",
]
