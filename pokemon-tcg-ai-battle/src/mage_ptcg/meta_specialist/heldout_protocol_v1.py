"""Shared identity for the fixed V4/V2 held-out evaluation protocol."""

from __future__ import annotations

import hashlib


HELDOUT_PROTOCOL_SCHEMA_V1 = "meta-specialist-heldout-protocol-v1"


def heldout_protocol_sha256_v1() -> str:
    payload = (
        HELDOUT_PROTOCOL_SCHEMA_V1
        + "|fixed-six-opponents|opponent-order-preserved|seat-0-then-seat-1|"
        + "seed=base_seed+game_index|faults-remain-in-denominator|run_match-v1"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["HELDOUT_PROTOCOL_SCHEMA_V1", "heldout_protocol_sha256_v1"]

