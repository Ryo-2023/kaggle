"""Velocity-aware association over the fixed ``blob_lap`` candidate cache.

This lane deliberately does not expose or rerun an official detector.  It
consumes the image-only candidate rows written by ``blob_lap`` and changes
only the association cost: a deterministic first-order velocity prior and an
acceleration penalty are used before a frame-local one-to-one LAP solve.
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
from scipy.optimize import linear_sum_assignment

from biohub.benchmark_race.blob_lap import (
    CandidateTable,
    EdgeTable,
    PredictionArtifact,
    voxel_to_physical,
)
from biohub.benchmark_race.cache import build_cache_manifest
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec, _contains_ground_truth
from biohub.strong_baseline.manifest import prediction_directory_manifest, write_prediction_manifest

DEFAULT_MAX_LINK_DISTANCE_UM = 7.0
DEFAULT_VELOCITY_WEIGHT = 1.0
DEFAULT_ACCELERATION_PENALTY = 0.25
DEFAULT_INITIAL_VELOCITY_POLICY = "zero"
DEFAULT_SCALE = (1.625, 0.40625, 0.40625)
_INITIAL_VELOCITY_POLICIES = frozenset({"zero", "nearest"})


def _three_values(name: str, value: Sequence[Any]) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values in (Z, Y, X) order")
    converted = tuple(float(item) for item in value)
    if any(not np.isfinite(item) or item <= 0.0 for item in converted):
        raise ValueError(f"{name} must contain positive finite values")
    return converted


@dataclass(frozen=True, slots=True)
class MotionLapConfig:
    """Fixed image-only configuration for the ``motion_lap`` lane."""

    max_link_distance_um: float = DEFAULT_MAX_LINK_DISTANCE_UM
    velocity_weight: float = DEFAULT_VELOCITY_WEIGHT
    acceleration_penalty: float = DEFAULT_ACCELERATION_PENALTY
    initial_velocity_policy: str = DEFAULT_INITIAL_VELOCITY_POLICY
    scale: tuple[float, float, float] = DEFAULT_SCALE
    division_enabled: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for name in ("max_link_distance_um", "velocity_weight", "acceleration_penalty"):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        policy = str(self.initial_velocity_policy).casefold()
        if policy not in _INITIAL_VELOCITY_POLICIES:
            allowed = ", ".join(sorted(_INITIAL_VELOCITY_POLICIES))
            raise ValueError(f"initial_velocity_policy must be one of: {allowed}")
        object.__setattr__(self, "initial_velocity_policy", policy)
        object.__setattr__(self, "scale", _three_values("scale", self.scale))

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None = None,
        *,
        scale: Sequence[float] = DEFAULT_SCALE,
    ) -> MotionLapConfig:
        """Build a fixed config from a JSON-like mapping.

        Aliases are accepted only for the public distance and weight names so
        that old smoke command snippets remain readable.  Unknown options are
        rejected instead of silently changing a benchmark lane.
        """

        raw = dict(values or {})
        division_enabled = raw.pop("division_enabled", False)
        if division_enabled:
            raise ValueError("motion_lap division is fixed disabled")
        aliases = {
            "link_distance_um": "max_link_distance_um",
            "velocity_cost_weight": "velocity_weight",
            "acceleration_weight": "acceleration_penalty",
        }
        for source, target in aliases.items():
            if source in raw:
                if target in raw:
                    raise ValueError(f"duplicate config options {source!r} and {target!r}")
                raw[target] = raw.pop(source)
        allowed = {
            "max_link_distance_um",
            "velocity_weight",
            "acceleration_penalty",
            "initial_velocity_policy",
            "scale",
        }
        unknown = sorted(set(raw).difference(allowed))
        if unknown:
            raise ValueError(f"unsupported motion_lap config option(s): {', '.join(unknown)}")
        raw.setdefault("scale", scale)
        return cls(**raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_link_distance_um": self.max_link_distance_um,
            "velocity_weight": self.velocity_weight,
            "acceleration_penalty": self.acceleration_penalty,
            "initial_velocity_policy": self.initial_velocity_policy,
            "scale": list(self.scale),
            "division_enabled": False,
        }


def _as_position(name: str, value: Sequence[float] | np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (3,) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a finite (Z, Y, X) position")
    return array


def motion_cost(
    source: Sequence[float] | np.ndarray,
    target: Sequence[float] | np.ndarray,
    velocity: Sequence[float] | np.ndarray,
    config: MotionLapConfig | Mapping[str, Any],
    *,
    target_velocity: Sequence[float] | np.ndarray | None = None,
) -> float:
    """Return a deterministic predicted-position plus acceleration cost.

    ``velocity`` is the source candidate's estimated one-frame velocity.  The
    public four-argument form uses the observed source-to-target displacement
    as the target velocity for its acceleration term.  The linker supplies an
    independently estimated ``target_velocity`` when available, which lets a
    crossing fixture distinguish a velocity-consistent identity from a
    distance-tied swap.
    """

    config = config if isinstance(config, MotionLapConfig) else MotionLapConfig.from_mapping(config)
    source_array = _as_position("source", source)
    target_array = _as_position("target", target)
    velocity_array = _as_position("velocity", velocity)
    observed_velocity = target_array - source_array
    next_velocity = observed_velocity if target_velocity is None else _as_position("target_velocity", target_velocity)
    prediction_error = float(np.linalg.norm(target_array - (source_array + velocity_array)))
    acceleration = float(np.linalg.norm(next_velocity - velocity_array))
    return float(config.velocity_weight * prediction_error + config.acceleration_penalty * acceleration)


def _frame_indices(candidates: CandidateTable) -> dict[int, np.ndarray]:
    frames = np.asarray(candidates.coordinates[:, 0], dtype=np.float64)
    return {
        frame: np.flatnonzero(frames == frame).astype(np.int64)
        for frame in sorted({int(value) for value in frames})
    }


def _nearest_candidate(
    current_id: int,
    other_ids: np.ndarray,
    candidates: CandidateTable,
    *,
    max_distance_um: float,
) -> int | None:
    if len(other_ids) == 0:
        return None
    current = candidates.physical_coordinates[current_id]
    distances = np.linalg.norm(candidates.physical_coordinates[other_ids] - current[None, :], axis=1)
    nearest_position = int(np.argmin(distances))
    nearest_distance = float(distances[nearest_position])
    if nearest_distance > max_distance_um:
        return None
    return int(other_ids[nearest_position])


def _velocity_and_predecessors(
    candidates: CandidateTable,
    config: MotionLapConfig,
) -> tuple[np.ndarray, np.ndarray]:
    velocities = np.zeros((len(candidates), 3), dtype=np.float64)
    predecessors = np.full(len(candidates), -1, dtype=np.int64)
    frames = _frame_indices(candidates)
    for frame, current_ids in frames.items():
        previous_ids = frames.get(frame - 1, np.empty((0,), dtype=np.int64))
        next_ids = frames.get(frame + 1, np.empty((0,), dtype=np.int64))
        for current_id in current_ids:
            predecessor = _nearest_candidate(
                int(current_id),
                previous_ids,
                candidates,
                max_distance_um=config.max_link_distance_um,
            )
            if predecessor is not None:
                predecessors[current_id] = predecessor
                velocities[current_id] = (
                    candidates.physical_coordinates[current_id] - candidates.physical_coordinates[predecessor]
                )
                continue
            if frame == min(frames, default=frame) and config.initial_velocity_policy == "nearest":
                successor = _nearest_candidate(
                    int(current_id),
                    next_ids,
                    candidates,
                    max_distance_um=config.max_link_distance_um,
                )
                if successor is not None:
                    velocities[current_id] = (
                        candidates.physical_coordinates[successor] - candidates.physical_coordinates[current_id]
                    )
    return velocities, predecessors


def estimate_velocities(
    candidates: CandidateTable,
    config: MotionLapConfig | Mapping[str, Any],
) -> np.ndarray:
    """Estimate one-frame physical velocities deterministically per candidate."""

    if not isinstance(candidates, CandidateTable):
        raise TypeError("candidates must be a blob_lap CandidateTable")
    config = config if isinstance(config, MotionLapConfig) else MotionLapConfig.from_mapping(config)
    velocities, _ = _velocity_and_predecessors(candidates, config)
    return velocities


def estimate_accelerations(
    candidates: CandidateTable,
    config: MotionLapConfig | Mapping[str, Any],
) -> np.ndarray:
    """Estimate velocity changes against each candidate's previous candidate."""

    if not isinstance(candidates, CandidateTable):
        raise TypeError("candidates must be a blob_lap CandidateTable")
    config = config if isinstance(config, MotionLapConfig) else MotionLapConfig.from_mapping(config)
    velocities, predecessors = _velocity_and_predecessors(candidates, config)
    accelerations = velocities.copy()
    for candidate_id, predecessor in enumerate(predecessors):
        if predecessor >= 0:
            accelerations[candidate_id] = velocities[candidate_id] - velocities[predecessor]
    return accelerations


def _coerce_edge_scores(
    edge_scores: Mapping[tuple[int, int], float] | Sequence[Any] | np.ndarray | None,
    candidates: CandidateTable,
) -> dict[tuple[int, int], float]:
    if edge_scores is None:
        return {}
    if isinstance(edge_scores, Mapping):
        values: dict[tuple[int, int], float] = {}
        for key, value in edge_scores.items():
            if not isinstance(key, Sequence) or len(key) != 2:
                raise ValueError("edge score mapping keys must be (source_id, target_id) pairs")
            pair = (int(key[0]), int(key[1]))
            score = float(value)
            if not np.isfinite(score):
                raise ValueError("edge scores must be finite")
            values[pair] = score
        return values
    array = np.asarray(edge_scores)
    if array.ndim == 2 and array.shape == (len(candidates), len(candidates)):
        values = {}
        for source_id in range(len(candidates)):
            for target_id in range(len(candidates)):
                score = float(array[source_id, target_id])
                if np.isfinite(score):
                    values[(source_id, target_id)] = score
        return values
    if array.ndim == 2 and array.shape[1:] == (3,):
        values = {}
        for row in array:
            source_id, target_id = int(row[0]), int(row[1])
            score = float(row[2])
            if not np.isfinite(score):
                raise ValueError("edge scores must be finite")
            values[(source_id, target_id)] = score
        return values
    raise ValueError("edge_scores must be a (source, target) mapping, an (E, 3) table, or an (N, N) matrix")


def _edge_score(
    source_id: int,
    target_id: int,
    candidates: CandidateTable,
    velocities: np.ndarray,
    config: MotionLapConfig,
) -> tuple[float, float, float, float]:
    source = candidates.physical_coordinates[source_id]
    target = candidates.physical_coordinates[target_id]
    source_velocity = velocities[source_id]
    target_velocity = velocities[target_id]
    residual = float(np.linalg.norm(target - (source + source_velocity)))
    acceleration = float(np.linalg.norm(target_velocity - source_velocity))
    cost = float(config.velocity_weight * residual + config.acceleration_penalty * acceleration)
    return cost, residual, acceleration, float(np.linalg.norm(target - source))


def link_motion(
    candidates: CandidateTable,
    edge_scores: Mapping[tuple[int, int], float] | Sequence[Any] | np.ndarray | None,
    config: MotionLapConfig | Mapping[str, Any],
) -> EdgeTable:
    """Link adjacent frames with a deterministic velocity-aware one-to-one LAP.

    ``edge_scores`` may be omitted (``None``), supplied as a pair mapping, an
    ``(E, 3)`` table of ``source,target,cost`` rows, or an ``(N, N)`` matrix.
    Missing entries are computed from the motion prior, so callers can inject
    only a bounded subset of precomputed scores without changing the contract.
    """

    if not isinstance(candidates, CandidateTable):
        raise TypeError("candidates must be a blob_lap CandidateTable")
    config = config if isinstance(config, MotionLapConfig) else MotionLapConfig.from_mapping(config)
    if len(candidates) == 0:
        return EdgeTable(np.empty((0, 2), dtype=np.int64), np.empty((0,), dtype=np.float64))
    lookup = _coerce_edge_scores(edge_scores, candidates)
    velocities = estimate_velocities(candidates, config)
    frames = _frame_indices(candidates)
    pairs: list[tuple[int, int]] = []
    distances: list[float] = []
    for frame in sorted(frames):
        source_ids = frames[frame]
        target_ids = frames.get(frame + 1, np.empty((0,), dtype=np.int64))
        if len(source_ids) == 0 or len(target_ids) == 0:
            continue
        matrix = np.full((len(source_ids), len(target_ids)), np.inf, dtype=np.float64)
        distances_matrix = np.linalg.norm(
            candidates.physical_coordinates[source_ids, None, :]
            - candidates.physical_coordinates[None, target_ids, :],
            axis=2,
        )
        for source_position, source_id in enumerate(source_ids):
            for target_position, target_id in enumerate(target_ids):
                distance = float(distances_matrix[source_position, target_position])
                if distance > config.max_link_distance_um:
                    continue
                pair = (int(source_id), int(target_id))
                if pair in lookup:
                    score = lookup[pair]
                else:
                    score, _, _, _ = _edge_score(pair[0], pair[1], candidates, velocities, config)
                matrix[source_position, target_position] = score
        valid = np.isfinite(matrix)
        if not valid.any():
            continue
        finite_scores = matrix[valid]
        invalid_cost = float(finite_scores.max() + max(config.max_link_distance_um, 1.0) * 1.0e6)
        tie_break = (
            np.arange(len(source_ids), dtype=np.float64)[:, None] * (len(target_ids) + 1)
            + np.arange(len(target_ids), dtype=np.float64)[None, :]
        )
        assignment_cost = np.where(valid, matrix + tie_break * 1.0e-10, invalid_cost)
        row_indices, column_indices = linear_sum_assignment(assignment_cost)
        for row, column in zip(row_indices, column_indices, strict=True):
            if not valid[row, column]:
                continue
            source_id = int(source_ids[row])
            target_id = int(target_ids[column])
            pairs.append((source_id, target_id))
            distances.append(float(distances_matrix[row, column]))
    order = sorted(range(len(pairs)), key=lambda index: pairs[index])
    return EdgeTable(
        np.asarray([pairs[index] for index in order], dtype=np.int64).reshape(-1, 2),
        np.asarray([distances[index] for index in order], dtype=np.float64),
    )


def build_prediction_graph(
    candidates: CandidateTable,
    edges: EdgeTable,
    config: MotionLapConfig | Mapping[str, Any],
) -> Any:
    """Build a reloadable tracksdata GEFF graph with motion provenance attrs."""

    import polars as pl
    import tracksdata as td

    config = config if isinstance(config, MotionLapConfig) else MotionLapConfig.from_mapping(config)
    velocities = estimate_velocities(candidates, config)
    graph = td.graph.IndexedRXGraph()
    for name in (
        "z",
        "y",
        "x",
        "physical_z",
        "physical_y",
        "physical_x",
        "score",
        "velocity_z",
        "velocity_y",
        "velocity_x",
    ):
        graph.add_node_attr_key(name, dtype=pl.Float64, default_value=0.0)
    for name in ("distance_um", "motion_cost", "prediction_error_um", "acceleration_um"):
        graph.add_edge_attr_key(name, dtype=pl.Float64, default_value=0.0)
    for index, (coordinate, physical, score, velocity) in enumerate(
        zip(candidates.coordinates, candidates.physical_coordinates, candidates.scores, velocities, strict=True),
    ):
        frame, z, y, x = coordinate
        physical_z, physical_y, physical_x = physical
        velocity_z, velocity_y, velocity_x = velocity
        node_id = graph.add_node(
            {
                "t": int(frame),
                "z": float(z),
                "y": float(y),
                "x": float(x),
                "physical_z": float(physical_z),
                "physical_y": float(physical_y),
                "physical_x": float(physical_x),
                "score": float(score),
                "velocity_z": float(velocity_z),
                "velocity_y": float(velocity_y),
                "velocity_x": float(velocity_x),
            },
            index=int(index),
        )
        if node_id != index:
            raise RuntimeError("tracksdata assigned a non-deterministic node ID")
    for pair, distance in zip(edges.pairs, edges.distances_um, strict=True):
        source_id, target_id = int(pair[0]), int(pair[1])
        cost, residual, acceleration, distance_value = _edge_score(
            source_id,
            target_id,
            candidates,
            velocities,
            config,
        )
        graph.add_edge(
            source_id,
            target_id,
            {
                "distance_um": float(distance_value if distance is None else distance),
                "motion_cost": float(cost),
                "prediction_error_um": float(residual),
                "acceleration_um": float(acceleration),
            },
        )
    return graph


def _source_revision() -> str:
    explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_REVISION")
    if explicit is None:
        explicit = os.environ.get("BIOHUB_BENCHMARK_RACE_SOURCE_COMMIT")
    if explicit and explicit.strip():
        return explicit.strip()
    return "unavailable-in-container"


def _source_file_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _manifest_contains_gt_reference(payload: object) -> bool:
    """Detect GT paths without treating the required false flag as a leak."""

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key).casefold()
            if key_text == "ground_truth_included":
                continue
            gt_markers = ("ground_truth_path", "ground_truth_digest", "gt_path", "gt_digest")
            if any(marker in key_text for marker in gt_markers):
                if value not in (None, ""):
                    return True
            if _manifest_contains_gt_reference(value):
                return True
        return False
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return any(_manifest_contains_gt_reference(item) for item in payload)
    if isinstance(payload, str):
        return payload.casefold().endswith(".geff") or _contains_ground_truth(payload)
    return False


def _candidate_table_from_npz(path: Path) -> CandidateTable:
    with np.load(path, allow_pickle=False) as data:
        required = {"coordinates", "physical_coordinates", "scores"}
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"blob candidate cache is missing arrays: {', '.join(missing)}")
        return CandidateTable(
            coordinates=np.asarray(data["coordinates"]),
            physical_coordinates=np.asarray(data["physical_coordinates"]),
            scores=np.asarray(data["scores"]),
        )


def _candidate_table_digest(candidates: CandidateTable) -> str:
    """Digest semantic candidate arrays in a stable order."""

    digest = hashlib.sha256()
    for name, values in (
        ("coordinates", candidates.coordinates),
        ("physical_coordinates", candidates.physical_coordinates),
        ("scores", candidates.scores),
    ):
        array = np.ascontiguousarray(values)
        digest.update(name.encode("ascii"))
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(repr(tuple(array.shape)).encode("ascii"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def _resolve_cache_paths(
    blob_cache: str | Path | PredictionArtifact,
) -> tuple[CandidateTable | None, Path | None, dict[str, Any]]:
    if isinstance(blob_cache, CandidateTable):
        raise ValueError(
            "run_motion_lap requires a persisted blob candidate cache; "
            "use link_motion for in-memory fixtures",
        )
    if isinstance(blob_cache, PredictionArtifact):
        manifest_path = Path(blob_cache.cache_manifest_path)
        detections_path = manifest_path.parent / "detections.npz"
    else:
        cache_path = Path(blob_cache)
        if cache_path.suffix.casefold() == ".npz":
            detections_path = cache_path
            manifest_path = cache_path.parent / "cache_manifest.json"
        elif cache_path.name == "cache_manifest.json":
            manifest_path = cache_path
            detections_path = cache_path.parent / "detections.npz"
        elif cache_path.is_dir():
            detections_path = cache_path / "detections.npz"
            manifest_path = cache_path / "cache_manifest.json"
        else:
            raise FileNotFoundError(f"blob candidate cache path not found or unsupported: {cache_path}")
    if not detections_path.exists():
        raise FileNotFoundError(f"blob candidate cache detections not found: {detections_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"blob candidate cache manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid blob candidate cache manifest: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("blob candidate cache manifest must be a JSON object")
    if payload.get("ground_truth_included") is not False:
        raise ValueError("blob candidate cache must declare ground_truth_included=false")
    if _manifest_contains_gt_reference(payload):
        raise ValueError("blob candidate cache manifest contains a ground-truth reference")
    detector_config = payload.get("detector_config")
    if not isinstance(detector_config, Mapping):
        raise ValueError("blob candidate cache manifest must contain detector_config")
    if detector_config.get("division_enabled", False):
        raise ValueError("blob candidate cache division must be disabled")
    if "peak_threshold" not in detector_config or "gaussian_sigma" not in detector_config:
        raise ValueError("motion_lap requires a blob_lap detector candidate cache")
    return _candidate_table_from_npz(detections_path), manifest_path, payload


def _validate_cache_for_request(
    candidates: CandidateTable,
    manifest_path: Path | None,
    manifest: Mapping[str, Any],
    request: RaceRequest,
    *,
    detections_digest: str,
) -> str:
    if manifest_path is None:
        return "in_memory"
    image_stem = manifest.get("image_stem")
    if image_stem != request.sample.image_stem.as_posix():
        raise ValueError(
            "blob candidate cache image_stem disagrees with request: "
            f"got {image_stem!r}, expected {request.sample.image_stem.as_posix()!r}",
        )
    shape = manifest.get("shape")
    if not isinstance(shape, Sequence) or len(shape) != 4 or list(shape) != list(request.sample.shape):
        raise ValueError("blob candidate cache shape disagrees with request sample")
    if manifest.get("quantiles") != dict(request.sample.quantiles):
        raise ValueError("blob candidate cache quantiles disagree with request sample")
    scale = manifest.get("scale")
    if not isinstance(scale, Sequence) or len(scale) != 3 or not np.allclose(scale, request.sample.scale):
        raise ValueError("blob candidate cache scale disagrees with request sample")
    detector_config = manifest.get("detector_config")
    if not isinstance(detector_config, Mapping):
        raise ValueError("blob candidate cache manifest must contain detector_config")
    requested_max_frames = request.config.get("max_frames")
    cached_max_frames = detector_config.get("max_frames")
    if requested_max_frames is None:
        if cached_max_frames is not None:
            raise ValueError(
                "blob candidate cache max_frames disagrees with full request: "
                f"got {cached_max_frames!r}, expected None",
            )
    elif cached_max_frames is None or int(cached_max_frames) != int(requested_max_frames):
        raise ValueError(
            "blob candidate cache max_frames disagrees with request: "
            f"got {cached_max_frames!r}, expected {requested_max_frames!r}",
        )
    try:
        manifest_sample = SampleSpec(
            sample_id=str(manifest["sample_id"]),
            image_stem=str(manifest["image_stem"]),
            shape=tuple(int(value) for value in manifest["shape"]),
            scale=tuple(float(value) for value in manifest["scale"]),
            quantiles=manifest["quantiles"],
        )
        expected_manifest = build_cache_manifest(
            sample=manifest_sample,
            image_digest=str(manifest["image_digest"]),
            detector_config=detector_config,
            source_commit=str(manifest["source_commit"]),
            checkpoint_sha256=manifest.get("checkpoint_sha256"),
            schema_version=str(manifest["schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("blob candidate cache manifest is not a valid cache contract") from exc
    if len(candidates):
        if float(np.max(candidates.coordinates[:, 0])) >= request.sample.shape[0]:
            raise ValueError("blob candidate cache contains a frame outside the request sample")
        expected_physical = voxel_to_physical(candidates.coordinates[:, 1:], request.sample.scale)
        if not np.allclose(candidates.physical_coordinates, expected_physical, rtol=0.0, atol=1.0e-8):
            raise ValueError("blob candidate cache physical coordinates disagree with sample scale")
    cache_key = manifest.get("cache_key")
    if not isinstance(cache_key, str) or not cache_key:
        raise ValueError("blob candidate cache manifest must contain cache_key")
    if cache_key != expected_manifest["cache_key"]:
        raise ValueError("blob candidate cache cache_key does not match manifest contents")
    expected_detections_digest = manifest.get("detections_sha256")
    if not isinstance(expected_detections_digest, str) or expected_detections_digest != detections_digest:
        raise ValueError("blob candidate cache detections_sha256 is missing or does not match detections")
    return cache_key


def _subset_candidates(candidates: CandidateTable, max_frames: int | None) -> CandidateTable:
    if max_frames is None:
        return candidates
    mask = candidates.coordinates[:, 0] < float(max_frames)
    if bool(mask.all()):
        return candidates
    return CandidateTable(
        coordinates=candidates.coordinates[mask],
        physical_coordinates=candidates.physical_coordinates[mask],
        scores=candidates.scores[mask],
    )


def _find_existing_blob_cache(request: RaceRequest) -> Path | None:
    root = Path(request.cache_root).resolve()
    if not root.exists():
        return None
    requested_max_frames = request.config.get("max_frames")
    for manifest_path in sorted(root.rglob("cache_manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("ground_truth_included") is not False:
            continue
        if payload.get("image_stem") != request.sample.image_stem.as_posix():
            continue
        shape = payload.get("shape")
        if not isinstance(shape, Sequence) or list(shape) != list(request.sample.shape):
            continue
        detector_config = payload.get("detector_config")
        if not isinstance(detector_config, Mapping) or "peak_threshold" not in detector_config:
            continue
        cached_max_frames = detector_config.get("max_frames")
        if requested_max_frames is None and cached_max_frames is not None:
            continue
        if requested_max_frames is not None and cached_max_frames != requested_max_frames:
            continue
        detections = manifest_path.parent / "detections.npz"
        if detections.exists() and payload.get("detections_sha256") == _file_sha256(detections):
            return manifest_path
    return None


def ensure_blob_cache(request: RaceRequest) -> Path:
    """Return a compatible blob cache, creating it with the existing adapter once."""

    if not isinstance(request, RaceRequest):
        raise TypeError("request must be a RaceRequest")
    existing = _find_existing_blob_cache(request)
    if existing is not None:
        return existing
    from biohub.benchmark_race.blob_lap import run_blob_lap

    blob_config: dict[str, Any] = {}
    if "max_frames" in request.config:
        blob_config["max_frames"] = request.config["max_frames"]
    blob_request = RaceRequest(
        sample=request.sample,
        cache_root=request.cache_root,
        output_root=request.output_root,
        expected_device=request.expected_device,
        config=blob_config,
    )
    try:
        artifact = run_blob_lap(blob_request)
    except FileExistsError:
        existing = _find_existing_blob_cache(request)
        if existing is None:
            raise
        return existing
    return Path(artifact.cache_manifest_path)


def run_motion_lap(
    request: RaceRequest,
    blob_cache: str | Path | PredictionArtifact | CandidateTable,
) -> PredictionArtifact:
    """Run image-only velocity-aware association over a fixed blob cache."""

    if not isinstance(request, RaceRequest):
        raise TypeError("request must be a RaceRequest")
    if request.expected_device.casefold() != "cpu":
        raise ValueError("motion_lap currently supports only the CPU device")
    started_at = _timestamp()
    started = time.monotonic()
    request_config = dict(request.config)
    max_frames_value = request_config.pop("max_frames", None)
    if max_frames_value is None:
        max_frames = None
    else:
        if isinstance(max_frames_value, bool):
            raise ValueError("max_frames must be a positive integer")
        max_frames = int(max_frames_value)
        if max_frames <= 0:
            raise ValueError("max_frames must be a positive integer")
    config = MotionLapConfig.from_mapping(request_config, scale=request.sample.scale)
    expected_scale = tuple(float(value) for value in request.sample.scale)
    if config.scale != expected_scale:
        raise ValueError(
            "motion_lap config scale must match request.sample.scale; "
            f"got {config.scale!r}, expected {expected_scale!r}",
        )
    candidates, manifest_path, manifest = _resolve_cache_paths(blob_cache)
    if candidates is None:  # pragma: no cover - _resolve_cache_paths always returns a table
        raise RuntimeError("blob candidate cache did not produce candidate rows")
    if manifest_path is None:  # pragma: no cover - persisted cache is required above
        raise RuntimeError("motion_lap requires a persisted candidate cache manifest")
    detections_digest = _file_sha256(manifest_path.parent / "detections.npz")
    cache_key = _validate_cache_for_request(
        candidates,
        manifest_path,
        manifest,
        request,
        detections_digest=detections_digest,
    )
    candidates = _subset_candidates(candidates, max_frames)
    edges = link_motion(candidates, None, config)
    source_revision = _source_revision()
    source_file_sha256 = _source_file_digest()

    output_root = Path(request.output_root).resolve()
    target = output_root / "methods" / "motion_lap" / f"{Path(request.sample.image_stem).stem}.geff"
    if target.exists():
        raise FileExistsError(f"prediction destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    graph = build_prediction_graph(candidates, edges, config)
    graph.to_geff(target)
    manifest_payload = prediction_directory_manifest(target)
    manifest_payload.update(
        {
            "method_id": "motion_lap",
            "method_family": "classical_motion_association",
            "candidate_count": len(candidates),
            "edge_count": len(edges),
            "division_enabled": False,
            "ground_truth_included": False,
            "candidate_cache_method": "blob_lap",
            "candidate_cache_key": cache_key,
            "candidate_cache_manifest": str(manifest_path) if manifest_path is not None else None,
            "config": config.as_dict(),
            "source_revision": source_revision,
            "source_file_sha256": source_file_sha256,
        },
    )
    manifest_path_out = write_prediction_manifest(target, manifest_payload)
    elapsed = time.monotonic() - started
    actual_shape = list(request.sample.shape)
    if max_frames is not None:
        actual_shape[0] = max_frames
    run_payload = {
        "method_id": "motion_lap",
        "method_family": "classical_motion_association",
        "detector_id": "blob_lap_fixed_image_only_candidates",
        "linker_id": "velocity_acceleration_hungarian_lap",
        "version": "motion_lap.v1",
        "source_module": "biohub.benchmark_race.motion",
        "source_revision": source_revision,
        "source_commit": source_revision,
        "source_file_sha256": source_file_sha256,
        "checkpoint_sha256": None,
        "official_detector_shared": False,
        "official_detector_motion": "deferred",
        "candidate_cache_method": "blob_lap",
        "candidate_cache_key": cache_key,
        "candidate_cache_manifest": str(manifest_path) if manifest_path is not None else None,
        "sample_id": request.sample.sample_id,
        "image_stem": request.sample.image_stem.as_posix(),
        "image_shape": actual_shape,
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
        "solver": "scipy.optimize.linear_sum_assignment",
        "solver_status": "optimal",
        "graph_optimization": "frame_local_velocity_acceleration_lap",
        "prediction_manifest": str(manifest_path_out),
    }
    run_json_path = target.parent / "run.json"
    run_json_path.write_text(json.dumps(run_payload, indent=2, sort_keys=True) + "\n")
    (target.parent / "inference.log").write_text(
        f"method=motion_lap candidates={len(candidates)} edges={len(edges)} "
        "solver=scipy.optimize.linear_sum_assignment device=cpu\n",
    )
    cache_manifest_path = manifest_path if manifest_path is not None else Path("in_memory")
    return PredictionArtifact(
        prediction_path=target,
        prediction_manifest_path=manifest_path_out,
        run_json_path=run_json_path,
        candidate_count=len(candidates),
        edge_count=len(edges),
        cache_manifest_path=cache_manifest_path,
    )


__all__ = [
    "MotionLapConfig",
    "build_prediction_graph",
    "ensure_blob_cache",
    "estimate_accelerations",
    "estimate_velocities",
    "link_motion",
    "motion_cost",
    "run_motion_lap",
]
