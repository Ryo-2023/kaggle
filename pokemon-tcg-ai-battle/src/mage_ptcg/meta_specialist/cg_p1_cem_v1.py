"""Small dependency-free Cross-Entropy Method core for cg P1 research."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import statistics
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (
    PARAMETER_BOUNDS,
    P1ParameterConfig,
)


SCHEMA = "cg-p1-cem-state-v1"


@dataclass(frozen=True, slots=True)
class CemCampaignConfig:
    population_size: int = 24
    elite_count: int = 6
    generations: int = 6
    games_per_candidate: int = 48
    top6_independent_games: int = 96
    dev_games: int = 96
    workers: int = 12
    worker_recycle_games: int = 16
    seed: int = 20260815
    max_steps: int = 2000

    def validate(self) -> None:
        for name in (
            "population_size", "elite_count", "generations", "games_per_candidate",
            "top6_independent_games", "dev_games", "workers", "worker_recycle_games",
            "seed", "max_steps",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.elite_count > self.population_size:
            raise ValueError("elite_count cannot exceed population_size")
        if self.games_per_candidate != 48:
            raise ValueError("weekend CEM requires 48 games per candidate")
        if self.workers != 12 or self.worker_recycle_games != 16:
            raise ValueError("weekend CEM requires workers=12 and recycle=16")


def _clamp(name: str, value: int) -> int:
    lower, upper = PARAMETER_BOUNDS[name]
    return max(lower, min(upper, int(value)))


def sample_population(
    center: P1ParameterConfig,
    *,
    generation: int,
    population_size: int = 24,
    seed: int = 20260815,
    scales: Mapping[str, float] | None = None,
) -> tuple[P1ParameterConfig, ...]:
    """Sample a deterministic integer population with the center as control."""

    center.validate()
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if type(population_size) is not int or population_size <= 0:
        raise ValueError("population_size must be positive")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    rng = random.Random(seed + generation * 1_000_003)
    values = [center]
    center_values = center.as_dict()
    if scales is not None and set(scales) != set(center_values):
        raise ValueError("CEM scales do not match parameter surface")
    while len(values) < population_size:
        candidate: dict[str, int] = {}
        for name, current in center_values.items():
            lower, upper = PARAMETER_BOUNDS[name]
            span = upper - lower
            sigma = max(1.0, float(scales[name]) if scales is not None else span * 0.25)
            candidate[name] = _clamp(name, int(round(rng.gauss(current, sigma))))
        values.append(P1ParameterConfig.from_mapping(candidate))
    return tuple(values)


def rank_valid_results(results: Sequence[Mapping[str, object]], *, elite_count: int) -> tuple[dict[str, object], ...]:
    if type(elite_count) is not int or elite_count <= 0:
        raise ValueError("elite_count must be positive")
    valid: list[dict[str, object]] = []
    for raw in results:
        config_raw = raw.get("config")
        if isinstance(config_raw, P1ParameterConfig):
            config = config_raw
        elif isinstance(config_raw, Mapping):
            config = P1ParameterConfig.from_mapping(config_raw)
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
        raise ValueError(f"not enough valid candidates for elite update: {len(valid)} < {elite_count}")
    return tuple(valid[:elite_count])


def update_distribution(
    center: P1ParameterConfig,
    elites: Sequence[Mapping[str, object]],
) -> tuple[P1ParameterConfig, dict[str, float]]:
    center.validate()
    if not elites:
        raise ValueError("elites cannot be empty")
    configs = []
    for item in elites:
        value = item.get("config")
        config = value if isinstance(value, P1ParameterConfig) else P1ParameterConfig.from_mapping(value)
        configs.append(config)
    updated: dict[str, int] = {}
    scales: dict[str, float] = {}
    for name in center.as_dict():
        values = [getattr(config, name) for config in configs]
        mean = int(round(statistics.fmean(values)))
        updated[name] = _clamp(name, mean)
        lower, upper = PARAMETER_BOUNDS[name]
        floor = max(1.0, (upper - lower) / 64.0)
        scales[name] = max(floor, float(statistics.pstdev(values)))
    return P1ParameterConfig.from_mapping(updated), scales


def aggregate_candidate_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    weights: Mapping[str, float],
) -> dict[str, object]:
    """Return a fault-inclusive weighted objective and bounded diagnostics."""

    requested = len(rows)
    outcomes = {"win": 0, "draw": 0, "loss": 0, "fault": 0}
    by_opponent: dict[str, dict[str, object]] = {}
    by_opponent_seat: dict[str, dict[str, dict[str, int]]] = {}
    by_seat: dict[str, dict[str, int]] = {"0": {"win": 0, "draw": 0, "loss": 0, "fault": 0}, "1": {"win": 0, "draw": 0, "loss": 0, "fault": 0}}
    for row in rows:
        outcome = str(row.get("outcome", "fault"))
        if outcome not in outcomes:
            outcome = "fault"
        outcomes[outcome] += 1
        opponent_id = str(row.get("opponent_id", "unknown"))
        bucket = by_opponent.setdefault(opponent_id, {"wins": 0, "draws": 0, "losses": 0, "faults": 0, "requested_games": 0})
        bucket["requested_games"] = int(bucket["requested_games"]) + 1
        bucket_key = {"win": "wins", "draw": "draws", "loss": "losses", "fault": "faults"}[outcome]
        bucket[bucket_key] = int(bucket[bucket_key]) + 1
        seat = str(row.get("seat", "-1"))
        if seat in by_seat:
            by_seat[seat][outcome] += 1
            opponent_seats = by_opponent_seat.setdefault(
                opponent_id,
                {
                    "0": {"win": 0, "draw": 0, "loss": 0, "fault": 0},
                    "1": {"win": 0, "draw": 0, "loss": 0, "fault": 0},
                },
            )
            opponent_seats[seat][outcome] += 1
    total_weight = 0.0
    weighted_score = 0.0
    for opponent_id, bucket in by_opponent.items():
        count = int(bucket["requested_games"])
        score = (int(bucket["wins"]) + 0.5 * int(bucket["draws"])) / count if count else 0.0
        bucket["score_rate"] = score
        weight = float(weights.get(opponent_id, 0.0))
        if weight > 0:
            total_weight += weight
            weighted_score += weight * score
    fault_rate = outcomes["fault"] / requested if requested else 1.0
    objective = (weighted_score / total_weight if total_weight else 0.0) - fault_rate
    seat_rates = {}
    for seat, bucket in by_seat.items():
        count = sum(bucket.values())
        seat_rates[seat] = (bucket["win"] + 0.5 * bucket["draw"]) / count if count else None
    opponent_seat_rates: dict[str, dict[str, float]] = {}
    for opponent_id, seats in by_opponent_seat.items():
        rates: dict[str, float] = {}
        for seat, bucket in seats.items():
            count = sum(bucket.values())
            if count:
                rates[seat] = (bucket["win"] + 0.5 * bucket["draw"]) / count
        if rates:
            opponent_seat_rates[opponent_id] = rates
    populated_seats = [value for value in seat_rates.values() if value is not None]
    # 48-game candidates have only 24 games per seat.  Treat a zero-win seat
    # as catastrophic, but do not reject a candidate for a single observed
    # win; that is sampling noise at this pilot size.
    seat_collapse = bool(populated_seats and min(populated_seats) < 0.02)
    valid = outcomes["fault"] == 0 and requested > 0 and not seat_collapse
    return {
        "requested_games": requested,
        "wins": outcomes["win"],
        "draws": outcomes["draw"],
        "losses": outcomes["loss"],
        "faults": outcomes["fault"],
        "fault_rate": fault_rate,
        "objective": objective,
        "valid": valid,
        "seat_collapse": seat_collapse,
        "by_opponent": by_opponent,
        "by_seat": by_seat,
        "opponent_seat_rates": opponent_seat_rates,
        "seat_rates": seat_rates,
    }


@dataclass(frozen=True, slots=True)
class CemState:
    generation: int
    center: P1ParameterConfig
    scales: dict[str, float]
    next_candidate_index: int
    evaluated: list[dict[str, object]]
    campaign_identity: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        self.center.validate()
        return {
            "schema_version": SCHEMA,
            "generation": self.generation,
            "center": self.center.as_dict(),
            "scales": {str(key): float(value) for key, value in sorted(self.scales.items())},
            "next_candidate_index": self.next_candidate_index,
            "evaluated": self.evaluated,
            "campaign_identity": self.campaign_identity,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CemState":
        if payload.get("schema_version") != SCHEMA:
            raise ValueError("unexpected CEM state schema")
        center = P1ParameterConfig.from_mapping(payload.get("center", {}))
        scales = payload.get("scales")
        if not isinstance(scales, Mapping) or set(scales) != set(center.as_dict()):
            raise ValueError("CEM scales do not match parameter surface")
        normalized_scales: dict[str, float] = {}
        for name, value in scales.items():
            if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"invalid CEM scale: {name}")
            normalized_scales[str(name)] = float(value)
        generation = payload.get("generation")
        next_index = payload.get("next_candidate_index")
        if type(generation) is not int or generation < 0 or type(next_index) is not int or next_index < 0:
            raise ValueError("invalid CEM state counters")
        evaluated = payload.get("evaluated")
        identity = payload.get("campaign_identity")
        if not isinstance(evaluated, list) or not isinstance(identity, Mapping):
            raise ValueError("invalid CEM state payload")
        return cls(generation, center, normalized_scales, next_index, list(evaluated), dict(identity))


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
        raise FileNotFoundError("no CEM checkpoint")
    return CemState.from_dict(json.loads(paths[-1].read_text(encoding="utf-8")))
