"""Bounded public-state overlay for a research-only Rule v0 policy candidate.

The existing fixed action-type screens changed the same score in every main
selection.  This candidate is deliberately different: only a mandatory MAIN
selection after a public energy attachment and at least two actions this turn
gets a bounded ATTACK bonus.  Every malformed, non-MAIN, optional, or
unsupported observation returns the exact Rule v0 selection supplied by the
caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agents.rule_agent import rank_rule_indices


POLICY_ID = "rule-v0-phase-conditioned-attack-after-energy-v1"
ATTACK_BONUS = 240
MIN_TURN_ACTION_COUNT = 2
_MAIN_SELECT_TYPE = 0
_ATTACK_TYPE = 13


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _strict_int(value: object) -> int | None:
    return value if type(value) is int else None


def _is_main(value: object) -> bool:
    if value == _MAIN_SELECT_TYPE:
        return True
    name = getattr(value, "name", None)
    return isinstance(name, str) and name.rsplit(".", 1)[-1].upper() == "MAIN"


def _option_type(option: object) -> int | None:
    data = _mapping(option)
    if data is None:
        return None
    value = data.get("type")
    return _strict_int(value)


def _eligible(obs: object) -> tuple[list[object], int, int] | None:
    observation = _mapping(obs)
    if observation is None:
        return None
    select = _mapping(observation.get("select"))
    current = _mapping(observation.get("current"))
    if select is None or current is None or not _is_main(select.get("type")):
        return None
    options = select.get("option")
    if not isinstance(options, list):
        return None
    minimum = _strict_int(select.get("minCount"))
    maximum = _strict_int(select.get("maxCount"))
    turn_action_count = _strict_int(current.get("turnActionCount"))
    if minimum is None or maximum is None or turn_action_count is None:
        return None
    if minimum < 1 or maximum < minimum or maximum > len(options):
        return None
    if current.get("energyAttached") is not True:
        return None
    if turn_action_count < MIN_TURN_ACTION_COUNT:
        return None
    return list(options), minimum, maximum


def choose_phase_conditioned_indices(
    obs: object,
    fallback_selection: Sequence[int],
) -> list[int]:
    """Return the bounded overlay choice or the exact fallback selection."""

    fallback = list(fallback_selection)
    eligible = _eligible(obs)
    if eligible is None:
        return fallback
    options, minimum, maximum = eligible
    ranked = rank_rule_indices(obs)
    if not ranked or len(ranked) != len(options):
        return fallback
    scored: list[tuple[int, int, int]] = []
    for index, base_score in ranked:
        if type(index) is not int or index < 0 or index >= len(options):
            return fallback
        if type(base_score) is not int:
            return fallback
        bonus = ATTACK_BONUS if _option_type(options[index]) == _ATTACK_TYPE else 0
        scored.append((-(base_score + bonus), index, base_score))
    scored.sort(key=lambda item: (item[0], item[1]))
    count = min(minimum, maximum)
    selected = [item[1] for item in scored[:count]]
    if len(selected) != count or len(set(selected)) != len(selected):
        return fallback
    return selected


__all__ = [
    "ATTACK_BONUS",
    "MIN_TURN_ACTION_COUNT",
    "POLICY_ID",
    "choose_phase_conditioned_indices",
]
