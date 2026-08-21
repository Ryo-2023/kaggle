"""Instrumented, one-pass capture of the pinned official detector outputs.

The upstream checkout is loaded as-is.  The adapter temporarily wraps only the
detector peak helper and ``predict_edges`` so that the original forward path
still produces its normal return value.  Reverse logits are computed after the
image/video pass from captured node features; no second UNet encode is made.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import torch

from biohub.benchmark_race.contracts import SampleSpec, _contains_ground_truth, _normalise_json_value
from biohub.detector_fixed_race.cache import build_detector_cache_manifest, write_detector_cache
from biohub.detector_fixed_race.schema import CacheReceipt, CandidateEdgeArrays, NodeArrays
from biohub.device import resolve_torch_device

PINNED_UPSTREAM_COMMIT = "075fc5f5a52d11077f9dc2b074644618f26939e2"
UPSTREAM_REPOSITORY = "https://github.com/royerlab/kaggle-cell-tracking-competition.git"


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Inference values that affect detector and raw edge outputs."""

    det_threshold: float = 0.99
    det_tta: bool = True
    pool_kernel_um: float = 3.0
    edge_activation: str = "softmax"
    threshold: float = 0.5
    unet_batch_size: int = 4

    def __post_init__(self) -> None:
        for name in ("det_threshold", "pool_kernel_um", "threshold"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.det_threshold <= 1.0:
            raise ValueError("det_threshold must be in [0, 1]")
        if self.pool_kernel_um <= 0.0:
            raise ValueError("pool_kernel_um must be positive")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        if self.edge_activation not in {"softmax", "sigmoid"}:
            raise ValueError("edge_activation must be softmax or sigmoid")
        if (
            isinstance(self.unet_batch_size, bool)
            or not isinstance(self.unet_batch_size, int)
            or self.unet_batch_size < 1
        ):
            raise ValueError("unet_batch_size must be a positive integer")


@dataclass(slots=True)
class _NodeCapture:
    t: int
    downsampled_tzyx: np.ndarray
    peak_logits: np.ndarray


@dataclass(slots=True)
class _PairCapture:
    source_t: int | None
    source_coords: np.ndarray
    target_coords: np.ndarray
    source_features: np.ndarray
    target_features: np.ndarray
    source_positions: np.ndarray
    target_positions: np.ndarray
    source_mask: np.ndarray
    target_mask: np.ndarray
    forward_logits: np.ndarray
    reverse_logits: np.ndarray | None = None
    storage_path: Path | None = None
    reverse_storage_path: Path | None = None


@dataclass(slots=True)
class _CaptureState:
    nodes_by_t: dict[int, _NodeCapture]
    pairs: list[_PairCapture]
    pair_store_root: Path | None = None
    detector_calls: int = 0
    forward_edge_calls: int = 0
    reverse_edge_calls: int = 0


def _load_upstream_predictor(upstream_root: Path) -> ModuleType:
    predictor_path = upstream_root / "scripts" / "predict_unet_transformer.py"
    if not predictor_path.is_file():
        raise FileNotFoundError(f"pinned upstream predictor is missing: {predictor_path}")
    old_path = list(sys.path)
    sys.path[:0] = [str(predictor_path.parent), str(upstream_root / "src")]
    module_name = f"_biohub_detector_fixed_upstream_{time.monotonic_ns()}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, predictor_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to import upstream predictor: {predictor_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_path(path: Path) -> str:
    """Hash a file or a directory deterministically, including relative names."""

    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise FileNotFoundError(f"image/checkpoint path is missing: {path}")
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(item)))
    return digest.hexdigest()


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _source_hash() -> str:
    return _sha256_file(Path(__file__))


def _as_numpy(value: torch.Tensor, *, name: str) -> np.ndarray:
    if not isinstance(value, torch.Tensor):
        raise TypeError(f"{name} must be a torch.Tensor")
    return value.detach().cpu().numpy().copy()


def _without_batch(value: torch.Tensor, *, name: str) -> np.ndarray:
    array = _as_numpy(value, name=name)
    if array.ndim < 1 or array.shape[0] != 1:
        raise ValueError(f"{name} must have batch dimension one, got {array.shape}")
    return array[0]


_PAIR_ARRAY_NAMES = (
    "source_coords",
    "target_coords",
    "source_features",
    "target_features",
    "source_positions",
    "target_positions",
    "source_mask",
    "target_mask",
    "forward_logits",
)


def _empty_pair_capture(pair: _PairCapture) -> None:
    """Drop large in-memory pair payloads after persisting them."""

    empty_coords = np.empty((0, 3), dtype=np.float32)
    empty_features = np.empty((0, 0), dtype=np.float32)
    empty_mask = np.empty((0,), dtype=np.bool_)
    empty_logits = np.empty((0, 0), dtype=np.float32)
    pair.source_coords = empty_coords
    pair.target_coords = empty_coords
    pair.source_features = empty_features
    pair.target_features = empty_features
    pair.source_positions = empty_features
    pair.target_positions = empty_features
    pair.source_mask = empty_mask
    pair.target_mask = empty_mask
    pair.forward_logits = empty_logits


def _persist_pair_capture(state: _CaptureState, pair: _PairCapture, index: int) -> None:
    """Store one pair payload on disk so dense videos do not retain all pairs."""

    if state.pair_store_root is None:
        return
    path = state.pair_store_root / f"pair-{index:04d}.npz"
    np.savez(
        path,
        **{name: getattr(pair, name) for name in _PAIR_ARRAY_NAMES},
    )
    pair.storage_path = path
    _empty_pair_capture(pair)


@contextmanager
def _pair_payload(
    pair: _PairCapture,
    *,
    include_forward: bool = True,
) -> Iterator[dict[str, np.ndarray]]:
    """Yield one pair's arrays, loading disk-backed payloads one at a time."""

    if pair.storage_path is None:
        payload = {name: getattr(pair, name) for name in _PAIR_ARRAY_NAMES}
        if not include_forward:
            payload["forward_logits"] = np.empty((0, 0), dtype=np.float32)
        if pair.reverse_logits is not None:
            payload["reverse_logits"] = pair.reverse_logits
        else:
            payload["reverse_logits"] = np.empty((0, 0), dtype=np.float32)
        yield payload
        return

    with np.load(pair.storage_path, allow_pickle=False) as loaded:
        payload = {
            name: loaded[name]
            for name in _PAIR_ARRAY_NAMES
            if include_forward or name != "forward_logits"
        }
        if not include_forward:
            payload["forward_logits"] = np.empty((0, 0), dtype=np.float32)
        if pair.reverse_storage_path is None:
            payload["reverse_logits"] = np.empty((0, 0), dtype=np.float32)
            yield payload
            return
        reverse = np.load(pair.reverse_storage_path, mmap_mode="r", allow_pickle=False)
        payload["reverse_logits"] = reverse
        try:
            yield payload
        finally:
            del reverse


def _peak_logits(det_logits: torch.Tensor, coords: np.ndarray) -> np.ndarray:
    array = det_logits.detach().cpu()
    if array.ndim == 4 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 3:
        raise ValueError(f"detector logits must have shape (Z,Y,X) or (1,Z,Y,X), got {tuple(array.shape)}")
    if coords.size == 0:
        return np.empty((0,), dtype=np.float32)
    spatial = coords[:, 1:].astype(np.int64, copy=False)
    if np.any(spatial < 0) or np.any(spatial >= np.asarray(array.shape, dtype=np.int64)):
        raise ValueError("detector peak coordinates are outside the detector-logit grid")
    return array[spatial[:, 0], spatial[:, 1], spatial[:, 2]].numpy().astype(np.float32, copy=False)


def _sigmoid(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=np.float32)
    result = np.empty_like(values)
    positive = values >= 0
    result[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_values = np.exp(values[~positive])
    result[~positive] = exp_values / (1.0 + exp_values)
    return result.astype(np.float32, copy=False)


def _softmax(values: np.ndarray, axis: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    shifted = array - np.max(array, axis=axis, keepdims=True)
    exp_values = np.exp(shifted)
    denominator = np.sum(exp_values, axis=axis, keepdims=True)
    return (exp_values / np.maximum(denominator, 1e-12)).astype(np.float32, copy=False)


def _build_node_arrays(
    state: _CaptureState,
    *,
    scale: tuple[float, float, float],
    downsample: tuple[int, ...],
) -> tuple[NodeArrays, dict[tuple[int, int, int, int], int], int]:
    if not state.nodes_by_t:
        raise ValueError("detector produced no node frames")
    if len(downsample) != 3:
        raise ValueError("downsample must have three spatial factors")
    spatial_scale = np.asarray(downsample, dtype=np.float32)
    rows: list[np.ndarray] = []
    peak_logits: list[np.ndarray] = []
    key_to_id: dict[tuple[int, int, int, int], int] = {}
    for t in sorted(state.nodes_by_t):
        capture = state.nodes_by_t[t]
        coords = capture.downsampled_tzyx.astype(np.int32, copy=True)
        if coords.ndim != 2 or coords.shape[1] != 4:
            raise ValueError(f"detector coordinates for t={t} must have shape (N,4)")
        coords[:, 1:] = np.rint(coords[:, 1:] * spatial_scale).astype(np.int32)
        for row in coords:
            key = tuple(int(value) for value in row)
            if key in key_to_id:
                raise ValueError(f"duplicate detector node coordinate: {key}")
            key_to_id[key] = len(key_to_id)
        rows.append(coords)
        peak_logits.append(capture.peak_logits.astype(np.float32, copy=False))
    tzyx = np.concatenate(rows, axis=0).astype(np.int32, copy=False)
    logits = np.concatenate(peak_logits, axis=0).astype(np.float32, copy=False)
    if logits.shape[0] != tzyx.shape[0]:
        raise ValueError("detector peak-logit count does not match node count")

    node_features: np.ndarray | None = None
    feature_seen = np.zeros((tzyx.shape[0],), dtype=bool)
    feature_conflict_observation_count = 0
    for pair in state.pairs:
        if pair.source_t is None:
            raise ValueError("edge pair frame assignment was not resolved")
        source_t = pair.source_t
        target_t = source_t + 1
        with _pair_payload(pair, include_forward=False) as payload:
            source_ids = _lookup_node_ids(key_to_id, source_t, payload["source_coords"])
            target_ids = _lookup_node_ids(key_to_id, target_t, payload["target_coords"])
            source_features = payload["source_features"]
            target_features = payload["target_features"]
            if node_features is None:
                if source_features.ndim != 2 or target_features.ndim != 2:
                    raise ValueError("captured node features must have shape (N,C)")
                channels = source_features.shape[1]
                if target_features.shape[1] != channels:
                    raise ValueError("source and target node feature widths differ")
                node_features = np.full((tzyx.shape[0], channels), np.nan, dtype=np.float32)
            feature_conflict_observation_count += _assign_features(
                node_features, feature_seen, source_ids, source_features
            )
            feature_conflict_observation_count += _assign_features(
                node_features, feature_seen, target_ids, target_features
            )
    if node_features is None or not np.all(feature_seen):
        missing = np.flatnonzero(~feature_seen).tolist()
        raise ValueError(f"node feature capture is incomplete; missing node IDs: {missing[:8]}")

    physical = tzyx[:, 1:].astype(np.float32) * np.asarray(scale, dtype=np.float32)
    node_ids = np.arange(tzyx.shape[0], dtype=np.int64)
    nodes = NodeArrays(
        node_id=node_ids,
        tzyx=tzyx,
        physical_zyx=physical.astype(np.float32),
        detector_peak_logit=logits,
        detector_peak_probability=_sigmoid(logits),
        node_features=node_features.astype(np.float32),
    )
    nodes.validate()
    return nodes, key_to_id, feature_conflict_observation_count


def _lookup_node_ids(
    key_to_id: Mapping[tuple[int, int, int, int], int],
    t: int,
    coords: np.ndarray,
) -> np.ndarray:
    values = np.asarray(coords, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("captured edge coordinates must have shape (N,3)")
    ids: list[int] = []
    for row in values:
        key = (int(t), *(round(float(value)) for value in row))
        try:
            ids.append(key_to_id[key])
        except KeyError as exc:
            raise ValueError(f"captured edge coordinate has no detector node: {key}") from exc
    return np.asarray(ids, dtype=np.int64)


def _assign_features(
    target: np.ndarray,
    seen: np.ndarray,
    node_ids: np.ndarray,
    features: np.ndarray,
) -> int:
    values = np.asarray(features, dtype=np.float32)
    if values.ndim != 2 or values.shape[0] != node_ids.shape[0]:
        raise ValueError("captured node feature rows do not match coordinates")
    if not np.isfinite(values).all():
        raise ValueError("captured node features must be finite")
    conflict_count = 0
    for node_id, row in zip(node_ids.tolist(), values, strict=True):
        if seen[node_id]:
            if not np.allclose(target[node_id], row, rtol=1e-5, atol=1e-5):
                conflict_count += 1
            continue
        target[node_id] = row
        seen[node_id] = True
    return conflict_count


def _resolve_pair_times(state: _CaptureState, frame_count: int) -> None:
    available = {
        t
        for t, capture in state.nodes_by_t.items()
        if capture.downsampled_tzyx.shape[0] > 0 and t + 1 in state.nodes_by_t
        and state.nodes_by_t[t + 1].downsampled_tzyx.shape[0] > 0
    }
    expected = sorted(t for t in available if 0 <= t < frame_count - 1)
    if len(state.pairs) != len(expected):
        raise ValueError(
            f"captured edge pair count {len(state.pairs)} does not match non-empty adjacent frames {len(expected)}"
        )
    for pair, source_t in zip(state.pairs, expected, strict=True):
        pair.source_t = source_t


def _build_edge_arrays(
    state: _CaptureState,
    nodes: NodeArrays,
    key_to_id: Mapping[tuple[int, int, int, int], int],
    *,
    edge_activation: str,
) -> CandidateEdgeArrays:
    # Keep one compact description per frame pair, then fill the final flat
    # arrays in-place.  The previous concatenate-based implementation held
    # (a) every pair's repeated IDs/logits and (b) the concatenated copies at
    # the same time.  On dense samples that transient duplication could push
    # the CPU container into the OOM killer even though the final cache fit.
    pair_refs: list[tuple[_PairCapture, np.ndarray, np.ndarray, int]] = []
    total_length = 0
    for pair in state.pairs:
        if pair.source_t is None:
            raise ValueError("edge capture is incomplete")
        with _pair_payload(pair) as payload:
            source_ids = _lookup_node_ids(key_to_id, pair.source_t, payload["source_coords"])
            target_ids = _lookup_node_ids(key_to_id, pair.source_t + 1, payload["target_coords"])
            forward = np.asarray(payload["forward_logits"], dtype=np.float32)
            reverse = np.asarray(payload["reverse_logits"], dtype=np.float32)
            if forward.shape != (source_ids.size, target_ids.size):
                raise ValueError(f"forward logits shape {forward.shape} does not match captured nodes")
            if reverse.shape != forward.shape:
                raise ValueError("reverse logits shape does not match forward logits")
            count = int(source_ids.size * target_ids.size)
            pair_refs.append((pair, source_ids, target_ids, count))
            total_length += count
    if total_length == 0:
        empty_i64 = np.empty((0,), dtype=np.int64)
        empty_i16 = np.empty((0,), dtype=np.int16)
        empty_f32 = np.empty((0,), dtype=np.float32)
        empty_f32_3 = np.empty((0, 3), dtype=np.float32)
        return CandidateEdgeArrays(
            source_node_id=empty_i64,
            target_node_id=empty_i64,
            delta_t=empty_i16,
            voxel_delta=empty_f32_3,
            physical_delta=empty_f32_3,
            voxel_distance=empty_f32,
            physical_distance=empty_f32,
            forward_logit=empty_f32,
            reverse_logit=empty_f32,
            forward_probability=empty_f32,
            reverse_probability=empty_f32,
        )

    def allocate_edge_array(name: str, dtype: np.dtype[Any], shape: tuple[int, ...]) -> np.ndarray:
        if state.pair_store_root is None:
            return np.empty(shape, dtype=dtype)
        path = state.pair_store_root / f"edge-{name}.mmap"
        return np.memmap(path, mode="w+", dtype=dtype, shape=shape)

    source = allocate_edge_array("source", np.dtype(np.int64), (total_length,))
    target = allocate_edge_array("target", np.dtype(np.int64), (total_length,))
    forward_logits = allocate_edge_array("forward_logits", np.dtype(np.float32), (total_length,))
    reverse_logits = allocate_edge_array("reverse_logits", np.dtype(np.float32), (total_length,))
    offset = 0
    for pair, source_ids, target_ids, count in pair_refs:
        end = offset + count
        source[offset:end] = np.repeat(source_ids, target_ids.size)
        target[offset:end] = np.tile(target_ids, source_ids.size)
        with _pair_payload(pair) as payload:
            forward_logits[offset:end] = np.asarray(payload["forward_logits"], dtype=np.float32).reshape(-1)
            reverse_logits[offset:end] = np.asarray(payload["reverse_logits"], dtype=np.float32).reshape(-1)
        offset = end

    delta_t = allocate_edge_array("delta_t", np.dtype(np.int16), (total_length,))
    voxel_delta = allocate_edge_array("voxel_delta", np.dtype(np.float32), (total_length, 3))
    physical_delta = allocate_edge_array("physical_delta", np.dtype(np.float32), (total_length, 3))
    voxel_distance = allocate_edge_array("voxel_distance", np.dtype(np.float32), (total_length,))
    physical_distance = allocate_edge_array("physical_distance", np.dtype(np.float32), (total_length,))
    chunk_length = 1_000_000
    for start in range(0, total_length, chunk_length):
        end = min(start + chunk_length, total_length)
        source_chunk = np.asarray(source[start:end], dtype=np.int64)
        target_chunk = np.asarray(target[start:end], dtype=np.int64)
        source_t = nodes.tzyx[source_chunk, 0].astype(np.int16, copy=False)
        target_t = nodes.tzyx[target_chunk, 0].astype(np.int16, copy=False)
        delta_t[start:end] = target_t - source_t
        voxel_chunk = (
            nodes.tzyx[target_chunk, 1:].astype(np.float32)
            - nodes.tzyx[source_chunk, 1:].astype(np.float32)
        )
        physical_chunk = nodes.physical_zyx[target_chunk] - nodes.physical_zyx[source_chunk]
        voxel_delta[start:end] = voxel_chunk
        physical_delta[start:end] = physical_chunk
        voxel_distance[start:end] = np.linalg.norm(voxel_chunk, axis=1).astype(np.float32, copy=False)
        physical_distance[start:end] = np.linalg.norm(physical_chunk, axis=1).astype(np.float32, copy=False)
    if edge_activation == "softmax":
        # Preserve upstream row-wise softmax per frame pair.  Do not first
        # normalize the flattened cache: that pass is both mathematically
        # discarded below and needlessly allocates temporary arrays for
        # millions of candidate pairs.
        forward_probability = allocate_edge_array("forward_probability", np.dtype(np.float32), (total_length,))
        reverse_probability = allocate_edge_array("reverse_probability", np.dtype(np.float32), (total_length,))
        offset = 0
        for pair, _source_ids, _target_ids, count in pair_refs:
            with _pair_payload(pair) as payload:
                forward_probability[offset : offset + count] = _softmax(
                    np.asarray(payload["forward_logits"], dtype=np.float32), axis=0
                ).reshape(-1)
                reverse_probability[offset : offset + count] = _softmax(
                    np.asarray(payload["reverse_logits"], dtype=np.float32), axis=0
                ).reshape(-1)
            offset += count
    elif edge_activation == "sigmoid":
        forward_probability = allocate_edge_array("forward_probability", np.dtype(np.float32), (total_length,))
        reverse_probability = allocate_edge_array("reverse_probability", np.dtype(np.float32), (total_length,))
        for start in range(0, total_length, chunk_length):
            end = min(start + chunk_length, total_length)
            forward_probability[start:end] = _sigmoid(np.asarray(forward_logits[start:end]))
            reverse_probability[start:end] = _sigmoid(np.asarray(reverse_logits[start:end]))
    else:
        raise ValueError(f"unsupported edge activation: {edge_activation}")
    return CandidateEdgeArrays(
        source_node_id=source,
        target_node_id=target,
        delta_t=delta_t,
        voxel_delta=voxel_delta,
        physical_delta=physical_delta,
        voxel_distance=voxel_distance,
        physical_distance=physical_distance,
        forward_logit=forward_logits,
        reverse_logit=reverse_logits,
        forward_probability=forward_probability,
        reverse_probability=reverse_probability,
    )


def materialize_detector_cache(
    *,
    image_path: Path,
    upstream_root: Path,
    checkpoint: Path,
    output_root: Path,
    sample: SampleSpec,
    config: CaptureConfig,
    expected_device: str | torch.device,
    max_frames: int | None = None,
) -> CacheReceipt:
    """Run the pinned detector once and publish a GT-free cache."""

    image_path = Path(image_path)
    upstream_root = Path(upstream_root)
    checkpoint = Path(checkpoint)
    output_root = Path(output_root)
    if _contains_ground_truth(image_path) or _contains_ground_truth(upstream_root):
        raise ValueError("materialization paths must not reference ground truth")
    if not image_path.exists():
        raise FileNotFoundError(f"image path is missing: {image_path}")
    if not checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint is missing: {checkpoint}")
    if max_frames is not None and (isinstance(max_frames, bool) or max_frames < 1):
        raise ValueError("max_frames must be positive when supplied")
    device = resolve_torch_device(expected_device)

    image_sha256 = _sha256_path(image_path)
    checkpoint_sha256 = _sha256_file(checkpoint)
    upstream_commit = _git_commit(upstream_root)
    if upstream_commit is not None and upstream_commit != PINNED_UPSTREAM_COMMIT:
        raise ValueError(
            f"upstream checkout commit {upstream_commit} does not match pinned {PINNED_UPSTREAM_COMMIT}"
        )
    upstream = _load_upstream_predictor(upstream_root)
    model, window_size, downsample = upstream.load_model(checkpoint, device)
    config_kwargs = {
        "det_threshold": config.det_threshold,
        "det_tta": config.det_tta,
        "pool_kernel_um": config.pool_kernel_um,
        "edge_activation": config.edge_activation,
        "threshold": config.threshold,
        "use_ilp": False,
    }
    upstream_config = upstream.PredictConfig(**config_kwargs)
    output_root.mkdir(parents=True, exist_ok=True)
    pair_store_root = output_root / f".{sample.sample_id}.pairs-{time.monotonic_ns()}"
    shutil.rmtree(pair_store_root, ignore_errors=True)
    pair_store_root.mkdir(parents=True, exist_ok=False)
    state = _CaptureState(nodes_by_t={}, pairs=[], pair_store_root=pair_store_root)
    original_detect = upstream._detect_cells_pooled
    original_predict_edges = model.predict_edges

    def capture_detect(
        det_logits: torch.Tensor,
        t: int,
        det_threshold: float = 0.5,
        pool_kernel: tuple[int, ...] = (3, 3, 3),
    ) -> np.ndarray:
        arr = original_detect(det_logits, t, det_threshold, pool_kernel)
        peak_logits = _peak_logits(det_logits, arr)
        state.detector_calls += 1
        state.nodes_by_t[int(t)] = _NodeCapture(
            t=int(t),
            downsampled_tzyx=np.asarray(arr, dtype=np.int16).copy(),
            peak_logits=peak_logits,
        )
        return arr

    def capture_predict_edges(
        feat_source: torch.Tensor,
        feat_target: torch.Tensor,
        coords_source: torch.Tensor,
        coords_target: torch.Tensor,
        pos_source: torch.Tensor,
        pos_target: torch.Tensor,
        mask_source: torch.Tensor,
        mask_target: torch.Tensor,
    ) -> torch.Tensor:
        forward = original_predict_edges(
            feat_source,
            feat_target,
            coords_source,
            coords_target,
            pos_source,
            pos_target,
            mask_source,
            mask_target,
        )
        state.forward_edge_calls += 1
        pair = _PairCapture(
            source_t=None,
            source_coords=_without_batch(coords_source, name="coords_source"),
            target_coords=_without_batch(coords_target, name="coords_target"),
            source_features=_without_batch(feat_source, name="feat_source"),
            target_features=_without_batch(feat_target, name="feat_target"),
            source_positions=_without_batch(pos_source, name="pos_source"),
            target_positions=_without_batch(pos_target, name="pos_target"),
            source_mask=_without_batch(mask_source, name="source_mask"),
            target_mask=_without_batch(mask_target, name="target_mask"),
            forward_logits=_without_batch(forward, name="forward_logits"),
        )
        state.pairs.append(pair)
        _persist_pair_capture(state, pair, len(state.pairs) - 1)
        return forward

    started = time.monotonic()
    try:
        upstream._detect_cells_pooled = capture_detect
        model.predict_edges = capture_predict_edges  # type: ignore[method-assign]
        upstream.predict_video(
            model,
            image_path,
            device,
            upstream_config,
            window_size=window_size,
            max_frames=max_frames,
            unet_batch_size=config.unet_batch_size,
            downsample=downsample,
        )
    finally:
        upstream._detect_cells_pooled = original_detect
        model.predict_edges = original_predict_edges  # type: ignore[method-assign]

    frame_count = sample.shape[0] if max_frames is None else min(sample.shape[0], max_frames)
    _resolve_pair_times(state, frame_count)
    for pair_index, pair in enumerate(state.pairs):
        if pair.source_t is None:
            raise ValueError("pair frame assignment failed")
        with _pair_payload(pair, include_forward=False) as payload, torch.no_grad():
            reverse_native = original_predict_edges(
                torch.from_numpy(payload["target_features"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["source_features"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["target_coords"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["source_coords"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["target_positions"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["source_positions"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["target_mask"]).unsqueeze(0).to(device),
                torch.from_numpy(payload["source_mask"]).unsqueeze(0).to(device),
            )
        state.reverse_edge_calls += 1
        reverse_logits = _without_batch(reverse_native, name="reverse_logits").T.astype(np.float32)
        if pair.storage_path is None:
            pair.reverse_logits = reverse_logits
        else:
            reverse_path = pair_store_root / f"reverse-{pair_index:04d}.npy"
            np.save(reverse_path, reverse_logits)
            pair.reverse_storage_path = reverse_path

    nodes, key_to_id, feature_conflict_observation_count = _build_node_arrays(
        state, scale=sample.scale, downsample=downsample
    )
    # Node features/positional tensors are only needed to build the canonical
    # node table and to compute reverse logits.  Release their per-pair copies
    # before constructing the dense flat edge arrays; retaining them here
    # needlessly increases the peak RSS on dense videos.
    empty_features = np.empty((0, 0), dtype=np.float32)
    empty_mask = np.empty((0,), dtype=np.bool_)
    for pair in state.pairs:
        pair.source_features = empty_features
        pair.target_features = empty_features
        pair.source_positions = empty_features
        pair.target_positions = empty_features
        pair.source_mask = empty_mask
        pair.target_mask = empty_mask
    edges = _build_edge_arrays(state, nodes, key_to_id, edge_activation=config.edge_activation)
    state.pairs.clear()
    provenance = {
        "detector_id": "TemporalUNet3D+SimpleNodeTransformer",
        "source_repo": UPSTREAM_REPOSITORY,
        "source_commit": upstream_commit or PINNED_UPSTREAM_COMMIT,
        "checkpoint_uri": checkpoint.name,
        "checkpoint_sha256": checkpoint_sha256,
        "requested_device": str(expected_device),
        "adapter_source_sha256": _source_hash(),
        "device": str(device),
        "detector_call_count": state.detector_calls,
        "forward_edge_call_count": state.forward_edge_calls,
        "reverse_edge_call_count": state.reverse_edge_calls,
        "node_feature_policy": "first_observation_per_node",
        "node_feature_conflict_observation_count": feature_conflict_observation_count,
        "elapsed_seconds": time.monotonic() - started,
    }
    detector_config = _normalise_json_value(
        {
            "det_threshold": config.det_threshold,
            "det_tta": config.det_tta,
            "pool_kernel_um": config.pool_kernel_um,
            "edge_activation": config.edge_activation,
            "edge_threshold": config.threshold,
            "window_size": window_size,
            "downsample": list(downsample),
            "max_frames": max_frames,
        }
    )
    manifest = build_detector_cache_manifest(
        sample,
        image_sha256=image_sha256,
        detector_config=detector_config,
        provenance=provenance,
        node_digest="pending",
        edge_digest="pending",
    )
    receipt = write_detector_cache(output_root / "cache" / sample.sample_id, manifest, nodes, edges)
    shutil.rmtree(pair_store_root, ignore_errors=True)
    return receipt


__all__ = [
    "PINNED_UPSTREAM_COMMIT",
    "UPSTREAM_REPOSITORY",
    "CaptureConfig",
    "materialize_detector_cache",
]
