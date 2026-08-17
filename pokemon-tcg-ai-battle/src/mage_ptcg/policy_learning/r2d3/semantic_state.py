"""Actor-visible state encoding for R2D3.

This intentionally accepts a public projection, never a CABT raw observation.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping


HIDDEN_KEYS = frozenset({"opponent_hand", "opponent_deck", "opponent_prizes", "opponent_prize", "deck_order", "rng", "hidden"})


class SemanticStateError(ValueError):
    pass


def assert_actor_visible(value: object) -> None:
    if isinstance(value, Mapping):
        forbidden = HIDDEN_KEYS.intersection(str(key).lower() for key in value)
        if forbidden:
            raise SemanticStateError(f"hidden information in actor input: {sorted(forbidden)}")
        for item in value.values(): assert_actor_visible(item)
    elif isinstance(value, (list, tuple)):
        for item in value: assert_actor_visible(item)


def _number(value: object, *, scale: float) -> float:
    return float(value) / scale if type(value) in (int, float) else 0.0


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _length(value: object) -> float:
    return float(len(value)) if isinstance(value, (list, tuple)) else 0.0


def _signed_hash_into(result: list[float], value: object, *, start: int) -> None:
    """Keep exact/categorical residuals without replacing semantic scalars."""
    width = len(result) - start
    if width <= 0:
        return
    digest = hashlib.sha256(repr(value).encode("utf-8")).digest()
    for offset in range(0, len(digest), 2):
        result[start + digest[offset] % width] += 1.0 if digest[offset + 1] & 1 else -1.0


def encode_public_state(public_state: Mapping[str, Any], *, dimension: int = 128) -> list[float]:
    """Encode explicit public game structure plus a stable residual sketch.

    The leading coordinates retain meaningful ordering and counts.  The hash
    tail preserves categorical/card details not yet represented by a learned
    entity encoder, so two states do not collapse merely because their counts
    match.
    """
    if dimension < 32: raise SemanticStateError("state dimension is too small")
    assert_actor_visible(public_state)
    own = _mapping(public_state.get("self"))
    other = _mapping(public_state.get("opponent"))
    select = _mapping(public_state.get("select"))
    board = _mapping(public_state.get("board"))
    own_status = _mapping(own.get("status"))
    other_status = _mapping(other.get("status"))
    result = [0.0] * dimension
    semantic = (
        _number(public_state.get("actor"), scale=1.0),
        _number(public_state.get("first_player"), scale=1.0),
        _number(public_state.get("step"), scale=256.0),
        _number(public_state.get("turn"), scale=64.0),
        _number(public_state.get("turn_action_count"), scale=32.0),
        _number(select.get("min_count"), scale=8.0),
        _number(select.get("max_count"), scale=8.0),
        _number(select.get("option_count"), scale=32.0),
        _number(own.get("hand_count"), scale=16.0),
        _number(own.get("deck_count"), scale=60.0),
        _number(own.get("prize_count"), scale=6.0),
        _length(own.get("active")),
        _length(own.get("bench")) / max(1.0, _number(own.get("bench_max"), scale=1.0)),
        _length(own.get("discard")) / 60.0,
        _number(other.get("hand_count"), scale=16.0),
        _number(other.get("deck_count"), scale=60.0),
        _number(other.get("prize_count"), scale=6.0),
        _length(other.get("active")),
        _length(other.get("bench")) / max(1.0, _number(other.get("bench_max"), scale=1.0)),
        _length(other.get("discard")) / 60.0,
        float(board.get("stadium") is not None),
        float(board.get("stadium_played") is True),
        float(board.get("supporter_played") is True),
        float(board.get("energy_attached") is True),
        float(board.get("retreated") is True),
        float(any(value is True for value in own_status.values())),
        float(any(value is True for value in other_status.values())),
        float(public_state.get("observed_result") is not None),
    )
    result[: len(semantic)] = semantic
    residual = tuple(sorted((str(key), repr(value)) for key, value in public_state.items()))
    _signed_hash_into(result, residual, start=32)
    return result


FEATURE_REGISTRY = {"schema": "r2d3-semantic-state-v2", "actor_visible_only": True,
                    "encoding": "explicit_public_scalars_plus_signed_hash_residual",
                    "entities": ["card_id", "owner", "zone", "role", "count", "damage", "energy", "evolution_state", "status"],
                    "public_fields": ["own_hand", "own_active", "own_bench", "own_discard", "opponent_public_board", "turn", "phase", "side", "visible_history"]}
