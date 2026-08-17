"""Process-group isolation for untrusted/native competition assets.

The controller never imports an asset module.  Each invocation receives a
command that owns all native imports, runs in a new session, and writes an
atomic result shard after the child exits.  This confines Python exceptions,
timeouts and ordinary SIGSEGV exit statuses to one game attempt.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class IsolatedResult:
    command: tuple[str, ...]
    cwd: str
    started_at: float
    ended_at: float
    pid: int | None
    process_group_id: int | None
    exit_code: int | None
    signal_number: int | None
    timed_out: bool
    stdout: str
    stderr: str
    status: str
    peak_rss_kib: int | None = None


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_isolated(
    command: Sequence[str], *, cwd: Path, shard_path: Path, timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> IsolatedResult:
    """Execute one asset attempt without importing its runtime in this process."""
    started = time.time()
    process = subprocess.Popen(
        tuple(command), cwd=cwd, env=dict(env) if env is not None else None,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pid, pgid = process.pid, process.pid
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(pgid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    ended = time.time()
    code = process.returncode
    signum = -code if code is not None and code < 0 else (11 if code == 139 else None)
    status = "TIMEOUT" if timed_out else "SIGSEGV" if signum == 11 else "NORMAL_EXIT" if code == 0 else "CHILD_FAILURE"
    result = IsolatedResult(tuple(command), str(cwd), started, ended, pid, pgid, code, signum, timed_out, stdout, stderr, status)
    _atomic_json(shard_path, asdict(result))
    return result
