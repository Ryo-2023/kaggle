"""Focused tests for O5's real safety/adversarial opponent agent family."""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.o5_adversarial_agents import (
    ADVERSARIAL_AGENT_FACTORIES,
    make_exception_agent,
    make_invalid_artifact_agent,
    make_slow_agent,
    make_unknown_selection_agent,
)

_DECK = list(range(1, 61))
_OBS = {"select": {"type": 0, "minCount": 1, "maxCount": 1, "option": [{"type": 14}]}}
_NO_SELECT_OBS = {"select": None}


def test_registry_has_every_safety_label():
    assert set(ADVERSARIAL_AGENT_FACTORIES) == {
        "exception_agent", "slow_agent", "invalid_artifact", "unknown_selection",
    }


def test_exception_agent_eventually_raises_deterministically():
    agent = make_exception_agent(deck=_DECK, seed=1)
    agent(_OBS)
    agent(_OBS)
    with pytest.raises(RuntimeError):
        agent(_OBS)


def test_exception_agent_submits_its_deck_when_there_is_no_selection():
    # select is None on the deck-submission call; the expected return value
    # is the 60-card deck, not an empty selection (see main._deck_supplier).
    agent = make_exception_agent(deck=_DECK, seed=1)
    assert agent(_NO_SELECT_OBS) == _DECK


def test_slow_agent_sleeps_a_bounded_deterministic_amount(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(
        "mage_ptcg.competition_intelligence.o5_adversarial_agents.time.sleep",
        lambda seconds: calls.append(seconds),
    )
    agent = make_slow_agent(deck=_DECK, seed=1, delay_seconds=0.01)
    result = agent(_OBS)
    assert calls == [0.01]
    assert result == [0]


def test_slow_agent_rejects_negative_delay():
    with pytest.raises(ValueError):
        make_slow_agent(deck=_DECK, seed=1, delay_seconds=-0.1)


def test_invalid_artifact_agent_returns_out_of_range_index():
    agent = make_invalid_artifact_agent(deck=_DECK, seed=1)
    assert agent(_OBS) == [999999]
    assert agent(_NO_SELECT_OBS) == _DECK


def test_unknown_selection_agent_returns_well_formed_but_unmapped_index():
    agent = make_unknown_selection_agent(deck=_DECK, seed=1)
    selection = agent(_OBS)
    assert selection == [len(_OBS["select"]["option"])]
    assert agent(_NO_SELECT_OBS) == _DECK


@pytest.mark.parametrize("factory", ADVERSARIAL_AGENT_FACTORIES.values())
def test_every_adversarial_factory_is_seed_reproducible(factory):
    first = factory(_DECK, 7)(_OBS)
    second = factory(_DECK, 7)(_OBS)
    assert first == second
