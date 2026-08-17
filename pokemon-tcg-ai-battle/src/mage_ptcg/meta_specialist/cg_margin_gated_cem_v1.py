"""Dependency-free CEM core for the margin-gated P1 surface."""

from __future__ import annotations

import math
import random
import statistics
from typing import Mapping, Sequence

from .cg_p1_margin_gated_renderer_v1 import PARAMETER_BOUNDS, MarginGatedConfig


SCHEMA = "cg-margin-gated-cem-state-v1"


def _clamp(name: str, value: int) -> int:
    lower, upper = PARAMETER_BOUNDS[name]
    return max(lower, min(upper, int(value)))


def sample_population(
    center: MarginGatedConfig,
    *,
    generation: int,
    population_size: int = 8,
    seed: int = 20260816,
    scales: Mapping[str, float] | None = None,
) -> tuple[MarginGatedConfig, ...]:
    """Sample a deterministic population, preserving the center at index 0."""

    center.validate()
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if type(population_size) is not int or population_size <= 0:
        raise ValueError("population_size must be positive")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    names = tuple(center.as_dict())
    if scales is not None and set(scales) != set(names):
        raise ValueError("CEM scales do not match parameter surface")
    rng = random.Random(seed + generation * 1_000_003)
    values = [center]
    center_values = center.as_dict()
    while len(values) < population_size:
        sampled: dict[str, int] = {}
        for name, current in center_values.items():
            lower, upper = PARAMETER_BOUNDS[name]
            sigma = max(1.0, float(scales[name]) if scales is not None else (upper - lower) * 0.15)
            sampled[name] = _clamp(name, int(round(rng.gauss(current, sigma))))
        values.append(MarginGatedConfig.from_mapping(sampled))
    return tuple(values)


def rank_valid_results(
    results: Sequence[Mapping[str, object]], *, elite_count: int
) -> tuple[dict[str, object], ...]:
    """Return finite, fault-free results ordered by objective descending."""

    if type(elite_count) is not int or elite_count <= 0:
        raise ValueError("elite_count must be positive")
    valid: list[dict[str, object]] = []
    for raw in results:
        config_raw = raw.get("config")
        if isinstance(config_raw, MarginGatedConfig):
            config = config_raw
        elif isinstance(config_raw, Mapping):
            try:
                config = MarginGatedConfig.from_mapping(config_raw)
            except ValueError:
                continue
        else:
            continue
        objective = raw.get("objective")
        faults = raw.get("faults", 0)
        if type(faults) is not int or faults != 0:
            continue
        if type(objective) not in (int, float) or not math.isfinite(float(objective)):
            continue
        if raw.get("valid") is False:
            continue
        normalized = dict(raw)
        normalized["config"] = config
        normalized["objective"] = float(objective)
        valid.append(normalized)
    valid.sort(key=lambda item: (-float(item["objective"]), item["config"].config_sha256()))
    if len(valid) < elite_count:
        raise ValueError(f"not enough valid candidates: {len(valid)} < {elite_count}")
    return tuple(valid[:elite_count])


def update_distribution(
    center: MarginGatedConfig,
    elites: Sequence[Mapping[str, object]],
) -> tuple[MarginGatedConfig, dict[str, float]]:
    """Update means and bounded standard-deviation scales from elites."""

    center.validate()
    if not elites:
        raise ValueError("elites cannot be empty")
    configs: list[MarginGatedConfig] = []
    for item in elites:
        value = item.get("config")
        config = value if isinstance(value, MarginGatedConfig) else MarginGatedConfig.from_mapping(value)
        configs.append(config)
    updated: dict[str, int] = {}
    scales: dict[str, float] = {}
    for name in center.as_dict():
        values = [getattr(config, name) for config in configs]
        updated[name] = _clamp(name, int(round(statistics.fmean(values))))
        lower, upper = PARAMETER_BOUNDS[name]
        floor = max(1.0, (upper - lower) / 64.0)
        scales[name] = max(floor, float(statistics.pstdev(values)))
    return MarginGatedConfig.from_mapping(updated), scales


__all__ = ["SCHEMA", "rank_valid_results", "sample_population", "update_distribution"]
