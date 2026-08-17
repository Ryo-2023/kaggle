"""Durable run layout, manifest, and single-writer lock for a Competition
Intelligence run directory.

Mirrors ``mage_ptcg.offline_training.runstate``'s lock/manifest mechanism
(PID + ``/proc`` start-time marker for stale-lock recovery, fsync-backed
atomic manifest writes) as its own copy rather than an import, so this
sidecar has no dependency on ``offline_training`` (the dependency, if any,
only ever goes the other way: ``offline_training`` may optionally read a
Competition Intelligence snapshot).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

from .atomic_io import append_jsonl_line, atomic_write_json

MANIFEST_SCHEMA_VERSION = "competition-intelligence-run-manifest-v1"
LOCK_NAME = "run.lock"
MANIFEST_NAME = "run_manifest.json"
EVENTS_NAME = "events.jsonl"
DEFAULT_RUN_ROOT = Path("runs/competition-intelligence")

_LAYOUT_DIRS = (
    "raw",
    "source_manifests",
    "normalized",
    "derived",
    "snapshots",
    "reports",
    "quarantine",
    "state",
    "datasets",
)


class RunStateError(RuntimeError):
    """Raised on lock contention or a corrupt/incompatible run directory."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def process_start_marker(pid: int) -> str | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 1 :].split()
    if len(fields) < 20:
        return None
    return fields[19]


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
    def raw(self) -> Path:
        return self.root / "raw" / "sha256"

    @property
    def source_manifests(self) -> Path:
        return self.root / "source_manifests"

    @property
    def normalized(self) -> Path:
        return self.root / "normalized"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def datasets(self) -> Path:
        return self.root / "datasets"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def catalog_db(self) -> Path:
        return self.state / "catalog.sqlite3"

    def ensure_layout(self) -> None:
        for name in _LAYOUT_DIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)


def new_manifest(*, run_id: str, git_commit: str, config_hash: str) -> dict[str, Any]:
    now = _timestamp()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "git_commit": git_commit,
        "config_hash": config_hash,
        "ingested_source_ids": [],
        "latest_snapshot_id": None,
        "resume_count": 0,
        "created_at": now,
        "updated_at": now,
    }


class RunState:
    """A loaded or freshly created Competition Intelligence run, lock held."""

    def __init__(self, paths: RunPaths, manifest: dict[str, Any]):
        self.paths = paths
        self.manifest = manifest

    def save(self) -> None:
        self.manifest["updated_at"] = _timestamp()
        atomic_write_json(self.paths.manifest, self.manifest)

    def append_event(self, event_type: str, **fields: Any) -> None:
        append_jsonl_line(self.paths.events, {"ts": _timestamp(), "type": event_type, **fields})

    def record_ingested_source(self, source_id: str) -> None:
        ids = set(self.manifest.get("ingested_source_ids", []))
        if source_id in ids:
            return
        ids.add(source_id)
        self.manifest["ingested_source_ids"] = sorted(ids)
        self.save()
        self.append_event("source_ingested", source_id=source_id)


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


def _lock_is_stale(existing: Mapping[str, Any]) -> bool:
    pid = existing.get("pid")
    if not isinstance(pid, int):
        return True
    if not pid_is_alive(pid):
        return True
    recorded = existing.get("process_start_marker")
    current = process_start_marker(pid)
    if recorded is not None and current is not None and recorded != current:
        return True  # PID reused by a different process
    if existing.get("hostname") not in (None, socket.gethostname()):
        return False  # a live PID on a different host cannot be reasoned about
    return False


def acquire_lock(paths: RunPaths, run_id: str) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = _lock_payload(run_id)
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
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
            raise RunStateError(f"run {run_id} is locked by an active process (pid={existing.get('pid')})")
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


def load_or_create(run_dir: str | Path, *, run_id: str, git_commit: str, config_hash: str, resume: bool) -> RunState:
    paths = RunPaths(Path(run_dir))
    if paths.manifest.exists():
        raw = json.loads(paths.manifest.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise RunStateError("run manifest is corrupt")
        manifest = dict(raw)
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise RunStateError("run manifest schema is incompatible")
        if not resume:
            raise RunStateError("run directory already exists; pass resume=True to continue it")
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
    manifest = new_manifest(run_id=run_id, git_commit=git_commit, config_hash=config_hash)
    state = RunState(paths, manifest)
    state.save()
    state.append_event("run_created", run_id=run_id)
    return state


__all__ = [
    "DEFAULT_RUN_ROOT",
    "EVENTS_NAME",
    "LOCK_NAME",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "RunPaths",
    "RunState",
    "RunStateError",
    "acquire_lock",
    "load_or_create",
    "pid_is_alive",
    "process_start_marker",
    "release_lock",
    "run_lock",
]
