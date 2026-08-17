"""PSRO population state and deterministic league sampling."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Mapping, Sequence


class LeagueError(ValueError):
    pass


def solve_meta_strategy(payoffs: Sequence[Sequence[float]], *, iterations: int = 5_000, learning_rate: float = 0.1) -> list[float]:
    """Solve a zero-sum empirical game with exponentiated-gradient updates."""
    size = len(payoffs)
    if size < 1 or any(len(row) != size for row in payoffs) or iterations < 1 or learning_rate <= 0:
        raise LeagueError("PSRO payoff matrix/configuration is invalid")
    if any(not math.isfinite(float(value)) for row in payoffs for value in row):
        raise LeagueError("PSRO payoff matrix must be finite")
    strategy = [1.0 / size] * size
    average = [0.0] * size
    for _ in range(iterations):
        utilities = [sum(float(payoffs[row][column]) * strategy[column] for column in range(size)) for row in range(size)]
        maximum = max(utilities)
        weights = [strategy[index] * math.exp(min(20.0, learning_rate * (utilities[index] - maximum))) for index in range(size)]
        normalizer = sum(weights)
        strategy = [value / normalizer for value in weights]
        average = [left + right for left, right in zip(average, strategy, strict=True)]
    normalizer = sum(average)
    return [value / normalizer for value in average]


@dataclass(frozen=True, slots=True)
class PopulationMember:
    policy_id: str
    role: str
    deck_family: str
    checkpoint: str


@dataclass(slots=True)
class PSROState:
    """Immutable-member empirical game; holdouts are never added here."""

    members: list[PopulationMember] = field(default_factory=list)
    payoffs: list[list[float]] = field(default_factory=list)

    def add_member(self, member: PopulationMember, *, against_existing: Sequence[float] | None = None) -> None:
        if not member.policy_id or member.policy_id in {item.policy_id for item in self.members}:
            raise LeagueError("population member id must be unique")
        prior = len(self.members)
        results = list(against_existing or ())
        if prior and len(results) != prior:
            raise LeagueError("best-response payoff count must match existing population")
        if any(not math.isfinite(float(value)) for value in results):
            raise LeagueError("best-response payoff must be finite")
        for index, row in enumerate(self.payoffs):
            row.append(-float(results[index]))
        self.payoffs.append([*map(float, results), 0.0])
        self.members.append(member)

    def meta_strategy(self) -> dict[str, float]:
        if not self.members:
            raise LeagueError("population is empty")
        probabilities = solve_meta_strategy(self.payoffs)
        return {member.policy_id: probability for member, probability in zip(self.members, probabilities, strict=True)}

    def sample_opponents(self, *, count: int, seed: int) -> list[str]:
        if count < 1:
            raise LeagueError("sample count must be positive")
        distribution = self.meta_strategy(); ids = sorted(distribution); weights = [distribution[item] for item in ids]
        return random.Random(seed).choices(ids, weights=weights, k=count)
