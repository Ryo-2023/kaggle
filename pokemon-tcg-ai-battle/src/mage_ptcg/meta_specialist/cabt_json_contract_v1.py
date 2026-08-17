"""Frozen kaggle-environments 1.32.0 CABT agent-JSON selection contract."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Final, Mapping, TypedDict


CABT_AGENT_JSON_CONTRACT_VERSION: Final = (
    "meta-specialist-cabt-agent-json-contract-v1"
)

# These are the zero-based integer values emitted to Python agents.  They are
# not the native engine's internal one-based C++ enum bytes.
CABT_AGENT_JSON_SELECTION_CONTEXTS_V1: Final[Mapping[int, tuple[int, ...]]] = (
    MappingProxyType(
        {
            0: (0,),
            1: tuple(range(1, 26)),
            2: (26, 27, 28),
            3: (29,),
            4: (30, 31, 32, 33),
            5: (34,),
            6: (35, 36),
            7: (37,),
            8: (38, 39, 40),
            9: (41, 42, 43, 44, 45, 46),
            10: (47, 48),
        }
    )
)
CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1: Final = frozenset({(5, 34)})
CABT_AGENT_JSON_UNORDERED_SELECTION_SCHEMAS_V1: Final = frozenset(
    (selection_type, context)
    for selection_type, contexts in CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.items()
    for context in contexts
    if (selection_type, context) not in CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1
)


def is_ordered_selection(selection_type: object, context: object) -> bool:
    """Return the frozen order semantics for one exact CABT JSON schema."""
    if type(selection_type) is not int or type(context) is not int:
        raise ValueError("selection type and context must be non-bool ints")
    schema = (selection_type, context)
    if schema in CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1:
        return True
    if schema in CABT_AGENT_JSON_UNORDERED_SELECTION_SCHEMAS_V1:
        return False
    raise ValueError(f"unrecognized CABT selection schema {schema!r}")


class _CabtAgentJsonContractPayloadV1(TypedDict):
    schema_version: str
    selection_schemas: list[list[int]]
    ordered_selection_schemas: list[list[int]]


def cabt_agent_json_contract_payload_v1() -> _CabtAgentJsonContractPayloadV1:
    """Return a fresh JSON-like payload for the frozen agent-JSON contract."""
    selection_schemas = sorted(
        [selection_type, context]
        for selection_type, contexts in CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.items()
        for context in contexts
    )
    ordered_selection_schemas = sorted(
        [selection_type, context]
        for selection_type, context in CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1
    )
    return {
        "schema_version": CABT_AGENT_JSON_CONTRACT_VERSION,
        "selection_schemas": selection_schemas,
        "ordered_selection_schemas": ordered_selection_schemas,
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


CABT_AGENT_JSON_CONTRACT_CANONICAL_BYTES_V1: Final = _canonical_json_bytes(
    cabt_agent_json_contract_payload_v1()
)
CABT_AGENT_JSON_CONTRACT_SHA256_V1: Final = hashlib.sha256(
    CABT_AGENT_JSON_CONTRACT_VERSION.encode("utf-8")
    + b"\0"
    + CABT_AGENT_JSON_CONTRACT_CANONICAL_BYTES_V1
).hexdigest()


__all__ = [
    "CABT_AGENT_JSON_CONTRACT_CANONICAL_BYTES_V1",
    "CABT_AGENT_JSON_CONTRACT_SHA256_V1",
    "CABT_AGENT_JSON_CONTRACT_VERSION",
    "CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1",
    "CABT_AGENT_JSON_SELECTION_CONTEXTS_V1",
    "CABT_AGENT_JSON_UNORDERED_SELECTION_SCHEMAS_V1",
    "cabt_agent_json_contract_payload_v1",
    "is_ordered_selection",
]
