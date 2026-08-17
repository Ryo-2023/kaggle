"""Byte-safe extraction and archival of noisy structured CLI payloads."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any

from .canonical import canonical_json_bytes, sha256_hex

_ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


class PayloadExtractionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StructuredPayloadCandidate:
    raw_content_hash: str
    envelope_kind: str
    payload: object
    prefix_summary: str
    suffix_summary: str
    schema_fingerprint: str
    trusted: bool = False


def _summary(value: bytes) -> str:
    return _ANSI.sub(b"", value).decode("utf-8", "replace").replace("\n", " ").strip()[:160]


def _scan_json_values(data: bytes) -> list[tuple[int, int, object]]:
    """Find complete object/array values while honoring JSON string escapes."""
    values: list[tuple[int, int, object]] = []
    index = 0
    while index < len(data):
        if data[index] not in (ord("{"), ord("[")):
            index += 1; continue
        start, stack, quoted, escaped = index, [data[index]], False, False
        cursor = index + 1
        while cursor < len(data):
            char = data[cursor]
            if quoted:
                if escaped: escaped = False
                elif char == ord("\\"): escaped = True
                elif char == ord('"'): quoted = False
            elif char == ord('"'):
                quoted = True
            elif char in (ord("{"), ord("[")):
                stack.append(char)
            elif char in (ord("}"), ord("]")):
                opener = stack[-1] if stack else None
                if (opener, char) not in ((ord("{"), ord("}")), (ord("["), ord("]"))):
                    break
                stack.pop()
                if not stack:
                    raw = data[start:cursor + 1]
                    try: value = json.loads(raw.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError): break
                    values.append((start, cursor + 1, value)); index = cursor + 1; break
            cursor += 1
        else:
            # A plausible JSON start with no complete balanced value is
            # unambiguously truncated, not an empty successful response.
            raise PayloadExtractionError("truncated_json_payload")
        if index == start:
            index += 1
    return values


def extract_structured_payload(stdout: bytes, stderr: bytes = b"") -> StructuredPayloadCandidate:
    raw_hash = sha256_hex(stdout + b"\0" + stderr)
    normalized = stdout[3:] if stdout.startswith(b"\xef\xbb\xbf") else stdout
    # ANSI control sequences are transport decoration, not payload bytes.
    # Strip them only in the parser view; the archive/hash above remains over
    # the original unmodified stream.
    parser_view = _ANSI.sub(b"", normalized)
    values = _scan_json_values(parser_view)
    if not values:
        raise PayloadExtractionError("no_structured_payload")
    first_start, last_end = values[0][0], values[-1][1]
    prefix, suffix = parser_view[:first_start], parser_view[last_end:]
    if len(values) == 1:
        kind, payload = "SINGLE_JSON", values[0][2]
    else:
        separator = parser_view[values[0][1]:values[1][0]]
        kind, payload = ("JSON_LINES" if separator.strip() == b"" else "MULTIPLE_JSON"), tuple(item[2] for item in values)
    fingerprint = sha256_hex(canonical_json_bytes({"type": type(payload).__name__, "payload": payload}))
    return StructuredPayloadCandidate(raw_hash, kind, payload, _summary(prefix), _summary(suffix + (b" stderr=" + stderr if stderr else b"")), fingerprint, trusted=False)


def archive_raw_response(root: str | Path, *, stdout: bytes, stderr: bytes, exit_code: int | None, cli_version: str | None, request_arguments: tuple[str, ...] = ()) -> dict[str, object]:
    """Archive unmodified streams before parsing; arguments are only summaries."""
    destination = Path(root); destination.mkdir(parents=True, exist_ok=True)
    raw_hash = sha256_hex(stdout + b"\0" + stderr)
    (destination / f"{raw_hash}.stdout.bin").write_bytes(stdout)
    (destination / f"{raw_hash}.stderr.bin").write_bytes(stderr)
    manifest = {"schema_version": "o5-raw-response-archive-v1", "raw_content_hash": raw_hash, "stdout_hash": sha256_hex(stdout), "stderr_hash": sha256_hex(stderr), "exit_code": exit_code, "cli_version": cli_version, "request_argument_count": len(request_arguments), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (destination / f"{raw_hash}.manifest.json").write_bytes(canonical_json_bytes(manifest))
    return {**manifest, "manifest_path": str(destination / f"{raw_hash}.manifest.json")}


__all__ = ["PayloadExtractionError", "StructuredPayloadCandidate", "archive_raw_response", "extract_structured_payload"]
