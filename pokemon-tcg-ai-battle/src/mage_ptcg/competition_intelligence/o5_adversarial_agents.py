"""Deterministic local opponent agents for O5's Benchmark ``safety`` set.

These are never candidates and never reachable from ``main.py``. They exist
only so the Evaluation Runner can exercise real cabt fault paths (exception,
slow/latency-adjacent response, invalid action, unmapped selection) against a
real candidate (e.g. Rule Agent v0) and record how the harness classifies
each, instead of leaving the ``safety`` set's labels unimplemented.

A cabt observation with ``select is None`` is the deck-submission call (the
same contract ``main._deck_supplier`` implements): the expected return value
is the 60-card deck itself, not an empty selection. Getting this wrong was
caught empirically -- an earlier revision returned ``[]`` there and every
member was misclassified ``AGENT_INVALID`` from turn one, before any of the
intended exception/latency/selection fault could be observed.

``exception_agent`` and ``slow_agent`` inject exactly one fault dimension
each (an exception, extra latency) and otherwise play a genuinely legal
selection -- mirroring ``main._selection_contract``'s ``minCount``/
``maxCount`` bounds -- so their measured ``invalid_actions`` stays 0 and a
benchmark reader can attribute any invalid count to a real selection fault,
not to an accidental bug in this test double. ``invalid_artifact`` and
``unknown_selection`` are the only two members that deliberately return an
illegal in-game selection.
"""

from __future__ import annotations

import time
from typing import Callable, Mapping, Sequence

from main import validate_deck

Agent = Callable[[dict], list[int]]


def _selection_contract(obs_dict: dict) -> tuple[list, int, int] | None:
    """A local, read-only mirror of ``main._selection_contract``.

    Duplicated rather than imported because that function is private to
    ``main.py``; the contract itself (``select.option``/``minCount``/
    ``maxCount``) is the same actor-visible public allowlist every agent in
    this repository already relies on.
    """
    select = obs_dict.get("select")
    if not isinstance(select, Mapping):
        return None
    options = select.get("option")
    min_count = select.get("minCount")
    max_count = select.get("maxCount")
    if (
        not isinstance(options, Sequence)
        or isinstance(options, (str, bytes))
        or isinstance(min_count, bool)
        or isinstance(max_count, bool)
        or not isinstance(min_count, int)
        or not isinstance(max_count, int)
        or not 0 <= min_count <= max_count <= len(options)
    ):
        return None
    return list(options), min_count, max_count


def _legal_selection(obs_dict: dict) -> list[int] | None:
    """The first ``maxCount`` option indices, or ``None`` if there is nothing to pick."""
    contract = _selection_contract(obs_dict)
    if contract is None:
        return None
    options, _min_count, max_count = contract
    if max_count == 0:
        return []
    return list(range(max_count))


def make_exception_agent(deck: Sequence[int], seed: int) -> Agent:
    """Submits its deck, plays two legal choices, then raises on the third."""
    supplied_deck = list(validate_deck(deck))
    calls = {"n": 0}

    def agent(obs_dict: dict) -> list[int]:
        if obs_dict.get("select") is None:
            return supplied_deck
        selection = _legal_selection(obs_dict)
        if selection is None:
            return []
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("o5-adversarial-exception-agent: deterministic fault injection")
        return selection

    agent.__name__ = "o5_exception_agent"
    return agent


def make_slow_agent(deck: Sequence[int], seed: int, *, delay_seconds: float = 0.05) -> Agent:
    """Submits its deck, then sleeps a fixed, deterministic amount before each legal choice."""
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    supplied_deck = list(validate_deck(deck))

    def agent(obs_dict: dict) -> list[int]:
        if obs_dict.get("select") is None:
            return supplied_deck
        selection = _legal_selection(obs_dict)
        if selection is None:
            return []
        time.sleep(delay_seconds)
        return selection

    agent.__name__ = "o5_slow_agent"
    return agent


def make_invalid_artifact_agent(deck: Sequence[int], seed: int) -> Agent:
    """Submits its deck, then always returns a well-typed but out-of-range index."""
    supplied_deck = list(validate_deck(deck))

    def agent(obs_dict: dict) -> list[int]:
        if obs_dict.get("select") is None:
            return supplied_deck
        if _selection_contract(obs_dict) is None:
            return []
        return [999999]

    agent.__name__ = "o5_invalid_artifact_agent"
    return agent


def make_unknown_selection_agent(deck: Sequence[int], seed: int) -> Agent:
    """Submits its deck, then always returns a syntactically valid but unmapped index."""
    supplied_deck = list(validate_deck(deck))

    def agent(obs_dict: dict) -> list[int]:
        if obs_dict.get("select") is None:
            return supplied_deck
        contract = _selection_contract(obs_dict)
        if contract is None:
            return []
        options, _min_count, _max_count = contract
        return [len(options)]

    agent.__name__ = "o5_unknown_selection_agent"
    return agent


ADVERSARIAL_AGENT_FACTORIES: Mapping[str, Callable[[Sequence[int], int], Agent]] = {
    "exception_agent": make_exception_agent,
    "slow_agent": make_slow_agent,
    "invalid_artifact": make_invalid_artifact_agent,
    "unknown_selection": make_unknown_selection_agent,
}


__all__ = [
    "ADVERSARIAL_AGENT_FACTORIES",
    "make_exception_agent",
    "make_invalid_artifact_agent",
    "make_slow_agent",
    "make_unknown_selection_agent",
]
