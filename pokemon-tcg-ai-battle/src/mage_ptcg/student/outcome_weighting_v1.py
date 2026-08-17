"""Outcome-weighted, self-owned Rule v0 dataset helpers.

This module only consumes decisions made by the repository's own Rule v0
agent.  Opponent identities stay in the evaluation manifest; they are never
copied into a training example.  The weight is an episode-level WDL weight,
not a teacher label or an estimate from a local-eval-only opponent.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping

from .dataset import DatasetValidationError, RuleBCExample


SCHEMA_VERSION = "student-self-owned-outcome-weight-v1"


class OutcomeWeightingError(ValueError):
    """Raised when an outcome sidecar cannot be bound to examples."""


@dataclass(frozen=True, slots=True)
class EpisodeOutcomeV1:
    """One subject episode and its deterministic training weight."""

    game_id: str
    subject_seat: int
    status: str
    winner: int | None
    example_ids: tuple[str, ...]
    outcome_weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "game_id": self.game_id,
            "subject_seat": self.subject_seat,
            "status": self.status,
            "winner": self.winner,
            "example_ids": list(self.example_ids),
            "outcome_weight": self.outcome_weight,
        }


def episode_outcome_weight(
    *,
    status: object,
    winner: object,
    subject_seat: object,
    win_weight: float = 1.5,
    loss_weight: float = 0.5,
    draw_weight: float = 1.0,
) -> float:
    """Return a finite positive weight from terminal WDL only.

    Non-terminal/faulted episodes receive zero and must not enter the training
    dataset.  ``winner == 2`` is the CABT draw convention.
    """
    if type(subject_seat) is not int or subject_seat not in (0, 1):
        raise OutcomeWeightingError("subject_seat must be 0 or 1")
    values = (win_weight, loss_weight, draw_weight)
    if any(type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) <= 0.0 for value in values):
        raise OutcomeWeightingError("outcome weights must be finite and positive")
    if status != "DONE":
        return 0.0
    if winner == 2:
        return float(draw_weight)
    if winner == subject_seat:
        return float(win_weight)
    if winner in (0, 1):
        return float(loss_weight)
    raise OutcomeWeightingError("DONE episode has no valid CABT winner")


def build_episode_outcome_v1(
    *,
    game_id: str,
    subject_seat: int,
    status: str,
    winner: int | None,
    examples: Iterable[RuleBCExample],
) -> EpisodeOutcomeV1:
    """Bind a completed game to its examples without opponent metadata."""
    if type(game_id) is not str or not game_id:
        raise OutcomeWeightingError("game_id must be non-empty")
    values = tuple(examples)
    for example in values:
        try:
            from .dataset import validate_example

            validate_example(example)
        except DatasetValidationError as exc:
            raise OutcomeWeightingError("episode contains an invalid RuleBCExample") from exc
    example_ids = tuple(example.example_id for example in values)
    if len(set(example_ids)) != len(example_ids):
        raise OutcomeWeightingError("episode contains duplicate example ids")
    weight = episode_outcome_weight(status=status, winner=winner, subject_seat=subject_seat)
    if weight <= 0.0:
        raise OutcomeWeightingError("only completed terminal episodes may enter training")
    return EpisodeOutcomeV1(
        game_id=game_id,
        subject_seat=subject_seat,
        status=status,
        winner=winner,
        example_ids=example_ids,
        outcome_weight=weight,
    )


def build_example_weight_map(
    episodes: Iterable[EpisodeOutcomeV1],
    examples: Iterable[RuleBCExample],
) -> dict[str, float]:
    """Join episode weights to examples and reject missing/duplicate joins."""
    episode_values = tuple(episodes)
    example_values = tuple(examples)
    available = {example.example_id for example in example_values}
    if len(available) != len(example_values):
        raise OutcomeWeightingError("dataset contains duplicate example ids")
    result: dict[str, float] = {}
    for episode in episode_values:
        for example_id in episode.example_ids:
            if example_id not in available:
                raise OutcomeWeightingError(f"episode references missing example {example_id}")
            if example_id in result:
                raise OutcomeWeightingError(f"duplicate outcome assignment for example: {example_id}")
            result[example_id] = episode.outcome_weight
    if set(result) != available:
        raise OutcomeWeightingError("every dataset example must have exactly one outcome weight")
    return result


def validate_weight_map(examples: Iterable[RuleBCExample], weights: Mapping[str, object]) -> None:
    """Validate a map before handing it to the deterministic trainer."""
    values = tuple(examples)
    if set(weights) != {example.example_id for example in values}:
        raise OutcomeWeightingError("weight map must cover exactly the dataset examples")
    for example_id, weight in weights.items():
        if type(weight) not in (int, float) or not math.isfinite(float(weight)) or float(weight) <= 0.0:
            raise OutcomeWeightingError(f"invalid weight for {example_id}")


__all__ = [
    "SCHEMA_VERSION",
    "EpisodeOutcomeV1",
    "OutcomeWeightingError",
    "build_episode_outcome_v1",
    "build_example_weight_map",
    "episode_outcome_weight",
    "validate_weight_map",
]
