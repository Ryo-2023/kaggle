"""Contracts for self-owned WDL weighting and weighted Student training."""

from __future__ import annotations

import pytest

from mage_ptcg.student.dataset import build_rule_bc_example
from mage_ptcg.student.model import train_model
from mage_ptcg.student.outcome_weighting_v1 import (
    OutcomeWeightingError,
    build_episode_outcome_v1,
    build_example_weight_map,
    episode_outcome_weight,
)


def _observation() -> dict[str, object]:
    card = {"id": 100, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {
        "current": {"energyAttached": False, "firstPlayer": 0, "players": [player, dict(player)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0},
        "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0},
        "step": 7,
    }


def _examples() -> list:
    return [
        build_rule_bc_example(_observation(), deck=[1] * 60, source_id=f"game-{index}", source_revision="test")
        for index in range(2)
    ]


def test_episode_wdl_weights_are_terminal_and_subject_relative() -> None:
    assert episode_outcome_weight(status="DONE", winner=0, subject_seat=0) == 1.5
    assert episode_outcome_weight(status="DONE", winner=0, subject_seat=1) == 0.5
    assert episode_outcome_weight(status="DONE", winner=2, subject_seat=0) == 1.0
    assert episode_outcome_weight(status="AGENT_ERROR", winner=0, subject_seat=0) == 0.0


def test_episode_join_rejects_missing_or_duplicate_examples() -> None:
    examples = _examples()
    episode = build_episode_outcome_v1(game_id="g0", subject_seat=0, status="DONE", winner=0, examples=examples)
    assert build_example_weight_map([episode], examples) == {item.example_id: 1.5 for item in examples}
    with pytest.raises(OutcomeWeightingError, match="duplicate"):
        build_example_weight_map([episode, episode], examples)


def test_weighted_train_model_accepts_wdl_weights() -> None:
    examples = _examples()
    weights = {item.example_id: (2.0 if index == 0 else 0.5) for index, item in enumerate(examples)}
    model = train_model(examples, epochs=3, learning_rate=0.05, example_weights=weights)
    assert len(model.weights) > 0
    with pytest.raises(ValueError, match="weights"):
        train_model(examples, epochs=1, example_weights={item.example_id: 0.0 for item in examples})
