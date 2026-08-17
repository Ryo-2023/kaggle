"""Action-specific normalizers for the documented Kaggle CLI read surfaces.

The CLI's display formats are not the Competition Intelligence contract.  This
module accepts only a small, explicit set of list/object/table encodings and
returns a value-free canonical payload for schema validation.  Raw response
bytes remain the archived source of record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


LIVE_PAYLOAD_SCHEMA_VERSION = "kaggle-cli-live-payload-v1"


class LivePayloadError(ValueError):
    """The CLI response cannot be safely interpreted for its requested action."""


@dataclass(frozen=True, slots=True)
class NormalizedPayload:
    action: str
    payload: Mapping[str, Any]


def _as_text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LivePayloadError("response is not UTF-8") from exc


def _json_value(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Kaggle CLI 2.2.3's ``leaderboard --show --format json`` prepends a
        # human display heading before an otherwise valid JSON array.  Accept
        # an embedded complete JSON document, never a JSON-looking fragment.
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            trailing = text[index + end:].strip()
            # Kaggle CLI appends a deterministic usage hint after episode
            # listings.  Accept that framing, but reject arbitrary trailing
            # bytes so malformed payloads remain quarantined.
            if not trailing or trailing.startswith('Use "kaggle competitions '):
                return value
        return None


def _records(value: Any, *, action: str) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, Mapping):
        for key in ("items", "submissions", "episodes", "results", "data"):
            if isinstance(value.get(key), list):
                rows = value[key]
                break
        else:
            rows = [value]
    else:
        raise LivePayloadError(f"{action} must be a JSON list or object")
    if not all(isinstance(row, Mapping) for row in rows):
        raise LivePayloadError(f"{action} rows must be objects")
    return [dict(row) for row in rows]


def _value(row: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        candidate = row.get(name)
        if isinstance(candidate, (str, int)) and str(candidate).strip():
            return str(candidate).strip()
    return None


def _index_value(row: Mapping[str, Any], names: Sequence[str]) -> int | None:
    """Read one non-negative API agent index without coercing floats/bools."""
    for name in names:
        candidate = row.get(name)
        if type(candidate) is int and candidate >= 0:
            return candidate
        if isinstance(candidate, str) and candidate.isdecimal():
            return int(candidate)
    return None


def _number_value(row: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        candidate = row.get(name)
        if type(candidate) in (int, float):
            return float(candidate)
    return None


def _normalized_episode_agents(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep the SDK episode-agent mapping in a small, explicit DTO.

    The command-line renderer in Kaggle CLI 2.2.3 omits this nested field.
    An absent field is therefore represented as an empty list and is handled
    by the caller as a fail-closed mapping miss; a present malformed field is
    a payload error rather than something to guess at.
    """
    raw_agents = row.get("agents", row.get("Agents"))
    if raw_agents is None:
        return []
    if not isinstance(raw_agents, Sequence) or isinstance(raw_agents, (str, bytes)):
        raise LivePayloadError("own_episode_listing agents must be a list")
    normalized: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for agent in raw_agents:
        if not isinstance(agent, Mapping):
            raise LivePayloadError("own_episode_listing agent must be an object")
        submission_id = _value(agent, ("submissionId", "SubmissionId", "submission_id"))
        index = _index_value(agent, ("index", "Index", "agentIndex", "agent_index"))
        if submission_id is None or index is None:
            raise LivePayloadError("own_episode_listing agent lacks submissionId or index")
        if index in seen_indices:
            raise LivePayloadError("own_episode_listing agent index is duplicated")
        seen_indices.add(index)
        normalized.append({
            "submission_id": submission_id,
            "agent_index": index,
            "team_id": _value(agent, ("teamId", "TeamId", "team_id")),
            "team_name": _value(agent, ("teamName", "TeamName", "team_name")),
            "reward": _number_value(agent, ("reward", "Reward")),
            "state": _value(agent, ("state", "State")),
        })
    return normalized


def _table_records(text: str) -> list[Mapping[str, str]]:
    """Parse a conservative CLI table without treating prose as a table.

    Kaggle CLI 2.2.3 can ignore ``--format json`` for leaderboard output.
    Both pipe/box tables and whitespace-column tables are accepted; any row
    shape ambiguity raises instead of silently shifting columns.
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise LivePayloadError("table response has no header and data rows")
    vertical = "│" if any("│" in line for line in lines) else "|" if any("|" in line for line in lines) else None
    if vertical:
        usable = [line for line in lines if vertical in line and not set(line.strip()) <= {vertical, "-", "─", "+", " "}]
        if len(usable) < 2:
            raise LivePayloadError("table response has no parseable rows")
        split = lambda line: [cell.strip() for cell in line.strip().strip(vertical).split(vertical)]
    else:
        # Header and rows must use at least two columns separated by 2+ spaces.
        split = lambda line: [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]
        usable = lines
    header = split(usable[0])
    if len(header) < 2 or any(not name for name in header) or len(set(header)) != len(header):
        raise LivePayloadError("table header is ambiguous")
    rows: list[Mapping[str, str]] = []
    for line in usable[1:]:
        cells = split(line)
        # CLI tables commonly omit trailing blank cells (notably
        # ``privateScore``).  Restore only those trailing blanks; a surplus
        # cell still proves an ambiguous schema and is rejected.
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        if len(cells) != len(header):
            raise LivePayloadError("table row column count differs from header")
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _normalize_records(action: str, raw: Any) -> Mapping[str, Any]:
    rows = _records(raw, action=action)
    normalized: list[dict[str, Any]] = []
    required_aliases: tuple[str, ...] | None = None
    if action == "own_submission_listing":
        required_aliases = ("id", "submissionId", "SubmissionId", "ref")
    elif action == "own_episode_listing":
        required_aliases = ("episodeId", "EpisodeId", "id")
    for row in rows:
        item = dict(row)
        if required_aliases is not None:
            identifier = _value(row, required_aliases)
            if identifier is None:
                raise LivePayloadError(f"{action} row lacks an identifier")
            item["_normalized_id"] = identifier
        if action == "own_episode_listing":
            item["_normalized_agents"] = _normalized_episode_agents(row)
        normalized.append(item)
    return {"schema_version": LIVE_PAYLOAD_SCHEMA_VERSION, "action": action, "records": normalized}


def normalize_live_payload(action: str, body: bytes) -> NormalizedPayload:
    """Return the canonical payload for one Kaggle CLI action or raise.

    Unknown extra fields remain in the raw archive but do not affect required
    identifier extraction.  Replay is deliberately stricter because it is
    the only payload that can enter a training path.
    """
    text = _as_text(body)
    parsed = _json_value(text)
    if action in {"own_submission_listing", "own_episode_listing", "team_submission_listing", "public_artifacts"}:
        if parsed is None:
            raise LivePayloadError(f"{action} is not JSON")
        payload = _normalize_records(action, parsed)
    elif action == "leaderboard":
        if parsed is None:
            payload = {
                "schema_version": LIVE_PAYLOAD_SCHEMA_VERSION,
                "action": action,
                "records": [dict(row) for row in _table_records(text)],
                "input_format": "table",
            }
        else:
            payload = dict(_normalize_records(action, parsed))
            payload["input_format"] = "json"
    elif action == "replay":
        if not isinstance(parsed, Mapping):
            raise LivePayloadError("replay must be a JSON object")
        info = parsed.get("info")
        if not isinstance(info, Mapping):
            raise LivePayloadError("replay lacks info object")
        if not any(key in parsed for key in ("events", "steps", "actions", "decisions", "turns")):
            raise LivePayloadError("replay lacks progression/action field")
        payload = {
            "schema_version": LIVE_PAYLOAD_SCHEMA_VERSION,
            "action": action,
            "info": dict(info),
            "progression_field": next(key for key in ("events", "steps", "actions", "decisions", "turns") if key in parsed),
        }
    elif action == "own_logs":
        # Logs are opaque text and archive-only diagnostic material; no model
        # path receives them.  An empty response is nevertheless an error.
        if not body:
            raise LivePayloadError("own_logs response is empty")
        payload = {"schema_version": LIVE_PAYLOAD_SCHEMA_VERSION, "action": action, "byte_length": len(body)}
    else:
        raise LivePayloadError(f"unsupported live payload action {action!r}")
    return NormalizedPayload(action=action, payload=payload)


def normalized_submission_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    records = payload.get("records")
    if not isinstance(records, list):
        return ()
    return tuple(sorted({str(row["_normalized_id"]) for row in records if isinstance(row, Mapping) and isinstance(row.get("_normalized_id"), str)}))


def normalized_episode_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    return normalized_submission_ids(payload)


def normalized_episode_records(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return unique normalized episode rows in source order for mapping."""
    records = payload.get("records")
    if not isinstance(records, list):
        return ()
    seen: set[str] = set()
    result: list[Mapping[str, Any]] = []
    for row in records:
        if not isinstance(row, Mapping) or not isinstance(row.get("_normalized_id"), str):
            continue
        episode_id = row["_normalized_id"]
        if episode_id not in seen:
            seen.add(episode_id)
            result.append(row)
    return tuple(result)


def identity_from_submission_payload(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return an unambiguous team id/name advertised by own submissions."""
    records = payload.get("records")
    if not isinstance(records, list):
        return None, None
    ids = {_value(row, ("teamId", "TeamId", "team_id")) for row in records if isinstance(row, Mapping)} - {None}
    names = {_value(row, ("teamName", "TeamName", "team_name")) for row in records if isinstance(row, Mapping)} - {None}
    return (next(iter(ids)) if len(ids) == 1 else None, next(iter(names)) if len(names) == 1 else None)


__all__ = [
    "LIVE_PAYLOAD_SCHEMA_VERSION", "LivePayloadError", "NormalizedPayload", "identity_from_submission_payload",
    "normalize_live_payload", "normalized_episode_ids", "normalized_episode_records", "normalized_submission_ids",
]
