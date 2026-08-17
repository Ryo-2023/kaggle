"""Semantic legal-action features; digest is identity, not the only signal."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping


class SemanticActionError(ValueError):
    pass


ACTION_FIELDS = ("digest", "action_type", "card_id", "source_zone", "target_zone", "target_card", "amount", "selection_order", "phase", "optional", "semantic_role")


def _number(value: object, *, scale: float) -> float:
    return float(value) / scale if type(value) in (int, float) else 0.0


def _signed_hash_into(result: list[float], field: str, value: object, *, start: int) -> None:
    width = len(result) - start
    if width <= 0:
        return
    digest = hashlib.sha256(f"{field}={value!r}".encode("utf-8")).digest()
    for offset in range(0, len(digest), 2):
        result[start + digest[offset] % width] += 1.0 if digest[offset + 1] & 1 else -1.0


def encode_legal_action(action: Mapping[str, Any], *, dimension: int = 64) -> list[float]:
    if not isinstance(action.get("digest"), str) or not action["digest"]:
        raise SemanticActionError("legal action needs a stable digest")
    if dimension < 24:
        raise SemanticActionError("action dimension is too small")
    result = [0.0] * dimension
    # Preserve magnitude/order semantics in dedicated coordinates.  Exact
    # identity and categorical fields remain represented in the residual.
    result[0] = _number(action.get("action_type"), scale=32.0)
    result[1] = _number(action.get("card_id"), scale=10_000.0)
    result[2] = _number(action.get("amount"), scale=16.0)
    result[3] = _number(action.get("selection_order"), scale=32.0)
    result[4] = float(action.get("optional") is True)
    result[5] = float(action.get("target_card") is not None)
    result[6] = float(action.get("source_zone") is not None)
    result[7] = float(action.get("target_zone") is not None)
    for field in ACTION_FIELDS:
        _signed_hash_into(result, field, action.get(field, "UNKNOWN"), start=16)
    return result


def validate_legal_mask(actions: list[Mapping[str, Any]], selected_index: int) -> None:
    if not actions or not 0 <= selected_index < len(actions): raise SemanticActionError("selected action is not legal")
