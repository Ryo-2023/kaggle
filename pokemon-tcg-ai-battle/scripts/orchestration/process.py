"""Safe subprocess execution with allowlisted environments and group timeouts."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

from .policy import validate_command


@dataclass(frozen=True)
class ProcessResult:
    """Captured result of one bounded argv-based process."""

    argv: tuple[str, ...]
    cwd: str
    environment_digest: str
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: str
    ended_at: str
    duration_seconds: float
    timed_out: bool

    @property
    def passed(self) -> bool:
        """Return true only for a non-timeout zero exit status."""

        return not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable evidence record."""

        return asdict(self)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def allowlisted_environment(
    names: Sequence[str],
    extra: Mapping[str, str] | None = None,
    *,
    inject_orchestrator_child: bool = False,
) -> dict[str, str]:
    """Build an environment containing only explicitly named variables."""

    environment = {name: os.environ[name] for name in names if name in os.environ}
    if extra:
        disallowed = set(extra).difference(names)
        if disallowed:
            raise ValueError(f"environment values not on allowlist: {sorted(disallowed)}")
        environment.update(extra)
    environment.pop("MAGE_ORCHESTRATOR_CHILD", None)
    if inject_orchestrator_child:
        environment["MAGE_ORCHESTRATOR_CHILD"] = "1"
    return environment


def run_process(
    argv: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    environment_allowlist: Sequence[str] = (),
    extra_environment: Mapping[str, str] | None = None,
    output_limit: int = 1_000_000,
    inject_orchestrator_child: bool = False,
) -> ProcessResult:
    """Run an argv command and kill its whole process group on timeout."""

    validate_command(argv)
    resolved_argv = list(argv)
    if "/" not in resolved_argv[0]:
        executable = shutil.which(resolved_argv[0])
        if executable is None:
            raise FileNotFoundError(f"command executable not found: {resolved_argv[0]}")
        resolved_argv[0] = executable
    environment = allowlisted_environment(
        environment_allowlist,
        extra_environment,
        inject_orchestrator_child=inject_orchestrator_child,
    )
    digest = hashlib.sha256(
        "\0".join(f"{key}={environment[key]}" for key in sorted(environment)).encode()
    ).hexdigest()
    started_at = _timestamp()
    started = time.monotonic()
    process = subprocess.Popen(
        resolved_argv,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    duration = time.monotonic() - started
    return ProcessResult(
        argv=tuple(resolved_argv),
        cwd=str(cwd),
        environment_digest=digest,
        exit_code=process.returncode,
        stdout=stdout[:output_limit],
        stderr=stderr[:output_limit],
        started_at=started_at,
        ended_at=_timestamp(),
        duration_seconds=duration,
        timed_out=timed_out,
    )
