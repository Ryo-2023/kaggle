"""Deterministic, legal-action-preserving policy perturbation for research pools.

The adapter is deliberately small: it delegates the policy decision to a base
agent and only replaces a selected option when another option with the same
``type`` is available.  This produces a locally-owned behavioural variant
without inventing an action type or bypassing the simulator's option list.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _indices(value: object) -> list[int] | None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return None
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        result.append(item)
    return result


def _option_type(option: object) -> object:
    if isinstance(option, Mapping):
        return option.get("type")
    return None


def _observation_digest(observation: Mapping[str, Any], salt: str) -> bytes:
    try:
        payload = json.dumps(
            {"salt": salt, "observation": observation},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        payload = repr((salt, observation)).encode("utf-8", errors="backslashreplace")
    return hashlib.sha256(payload).digest()


def adapt_action_v1(
    base_action: object,
    observation: Mapping[str, Any],
    *,
    salt: str,
    perturbation_rate: float = 0.12,
) -> list[int]:
    """Return a deterministic action variant while preserving the selection contract.

    ``select is None`` is the deck-registration path and is returned unchanged.
    For a live selection, an invalid base action falls back to the required
    prefix.  Perturbation is attempted only when a same-type, not-yet-selected
    option exists; otherwise the validated base action is retained.
    """

    if not isinstance(observation, Mapping):
        return list(base_action) if _indices(base_action) is not None else []
    select = observation.get("select")
    base = _indices(base_action)
    if select is None:
        return list(base_action) if base is not None else []
    if not isinstance(select, Mapping):
        return base or []
    options = select.get("option")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes, bytearray)):
        return base or []
    option_count = len(options)
    try:
        minimum = int(select.get("minCount", 0))
        maximum = int(select.get("maxCount", minimum))
    except (TypeError, ValueError):
        return base or []
    if minimum < 0 or maximum < minimum or maximum > option_count:
        return base or []

    valid_base = (
        base is not None
        and minimum <= len(base) <= maximum
        and len(base) == len(set(base))
        and all(0 <= index < option_count for index in base)
    )
    chosen = list(base) if valid_base else list(range(minimum))
    if len(chosen) < minimum or len(chosen) > maximum:
        return chosen
    if not 0.0 <= perturbation_rate <= 1.0:
        raise ValueError("perturbation_rate must be between 0 and 1")
    if not chosen or not options or perturbation_rate == 0.0:
        return chosen

    digest = _observation_digest(observation, salt)
    threshold = int.from_bytes(digest[:8], "big") / float(1 << 64)
    if threshold >= perturbation_rate:
        return chosen

    selected = set(chosen)
    for position, selected_index in enumerate(chosen):
        selected_type = _option_type(options[selected_index])
        alternatives = [
            index
            for index, option in enumerate(options)
            if index not in selected and _option_type(option) == selected_type
        ]
        if not alternatives:
            continue
        replacement = alternatives[digest[8 + position] % len(alternatives)]
        result = list(chosen)
        result[position] = replacement
        return result
    return chosen

