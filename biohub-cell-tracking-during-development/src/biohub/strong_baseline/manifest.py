"""Persisted prediction manifest creation and pre-GT validation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HASH_ALGORITHM = "sha256(sorted relative file path + NUL + file bytes + NUL)"


def _prediction_graph_counts(path: Path) -> tuple[int, int]:
    import tracksdata

    loaded = tracksdata.graph.IndexedRXGraph.from_geff(path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    return int(graph.num_nodes()), int(graph.num_edges())


def prediction_directory_manifest(path: Path) -> dict[str, Any]:
    """Return deterministic file/hash and structural counts for a GEFF dir."""

    path = Path(path)
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = child.relative_to(path).as_posix().encode()
        payload = child.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
        files += 1
        total_bytes += len(payload)
    nodes, edges = _prediction_graph_counts(path)
    return {
        "prediction_path": str(path),
        "directory_sha256": digest.hexdigest(),
        "hash_algorithm": HASH_ALGORITHM,
        "files": files,
        "total_bytes": total_bytes,
        "nodes": nodes,
        "edges": edges,
        "structural_reload": "tracksdata.graph.IndexedRXGraph.from_geff succeeded",
    }


def write_prediction_manifest(path: Path, payload: dict[str, Any]) -> Path:
    """Write a compact manifest beside ``path`` after prediction creation."""

    path = Path(path)
    manifest_path = path.parent / "prediction_manifest.json"
    payload = dict(payload)
    payload.setdefault("manifest_created_at", datetime.now(UTC).isoformat())
    payload.setdefault(
        "manifest_action",
        "created automatically after prediction GEFF structural reload",
    )
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return manifest_path


def validate_prediction_manifest(path: Path) -> dict[str, Any]:
    """Validate a persisted manifest and return its receipt before GT access."""

    path = Path(path)
    manifest_path = path.parent / "prediction_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"prediction manifest is missing: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"prediction manifest is unreadable: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"prediction manifest must contain an object: {manifest_path}")

    recorded_path = payload.get("prediction_path")
    if not isinstance(recorded_path, str):
        raise ValueError(f"prediction manifest is missing prediction_path: {manifest_path}")
    if Path(recorded_path).resolve() != path.resolve():
        raise ValueError(
            f"prediction manifest path mismatch: expected {path}, got {recorded_path}",
        )

    expected = prediction_directory_manifest(path)
    for key in ("directory_sha256", "files", "total_bytes", "nodes", "edges"):
        if payload.get(key) != expected[key]:
            raise ValueError(
                f"prediction manifest {key} mismatch: expected {expected[key]!r}, "
                f"got {payload.get(key)!r}",
            )
    return {
        "manifest_path": str(manifest_path),
        "prediction_path": str(path),
        "directory_sha256": expected["directory_sha256"],
        "files": expected["files"],
        "total_bytes": expected["total_bytes"],
        "nodes": expected["nodes"],
        "edges": expected["edges"],
        "manifest_created_at": payload.get("manifest_created_at"),
        "manifest_action": payload.get("manifest_action"),
        "validated_at": datetime.now(UTC).isoformat(),
        "validation_action": "validated persisted prediction manifest before opening ground truth",
    }


__all__ = [
    "HASH_ALGORITHM",
    "prediction_directory_manifest",
    "validate_prediction_manifest",
    "write_prediction_manifest",
]
