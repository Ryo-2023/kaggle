"""Deterministic, observation-only selection policy for cabt.

The policy deliberately reads only legal selection metadata and a short
allowlist of public scalar option fields.  Deck registration is handled by the
factory in :mod:`main`; this module only chooses option indices.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_MAIN_SELECT_TYPE = 0
_OPTION_TYPE_NAMES = {
    7: "PLAY",
    8: "ATTACH",
    9: "EVOLVE",
    10: "ABILITY",
    13: "ATTACK",
    14: "END",
}
_MAIN_ACTION_SCORES = {
    "EVOLVE": 600,
    "ATTACH": 500,
    "PLAY": 400,
    "ABILITY": 300,
    "ATTACK": 200,
    "END": -1_000,
}
_SAFE_OPTION_FIELDS = frozenset(
    {
        "type",
        "area",
        "inPlayArea",
        "inPlayIndex",
        "index",
        "playerIndex",
        "attackId",
        "number",
        "damage",
        "hp",
        "energyAttached",
    }
)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _safe_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _enum_name(value: object, numeric_names: Mapping[int, str]) -> str | None:
    """Normalize enum-like values without coercing or decoding payloads."""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.rsplit(".", 1)[-1].upper()
    if isinstance(value, str):
        return value.rsplit(".", 1)[-1].upper()
    numeric_value = _safe_int(value)
    return numeric_names.get(numeric_value) if numeric_value is not None else None


def _option_type_name(option: object) -> str | None:
    data = _mapping(option)
    return _enum_name(data.get("type"), _OPTION_TYPE_NAMES) if data is not None else None


def _selection_bounds(select: Mapping[str, Any], option_count: int) -> tuple[int, int]:
    """Clamp malformed metadata to the only safely selectable option range."""
    raw_minimum = _safe_int(select.get("minCount"))
    raw_maximum = _safe_int(select.get("maxCount"))
    minimum = max(0, raw_minimum) if raw_minimum is not None else 0
    maximum = max(0, raw_maximum) if raw_maximum is not None else 0
    minimum = min(minimum, option_count)
    maximum = min(maximum, option_count)
    if maximum < minimum:
        maximum = minimum
    return minimum, maximum


def _is_main_selection(select: Mapping[str, Any]) -> bool:
    value = select.get("type")
    return value == _MAIN_SELECT_TYPE or _enum_name(value, {_MAIN_SELECT_TYPE: "MAIN"}) == "MAIN"


def _public_number(option: object, field: str) -> int | None:
    data = _mapping(option)
    if data is None or field not in _SAFE_OPTION_FIELDS:
        return None
    return _safe_int(data.get(field))


def _score_main_action(option: object) -> int:
    return _MAIN_ACTION_SCORES.get(_option_type_name(option) or "", 0)


def _score_target(option: object, your_index: int | None) -> int:
    """Rank only explicitly public target scalars, then retain stable order."""
    damage = _public_number(option, "damage") or 0
    hp = _public_number(option, "hp")
    score = damage * 10
    if hp is not None and hp <= damage:
        score += 1_000
    owner = _public_number(option, "playerIndex")
    if your_index is not None and owner == your_index:
        score += 1
    return score


def _ordered_indices(
    options: list[object],
    *,
    main_selection: bool,
    your_index: int | None,
) -> list[int]:
    scorer = _score_main_action if main_selection else lambda option: _score_target(option, your_index)
    return sorted(range(len(options)), key=lambda index: (-scorer(options[index]), index))


def rank_rule_indices(obs_dict: object) -> list[tuple[int, int]] | None:
    """Return every legal option's deterministic Rule v0 score and rank.

    This exposes no additional observation fields.  The Knowledge Pack adapter
    uses it solely to distinguish score ties; it never changes the ordering
    between distinct Rule v0 scores.
    """
    observation = _mapping(obs_dict)
    if observation is None:
        return None
    select = _mapping(observation.get("select"))
    if select is None:
        return None
    raw_options = select.get("option")
    if not isinstance(raw_options, list):
        return []
    options = list(raw_options)
    _minimum, maximum = _selection_bounds(select, len(options))
    if maximum == 0:
        return []
    main_selection = _is_main_selection(select)
    if not main_selection and _minimum == 0:
        return []
    current = _mapping(observation.get("current"))
    your_index = _safe_int(current.get("yourIndex")) if current is not None else None
    scorer = _score_main_action if main_selection else lambda option: _score_target(option, your_index)
    return [(index, scorer(options[index])) for index in _ordered_indices(options, main_selection=main_selection, your_index=your_index)]


def choose_rule_indices(obs_dict: object) -> list[int] | None:
    """Return a legal deterministic selection, or ``None`` for registration.

    A zero-length answer is used only for optional non-main selections.  For a
    mandatory selection, the result contains exactly ``minCount`` unique,
    in-range indices whenever that many options exist.
    """
    observation = _mapping(obs_dict)
    if observation is None:
        return None
    select = _mapping(observation.get("select"))
    if select is None:
        return None
    raw_options = select.get("option")
    if not isinstance(raw_options, list):
        return []
    options = list(raw_options)
    minimum, maximum = _selection_bounds(select, len(options))
    ranked = rank_rule_indices(obs_dict)
    if not ranked:
        return []
    count = minimum if minimum else 1
    return [index for index, _score in ranked[: min(count, maximum)]]
