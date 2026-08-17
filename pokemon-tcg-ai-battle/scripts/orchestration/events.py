"""Append-only event persistence, atomic JSON writes, and run locks."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .state import RunState, rebuild_state


def utc_now() -> str:
    """Return the current UTC time in a stable ISO-8601 form."""

    return datetime.now(UTC).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    """Durably replace *path* with a formatted JSON document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EventStore:
    """Append events and maintain a rebuildable state materialization."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.events_path = run_dir / "events.jsonl"
        self.state_path = run_dir / "state.json"

    def read_events(self) -> list[dict[str, Any]]:
        """Load all complete JSONL events in order."""

        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.events_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith("\n"):
                    break
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid event at line {line_number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"event at line {line_number} is not an object")
                events.append(value)
        return events

    def append(self, kind: str, payload: dict[str, Any]) -> RunState:
        """Durably append one event and atomically refresh state.json."""

        self.run_dir.mkdir(parents=True, exist_ok=True)
        event = {"version": 1, "kind": kind, "at": utc_now(), "payload": payload}
        data = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
        descriptor = os.open(self.events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return self.rebuild()

    def rebuild(self) -> RunState:
        """Rebuild state.json from events.jsonl and return it."""

        state = rebuild_state(self.read_events())
        atomic_write_json(self.state_path, state.to_dict())
        return state


class RunBusyError(RuntimeError):
    """Raised when another process already owns a run lock."""


class RunLock(AbstractContextManager["RunLock"]):
    """Non-blocking advisory lock preventing concurrent run mutations."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: Any = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise RunBusyError(f"run is already active: {self.path.stem}") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
