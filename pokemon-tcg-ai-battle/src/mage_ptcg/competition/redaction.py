"""Redaction and conservative secret scanning for archived probe responses."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTION_VERSION = 1
_SENSITIVE_KEY = re.compile(r"(?i)(api[_.-]?key|token|authorization|cookie|password|secret|credential)")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_HOME_PATH = re.compile(r"(?:/home/[^/\s]+|/Users/[^/\s]+|[A-Za-z]:\\\\Users\\\\[^\\\s]+)")
_SIGNED_QUERY = re.compile(
    r"(?i)([?&](?:x-amz-signature|x-goog-signature|signature|sig|token|api[_-]?key)=[^&#\s]+)"
)
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT = re.compile(r"(?i)\b(?:api[_.-]?key|token|password|secret)\s*[:=]\s*[^\s,;]{6,}")


def _redact_text(text: str) -> str:
    text = _SIGNED_QUERY.sub("?REDACTED_QUERY", text)
    text = _BEARER.sub("REDACTED_AUTH", text)
    text = _ASSIGNMENT.sub("REDACTED_SECRET", text)
    text = _EMAIL.sub("REDACTED_EMAIL", text)
    return _HOME_PATH.sub("<HOME>", text)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Create a derived safe value without modifying the source value."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return "<REDACTED>"
    if isinstance(value, Mapping):
        return {str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def secret_scan(value: Any) -> list[str]:
    """Return safe finding labels, never the sensitive text itself."""
    findings: set[str] = set()

    def walk(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for item_key, child in item.items():
                item_key = str(item_key)
                if _SENSITIVE_KEY.search(item_key) and child != "<REDACTED>":
                    findings.add(f"sensitive_key:{path}.{item_key}")
                walk(child, f"{path}.{item_key}")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")
        elif isinstance(item, (str, bytes, bytearray)):
            text = item.decode("utf-8", errors="replace") if not isinstance(item, str) else item
            if _SIGNED_QUERY.search(text):
                findings.add(f"signed_url:{path}")
            if _BEARER.search(text) or _ASSIGNMENT.search(text):
                findings.add(f"secret_like_value:{path}")
            if _EMAIL.search(text):
                findings.add(f"email:{path}")
            if _HOME_PATH.search(text):
                findings.add(f"home_path:{path}")

    walk(value, "$")
    return sorted(findings)
