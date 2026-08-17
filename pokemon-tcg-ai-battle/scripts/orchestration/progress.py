"""Best-effort, sanitized progress records for orchestration observers.

This module is deliberately outside the authoritative event stream.  Its
records are useful to humans following a run, but they must never influence
state reconstruction, retries, verification, or cleanup.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Iterable, TextIO

from .events import utc_now
from .state import RunStatus


class ProgressDataError(ValueError):
    """Raised for malformed sanitized observer data."""


_WARNING = "warning: progress observer disabled"
_ALLOWED_STAGES = frozenset(
    {
        "intake",
        "snapshot",
        "implementation",
        "provider",
        "patch",
        "verification",
        "command",
        "approval",
        "terminal",
    }
)
_ALLOWED_MESSAGES = frozenset(
    {
        "run created",
        "snapshot ready",
        "implementation started",
        "provider running",
        "patch captured",
        "deterministic verification started",
        "waiting for integration approval",
        "blocked",
        "done",
        "rejected",
        "aborted",
        "command started",
        "command passed",
        "command failed",
    }
)
_TERMINAL_STATES = frozenset({"DONE", "BLOCKED", "REJECTED", "ABORTED"})


def _plain_state(value: RunStatus | str) -> str:
    state = value.value if isinstance(value, RunStatus) else value
    if state not in {item.value for item in RunStatus}:
        raise ProgressDataError("invalid progress state")
    return state


def _record(
    run_id: str,
    state: RunStatus | str,
    stage: str,
    message: str,
    *,
    command_index: int | None = None,
    command_total: int | None = None,
    status: str | None = None,
) -> dict[str, object]:
    """Map trusted progress arguments to the small public record schema."""

    if not isinstance(run_id, str) or not run_id.startswith("run-"):
        raise ProgressDataError("invalid progress run identifier")
    if stage not in _ALLOWED_STAGES or message not in _ALLOWED_MESSAGES:
        raise ProgressDataError("invalid progress record")
    value: dict[str, object] = {
        "timestamp": utc_now(),
        "run_id": run_id,
        "state": _plain_state(state),
        "stage": stage,
        "message": message,
    }
    if command_index is not None or command_total is not None or status is not None:
        if (
            type(command_index) is not int
            or type(command_total) is not int
            or command_index < 1
            or command_total < command_index
        ):
            raise ProgressDataError("invalid command progress")
        value["command_index"] = command_index
        value["command_total"] = command_total
        if status is not None:
            if status not in {"started", "passed", "failed"}:
                raise ProgressDataError("invalid command status")
            value["status"] = status
    return value


def _validated_record(value: object) -> dict[str, object]:
    """Validate a completed observer-stream line without accepting extra data."""

    if not isinstance(value, dict):
        raise ProgressDataError("invalid progress record")
    allowed = {
        "timestamp",
        "run_id",
        "state",
        "stage",
        "message",
        "command_index",
        "command_total",
        "status",
    }
    if set(value).difference(allowed):
        raise ProgressDataError("invalid progress record")
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise ProgressDataError("invalid progress timestamp")
    record = _record(
        value.get("run_id"),
        value.get("state"),
        value.get("stage"),
        value.get("message"),
        command_index=value.get("command_index"),
        command_total=value.get("command_total"),
        status=value.get("status"),
    )
    record["timestamp"] = timestamp
    if ("command_index" in value) != ("command_total" in value):
        raise ProgressDataError("invalid command progress")
    if "status" in value and "command_index" not in value:
        raise ProgressDataError("invalid command status")
    return record


class Progress:
    """Append sanitized progress records and disable itself on observer failures."""

    def __init__(
        self,
        stderr: TextIO | None = None,
        sink: Callable[[dict[str, object]], None] | None = None,
    ):
        self._stderr = stderr if stderr is not None else sys.stderr
        self._sink = sink
        self._sink_disabled = False
        self._disabled = False
        self._warned = False
        self._last: dict[str, dict[str, object]] = {}

    @staticmethod
    def path(run_dir: Path) -> Path:
        return run_dir / "progress.jsonl"

    @property
    def disabled(self) -> bool:
        return self._disabled

    def emit(
        self,
        run_dir: Path,
        run_id: str,
        state: RunStatus | str,
        stage: str,
        message: str,
        *,
        command_index: int | None = None,
        command_total: int | None = None,
        status: str | None = None,
    ) -> None:
        """Best-effort append.  Any observer failure disables only this observer."""

        if self._disabled:
            return
        try:
            record = _record(
                run_id,
                state,
                stage,
                message,
                command_index=command_index,
                command_total=command_total,
                status=status,
            )
            prior = self._last.get(run_id)
            if prior is not None and all(
                prior.get(key) == record.get(key)
                for key in record
                if key != "timestamp"
            ):
                return
            self._append(self.path(run_dir), record)
            self._last[run_id] = record
            self._publish(record)
        except Exception:
            self._disable()

    def _publish(self, record: dict[str, object]) -> None:
        """Send a durable sanitized record to the optional live sink."""

        if self._sink is None or self._sink_disabled:
            return
        try:
            self._sink(dict(record))
        except Exception:
            self._sink_disabled = True
            self._warn()

    def _append(self, path: Path, record: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size:
            with path.open("rb") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    raise ProgressDataError("incomplete progress record")
        data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _disable(self) -> None:
        self._disabled = True
        self._warn()

    def _warn(self) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            self._stderr.write(_WARNING + "\n")
            self._stderr.flush()
        except Exception:
            pass

    @classmethod
    def read(cls, run_dir: Path) -> list[dict[str, object]]:
        """Read complete sanitized progress records, rejecting malformed data."""

        path = cls.path(run_dir)
        if not path.exists():
            raise ProgressDataError("progress data is missing")
        records: list[dict[str, object]] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.endswith("\n"):
                        raise ProgressDataError("malformed progress data")
                    try:
                        records.append(_validated_record(json.loads(line)))
                    except (json.JSONDecodeError, TypeError, ProgressDataError) as exc:
                        raise ProgressDataError("malformed progress data") from exc
        except OSError as exc:
            raise ProgressDataError("progress observer unavailable") from exc
        if not records:
            raise ProgressDataError("progress data is missing")
        return records


def new_records(records: Iterable[dict[str, object]], baseline: int) -> list[dict[str, object]]:
    """Return records appended after a previously observed record count."""

    if type(baseline) is not int or baseline < 0:
        raise ProgressDataError("invalid progress baseline")
    return list(records)[baseline:]


def is_terminal(record: dict[str, object]) -> bool:
    """Return whether a sanitized record represents a terminal state."""

    return record.get("state") in _TERMINAL_STATES


def is_terminal_state(state: RunStatus | str) -> bool:
    """Return whether a state is terminal for a human follow session."""

    return _plain_state(state) in _TERMINAL_STATES
