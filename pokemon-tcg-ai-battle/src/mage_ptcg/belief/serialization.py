"""Deterministic serialization for exact belief core values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, TypeAlias, TypeVar

from .card_counts import CardCounts
from .errors import BeliefValidationError
from .hidden_zone import HiddenZoneKnowledge


SCHEMA_VERSION = 1
HASH_PREFIX = b"mage_ptcg.belief:v1\0"
CARD_COUNTS_TYPE = "mage_ptcg.belief.CardCounts"
HIDDEN_ZONE_TYPE = "mage_ptcg.belief.HiddenZoneKnowledge"

BeliefValue: TypeAlias = CardCounts | HiddenZoneKnowledge
T = TypeVar("T", CardCounts, HiddenZoneKnowledge)


def _type_name(value: BeliefValue) -> str:
    if isinstance(value, CardCounts):
        return CARD_COUNTS_TYPE
    if isinstance(value, HiddenZoneKnowledge):
        return HIDDEN_ZONE_TYPE
    raise TypeError("value must be CardCounts or HiddenZoneKnowledge")


def to_canonical_payload(value: BeliefValue) -> dict[str, object]:
    return {
        "data": value.to_canonical_payload(),
        "schema_version": SCHEMA_VERSION,
        "type": _type_name(value),
    }


def to_canonical_bytes(value: BeliefValue) -> bytes:
    return json.dumps(
        to_canonical_payload(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise BeliefValidationError(
        "invalid_json_constant",
        (),
        f"JSON constant {value!r} is not allowed",
    )


def _no_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BeliefValidationError(
                "duplicate_json_key",
                (key,),
                "duplicate JSON keys are not allowed",
            )
        result[key] = value
    return result


def _load_json(payload: str | bytes | bytearray) -> object:
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BeliefValidationError(
                "invalid_json",
                (),
                "payload must be UTF-8 JSON",
            ) from exc
    if type(payload) is not str:
        raise BeliefValidationError(
            "invalid_serialized_type",
            (),
            "payload must be str or UTF-8 bytes",
        )

    try:
        return json.loads(
            payload,
            object_pairs_hook=_no_duplicate_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except BeliefValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise BeliefValidationError(
            "invalid_json",
            (),
            "payload is not valid JSON",
        ) from exc


def _decoder_for(type_name: str) -> tuple[type[BeliefValue], Callable[[object], BeliefValue]]:
    if type_name == CARD_COUNTS_TYPE:
        return CardCounts, CardCounts.from_canonical_payload
    if type_name == HIDDEN_ZONE_TYPE:
        return HiddenZoneKnowledge, HiddenZoneKnowledge.from_canonical_payload
    raise BeliefValidationError(
        "unknown_type",
        ("type",),
        "unknown belief schema type",
    )


def from_canonical_bytes(
    payload: str | bytes | bytearray,
    *,
    expected_type: type[T] | None = None,
) -> T | BeliefValue:
    document = _load_json(payload)
    if type(document) is not dict or set(document) != {
        "schema_version",
        "type",
        "data",
    }:
        raise BeliefValidationError(
            "invalid_document_fields",
            (),
            "document must contain only schema_version, type, and data",
        )

    version = document["schema_version"]
    if type(version) is not int:
        raise BeliefValidationError(
            "invalid_schema_version",
            ("schema_version",),
            "schema_version must be an int",
        )
    if version != SCHEMA_VERSION:
        raise BeliefValidationError(
            "unknown_schema_version",
            ("schema_version",),
            "unknown belief schema version",
        )

    type_name = document["type"]
    if type(type_name) is not str:
        raise BeliefValidationError(
            "invalid_type",
            ("type",),
            "type must be a str",
        )

    decoded_type, decoder = _decoder_for(type_name)
    if expected_type is not None:
        if expected_type not in (CardCounts, HiddenZoneKnowledge):
            raise TypeError("expected_type must be CardCounts or HiddenZoneKnowledge")
        if decoded_type is not expected_type:
            raise BeliefValidationError(
                "unexpected_type",
                ("type",),
                f"expected {expected_type.__name__}, got {decoded_type.__name__}",
            )

    return decoder(document["data"])


def canonical_digest(value: BeliefValue) -> str:
    return hashlib.sha256(HASH_PREFIX + to_canonical_bytes(value)).hexdigest()


__all__ = [
    "BeliefValue",
    "CARD_COUNTS_TYPE",
    "HASH_PREFIX",
    "HIDDEN_ZONE_TYPE",
    "SCHEMA_VERSION",
    "canonical_digest",
    "from_canonical_bytes",
    "to_canonical_bytes",
    "to_canonical_payload",
]
