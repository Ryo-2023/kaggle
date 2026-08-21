"""Low-RSS read-only view over a READY detector-fixed cache.

The canonical cache is Codex's digest-checked ``nodes.npz`` +
``candidate_edges.npz`` pair.  Expanding the edge NPZ whole costs roughly a
gigabyte of resident memory for the 7,240,938-candidate development sample,
which is more than the shared container can spare while a detector run is in
flight.  This module therefore

* reuses Codex's manifest, hash and ground-truth-free checks verbatim
  (imported, not re-derived, so integrity semantics cannot drift);
* materialises **only the six columns a scoring rule needs** into ``.npy``
  sidecars under the caller's own artifacts directory, one column at a time;
* hands back ``numpy.memmap`` views so per-frame-pair slices never pull the
  whole array into RAM.

The read-only Codex worktree is never written to: sidecars go wherever the
caller points ``sidecar_root``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Integrity helpers are imported rather than re-implemented so that a research
# replay accepts exactly the caches the published race accepts.
from biohub.detector_fixed_race.cache import (
    _cache_hash,
    _manifest_artifact_names,
    _normalise_manifest,
    _validate_manifest_ground_truth_free,
    _verify_artifact_digests,
)

SCORING_EDGE_COLUMNS: tuple[str, ...] = (
    "source_node_id",
    "target_node_id",
    "forward_logit",
    "reverse_logit",
    "forward_probability",
    "physical_distance",
)
"""The only edge columns a scoring rule reads.

Deliberately excludes ``voxel_delta``/``physical_delta`` (each ``(E, 3)``
float32, ~87 MB on the development sample) and the remaining scalar columns.
"""

_CODEX_MMAP_DIR = "candidate_edges.mmap"
"""Sidecar directory name Codex's ``build_edge_memory_map`` publishes."""


@dataclass(frozen=True, slots=True)
class LeanNodes:
    """Just the node fields the association path needs."""

    tzyx: np.ndarray

    @property
    def length(self) -> int:
        return int(self.tzyx.shape[0])


@dataclass(frozen=True, slots=True)
class LeanCache:
    """Memmap-backed, ground-truth-free view of one detector cache."""

    root: Path
    manifest: Mapping[str, Any]
    cache_hash: str
    nodes: LeanNodes
    columns: Mapping[str, np.ndarray]

    @property
    def edge_count(self) -> int:
        return int(self.columns["source_node_id"].shape[0])

    def column(self, name: str) -> np.ndarray:
        try:
            return self.columns[name]
        except KeyError as exc:
            raise KeyError(f"edge column {name!r} was not materialised") from exc


def _read_manifest(root: Path) -> tuple[Mapping[str, Any], str]:
    root = Path(root)
    ready_path = root / "READY"
    manifest_path = root / "manifest.json"
    if not ready_path.is_file():
        raise ValueError(f"detector cache is not READY: {root}")
    if not manifest_path.is_file():
        raise ValueError(f"detector cache manifest is missing: {root}")
    manifest = _normalise_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    _validate_manifest_ground_truth_free(manifest)
    declared = manifest.get("cache_hash")
    if not isinstance(declared, str) or _cache_hash(manifest) != declared:
        raise ValueError("detector cache manifest hash is invalid")
    ready_text = ready_path.read_text(encoding="utf-8").strip()
    if ready_text and ready_text != declared:
        raise ValueError("READY marker cache_hash mismatch")
    _verify_artifact_digests(root, manifest)
    return manifest, declared


def _existing_sidecar(root: Path, columns: Sequence[str]) -> dict[str, np.ndarray] | None:
    """Reuse Codex's published memory-map sidecar when the cache has one."""

    sidecar = Path(root) / _CODEX_MMAP_DIR
    if not (sidecar / "manifest.json").is_file():
        return None
    loaded: dict[str, np.ndarray] = {}
    for name in columns:
        path = sidecar / f"{name}.npy"
        if not path.is_file():
            return None
        loaded[name] = np.load(path, mmap_mode="r")
    return loaded


def _materialise_columns(
    root: Path,
    edges_file: str,
    cache_hash: str,
    sidecar_root: Path,
    columns: Sequence[str],
) -> dict[str, np.ndarray]:
    destination = Path(sidecar_root) / cache_hash
    destination.mkdir(parents=True, exist_ok=True)
    missing = [name for name in columns if not (destination / f"{name}.npy").is_file()]
    if missing:
        # One member at a time: the NPZ reader decompresses a single array per
        # iteration, so peak RSS is one column, not the whole edge table.
        with np.load(Path(root) / edges_file, allow_pickle=False) as payload:
            for name in missing:
                array = payload[name]
                temporary = destination / f".{name}.npy.tmp"
                np.save(temporary, array)
                temporary.replace(destination / f"{name}.npy")
                del array
    return {name: np.load(destination / f"{name}.npy", mmap_mode="r") for name in columns}


def open_lean_cache(
    root: Path,
    *,
    sidecar_root: Path,
    columns: Sequence[str] = SCORING_EDGE_COLUMNS,
) -> LeanCache:
    """Open a READY cache with memmap-backed edge columns.

    ``sidecar_root`` must be inside the caller's own writable tree.  Sidecars
    are keyed by ``cache_hash`` so two different caches never collide and a
    re-run is free.
    """

    root = Path(root)
    manifest, cache_hash = _read_manifest(root)
    nodes_file, edges_file = _manifest_artifact_names(manifest)
    with np.load(root / nodes_file, allow_pickle=False) as payload:
        tzyx = np.array(payload["tzyx"])
    edge_columns = _existing_sidecar(root, columns)
    if edge_columns is None:
        edge_columns = _materialise_columns(root, edges_file, cache_hash, Path(sidecar_root), columns)

    declared_nodes = manifest.get("node_count")
    if declared_nodes is not None and int(declared_nodes) != int(tzyx.shape[0]):
        raise ValueError("manifest node_count does not match the nodes artifact")
    declared_edges = manifest.get("edge_count")
    length = int(edge_columns[columns[0]].shape[0])
    if declared_edges is not None and int(declared_edges) != length:
        raise ValueError("manifest edge_count does not match the candidate edge artifact")
    for name, array in edge_columns.items():
        if int(array.shape[0]) != length:
            raise ValueError(f"edge column {name} has an inconsistent length")

    return LeanCache(
        root=root,
        manifest=manifest,
        cache_hash=cache_hash,
        nodes=LeanNodes(tzyx=tzyx),
        columns=edge_columns,
    )


__all__ = [
    "SCORING_EDGE_COLUMNS",
    "LeanCache",
    "LeanNodes",
    "open_lean_cache",
]
