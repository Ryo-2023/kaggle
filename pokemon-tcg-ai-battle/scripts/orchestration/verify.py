"""Authoritative verification in a clean snapshot-derived worktree."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from .events import atomic_write_json
from .process import ProcessResult, run_process
from .schemas import TaskContract
from .snapshot import tree_digest


_DEFAULT_VERIFICATION_ENVIRONMENT = {
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_BLOCKED_LEGACY_PYTHON_ENVIRONMENT = frozenset(
    {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
)


def _verification_environment(
    contract: TaskContract,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Build the isolated verification environment without exposing its values."""

    inherited_names = tuple(
        dict.fromkeys(
            name
            for name in contract.environment_allowlist
            if name not in _BLOCKED_LEGACY_PYTHON_ENVIRONMENT and name in os.environ
        )
    )
    environment = dict(_DEFAULT_VERIFICATION_ENVIRONMENT)
    environment.update({name: os.environ[name] for name in inherited_names})
    environment.update(contract.verification_environment)
    allowlist = tuple(dict.fromkeys((*inherited_names, *environment)))
    return allowlist, environment


def verify(
    worktree: Path,
    contract: TaskContract,
    evidence_dir: Path,
    expected_protected_digest: str,
    snapshot_digest: str,
    patch_digest: str,
    command_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[bool, tuple[dict[str, object], ...]]:
    """Run all authoritative commands after checking protected content."""

    evidence_dir.mkdir(parents=True, exist_ok=True)
    actual_protected_digest = tree_digest(worktree, contract.protected_paths)
    evidence: list[dict[str, object]] = []
    protected_ok = actual_protected_digest == expected_protected_digest
    evidence.append(
        {
            "kind": "protected_hash",
            "expected": expected_protected_digest,
            "actual": actual_protected_digest,
            "passed": protected_ok,
            "snapshot_digest": snapshot_digest,
            "patch_digest": patch_digest,
        }
    )
    passed = protected_ok
    timeout = float(contract.resource_budget.get("verification_timeout_seconds", 30.0))
    environment_allowlist, extra_environment = _verification_environment(contract)
    progress = command_progress
    command_total = len(contract.verification_commands)

    def notify(command_index: int, status: str) -> None:
        nonlocal progress
        if progress is None:
            return
        try:
            progress(command_index, command_total, status)
        except Exception:
            progress = None

    for index, command in enumerate(contract.verification_commands):
        notify(index + 1, "started")
        result: ProcessResult = run_process(
            command,
            cwd=worktree,
            timeout_seconds=timeout,
            environment_allowlist=environment_allowlist,
            extra_environment=extra_environment,
            inject_orchestrator_child=False,
        )
        record = {"kind": "command", **result.to_dict()}
        evidence.append(record)
        atomic_write_json(evidence_dir / f"command-{index:03d}.json", record)
        notify(index + 1, "passed" if result.passed else "failed")
        if not result.passed:
            passed = False
            break
    atomic_write_json(evidence_dir / "summary.json", {"passed": passed, "evidence": evidence})
    return passed, tuple(evidence)
