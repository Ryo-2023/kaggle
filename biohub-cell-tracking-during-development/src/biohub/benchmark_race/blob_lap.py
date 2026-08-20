"""Image-only 3-D blob detector with deterministic frame-local LAP linking.

The adapter intentionally has no ground-truth input.  It normalizes the raw
``(T, Z, Y, X)`` image, extracts local maxima independently per frame, and
links consecutive frames with a physical-distance gated one-to-one assignment.
The implementation is deliberately small so later race tasks can use the same
candidate/edge contracts without sharing association scores.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.optimize import linear_sum_assignment

from biohub.benchmark_race.cache import build_cache_manifest
from biohub.benchmark_race.contracts import RaceRequest
from biohub.strong_baseline.manifest import prediction_directory_manifest, write_prediction_manifest

DEFAULT_QUANTILE_LOW = 0.001
DEFAULT_QUANTILE_HIGH = 0.999
DEFAULT_GAUSSIAN_SIGMA = (1.0, 1.0, 1.0)
DEFAULT_LOCAL_MAX_SIZE = (3, 3, 3)
DEFAULT_PEAK_THRESHOLD = 0.25
DEFAULT_NMS_DISTANCE_UM = 3.0
DEFAULT_MAX_LINK_DISTANCE_UM = 7.0
DEFAULT_SCALE = (1.625, 0.40625, 0.40625)


def _three_values(name: str, value: Sequence[Any], *, cast: type) -> tuple[Any, Any, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        converted = cast(value)
        return converted, converted, converted  # type: ignore[return-value]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values in (Z, Y, X) order")
    converted = tuple(cast(item) for item in value)
    return converted  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class BlobLapConfig:
    """Fixed, image-only configuration for the ``blob_lap`` lane."""

    q_low: float = DEFAULT_QUANTILE_LOW
    q_high: float = DEFAULT_QUANTILE_HIGH
    gaussian_sigma: tuple[float, float, float] = DEFAULT_GAUSSIAN_SIGMA
    local_max_size: tuple[int, int, int] = DEFAULT_LOCAL_MAX_SIZE
    peak_threshold: float = DEFAULT_PEAK_THRESHOLD
    nms_distance_um: float = DEFAULT_NMS_DISTANCE_UM
    max_link_distance_um: float = DEFAULT_MAX_LINK_DISTANCE_UM
    scale: tuple[float, float, float] = DEFAULT_SCALE
    division_enabled: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        q_low = float(self.q_low)
        q_high = float(self.q_high)
        if not np.isfinite(q_low) or not np.isfinite(q_high) or not 0.0 <= q_low < q_high <= 1.0:
            raise ValueError("q_low and q_high must satisfy 0 <= q_low < q_high <= 1")
        sigma = tuple(float(value) for value in _three_values("gaussian_sigma", self.gaussian_sigma, cast=float))
        if any(not np.isfinite(value) or value < 0.0 for value in sigma):
            raise ValueError("gaussian_sigma must contain finite non-negative values")
        local_max_size = tuple(int(value) for value in _three_values("local_max_size", self.local_max_size, cast=int))
        if any(value <= 0 for value in local_max_size):
            raise ValueError("local_max_size must contain positive integers")
        scale = tuple(float(value) for value in _three_values("scale", self.scale, cast=float))
        if any(not np.isfinite(value) or value <= 0.0 for value in scale):
            raise ValueError("scale must contain positive finite values")
        for name in ("peak_threshold", "nms_distance_um", "max_link_distance_um"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        object.__setattr__(self, "q_low", q_low)
        object.__setattr__(self, "q_high", q_high)
        object.__setattr__(self, "gaussian_sigma", sigma)
        object.__setattr__(self, "local_max_size", local_max_size)
        object.__setattr__(self, "peak_threshold", float(self.peak_threshold))
        object.__setattr__(self, "nms_distance_um", float(self.nms_distance_um))
        object.__setattr__(self, "max_link_distance_um", float(self.max_link_distance_um))
        object.__setattr__(self, "scale", scale)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None = None,
        *,
        scale: Sequence[float] = DEFAULT_SCALE,
    ) -> BlobLapConfig:
        """Build config from a JSON-like mapping and reject unknown options."""

        raw = dict(values or {})
        division_enabled = raw.pop("division_enabled", False)
        if division_enabled:
            raise ValueError("blob_lap division is fixed disabled")
        aliases = {
            "sigma": "gaussian_sigma",
            "local_maximum_size": "local_max_size",
            "threshold": "peak_threshold",
            "link_distance_um": "max_link_distance_um",
        }
        for source, target in aliases.items():
            if source in raw:
                if target in raw:
                    raise ValueError(f"duplicate config options {source!r} and {target!r}")
                raw[target] = raw.pop(source)
        allowed = {
            "q_low",
            "q_high",
            "gaussian_sigma",
            "local_max_size",
            "peak_threshold",
            "nms_distance_um",
            "max_link_distance_um",
            "scale",
        }
        unknown = sorted(set(raw).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported blob_lap config option(s): {', '.join(unknown)}")
        raw.setdefault("scale", scale)
        return cls(**raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "q_low": self.q_low,
            "q_high": self.q_high,
            "gaussian_sigma": list(self.gaussian_sigma),
            "local_max_size": list(self.local_max_size),
            "peak_threshold": self.peak_threshold,
            "nms_distance_um": self.nms_distance_um,
            "max_link_distance_um": self.max_link_distance_um,
            "scale": list(self.scale),
            "division_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class CandidateTable:
    """Deterministic candidate rows with voxel and physical coordinates."""

    coordinates: np.ndarray
    physical_coordinates: np.ndarray
    scores: np.ndarray

    def __post_init__(self) -> None:
        coordinates = np.asarray(self.coordinates, dtype=np.float64)
        physical = np.asarray(self.physical_coordinates, dtype=np.float64)
        scores = np.asarray(self.scores, dtype=np.float64)
        if coordinates.ndim != 2 or coordinates.shape[1] != 4:
            raise ValueError("candidate coordinates must have shape (N, 4) in (T, Z, Y, X) order")
        if physical.shape != (len(coordinates), 3):
            raise ValueError("candidate physical_coordinates must have shape (N, 3)")
        if scores.shape != (len(coordinates),):
            raise ValueError("candidate scores must have shape (N,)")
        if not np.isfinite(coordinates).all() or not np.isfinite(physical).all() or not np.isfinite(scores).all():
            raise ValueError("candidate coordinates and scores must be finite")
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "physical_coordinates", physical)
        object.__setattr__(self, "scores", scores)

    def __len__(self) -> int:
        return int(self.coordinates.shape[0])

    @property
    def voxel_coordinates(self) -> np.ndarray:
        return self.coordinates[:, 1:]

    @property
    def physical_coords(self) -> np.ndarray:
        return self.physical_coordinates

    @property
    def positions(self) -> np.ndarray:
        return self.coordinates

    @property
    def tzyx(self) -> np.ndarray:
        return self.coordinates

    @property
    def node_ids(self) -> np.ndarray:
        return np.arange(len(self), dtype=np.int64)


@dataclass(frozen=True, slots=True)
class EdgeTable:
    """One-to-one links represented as source/target candidate row IDs."""

    pairs: np.ndarray
    distances_um: np.ndarray

    def __post_init__(self) -> None:
        pairs = np.asarray(self.pairs, dtype=np.int64)
        distances = np.asarray(self.distances_um, dtype=np.float64)
        if pairs.ndim != 2 or pairs.shape[1] != 2:
            raise ValueError("edge pairs must have shape (E, 2)")
        if distances.shape != (len(pairs),):
            raise ValueError("edge distances_um must have shape (E,)")
        if not np.isfinite(distances).all() or (distances < 0.0).any():
            raise ValueError("edge distances_um must be finite and non-negative")
        object.__setattr__(self, "pairs", pairs)
        object.__setattr__(self, "distances_um", distances)

    def __len__(self) -> int:
        return int(self.pairs.shape[0])

    @property
    def source(self) -> np.ndarray:
        return self.pairs[:, 0]

    @property
    def target(self) -> np.ndarray:
        return self.pairs[:, 1]

    @property
    def source_ids(self) -> np.ndarray:
        return self.source

    @property
    def target_ids(self) -> np.ndarray:
        return self.target

    @property
    def distance_um(self) -> np.ndarray:
        return self.distances_um


@dataclass(frozen=True, slots=True)
class PredictionArtifact:
    """Paths and structural counts persisted by ``run_blob_lap``."""

    prediction_path: Path
    prediction_manifest_path: Path
    run_json_path: Path
    candidate_count: int
    edge_count: int
    cache_manifest_path: Path

    @property
    def manifest_path(self) -> Path:
        return self.prediction_manifest_path

    @property
    def run_receipt_path(self) -> Path:
        return self.run_json_path

    @property
    def node_count(self) -> int:
        return self.candidate_count


def voxel_to_physical(coordinates: np.ndarray, scale: Sequence[float]) -> np.ndarray:
    """Convert ``(Z, Y, X)`` voxel coordinates to physical micrometres."""

    values = np.asarray(coordinates, dtype=np.float64)
    if values.shape[-1:] != (3,):
        raise ValueError("coordinates must end with a (Z, Y, X) axis")
    scale_array = np.asarray(tuple(scale), dtype=np.float64)
    if scale_array.shape != (3,) or not np.isfinite(scale_array).all() or (scale_array <= 0.0).any():
        raise ValueError("scale must contain three positive finite values")
    return values * scale_array


def physical_distance(first: Sequence[float], second: Sequence[float], scale: Sequence[float] | None = None) -> float:
    """Return Euclidean distance in micrometres between two coordinates."""

    first_array = np.asarray(first, dtype=np.float64)
    second_array = np.asarray(second, dtype=np.float64)
    if first_array.shape != (3,) or second_array.shape != (3,):
        raise ValueError("physical_distance expects two (Z, Y, X) coordinates")
    if scale is not None:
        first_array = voxel_to_physical(first_array, scale)
        second_array = voxel_to_physical(second_array, scale)
    return float(np.linalg.norm(first_array - second_array))


def _normalise_image(image: np.ndarray, config: BlobLapConfig) -> np.ndarray:
    values = np.asarray(image, dtype=np.float32)
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros(values.shape, dtype=np.float32)
    low = float(np.nanquantile(values, config.q_low))
    high = float(np.nanquantile(values, config.q_high))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(values.shape, dtype=np.float32)
    normalised = (values - low) / (high - low)
    normalised = np.clip(normalised, 0.0, 1.0)
    return np.where(finite, normalised, 0.0).astype(np.float32)


def _normalise_frame(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    values = np.asarray(frame, dtype=np.float32)
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(values.shape, dtype=np.float32)
    finite = np.isfinite(values)
    normalised = np.clip((values - low) / (high - low), 0.0, 1.0)
    return np.where(finite, normalised, 0.0).astype(np.float32)


def _frame_candidates(frame: np.ndarray, frame_index: int, config: BlobLapConfig) -> list[tuple[float, int, int, int]]:
    smoothed = gaussian_filter(frame, sigma=config.gaussian_sigma, mode="nearest")
    local_maximum = maximum_filter(smoothed, size=config.local_max_size, mode="nearest")
    peak_mask = np.isfinite(smoothed) & (smoothed >= config.peak_threshold) & (smoothed == local_maximum)
    coordinates = np.argwhere(peak_mask)
    rows = [
        (float(smoothed[z, y, x]), frame_index, int(z), int(y), int(x))
        for z, y, x in coordinates
    ]
    rows.sort(key=lambda row: (-row[0], row[2], row[3], row[4]))
    accepted: list[tuple[float, int, int, int]] = []
    accepted_physical: list[np.ndarray] = []
    for score, _, z, y, x in rows:
        physical = voxel_to_physical(np.array([z, y, x]), config.scale)
        if any(float(np.linalg.norm(physical - other)) <= config.nms_distance_um for other in accepted_physical):
            continue
        accepted.append((score, z, y, x))
        accepted_physical.append(physical)
    accepted.sort(key=lambda row: (row[1], row[2], row[3]))
    return [(score, frame_index, z, y, x) for score, z, y, x in accepted]


def detect_blob_candidates(image: np.ndarray, config: BlobLapConfig | Mapping[str, Any]) -> CandidateTable:
    """Detect 3-D local peaks independently in every image frame."""

    config = config if isinstance(config, BlobLapConfig) else BlobLapConfig.from_mapping(config)
    values = np.asarray(image)
    if values.ndim != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {values.shape!r}")
    normalised = _normalise_image(values, config)
    rows: list[tuple[float, int, int, int, int]] = []
    for frame_index in range(normalised.shape[0]):
        rows.extend(_frame_candidates(normalised[frame_index], frame_index, config))
    rows.sort(key=lambda row: (row[1], row[2], row[3], row[4]))
    coordinates = np.asarray([[frame, z, y, x] for _, frame, z, y, x in rows], dtype=np.float64).reshape(-1, 4)
    physical = voxel_to_physical(coordinates[:, 1:], config.scale).reshape(-1, 3)
    scores = np.asarray([score for score, *_ in rows], dtype=np.float64)
    return CandidateTable(coordinates, physical, scores)


def detect_blob_candidates_streaming(
    image: Any,
    config: BlobLapConfig | Mapping[str, Any],
    *,
    quantiles: Mapping[str, float],
    max_frames: int | None = None,
) -> CandidateTable:
    """Detect candidates frame-by-frame from a Zarr-like ``(T,Z,Y,X)`` array.

    The sample metadata already stores the fixed image quantiles, so the full
    movie never needs to be materialized in memory.  The ndarray API above is
    retained for small unit fixtures.
    """

    config = config if isinstance(config, BlobLapConfig) else BlobLapConfig.from_mapping(config)
    shape = tuple(int(value) for value in image.shape)
    if len(shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {shape!r}")
    frame_count = shape[0] if max_frames is None else int(max_frames)
    if frame_count <= 0 or frame_count > shape[0]:
        raise ValueError(f"max_frames must be in [1, {shape[0]}]")
    try:
        low = float(quantiles[str(config.q_low)])
        high = float(quantiles[str(config.q_high)])
    except KeyError as exc:
        raise ValueError("sample quantiles are missing detector quantile bounds") from exc
    rows: list[tuple[float, int, int, int, int]] = []
    for frame_index in range(frame_count):
        frame = _normalise_frame(np.asarray(image[frame_index]), low, high)
        rows.extend(_frame_candidates(frame, frame_index, config))
    rows.sort(key=lambda row: (row[1], row[2], row[3], row[4]))
    coordinates = np.asarray([[frame, z, y, x] for _, frame, z, y, x in rows], dtype=np.float64).reshape(-1, 4)
    physical = voxel_to_physical(coordinates[:, 1:], config.scale).reshape(-1, 3)
    scores = np.asarray([score for score, *_ in rows], dtype=np.float64)
    return CandidateTable(coordinates, physical, scores)


def pairwise_physical_distances(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Compute a dense physical-distance matrix for two ``(N, 3)`` tables."""

    first_array = np.asarray(first, dtype=np.float64)
    second_array = np.asarray(second, dtype=np.float64)
    valid_shapes = (
        first_array.ndim == 2
        and second_array.ndim == 2
        and first_array.shape[1:] == (3,)
        and second_array.shape[1:] == (3,)
    )
    if not valid_shapes:
        raise ValueError("distance tables must have shape (N, 3) and (M, 3)")
    return np.linalg.norm(first_array[:, None, :] - second_array[None, :, :], axis=2)


def link_blob_lap(candidates: CandidateTable, config: BlobLapConfig | Mapping[str, Any]) -> EdgeTable:
    """Link adjacent frames with deterministic physical-distance Hungarian LAP."""

    if not isinstance(candidates, CandidateTable):
        raise TypeError("candidates must be a CandidateTable")
    config = config if isinstance(config, BlobLapConfig) else BlobLapConfig.from_mapping(config)
    frame_values = candidates.coordinates[:, 0]
    if len(candidates) == 0:
        return EdgeTable(np.empty((0, 2), dtype=np.int64), np.empty((0,), dtype=np.float64))
    pairs: list[tuple[int, int]] = []
    distances: list[float] = []
    for frame in range(int(frame_values.max())):
        source_ids = np.flatnonzero(frame_values == frame)
        target_ids = np.flatnonzero(frame_values == frame + 1)
        if len(source_ids) == 0 or len(target_ids) == 0:
            continue
        matrix = pairwise_physical_distances(
            candidates.physical_coordinates[source_ids],
            candidates.physical_coordinates[target_ids],
        )
        tie_break = (
            np.arange(len(source_ids), dtype=np.float64)[:, None] * (len(target_ids) + 1)
            + np.arange(len(target_ids), dtype=np.float64)[None, :]
        )
        valid = matrix <= config.max_link_distance_um
        cost = np.where(valid, matrix + tie_break * 1.0e-10, config.max_link_distance_um + 1.0e6)
        row_indices, column_indices = linear_sum_assignment(cost)
        for row, column in zip(row_indices, column_indices, strict=True):
            distance = float(matrix[row, column])
            if distance <= config.max_link_distance_um:
                pairs.append((int(source_ids[row]), int(target_ids[column])))
                distances.append(distance)
    order = sorted(range(len(pairs)), key=lambda index: pairs[index])
    return EdgeTable(
        np.asarray([pairs[index] for index in order], dtype=np.int64).reshape(-1, 2),
        np.asarray([distances[index] for index in order], dtype=np.float64),
    )


def build_prediction_graph(candidates: CandidateTable, edges: EdgeTable) -> Any:
    """Build a tracksdata graph using voxel node coordinates and frame edges."""

    import polars as pl
    import tracksdata as td

    graph = td.graph.IndexedRXGraph()
    graph.add_node_attr_key("z", dtype=pl.Float64, default_value=0.0)
    graph.add_node_attr_key("y", dtype=pl.Float64, default_value=0.0)
    graph.add_node_attr_key("x", dtype=pl.Float64, default_value=0.0)
    graph.add_node_attr_key("score", dtype=pl.Float64, default_value=0.0)
    graph.add_edge_attr_key("distance_um", dtype=pl.Float64, default_value=0.0)
    for index, (coordinate, score) in enumerate(zip(candidates.coordinates, candidates.scores, strict=True)):
        frame, z, y, x = coordinate
        node_id = graph.add_node(
            {"t": int(frame), "z": float(z), "y": float(y), "x": float(x), "score": float(score)},
            index=int(index),
        )
        if node_id != index:
            raise RuntimeError("tracksdata assigned a non-deterministic node ID")
    for pair, distance in zip(edges.pairs, edges.distances_um, strict=True):
        graph.add_edge(int(pair[0]), int(pair[1]), {"distance_um": float(distance)})
    return graph


def _open_image(path: Path) -> Any:
    import zarr

    root = zarr.open(str(path), mode="r")
    if hasattr(root, "shape"):
        array = root
    elif "0" in root:
        array = root["0"]
    else:
        raise ValueError(f"image Zarr is missing array '0': {path}")
    if len(array.shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {array.shape!r}")
    return array


def _git_commit() -> str:
    """Return explicitly supplied provenance or an honest container sentinel."""

    explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_REVISION")
    if explicit is None:
        explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_COMMIT")
    if explicit and explicit.strip():
        return explicit.strip()
    return "unavailable-in-container"


def _image_digest(image: Any, *, max_frames: int | None = None) -> str:
    digest = hashlib.sha256()
    digest.update(str(image.dtype).encode("ascii"))
    shape = tuple(int(value) for value in image.shape)
    frame_count = shape[0] if max_frames is None else int(max_frames)
    digest.update(repr((frame_count, *shape[1:])).encode("ascii"))
    for frame_index in range(frame_count):
        digest.update(np.ascontiguousarray(np.asarray(image[frame_index])).tobytes())
    return digest.hexdigest()


def _source_file_digest() -> str:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    """Return the digest of a persisted cache payload."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _sample_path(request: RaceRequest) -> Path:
    path = Path(request.sample.image_stem)
    if path.suffix.casefold() != ".zarr":
        path = path.with_suffix(".zarr")
    if not path.exists():
        raise FileNotFoundError(f"image Zarr not found: {path}")
    return path


def _validate_image_shape(image: Any, request: RaceRequest) -> tuple[int, ...]:
    actual_shape = tuple(int(value) for value in image.shape)
    expected_shape = tuple(int(value) for value in request.sample.shape)
    if actual_shape != expected_shape:
        raise ValueError(f"image shape {actual_shape} disagrees with sample image_shape {expected_shape}")
    return actual_shape


def _save_candidate_cache(
    request: RaceRequest,
    candidates: CandidateTable,
    image: Any,
    config: BlobLapConfig,
    max_frames: int | None,
    source_commit: str,
    source_file_sha256: str,
) -> tuple[str, Path]:
    _validate_image_shape(image, request)
    detector_config = config.as_dict()
    detector_config["max_frames"] = max_frames
    cache_manifest = build_cache_manifest(
        sample=request.sample,
        image_digest=_image_digest(image, max_frames=max_frames),
        detector_config=detector_config,
        source_commit=source_commit,
    )
    cache_dir = Path(request.cache_root).resolve() / cache_manifest["cache_key"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_dir / "detections.npz",
        coordinates=candidates.coordinates,
        physical_coordinates=candidates.physical_coordinates,
        scores=candidates.scores,
    )
    cache_manifest["detections_sha256"] = _file_sha256(cache_dir / "detections.npz")
    cache_manifest["source_file_sha256"] = source_file_sha256
    (cache_dir / "cache_manifest.json").write_text(json.dumps(cache_manifest, indent=2, sort_keys=True) + "\n")
    return str(cache_manifest["cache_key"]), cache_dir / "cache_manifest.json"


def run_blob_lap(request: RaceRequest) -> PredictionArtifact:
    """Run image-only blob/LAP inference and persist a reloadable prediction."""

    if not isinstance(request, RaceRequest):
        raise TypeError("request must be a RaceRequest")
    if request.expected_device.casefold() != "cpu":
        raise ValueError("blob_lap currently supports only the CPU device")
    started_at = _timestamp()
    started = time.monotonic()
    request_config = dict(request.config)
    max_frames_value = request_config.pop("max_frames", None)
    if max_frames_value is None:
        max_frames = None
    else:
        max_frames = int(max_frames_value)
        if max_frames <= 0:
            raise ValueError("max_frames must be a positive integer")
    config = BlobLapConfig.from_mapping(request_config, scale=request.sample.scale)
    image_path = _sample_path(request)
    image = _open_image(image_path)
    image_shape = _validate_image_shape(image, request)
    if max_frames is not None:
        if max_frames > image.shape[0]:
            raise ValueError(f"max_frames {max_frames} exceeds image frame count {image.shape[0]}")
    candidates = detect_blob_candidates_streaming(
        image,
        config,
        quantiles=request.sample.quantiles,
        max_frames=max_frames,
    )
    edges = link_blob_lap(candidates, config)
    source_commit = _git_commit()
    source_file_sha256 = _source_file_digest()
    cache_key, cache_manifest_path = _save_candidate_cache(
        request,
        candidates,
        image,
        config,
        max_frames,
        source_commit,
        source_file_sha256,
    )

    output_root = Path(request.output_root).resolve()
    target = output_root / "methods" / "blob_lap" / f"{Path(request.sample.image_stem).stem}.geff"
    if target.exists():
        raise FileExistsError(f"prediction destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    graph = build_prediction_graph(candidates, edges)
    graph.to_geff(target)
    manifest_payload = prediction_directory_manifest(target)
    manifest_payload.update(
        {
            "method_id": "blob_lap",
            "candidate_count": len(candidates),
            "edge_count": len(edges),
            "division_enabled": False,
            "ground_truth_included": False,
            "cache_key": cache_key,
            "config": config.as_dict(),
            "source_file_sha256": source_file_sha256,
        },
    )
    manifest_path = write_prediction_manifest(target, manifest_payload)
    elapsed = time.monotonic() - started
    run_payload = {
        "method_id": "blob_lap",
        "method_family": "classical_detector_and_lap_linker",
        "detector_id": "3d_gaussian_local_peak",
        "linker_id": "physical_distance_hungarian_lap",
        "version": "blob_lap.v1",
        "source_module": "biohub.benchmark_race.blob_lap",
        "source_commit": source_commit,
        "source_file_sha256": source_file_sha256,
        "sample_id": request.sample.sample_id,
        "image_stem": request.sample.image_stem.as_posix(),
        "image_shape": [max_frames or image_shape[0], *image_shape[1:]],
        "config": config.as_dict(),
        "expected_device": request.expected_device,
        "actual_device": "cpu",
        "runtime_seconds": float(elapsed),
        "started_at": started_at,
        "finished_at": _timestamp(),
        "candidate_count": len(candidates),
        "prediction_node_count": len(candidates),
        "edge_count": len(edges),
        "prediction_edge_count": len(edges),
        "division_enabled": False,
        "ground_truth_included": False,
        "cache_key": cache_key,
        "cache_manifest": str(cache_manifest_path),
        "prediction_manifest": str(manifest_path),
    }
    run_json_path = target.parent / "run.json"
    run_json_path.write_text(json.dumps(run_payload, indent=2, sort_keys=True) + "\n")
    (target.parent / "inference.log").write_text(
        f"method=blob_lap candidates={len(candidates)} edges={len(edges)} device=cpu\n",
    )
    return PredictionArtifact(
        prediction_path=target,
        prediction_manifest_path=manifest_path,
        run_json_path=run_json_path,
        candidate_count=len(candidates),
        edge_count=len(edges),
        cache_manifest_path=cache_manifest_path,
    )


__all__ = [
    "BlobLapConfig",
    "CandidateTable",
    "EdgeTable",
    "PredictionArtifact",
    "build_prediction_graph",
    "detect_blob_candidates",
    "link_blob_lap",
    "pairwise_physical_distances",
    "physical_distance",
    "run_blob_lap",
    "voxel_to_physical",
]
