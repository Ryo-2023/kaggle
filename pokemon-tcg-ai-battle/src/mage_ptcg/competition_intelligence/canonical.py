"""Deterministic canonical JSON serialization and content hashing.

This repository does not have one shared canonical-JSON/digest utility;
``mage_ptcg.knowledge.model``, ``mage_ptcg.distillation.contracts``,
``mage_ptcg.decision_state`` and others each carry their own near-identical
copy with a module-specific domain prefix. This module is the Competition
Intelligence sidecar's copy of that established pattern (sorted keys, fixed
separators, no NaN/Infinity, domain-prefixed sha256) rather than a new,
incompatible scheme.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

DOMAIN_PREFIX = "mage_ptcg:competition_intelligence"


class CanonicalizationError(ValueError):
    """Raised when a value cannot be serialized as canonical, finite JSON."""


def _reject_non_finite(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalizationError(f"non-finite float at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string key at {path}")
            _reject_non_finite(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_non_finite(child, path=f"{path}[{index}]")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize ``value`` to deterministic UTF-8 JSON bytes.

    Rejects NaN/Infinity and non-string keys so the same logical value always
    produces the same bytes, and unrepresentable values fail loudly instead of
    silently rounding through a lossy encoding.
    """
    _reject_non_finite(value, path="$")
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(f"value is not canonical-JSON serializable: {exc}") from exc
    return text.encode("utf-8")


def digest(value: Any, *, domain: str = "core") -> str:
    """Content hash of ``value``, namespaced by ``domain`` and schema version.

    The domain prefix guards against cross-record-type hash collisions (e.g. a
    ``DecisionRecord`` and an ``EpisodeRecord`` that happen to canonicalize to
    the same bytes must not share a digest).
    """
    prefix = f"{DOMAIN_PREFIX}:{domain}:v1\0".encode("utf-8")
    return hashlib.sha256(prefix + canonical_json_bytes(value)).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "DOMAIN_PREFIX",
    "CanonicalizationError",
    "canonical_json_bytes",
    "digest",
    "sha256_hex",
]
