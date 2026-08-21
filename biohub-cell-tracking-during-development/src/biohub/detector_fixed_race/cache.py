"""Atomic, ground-truth-free persistence for detector-fixed cache arrays."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from biohub.benchmark_race.contracts import (
    SampleSpec,
    _contains_ground_truth,
    _normalise_json_value,
)
from biohub.detector_fixed_race.schema import (
    EDGE_ARRAY_NAMES,
    EDGE_ARRAY_SCHEMA,
    NODE_ARRAY_NAMES,
    NODE_ARRAY_SCHEMA,
    CacheReceipt,
    CandidateEdgeArrays,
    DetectorCache,
    NodeArrays,
)

DETECTOR_CACHE_SCHEMA_VERSION = "detector_fixed.cache.v1"
# Keep the short name available to callers that use the existing benchmark
# cache naming convention.
CACHE_SCHEMA_VERSION = DETECTOR_CACHE_SCHEMA_VERSION
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NODES_FILE = "nodes.npz"
_EDGES_FILE = "candidate_edges.npz"
_EDGE_MMAP_DIR = "candidate_edges.mmap"
_EDGE_MMAP_SCHEMA_VERSION = "detector_fixed.cache_mmap.v1"


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    result = value.strip()
    if _contains_ground_truth(result):
        raise ValueError(f"{name} must not reference ground truth")
    return result


def _canonical_json(payload: Mapping[str, Any]) -> str:
    normalised = _normalise_json_value(payload)
    if not isinstance(normalised, dict):  # pragma: no cover - Mapping is checked by callers
        raise TypeError("canonical JSON payload must be a mapping")
    return json.dumps(
        normalised,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _cache_hash(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("cache_hash", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _normalise_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")
    normalised = _normalise_json_value(manifest)
    if not isinstance(normalised, dict):  # pragma: no cover - Mapping check above
        raise TypeError("manifest must be a mapping")
    return normalised


def _validate_manifest_ground_truth_free(manifest: Mapping[str, Any]) -> None:
    # ``ground_truth_included`` is the one required field whose name contains
    # a GT marker.  Its false value is an invariant, not a reference.
    without_flag = dict(manifest)
    without_flag.pop("ground_truth_included", None)
    if _contains_ground_truth(without_flag):
        raise ValueError(
            "detector cache manifest must not contain ground truth, annotation, label, or .geff references"
        )
    if manifest.get("ground_truth_included") is not False:
        raise ValueError("detector cache manifest ground_truth_included must be false")


def _safe_artifact_name(name: str, *, field: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{field} must be a non-empty relative path")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a relative artifact path")
    if _contains_ground_truth(path):
        raise ValueError(f"{field} must not contain a ground-truth or .geff path component")
    return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    np.savez_compressed(path, **arrays)


def _node_arrays_dict(nodes: NodeArrays) -> dict[str, np.ndarray]:
    return {name: getattr(nodes, name) for name in NODE_ARRAY_NAMES}


def _edge_arrays_dict(edges: CandidateEdgeArrays) -> dict[str, np.ndarray]:
    return {name: getattr(edges, name) for name in EDGE_ARRAY_NAMES}


def _load_npz(path: Path, *, kind: str) -> dict[str, np.ndarray]:
    expected = set(NODE_ARRAY_NAMES if kind == "nodes" else EDGE_ARRAY_NAMES)
    try:
        with np.load(path, allow_pickle=False) as payload:
            actual = set(payload.files)
            missing = expected - actual
            extra = actual - expected
            if missing:
                raise ValueError(f"{kind} artifact is missing arrays: {sorted(missing)}")
            if extra:
                raise ValueError(f"{kind} artifact contains unexpected arrays: {sorted(extra)}")
            return {name: np.array(payload[name], copy=True) for name in expected}
    except ValueError:
        raise
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f"could not read {kind} artifact {path}: {exc}") from exc


def _validate_npz_streaming(path: Path, arrays: Mapping[str, np.ndarray], *, kind: str) -> None:
    """Validate serialized array names/shapes without loading the whole NPZ.

    Dense detector caches can contain tens of millions of candidate rows.  A
    full ``dict(np.load(...))`` round-trip would temporarily duplicate every
    edge column and can exceed the memory available to the CPU container.  The
    source arrays have already passed schema validation; here we read one
    serialized column at a time and verify its structural/finite invariants.
    """

    expected = set(arrays)
    try:
        with np.load(path, allow_pickle=False) as payload:
            actual = set(payload.files)
            missing = expected - actual
            extra = actual - expected
            if missing:
                raise ValueError(f"{kind} artifact is missing arrays: {sorted(missing)}")
            if extra:
                raise ValueError(f"{kind} artifact contains unexpected arrays: {sorted(extra)}")
            for name, expected_array in arrays.items():
                loaded = payload[name]
                if loaded.dtype != expected_array.dtype:
                    raise ValueError(
                        f"{kind} artifact array {name} has dtype {loaded.dtype}, expected {expected_array.dtype}"
                    )
                if loaded.shape != expected_array.shape:
                    raise ValueError(
                        f"{kind} artifact array {name} has shape {loaded.shape}, expected {expected_array.shape}"
                    )
                if np.issubdtype(loaded.dtype, np.floating) and not np.isfinite(loaded).all():
                    raise ValueError(f"{kind} artifact array {name} contains non-finite values")
                del loaded
    except ValueError:
        raise
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f"could not read {kind} artifact {path}: {exc}") from exc


def build_edge_memory_map(root: Path) -> Path:
    """Create columnar ``.npy`` sidecars for low-RSS association replay.

    The canonical cache remains the digest-checked NPZ pair.  The sidecar is a
    deterministic, ground-truth-free derivative that lets ``load_detector_cache``
    use ``numpy.memmap`` instead of expanding a dense compressed NPZ into RAM.
    """

    root = Path(root)
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise ValueError("detector cache must be READY before building memory-map sidecars")
    try:
        manifest = _normalise_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read detector cache manifest: {exc}") from exc
    _validate_manifest_ground_truth_free(manifest)
    declared_hash = manifest.get("cache_hash")
    if not _is_sha256(declared_hash) or _cache_hash(manifest) != declared_hash:
        raise ValueError("detector cache manifest hash is invalid")
    _verify_artifact_digests(root, manifest)
    _, edges_file = _manifest_artifact_names(manifest)
    edges_path = root / edges_file
    temporary_root = root / f".{_EDGE_MMAP_DIR}.tmp-{os.getpid()}"
    sidecar_root = root / _EDGE_MMAP_DIR
    if temporary_root.exists():
        shutil.rmtree(temporary_root)
    temporary_root.mkdir(parents=False, exist_ok=False)
    published = False
    try:
        with np.load(edges_path, allow_pickle=False) as payload:
            for name in EDGE_ARRAY_NAMES:
                source = payload[name]
                target_path = temporary_root / f"{name}.npy"
                target = np.lib.format.open_memmap(
                    target_path,
                    mode="w+",
                    dtype=source.dtype,
                    shape=source.shape,
                )
                chunk_size = 1_000_000
                for start in range(0, source.shape[0], chunk_size):
                    end = min(start + chunk_size, source.shape[0])
                    target[start:end] = source[start:end]
                target.flush()
                del target, source
        metadata = {
            "schema_version": _EDGE_MMAP_SCHEMA_VERSION,
            "source_cache_hash": declared_hash,
            "edge_count": int(manifest.get("edge_count", 0)),
            "arrays": {
                name: {
                    "dtype": str(np.load(temporary_root / f"{name}.npy", mmap_mode="r").dtype),
                    "shape": list(np.load(temporary_root / f"{name}.npy", mmap_mode="r").shape),
                }
                for name in EDGE_ARRAY_NAMES
            },
        }
        (temporary_root / "manifest.json").write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if sidecar_root.exists():
            shutil.rmtree(sidecar_root)
        os.replace(temporary_root, sidecar_root)
        published = True
    finally:
        if not published and temporary_root.exists():
            shutil.rmtree(temporary_root)
    return sidecar_root


def _load_edge_memory_map(root: Path, manifest: Mapping[str, Any]) -> CandidateEdgeArrays | None:
    sidecar_root = Path(root) / _EDGE_MMAP_DIR
    metadata_path = sidecar_root / "manifest.json"
    if not sidecar_root.is_dir() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read edge memory-map manifest: {exc}") from exc
    if metadata.get("schema_version") != _EDGE_MMAP_SCHEMA_VERSION:
        raise ValueError("unsupported edge memory-map schema")
    if metadata.get("source_cache_hash") != manifest.get("cache_hash"):
        raise ValueError("edge memory-map source cache hash mismatch")
    arrays: dict[str, np.ndarray] = {}
    declared_arrays = metadata.get("arrays")
    if not isinstance(declared_arrays, Mapping):
        raise ValueError("edge memory-map manifest arrays are missing")
    for name in EDGE_ARRAY_NAMES:
        path = sidecar_root / f"{name}.npy"
        if not path.is_file():
            raise ValueError(f"edge memory-map array is missing: {name}")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        declared = declared_arrays.get(name)
        if not isinstance(declared, Mapping):
            raise ValueError(f"edge memory-map metadata is missing: {name}")
        if str(array.dtype) != declared.get("dtype") or list(array.shape) != declared.get("shape"):
            raise ValueError(f"edge memory-map metadata mismatch: {name}")
        arrays[name] = array
    return CandidateEdgeArrays(**arrays)


def _verify_artifact_digests(root: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    artifacts = manifest.get("artifact_digests")
    if not isinstance(artifacts, Mapping):
        raise ValueError("manifest must contain artifact_digests")
    nodes_file = _safe_artifact_name(str(manifest.get("nodes_file", _NODES_FILE)), field="nodes_file")
    edges_file = _safe_artifact_name(
        str(manifest.get("candidate_edges_file", _EDGES_FILE)), field="candidate_edges_file"
    )
    required = {nodes_file, edges_file}
    if not required.issubset(artifacts):
        raise ValueError("manifest artifact_digests must cover nodes and candidate edges")
    if len(required) != 2:
        raise ValueError("nodes_file and candidate_edges_file must be different")

    checked: dict[str, str] = {}
    for name, declared in artifacts.items():
        safe_name = _safe_artifact_name(name, field="artifact_digests path")
        if safe_name != name:
            raise ValueError("artifact_digests paths must be normalized relative paths")
        if not _is_sha256(declared):
            raise ValueError(f"artifact digest for {name} must be a lowercase SHA-256")
        path = root / safe_name
        if not path.is_file():
            raise ValueError(f"artifact is missing: {safe_name}")
        actual = _sha256_file(path)
        if actual != declared:
            raise ValueError(f"artifact digest mismatch for {safe_name}")
        checked[safe_name] = actual
    return checked


def _manifest_artifact_names(manifest: Mapping[str, Any]) -> tuple[str, str]:
    nodes_file = _safe_artifact_name(str(manifest.get("nodes_file", _NODES_FILE)), field="nodes_file")
    edges_file = _safe_artifact_name(
        str(manifest.get("candidate_edges_file", _EDGES_FILE)), field="candidate_edges_file"
    )
    if nodes_file == edges_file:
        raise ValueError("nodes_file and candidate_edges_file must be different")
    return nodes_file, edges_file


def build_detector_cache_manifest(
    sample: SampleSpec,
    image_sha256: str,
    detector_config: Mapping[str, Any],
    provenance: Mapping[str, Any],
    node_digest: str,
    edge_digest: str,
) -> dict[str, Any]:
    """Build a deterministic, GT-free detector cache manifest.

    ``node_digest`` and ``edge_digest`` are accepted as opaque caller-provided
    digests during manifest construction.  The cache writer replaces them with
    the SHA-256 digests of the serialized artifacts and recomputes
    ``cache_hash`` after the bytes have been written.
    """

    if not isinstance(sample, SampleSpec):
        raise TypeError("sample must be a SampleSpec")
    if not isinstance(detector_config, Mapping):
        raise TypeError("detector_config must be a mapping")
    if not isinstance(provenance, Mapping):
        raise TypeError("provenance must be a mapping")
    if _contains_ground_truth(detector_config):
        raise ValueError("detector_config must not contain a ground-truth reference")
    if _contains_ground_truth(provenance):
        raise ValueError("provenance must not contain a ground-truth reference")

    image_sha256 = _require_text("image_sha256", image_sha256)
    node_digest = _require_text("node_digest", node_digest)
    edge_digest = _require_text("edge_digest", edge_digest)
    normalised_config = _normalise_json_value(detector_config)
    normalised_provenance = _normalise_json_value(provenance)

    manifest: dict[str, Any] = {
        "schema_version": DETECTOR_CACHE_SCHEMA_VERSION,
        "sample_id": sample.sample_id,
        "image_stem": sample.image_stem.as_posix(),
        "shape": list(sample.shape),
        "scale": list(sample.scale),
        "image_sha256": image_sha256,
        "detector_config": normalised_config,
        "provenance": normalised_provenance,
        "detector_id": normalised_provenance.get("detector_id"),
        "source_repo": normalised_provenance.get("source_repo"),
        "source_commit": normalised_provenance.get("source_commit"),
        "checkpoint_uri": normalised_provenance.get("checkpoint_uri"),
        "checkpoint_sha256": normalised_provenance.get("checkpoint_sha256"),
        "nodes_file": _NODES_FILE,
        "candidate_edges_file": _EDGES_FILE,
        "node_digest": node_digest,
        "edge_digest": edge_digest,
        "artifact_digests": {_NODES_FILE: node_digest, _EDGES_FILE: edge_digest},
        "array_schema": {"nodes": NODE_ARRAY_SCHEMA, "candidate_edges": EDGE_ARRAY_SCHEMA},
        "ground_truth_included": False,
    }
    manifest["cache_hash"] = _cache_hash(manifest)
    return manifest


def write_detector_cache(
    root: Path,
    manifest: Mapping[str, Any],
    nodes: NodeArrays,
    edges: CandidateEdgeArrays,
) -> CacheReceipt:
    """Serialize and atomically publish a validated detector cache."""

    root = Path(root)
    if _contains_ground_truth(root):
        raise ValueError("cache root must not contain a ground-truth or .geff path component")
    if not isinstance(nodes, NodeArrays):
        raise TypeError("nodes must be NodeArrays")
    if not isinstance(edges, CandidateEdgeArrays):
        raise TypeError("edges must be CandidateEdgeArrays")
    nodes.validate()
    edges.validate(nodes)

    manifest_data = _normalise_manifest(manifest)
    if "ground_truth_included" not in manifest_data:
        manifest_data["ground_truth_included"] = False
    _validate_manifest_ground_truth_free(manifest_data)
    nodes_file, edges_file = _manifest_artifact_names(manifest_data)
    declared_artifacts = manifest_data.get("artifact_digests", {})
    if not isinstance(declared_artifacts, Mapping):
        raise ValueError("manifest artifact_digests must be a mapping")
    unsupported = set(declared_artifacts) - {nodes_file, edges_file}
    if unsupported:
        raise ValueError(f"manifest contains unsupported artifact digests: {sorted(unsupported)}")

    if root.exists():
        raise FileExistsError(f"cache root already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(f"{root}.tmp-{os.getpid()}")
    if temporary_root.exists():
        shutil.rmtree(temporary_root)
    temporary_root.mkdir(parents=False, exist_ok=False)
    published = False
    try:
        nodes_path = temporary_root / nodes_file
        edges_path = temporary_root / edges_file
        _write_npz(nodes_path, _node_arrays_dict(nodes))
        _write_npz(edges_path, _edge_arrays_dict(edges))
        actual_digests = {
            nodes_file: _sha256_file(nodes_path),
            edges_file: _sha256_file(edges_path),
        }
        for name, declared in declared_artifacts.items():
            if _is_sha256(declared) and declared != actual_digests[name]:
                raise ValueError(f"artifact digest mismatch for {name}")

        manifest_data["nodes_file"] = nodes_file
        manifest_data["candidate_edges_file"] = edges_file
        manifest_data["node_digest"] = actual_digests[nodes_file]
        manifest_data["edge_digest"] = actual_digests[edges_file]
        manifest_data["artifact_digests"] = actual_digests
        manifest_data["node_count"] = nodes.length
        manifest_data["edge_count"] = edges.length
        manifest_data["array_schema"] = {"nodes": NODE_ARRAY_SCHEMA, "candidate_edges": EDGE_ARRAY_SCHEMA}
        manifest_data["ground_truth_included"] = False
        manifest_data["cache_hash"] = _cache_hash(manifest_data)

        # Round-trip through the actual files before publishing READY.  For a
        # dense detector cache, validate one serialized column at a time to
        # avoid duplicating all edge arrays in RAM.
        node_arrays = _node_arrays_dict(nodes)
        edge_arrays = _edge_arrays_dict(edges)
        serialized_nbytes = sum(array.nbytes for array in (*node_arrays.values(), *edge_arrays.values()))
        if serialized_nbytes > 512 * 1024 * 1024:
            _validate_npz_streaming(nodes_path, node_arrays, kind="nodes")
            _validate_npz_streaming(edges_path, edge_arrays, kind="candidate_edges")
        else:
            loaded_nodes = NodeArrays(**_load_npz(nodes_path, kind="nodes"))
            loaded_edges = CandidateEdgeArrays(**_load_npz(edges_path, kind="candidate_edges"))
            loaded_nodes.validate()
            loaded_edges.validate(loaded_nodes)
        _verify_artifact_digests(temporary_root, manifest_data)

        manifest_path = temporary_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_data, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (temporary_root / "READY").write_text(f"{manifest_data['cache_hash']}\n", encoding="utf-8")
        os.replace(temporary_root, root)
        published = True
    finally:
        if not published and temporary_root.exists():
            shutil.rmtree(temporary_root)

    return CacheReceipt(
        root=root,
        cache_hash=manifest_data["cache_hash"],
        manifest_path=root / "manifest.json",
        nodes_path=root / nodes_file,
        candidate_edges_path=root / edges_file,
    )


def load_detector_cache(root: Path) -> DetectorCache:
    """Load a cache only after marker, manifest, digest, and schema checks."""

    root = Path(root)
    if _contains_ground_truth(root):
        raise ValueError("cache root must not contain a ground-truth or .geff path component")
    ready_path = root / "READY"
    if not ready_path.is_file():
        raise ValueError("detector cache is not READY")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("detector cache manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read detector cache manifest: {exc}") from exc
    manifest = _normalise_manifest(manifest)
    _validate_manifest_ground_truth_free(manifest)

    declared_hash = manifest.get("cache_hash")
    if not _is_sha256(declared_hash):
        raise ValueError("manifest cache_hash must be a lowercase SHA-256")
    if _cache_hash(manifest) != declared_hash:
        raise ValueError("manifest cache_hash mismatch")
    try:
        ready_text = ready_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"could not read READY marker: {exc}") from exc
    if ready_text and ready_text != declared_hash:
        raise ValueError("READY marker cache_hash mismatch")

    _verify_artifact_digests(root, manifest)
    nodes_file, edges_file = _manifest_artifact_names(manifest)
    nodes = NodeArrays(**_load_npz(root / nodes_file, kind="nodes"))
    edges = _load_edge_memory_map(root, manifest)
    if edges is None:
        edges = CandidateEdgeArrays(**_load_npz(root / edges_file, kind="candidate_edges"))
    nodes.validate()
    edges.validate(nodes)

    if "node_count" in manifest and manifest["node_count"] != nodes.length:
        raise ValueError("manifest node_count does not match nodes artifact")
    if "edge_count" in manifest and manifest["edge_count"] != edges.length:
        raise ValueError("manifest edge_count does not match candidate edge artifact")
    artifacts = manifest["artifact_digests"]
    if manifest.get("node_digest") != artifacts[nodes_file] or manifest.get("edge_digest") != artifacts[edges_file]:
        raise ValueError("manifest node/edge digest fields do not match artifact_digests")

    return DetectorCache(root=root, manifest=manifest, nodes=nodes, edges=edges)


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DETECTOR_CACHE_SCHEMA_VERSION",
    "build_detector_cache_manifest",
    "build_edge_memory_map",
    "load_detector_cache",
    "write_detector_cache",
]
