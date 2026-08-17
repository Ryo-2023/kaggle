"""Deterministic, seat-balanced bootstrap selection schedules and scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import quantiles
from typing import Any, Mapping, Sequence

from mage_ptcg.continuous_league.contracts import content_id, require_sha256

from .contracts import BootstrapContractError


@dataclass(frozen=True, slots=True)
class BootstrapTournamentSpec:
    screen_games_per_candidate: int = 256
    validation_games_per_candidate: int = 1024
    finalists: int = 4
    screen_seed_namespace: str = "bootstrap-screen-v1"
    validation_seed_namespace: str = "bootstrap-validation-v1"

    def validate(self) -> None:
        if self.screen_games_per_candidate < 2 or self.validation_games_per_candidate < 2 or self.finalists < 1:
            raise BootstrapContractError("invalid Bootstrap tournament specification")
        if self.screen_seed_namespace == self.validation_seed_namespace:
            raise BootstrapContractError("screen and validation seed namespaces must differ")


@dataclass(frozen=True, slots=True)
class BootstrapMatch:
    candidate_id: str
    opponent_instance_id: str
    seat: str
    repetition_index: int
    env_seed: int
    game_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "opponent_instance_id": self.opponent_instance_id,
            "seat": self.seat,
            "repetition_index": self.repetition_index,
            "env_seed": self.env_seed,
            "game_key": self.game_key,
        }


@dataclass(frozen=True, slots=True)
class BootstrapScore:
    candidate_id: str
    opponent_equal_score_rate: float
    worst_opponent_score_rate: float
    wilson_lower_bound: float
    latency_p95_seconds: float
    overall_score_rate: float
    seat_score_rates: Mapping[str, float]
    fault_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "opponent_equal_score_rate": self.opponent_equal_score_rate,
            "worst_opponent_score_rate": self.worst_opponent_score_rate,
            "wilson_lower_bound": self.wilson_lower_bound,
            "latency_p95_seconds": (
                self.latency_p95_seconds
                if math.isfinite(self.latency_p95_seconds)
                else None
            ),
            "overall_score_rate": self.overall_score_rate,
            "seat_score_rates": dict(self.seat_score_rates),
            "fault_count": self.fault_count,
        }


def build_candidate_schedule(
    *,
    candidate_ids: Sequence[str],
    opponent_instance_ids: Sequence[str],
    games_per_candidate: int,
    seed_namespace: str,
) -> tuple[BootstrapMatch, ...]:
    """Build common-random-number games without deriving seeds from candidate."""

    candidates = tuple(sorted(set(candidate_ids)))
    opponents = tuple(sorted(set(opponent_instance_ids)))
    if not candidates or not opponents or not seed_namespace:
        raise BootstrapContractError("Bootstrap schedule needs candidates, opponents, and seed namespace")
    for candidate in candidates:
        try:
            require_sha256(candidate, "candidate_id")
        except ValueError as exc:
            raise BootstrapContractError(str(exc)) from exc
    for opponent in opponents:
        try:
            require_sha256(opponent, "opponent_instance_id")
        except ValueError as exc:
            raise BootstrapContractError(str(exc)) from exc
    cells_per_repetition = len(opponents) * 2
    if games_per_candidate < cells_per_repetition or games_per_candidate % cells_per_repetition:
        raise BootstrapContractError(
            "games_per_candidate must be a positive multiple of opponents × two seats"
        )
    repetitions = games_per_candidate // cells_per_repetition
    matches: list[BootstrapMatch] = []
    for repetition_index in range(repetitions):
        for opponent_instance_id in opponents:
            for seat in ("subject_first", "subject_second"):
                cell = {
                    "seed_namespace": seed_namespace,
                    "opponent_instance_id": opponent_instance_id,
                    "seat": seat,
                    "repetition_index": repetition_index,
                }
                env_seed = int(content_id("bootstrap-env-seed-v1", cell)[:16], 16) % (2**31 - 1)
                for candidate_id in candidates:
                    identity = {"candidate_id": candidate_id, **cell}
                    matches.append(
                        BootstrapMatch(
                            candidate_id=candidate_id,
                            opponent_instance_id=opponent_instance_id,
                            seat=seat,
                            repetition_index=repetition_index,
                            env_seed=env_seed,
                            game_key=content_id("bootstrap-game-v1", identity),
                        )
                    )
    return tuple(matches)


def _score(outcome: str) -> float:
    if outcome == "win":
        return 1.0
    if outcome == "draw":
        return 0.5
    if outcome == "loss":
        return 0.0
    raise BootstrapContractError(f"unknown Bootstrap outcome: {outcome}")


def _wilson_lower(successes: float, total: int, z: float = 1.95996398454) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return max(0.0, (centre - radius) / denominator)


def summarize_candidate(rows: Sequence[Mapping[str, Any]]) -> BootstrapScore:
    if not rows:
        raise BootstrapContractError("cannot summarize an empty Bootstrap result")
    candidate_ids = {str(row.get("candidate_id")) for row in rows}
    if len(candidate_ids) != 1:
        raise BootstrapContractError("Bootstrap score rows must belong to one candidate")
    candidate_id = next(iter(candidate_ids))
    fault_count = sum(bool(row.get("fault")) for row in rows)
    valid = [row for row in rows if not row.get("fault")]
    if not valid:
        # Faulted candidates are never eligible for ranking.  Keep their
        # diagnostics JSON-safe so the screen ledger remains inspectable.
        return BootstrapScore(candidate_id, 0.0, 0.0, 0.0, 0.0, 0.0, {}, fault_count)
    by_opponent: dict[str, list[float]] = {}
    by_seat: dict[str, list[float]] = {}
    durations: list[float] = []
    for row in valid:
        value = _score(str(row["outcome"]))
        by_opponent.setdefault(str(row["opponent_instance_id"]), []).append(value)
        by_seat.setdefault(str(row["seat"]), []).append(value)
        duration = row.get("duration_seconds")
        if isinstance(duration, (int, float)) and math.isfinite(float(duration)) and float(duration) >= 0:
            durations.append(float(duration))
    opponent_rates = [sum(values) / len(values) for values in by_opponent.values()]
    total_score = sum(_score(str(row["outcome"])) for row in valid)
    if len(durations) < 2:
        p95 = durations[0] if durations else math.inf
    else:
        p95 = quantiles(durations, n=100, method="inclusive")[94]
    return BootstrapScore(
        candidate_id=candidate_id,
        opponent_equal_score_rate=sum(opponent_rates) / len(opponent_rates),
        worst_opponent_score_rate=min(opponent_rates),
        wilson_lower_bound=_wilson_lower(total_score, len(valid)),
        latency_p95_seconds=p95,
        overall_score_rate=total_score / len(valid),
        seat_score_rates={seat: sum(values) / len(values) for seat, values in sorted(by_seat.items())},
        fault_count=fault_count,
    )


def rank_candidates(scores: Sequence[BootstrapScore]) -> tuple[BootstrapScore, ...]:
    """Pick the robust winner from a one-point primary-score shortlist."""

    if not scores:
        raise BootstrapContractError("cannot rank empty Bootstrap scores")
    valid = [score for score in scores if score.fault_count == 0]
    invalid = [score for score in scores if score.fault_count != 0]
    if not valid:
        return tuple(sorted(invalid, key=lambda item: item.candidate_id))
    maximum = max(score.opponent_equal_score_rate for score in valid)
    shortlist = [score for score in valid if maximum - score.opponent_equal_score_rate <= 0.01 + 1e-12]
    non_shortlist = [score for score in valid if score not in shortlist]
    return tuple(
        sorted(
            shortlist,
            key=lambda item: (
                -item.worst_opponent_score_rate,
                -item.wilson_lower_bound,
                item.latency_p95_seconds,
                item.candidate_id,
            ),
        )
        + sorted(non_shortlist, key=lambda item: (-item.opponent_equal_score_rate, item.candidate_id))
        + sorted(invalid, key=lambda item: item.candidate_id)
    )
