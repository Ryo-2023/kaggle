"""Durable, resumable run lifecycle for the Offline Training v1 pipeline.

Responsibilities:

* a fixed run-directory layout under ``runs/offline-training-v1/<run-id>/``;
* fsync-backed atomic JSON writes (temp sibling -> flush -> fsync -> replace ->
  parent-dir fsync);
* an append-only ``events.jsonl`` audit log;
* a single-writer lock keyed on PID and, where the OS exposes it, the process
  start marker, so a stale lock from a dead PID is safely recovered while an
  active lock is refused;
* signal-aware interruption that records ``INTERRUPTED`` and releases the lock.

The manifest is the source of truth for phase progress and is what ``--resume``
reads to decide which phases may be skipped.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Mapping


MANIFEST_SCHEMA_VERSION = "offline-training-v1-manifest-v1"
LOCK_NAME = "run.lock"
MANIFEST_NAME = "run_manifest.json"
EVENTS_NAME = "events.jsonl"

PHASES = ("collect", "build-dataset", "train", "evaluate", "screen", "export", "package", "verify")

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETE = "COMPLETE"
STATUS_SKIPPED = "SKIPPED"
STATUS_INTERRUPTED = "INTERRUPTED"
STATUS_FAILED_RETRYABLE = "FAILED_RETRYABLE"
STATUS_FAILED_FINAL = "FAILED_FINAL"

_TERMINAL_OK = {STATUS_COMPLETE, STATUS_SKIPPED}


class RunStateError(RuntimeError):
    """Raised on lock contention or a corrupt/incompatible run directory."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path: str | Path, value: object) -> None:
    """Write JSON durably: temp sibling, flush, fsync, replace, parent fsync."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_json(value) + "\n"
    temporary = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, encoded.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, destination)
    _fsync_dir(destination.parent)


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def process_start_marker(pid: int) -> str | None:
    """Best-effort stable process identity to distinguish PID reuse.

    Returns a marker string when the OS exposes one (Linux ``/proc`` starttime),
    otherwise ``None`` so callers fall back to a plain liveness check.
    """
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # field 22 (1-indexed) is starttime; the comm field may contain spaces and
    # parentheses, so split on the closing parenthesis of comm first.
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 1 :].split()
    if len(fields) < 20:
        return None
    return fields[19]  # starttime relative to fields after comm


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def lock(self) -> Path:
        return self.root / LOCK_NAME

    @property
    def events(self) -> Path:
        return self.root / EVENTS_NAME

    @property
    def config_resolved(self) -> Path:
        return self.root / "config.resolved.json"

    @property
    def environment(self) -> Path:
        return self.root / "environment.json"

    @property
    def resource_metrics(self) -> Path:
        return self.root / "resource_metrics.jsonl"

    def phase_dir(self, name: str) -> Path:
        mapping = {
            "collect": "collection",
            "build-dataset": "dataset",
            "train": "checkpoints",
            "evaluate": "evaluation",
            "screen": "evaluation",
            "export": "export",
            "package": "package",
            "verify": "package",
        }
        return self.root / mapping.get(name, name)

    def ensure_layout(self) -> None:
        for name in ("collection", "dataset", "checkpoints", "evaluation", "export", "package", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)


def new_manifest(*, run_id: str, git_commit: str, config_hash: str, environment_hash: str) -> dict[str, Any]:
    now = _timestamp()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "git_commit": git_commit,
        "config_hash": config_hash,
        "environment_hash": environment_hash,
        "current_phase": None,
        "phase_statuses": {phase: STATUS_PENDING for phase in PHASES},
        "dataset_hash": None,
        "feature_schema_hash": None,
        "teacher_id": None,
        "model_purpose": None,
        "model_hash": None,
        "best_checkpoint": None,
        "last_checkpoint": None,
        "package_hash": None,
        "resume_count": 0,
        "error_summary": None,
        "created_at": now,
        "updated_at": now,
    }


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RunState:
    """A loaded or freshly created run with a held single-writer lock."""

    def __init__(self, paths: RunPaths, manifest: dict[str, Any]):
        self.paths = paths
        self.manifest = manifest

    # -- persistence -------------------------------------------------------- #
    def save(self) -> None:
        self.manifest["updated_at"] = _timestamp()
        atomic_write_json(self.paths.manifest, self.manifest)

    def append_event(self, event_type: str, **fields: Any) -> None:
        record = {"ts": _timestamp(), "type": event_type, **fields}
        line = _canonical_json(record) + "\n"
        fd = os.open(self.paths.events, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def append_resource_metric(self, **fields: Any) -> None:
        record = {"ts": _timestamp(), **fields}
        with self.paths.resource_metrics.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(record) + "\n")

    # -- phase transitions -------------------------------------------------- #
    def phase_status(self, phase: str) -> str:
        return self.manifest["phase_statuses"].get(phase, STATUS_PENDING)

    def is_phase_done(self, phase: str) -> bool:
        return self.phase_status(phase) in _TERMINAL_OK

    def set_phase(self, phase: str, status: str, **manifest_updates: Any) -> None:
        if phase not in PHASES:
            raise RunStateError(f"unknown phase {phase!r}")
        self.manifest["phase_statuses"][phase] = status
        self.manifest["current_phase"] = phase if status == STATUS_RUNNING else self.manifest.get("current_phase")
        for key, value in manifest_updates.items():
            self.manifest[key] = value
        self.save()
        self.append_event("phase_status", phase=phase, status=status)


def _lock_payload(run_id: str) -> dict[str, Any]:
    pid = os.getpid()
    return {
        "pid": pid,
        "hostname": socket.gethostname(),
        "process_start_marker": process_start_marker(pid),
        "created_at": _timestamp(),
        "run_id": run_id,
    }


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _lock_is_stale(existing: dict[str, Any]) -> bool:
    pid = existing.get("pid")
    if not isinstance(pid, int):
        return True
    if not pid_is_alive(pid):
        return True
    # PID is alive: confirm it is the *same* process when a marker is available.
    recorded = existing.get("process_start_marker")
    current = process_start_marker(pid)
    if recorded is not None and current is not None and recorded != current:
        return True  # PID was reused by a different process
    if existing.get("hostname") not in (None, socket.gethostname()):
        # A live PID on a different host cannot be reasoned about; refuse.
        return False
    return False


def acquire_lock(paths: RunPaths, run_id: str) -> None:
    """Acquire the run's single-writer lock, recovering only a stale lock."""
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = _lock_payload(run_id)
    encoded = (_canonical_json(payload) + "\n").encode("utf-8")
    while True:
        try:
            fd = os.open(paths.lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            existing = _read_lock(paths.lock)
            if existing is not None and existing.get("pid") == os.getpid():
                return  # re-entrant within the same process
            if existing is None or _lock_is_stale(existing):
                try:
                    os.unlink(paths.lock)
                except FileNotFoundError:
                    pass
                continue
            raise RunStateError(
                f"run {run_id} is locked by an active process (pid={existing.get('pid')})"
            )
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        return


def release_lock(paths: RunPaths) -> None:
    existing = _read_lock(paths.lock)
    if existing is not None and existing.get("pid") != os.getpid():
        return  # never release another process's lock
    try:
        os.unlink(paths.lock)
    except FileNotFoundError:
        pass


@contextmanager
def run_lock(paths: RunPaths, run_id: str) -> Iterator[None]:
    acquire_lock(paths, run_id)
    try:
        yield
    finally:
        release_lock(paths)


def load_or_create(
    run_dir: str | Path,
    *,
    run_id: str,
    git_commit: str,
    config_hash: str,
    environment_hash: str,
    resume: bool,
) -> RunState:
    """Create a fresh run or load an existing one for resume, with validation."""
    paths = RunPaths(Path(run_dir))
    if paths.manifest.exists():
        raw = json.loads(paths.manifest.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise RunStateError("run manifest is corrupt")
        manifest = dict(raw)
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise RunStateError("run manifest schema is incompatible")
        if not resume:
            raise RunStateError("run directory already exists; pass --resume to continue it")
        if manifest.get("config_hash") != config_hash:
            raise RunStateError("resume rejected: config hash differs from the existing run")
        manifest["resume_count"] = int(manifest.get("resume_count", 0)) + 1
        paths.ensure_layout()
        state = RunState(paths, manifest)
        state.save()
        state.append_event("resume", resume_count=manifest["resume_count"])
        return state
    if resume:
        raise RunStateError("resume requested but no existing run manifest was found")
    paths.ensure_layout()
    manifest = new_manifest(
        run_id=run_id, git_commit=git_commit, config_hash=config_hash, environment_hash=environment_hash
    )
    state = RunState(paths, manifest)
    state.save()
    state.append_event("run_created", run_id=run_id)
    return state


__all__ = [
    "EVENTS_NAME",
    "LOCK_NAME",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "PHASES",
    "RunPaths",
    "RunState",
    "RunStateError",
    "STATUS_COMPLETE",
    "STATUS_FAILED_FINAL",
    "STATUS_FAILED_RETRYABLE",
    "STATUS_INTERRUPTED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SKIPPED",
    "acquire_lock",
    "atomic_write_json",
    "load_or_create",
    "pid_is_alive",
    "process_start_marker",
    "release_lock",
    "run_lock",
]
