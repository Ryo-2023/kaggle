"""Value-independent, deterministic structural fingerprints for responses."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

FINGERPRINT_VERSION = 1


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "list"
    return f"unsupported:{type(value).__name__}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _shape(value: Any) -> dict[str, Any]:
    kind = _kind(value)
    if kind == "object":
        assert isinstance(value, Mapping)
        return {
            "type": kind,
            "fields": [
                {"name": str(key), "shape": _shape(child)}
                for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            ],
        }
    if kind == "list":
        assert isinstance(value, Sequence)
        # Repeated elements do not change the schema.  Unique structural
        # variants retain heterogeneous-list optionality without recording data.
        variants = {_canonical_json(_shape(child)) for child in value}
        return {
            "type": kind,
            "element_shapes": [json.loads(item) for item in sorted(variants)],
        }
    return {"type": kind}


def _paths(shape: Mapping[str, Any], prefix: str = "$") -> list[dict[str, str]]:
    records = [{"path": prefix, "type": str(shape["type"])}]
    if shape["type"] == "object":
        for field in shape["fields"]:
            records.extend(_paths(field["shape"], f"{prefix}.{field['name']}"))
    elif shape["type"] == "list":
        for index, element in enumerate(shape["element_shapes"]):
            records.extend(_paths(element, f"{prefix}[]#{index}"))
    return records


def fingerprint_document(value: Any) -> dict[str, Any]:
    """Return a safe schema document; it intentionally contains no values."""
    shape = _shape(value)
    canonical = _canonical_json(shape)
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "root_type": shape["type"],
        "field_count": len(_paths(shape)),
        "paths": _paths(shape),
        "shape": shape,
        "canonical_serialization": canonical,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def schema_fingerprint(value: Any) -> str:
    """Return only the SHA-256 digest for callers that need a compact key."""
    return str(fingerprint_document(value)["sha256"])
