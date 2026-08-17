"""Atomic session state and sanitized append-only progress events."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .events import atomic_write_json, utc_now


class OvernightStateError(RuntimeError):
    """Raised for missing, malformed, truncated, or incompatible durable state."""


TASK_STATES = frozenset(
    {
        "PENDING",
        "READY",
        "RUNNING",
        "VERIFYING",
        "REPAIRING",
        "REVIEWING",
        "APPROVED_LOW_RISK",
        "INTEGRATED",
        "WAITING_HUMAN",
        "FAILED",
        "BLOCKED",
        "SKIPPED_DEPENDENCY",
    }
)
TERMINAL_SESSION_STATES = frozenset(
    {
        "DONE",
        "WAITING_HUMAN",
        "FAILED",
        "BLOCKED",
        "STOPPED_BUDGET",
        "STOPPED_ERROR",
        "STOPPED_INTERRUPT",
    }
)
_STATE_FIELDS = {
    "version",
    "session_id",
    "name",
    "status",
    "source_head",
    "source_branch",
    "session_branch",
    "integration_worktree",
    "plan_snapshot_sha256",
    "started_at",
    "updated_at",
    "current_task",
    "tasks",
    "budget",
    "stop_reason",
    "integration_results",
    "report_paths",
}
_EVENT_FIELDS = {
    "version",
    "timestamp",
    "session_id",
    "stage",
    "status",
    "message",
    "task_id",
    "digest",
    "count",
}
_EVENT_REQUIRED_FIELDS = {
    "version",
    "timestamp",
    "session_id",
    "stage",
    "status",
    "message",
}


def validate_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise OvernightStateError("overnight state schema is invalid")
    if value.get("version") != 1:
        raise OvernightStateError("overnight state version is invalid")
    if not isinstance(value.get("tasks"), dict) or not isinstance(value.get("budget"), dict):
        raise OvernightStateError("overnight state collections are invalid")
    for task_id, record in value["tasks"].items():
        if not isinstance(task_id, str) or not isinstance(record, dict):
            raise OvernightStateError("overnight task state is invalid")
        if record.get("status") not in TASK_STATES:
            raise OvernightStateError("overnight task status is invalid")
    return value


def save_state(session_dir: Path, state: dict[str, Any]) -> None:
    validate_state(state)
    atomic_write_json(session_dir / "state.json", state)


def load_state(session_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OvernightStateError("overnight state is missing or malformed") from exc
    return validate_state(raw)


def append_event(
    session_dir: Path,
    *,
    session_id: str,
    stage: str,
    status: str,
    message: str,
    task_id: str | None = None,
    digest: str | None = None,
    count: int | None = None,
) -> None:
    """Append one fixed-shape event without accepting arbitrary provider text."""

    event: dict[str, object] = {
        "version": 1,
        "timestamp": utc_now(),
        "session_id": session_id,
        "stage": stage,
        "status": status,
        "message": message,
    }
    if task_id is not None:
        event["task_id"] = task_id
    if digest is not None:
        event["digest"] = digest
    if count is not None:
        event["count"] = count
    if not set(event).issubset(_EVENT_FIELDS):
        raise OvernightStateError("unsafe overnight event")
    encoded = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
    path = session_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_events(session_dir: Path) -> list[dict[str, object]]:
    path = session_dir / "events.jsonl"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OvernightStateError("overnight event stream is missing") from exc
    if not data or not data.endswith(b"\n"):
        raise OvernightStateError("overnight event stream is truncated")
    records: list[dict[str, object]] = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OvernightStateError("overnight event stream is malformed") from exc
        if (
            not isinstance(value, dict)
            or not _EVENT_REQUIRED_FIELDS.issubset(value)
            or not set(value).issubset(_EVENT_FIELDS)
            or value.get("version") != 1
            or any(
                not isinstance(value.get(field), str)
                for field in ("timestamp", "session_id", "stage", "status", "message")
            )
        ):
            raise OvernightStateError("overnight event schema is invalid")
        records.append(value)
    return records


def current_progress_record(state: dict[str, Any]) -> dict[str, object]:
    return {
        "version": 1,
        "timestamp": state["updated_at"],
        "session_id": state["session_id"],
        "stage": "current",
        "status": state["status"],
        "message": "current overnight state",
        **({"task_id": state["current_task"]} if state["current_task"] else {}),
    }
