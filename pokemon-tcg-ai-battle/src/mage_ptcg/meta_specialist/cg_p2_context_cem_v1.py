"""Small dependency-free Cross-Entropy Method core for the P2 context surface.

The core deliberately knows nothing about CABT execution.  It only samples a
bounded, deterministic population, applies a fail-closed result gate, and
updates the distribution from accepted elites.  The runner owns package
materialization, paired evaluation, and the research-only authority boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import statistics
import tempfile
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import (
    PARAMETER_BOUNDS,
    P2ContextConfig,
)


SCHEMA = "cg-p2-context-cem-state-v1"


def _clamp(name: str, value: int) -> int:
    try:
        lower, upper = PARAMETER_BOUNDS[name]
    except KeyError as exc:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown P2 context parameter: {name}") from exc
    return max(lower, min(upper, int(value)))


def _validate_scales(center: P2ContextConfig, scales: Mapping[str, float] | None) -> dict[str, float] | None:
    if scales is None:
        return None
    names = set(center.as_dict())
    if set(scales) != names:
        raise ValueError("CEM scales do not match parameter surface")
    normalized: dict[str, float] = {}
    for name, value in scales.items():
        if isinstance(value, bool) or type(value) not in (int, float):
            raise ValueError(f"invalid CEM scale: {name}")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"invalid CEM scale: {name}")
        normalized[str(name)] = numeric
    return normalized


def sample_population(
    center: P2ContextConfig,
    *,
    generation: int,
    population_size: int = 8,
    seed: int = 20260815,
    scales: Mapping[str, float] | None = None,
) -> tuple[P2ContextConfig, ...]:
    """Sample a deterministic integer population with the center first.

    The center is retained as the first arm in every generation.  Other arms
    are Gaussian perturbations clamped to the declared surface bounds.  A
    duplicate configuration is not emitted because the screen runner binds
    one package per configuration and requires unique candidate identities.
    """

    center.validate()
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if type(population_size) is not int or population_size <= 0:
        raise ValueError("population_size must be positive")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    normalized_scales = _validate_scales(center, scales)
    rng = random.Random(seed + generation * 1_000_003)
    values: list[P2ContextConfig] = [center]
    seen = {center.config_sha256()}
    center_values = center.as_dict()
    attempts = 0
    max_attempts = max(100, population_size * 200)
    while len(values) < population_size and attempts < max_attempts:
        attempts += 1
        candidate: dict[str, int] = {}
        for name, current in center_values.items():
            lower, upper = PARAMETER_BOUNDS[name]
            span = upper - lower
            sigma = max(1.0, normalized_scales[name] if normalized_scales is not None else span * 0.25)
            candidate[name] = _clamp(name, int(round(rng.gauss(current, sigma))))
        config = P2ContextConfig.from_mapping(candidate)
        digest = config.config_sha256()
        if digest not in seen:
            values.append(config)
            seen.add(digest)
    if len(values) != population_size:
        raise ValueError("unable to sample a unique P2 context population")
    return tuple(values)


def _result_objective(raw: Mapping[str, object]) -> float | None:
    value = raw.get("delta_objective", raw.get("objective"))
    if isinstance(value, bool) or type(value) not in (int, float):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def rank_valid_results(
    results: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
    positive_delta_gate: bool = True,
) -> tuple[dict[str, object], ...]:
    """Return deterministic elites after the P2 fail-closed quality gate."""

    if type(elite_count) is not int or elite_count <= 0:
        raise ValueError("elite_count must be positive")
    if type(positive_delta_gate) is not bool:
        raise ValueError("positive_delta_gate must be boolean")
    valid: list[dict[str, object]] = []
    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        config_raw = raw.get("config")
        try:
            config = config_raw if isinstance(config_raw, P2ContextConfig) else P2ContextConfig.from_mapping(config_raw)
        except (TypeError, ValueError):
            continue
        faults = raw.get("faults", 0)
        if type(faults) is not int or faults != 0:
            continue
        # An absent or ambiguous seat result is not safe enough for an update.
        if raw.get("candidate_seat_safe") is not True:
            continue
        if raw.get("valid") is False:
            continue
        objective = _result_objective(raw)
        if objective is None or (positive_delta_gate and objective <= 0.0):
            continue
        normalized = dict(raw)
        normalized["config"] = config
        normalized["delta_objective"] = objective
        normalized["objective"] = objective
        valid.append(normalized)
    valid.sort(
        key=lambda item: (
            -float(item["delta_objective"]),
            item["config"].config_sha256(),
            str(item.get("candidate_id", "")),
        )
    )
    if len(valid) < elite_count:
        raise ValueError(f"not enough valid candidates for elite update: {len(valid)} < {elite_count}")
    return tuple(valid[:elite_count])


def rank_robust_results(
    results: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
    min_independent_blocks: int = 2,
) -> tuple[dict[str, object], ...]:
    """Rank candidates by their worst independent positive block.

    A screen result is not sufficient for a robust CEM update.  Each row must
    carry at least ``min_independent_blocks`` re-evaluations, and every block
    must be fault-free, seat-safe, and strictly positive versus its paired
    control.  The minimum block delta is the only score used for ranking.
    """

    if type(elite_count) is not int or elite_count <= 0:
        raise ValueError("elite_count must be positive")
    if type(min_independent_blocks) is not int or min_independent_blocks <= 0:
        raise ValueError("min_independent_blocks must be positive")
    valid: list[dict[str, object]] = []
    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        config_raw = raw.get("config")
        try:
            config = config_raw if isinstance(config_raw, P2ContextConfig) else P2ContextConfig.from_mapping(config_raw)
        except (TypeError, ValueError):
            continue
        faults = raw.get("faults", 0)
        if type(faults) is not int or faults != 0:
            continue
        blocks = raw.get("independent_blocks")
        if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
            continue
        if len(blocks) < min_independent_blocks:
            continue
        deltas: list[float] = []
        safe = True
        for block in blocks:
            if not isinstance(block, Mapping):
                safe = False
                break
            block_faults = block.get("faults", 0)
            delta = _result_objective(block)
            if (
                type(block_faults) is not int
                or block_faults != 0
                or block.get("candidate_seat_safe") is not True
                or delta is None
                or delta <= 0.0
            ):
                safe = False
                break
            deltas.append(delta)
        if not safe:
            continue
        robust_delta = min(deltas)
        normalized = dict(raw)
        normalized["config"] = config
        normalized["robust_delta_objective"] = robust_delta
        normalized["delta_objective"] = robust_delta
        normalized["objective"] = robust_delta
        normalized["independent_block_count"] = len(deltas)
        normalized["candidate_seat_safe"] = True
        normalized["faults"] = 0
        valid.append(normalized)
    valid.sort(
        key=lambda item: (
            -float(item["robust_delta_objective"]),
            item["config"].config_sha256(),
            str(item.get("candidate_id", "")),
        )
    )
    if len(valid) < elite_count:
        raise ValueError(f"not enough valid robust candidates for elite update: {len(valid)} < {elite_count}")
    return tuple(valid[:elite_count])


def update_distribution(
    center: P2ContextConfig,
    elites: Sequence[Mapping[str, object]],
) -> tuple[P2ContextConfig, dict[str, float]]:
    """Move the center to the elite mean and retain a non-zero scale floor."""

    center.validate()
    if not elites:
        raise ValueError("elites cannot be empty")
    configs: list[P2ContextConfig] = []
    for item in elites:
        if not isinstance(item, Mapping):
            raise ValueError("elite rows must be mappings")
        value = item.get("config")
        config = value if isinstance(value, P2ContextConfig) else P2ContextConfig.from_mapping(value)
        configs.append(config)
    updated: dict[str, int] = {}
    scales: dict[str, float] = {}
    for name in center.as_dict():
        values = [getattr(config, name) for config in configs]
        updated[name] = _clamp(name, int(round(statistics.fmean(values))))
        lower, upper = PARAMETER_BOUNDS[name]
        floor = max(1.0, (upper - lower) / 64.0)
        scales[name] = max(floor, float(statistics.pstdev(values)))
    return P2ContextConfig.from_mapping(updated), scales


@dataclass(frozen=True, slots=True)
class CemState:
    """Serializable generation checkpoint for resumable research-only runs."""

    generation: int
    center: P2ContextConfig
    scales: dict[str, float]
    next_candidate_index: int
    evaluated: list[dict[str, object]]
    campaign_identity: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        self.center.validate()
        normalized_scales = _validate_scales(self.center, self.scales)
        assert normalized_scales is not None
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if type(self.next_candidate_index) is not int or self.next_candidate_index < 0:
            raise ValueError("next_candidate_index must be non-negative")
        if not isinstance(self.evaluated, list) or not isinstance(self.campaign_identity, Mapping):
            raise ValueError("invalid CEM state payload")
        return {
            "schema_version": SCHEMA,
            "generation": self.generation,
            "center": self.center.as_dict(),
            "scales": {str(key): float(value) for key, value in sorted(normalized_scales.items())},
            "next_candidate_index": self.next_candidate_index,
            "evaluated": self.evaluated,
            "campaign_identity": dict(self.campaign_identity),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CemState":
        if payload.get("schema_version") != SCHEMA:
            raise ValueError("unexpected P2 CEM state schema")
        center = P2ContextConfig.from_mapping(payload.get("center", {}))
        scales_raw = payload.get("scales")
        if not isinstance(scales_raw, Mapping):
            raise ValueError("invalid P2 CEM scales")
        scales = _validate_scales(center, scales_raw)
        assert scales is not None
        generation = payload.get("generation")
        next_index = payload.get("next_candidate_index")
        if type(generation) is not int or generation < 0 or type(next_index) is not int or next_index < 0:
            raise ValueError("invalid P2 CEM state counters")
        evaluated = payload.get("evaluated")
        identity = payload.get("campaign_identity")
        if not isinstance(evaluated, list) or not isinstance(identity, Mapping):
            raise ValueError("invalid P2 CEM state payload")
        return cls(generation, center, scales, next_index, list(evaluated), dict(identity))


def save_checkpoint(root: Path | str, state: CemState) -> Path:
    """Publish one generation checkpoint without clobbering an existing file."""

    state_path = Path(root).resolve() / "checkpoints" / f"checkpoint-g{state.generation:04d}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        raise FileExistsError(state_path)
    raw = (json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{state_path.name}.tmp-", dir=state_path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, state_path, follow_symlinks=False)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state_path


def load_latest_checkpoint(root: Path | str) -> CemState:
    paths = sorted((Path(root).resolve() / "checkpoints").glob("checkpoint-g*.json"))
    if not paths:
        raise FileNotFoundError("no P2 CEM checkpoint")
    return CemState.from_dict(json.loads(paths[-1].read_text(encoding="utf-8")))


__all__ = [
    "CemState",
    "SCHEMA",
    "load_latest_checkpoint",
    "rank_robust_results",
    "rank_valid_results",
    "sample_population",
    "save_checkpoint",
    "update_distribution",
]
