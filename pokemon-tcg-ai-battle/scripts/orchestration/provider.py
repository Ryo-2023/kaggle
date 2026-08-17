"""Provider protocol and deterministic adversarial Fake Provider."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .events import atomic_write_json, utc_now
from .process import ProcessResult, run_process
from .schemas import ProviderInvocation, TaskContract


@dataclass(frozen=True)
class ProviderResult:
    """Untrusted provider outcome; it is never authoritative verification."""

    succeeded: bool
    invocation: ProviderInvocation
    reported_evidence: tuple[dict[str, object], ...]
    finding: str | None = None
    child_pid: int | None = None


class Provider(Protocol):
    """Protocol implemented by isolated implementation providers."""

    def invoke(self, worktree: Path, contract: TaskContract, invocation_dir: Path) -> ProviderResult:
        """Run one implementation attempt in *worktree*."""


@dataclass(frozen=True)
class ReadOnlyReviewResult:
    """Untrusted raw reviewer response, intentionally without stream persistence."""

    succeeded: bool
    payload: str
    timed_out: bool = False


_REVIEW_PROMPT_PREFIX = (
    "Review this patch without changing files. Return only one JSON object with exactly "
    "verdict (PASS|PASS_WITH_NOTES|REJECT), risk (LOW|MEDIUM|HIGH), findings "
    "(string array), auto_integration_allowed (boolean).\n"
)


def build_codex_review_prompt(context: dict[str, object]) -> str:
    """Build the exact UTF-8 review prompt used for argv and budget accounting."""

    return _REVIEW_PROMPT_PREFIX + json.dumps(
        context, ensure_ascii=False, sort_keys=True
    )


def _write_configured_changes(worktree: Path, writes: object) -> None:
    if not isinstance(writes, dict) or not writes:
        raise ValueError("fake provider scenario requires a non-empty provider.writes object")
    for relative, content in writes.items():
        if not isinstance(relative, str) or not isinstance(content, str):
            raise ValueError("fake provider writes must map string paths to string contents")
        target = worktree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class FakeProvider:
    """Deterministic provider supporting success and safety-failure scenarios."""

    name = "fake"

    def invoke(self, worktree: Path, contract: TaskContract, invocation_dir: Path) -> ProviderResult:
        """Execute the configured fake scenario and persist invocation streams."""

        scenario = str(contract.provider.get("scenario", "valid"))
        timeout = float(contract.resource_budget.get("provider_timeout_seconds", 0.3))
        invocation_dir.mkdir(parents=True, exist_ok=True)
        started_at = utc_now()
        process_result: ProcessResult | None = None
        finding: str | None = None
        child_pid: int | None = None
        succeeded = False
        stdout = ""
        stderr = ""
        exit_code: int | None = 0
        if scenario == "valid":
            _write_configured_changes(worktree, contract.provider.get("writes"))
            artifacts = contract.provider.get("worker_artifacts")
            if artifacts:
                _write_configured_changes(worktree, artifacts)
            stdout = json.dumps({"status": "passed", "scenario": scenario})
            succeeded = True
        elif scenario in {"forbidden_write", "protected_write"}:
            default = "forbidden.txt"
            if scenario == "protected_write" and contract.protected_paths:
                default = contract.protected_paths[0]
            _write_configured_changes(worktree, {default: "modified by fake provider\n"})
            succeeded = True
            stdout = json.dumps({"status": "passed", "scenario": scenario})
        elif scenario == "partial_write":
            _write_configured_changes(worktree, contract.provider.get("writes"))
            exit_code = 7
            finding = "fake provider exited after a partial write"
            stderr = finding
        elif scenario == "nonzero_exit":
            exit_code = 9
            finding = "fake provider returned a non-zero exit status"
            stderr = finding
        elif scenario in {"timeout", "child_process_leak"}:
            pid_file = invocation_dir / "child.pid"
            if scenario == "timeout":
                code = "import time; time.sleep(60)"
            else:
                code = (
                    "import pathlib,subprocess,sys,time; "
                    f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(60)"
                )
            scenario_script = invocation_dir / "fake_process.py"
            scenario_script.write_text(code + "\n", encoding="utf-8")
            process_result = run_process(
                [sys.executable, str(scenario_script)],
                cwd=worktree,
                timeout_seconds=timeout,
                environment_allowlist=contract.environment_allowlist,
                inject_orchestrator_child=True,
            )
            stdout = process_result.stdout
            stderr = process_result.stderr
            exit_code = process_result.exit_code
            finding = "fake provider timed out"
            if pid_file.exists():
                child_pid = int(pid_file.read_text(encoding="utf-8"))
        else:
            raise ValueError(f"unsupported fake provider scenario: {scenario}")
        ended_at = utc_now()
        stdout_path = invocation_dir / "stdout.txt"
        stderr_path = invocation_dir / "stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        invocation = ProviderInvocation(
            provider="fake",
            exact_model_id=None,
            effort=None,
            cli_version="builtin",
            started_at=process_result.started_at if process_result else started_at,
            ended_at=process_result.ended_at if process_result else ended_at,
            exit_code=exit_code,
            input_tokens=None,
            output_tokens=None,
            usage_source=None,
            stdout_ref=str(stdout_path),
            stderr_ref=str(stderr_path),
            timed_out=process_result.timed_out if process_result else False,
        )
        atomic_write_json(invocation_dir / "invocation.json", asdict(invocation))
        return ProviderResult(
            succeeded=succeeded,
            invocation=invocation,
            reported_evidence=({"scenario": scenario, "claimed_success": succeeded},),
            finding=finding,
            child_pid=child_pid,
        )

    def review(
        self, worktree: Path, context: dict[str, object], invocation_dir: Path
    ) -> ReadOnlyReviewResult:
        """Return deterministic review data for isolated orchestration tests."""

        del worktree, context
        invocation_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"verdict": "PASS", "risk": "LOW", "findings": [], "auto_integration_allowed": True}
        )
        return ReadOnlyReviewResult(True, payload)


class CodexProvider:
    """Real single-worker provider backed by the local ``codex exec`` CLI."""

    name = "codex"

    def __init__(self, executable: str | None = None):
        resolved = executable or shutil.which("codex")
        if not resolved:
            raise RuntimeError("codex CLI is unavailable")
        self.executable = str(Path(resolved).resolve())

    def probe(self, environment_allowlist: tuple[str, ...] = ()) -> dict[str, object]:
        """Probe CLI availability and version without starting an AI worker."""

        result = run_process(
            [self.executable, "--version"],
            cwd=Path.cwd(),
            timeout_seconds=5,
            environment_allowlist=environment_allowlist,
        )
        return {
            "available": result.passed,
            "cli_version": result.stdout.strip() or result.stderr.strip() or None,
            "workspace_write": True,
            "structured_output": True,
            "timeout_group_kill": True,
        }

    def invoke(self, worktree: Path, contract: TaskContract, invocation_dir: Path) -> ProviderResult:
        """Run one ephemeral Codex worker inside the isolated worker worktree."""

        invocation_dir.mkdir(parents=True, exist_ok=True)
        model = contract.provider.get("model")
        effort = contract.provider.get("effort")
        argv = [
            self.executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            "allow_login_shell=false",
            "-c",
            "sandbox_workspace_write.network_access=false",
            "--color",
            "never",
            "-C",
            str(worktree),
        ]
        if isinstance(model, str) and model:
            argv.extend(["--model", model])
        if isinstance(effort, str) and effort:
            argv.extend(["-c", f'model_reasoning_effort="{effort}"'])
        argv.append(self._prompt(contract))
        validate_codex_launch_arguments(argv)
        timeout = float(contract.resource_budget.get("provider_timeout_seconds", 900.0))
        result = run_process(
            argv,
            cwd=worktree,
            timeout_seconds=timeout,
            environment_allowlist=contract.environment_allowlist,
            inject_orchestrator_child=True,
        )
        stdout_path = invocation_dir / "stdout.txt"
        stderr_path = invocation_dir / "stderr.txt"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        version = self.probe(contract.environment_allowlist)
        invocation = ProviderInvocation(
            provider="codex",
            exact_model_id=model if isinstance(model, str) else None,
            effort=effort if isinstance(effort, str) else None,
            cli_version=(
                str(version["cli_version"]) if version.get("cli_version") is not None else None
            ),
            started_at=result.started_at,
            ended_at=result.ended_at,
            exit_code=result.exit_code,
            input_tokens=None,
            output_tokens=None,
            usage_source=None,
            stdout_ref=str(stdout_path),
            stderr_ref=str(stderr_path),
            timed_out=result.timed_out,
        )
        atomic_write_json(invocation_dir / "invocation.json", asdict(invocation))
        succeeded = result.passed
        finding = None
        if not succeeded:
            finding = "codex provider timed out" if result.timed_out else "codex provider failed"
        return ProviderResult(
            succeeded=succeeded,
            invocation=invocation,
            reported_evidence=({"provider": "codex", "claimed_success": succeeded},),
            finding=finding,
        )

    def review(
        self, worktree: Path, context: dict[str, object], invocation_dir: Path
    ) -> ReadOnlyReviewResult:
        """Run a separate read-only reviewer without changing the worker sandbox policy."""

        invocation_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_codex_review_prompt(context)
        argv = [
            self.executable, "exec", "--ignore-user-config", "--ephemeral", "--sandbox", "read-only",
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "allow_login_shell=false", "-c", "sandbox_workspace_write.network_access=false",
            "--color", "never", "-C", str(worktree), prompt,
        ]
        model = context.get("model")
        effort = context.get("effort")
        insert_at = argv.index(prompt)
        if isinstance(model, str) and model:
            argv[insert_at:insert_at] = ["--model", model]
            insert_at += 2
        if isinstance(effort, str) and effort:
            argv[insert_at:insert_at] = ["-c", f'model_reasoning_effort="{effort}"']
        validate_codex_review_launch_arguments(argv)
        result = run_process(argv, cwd=worktree, timeout_seconds=300.0, inject_orchestrator_child=True)
        # Deliberately do not retain raw reviewer streams: they may contain patch context.
        return ReadOnlyReviewResult(result.passed, result.stdout.strip(), result.timed_out)

    @staticmethod
    def security_configuration() -> dict[str, object]:
        """Return non-sensitive security settings enforced on every Codex launch."""

        return {
            "approval_policy": "never",
            "sandbox_mode": "workspace-write",
            "web_search": "disabled",
            "allow_login_shell": False,
            "network_access": False,
            "ignore_user_config": True,
            "dangerous_flags": False,
        }

    @staticmethod
    def _prompt(contract: TaskContract) -> str:
        allowed = "\n".join(f"- {path}" for path in contract.allowed_paths)
        protected = "\n".join(f"- {path}" for path in contract.protected_paths) or "- none"
        return (
            f"{contract.provider['prompt']}\n\n"
            "You are an isolated implementation worker. Edit files only; do not commit, push, "
            "tag, submit to Kaggle, inspect secrets, or invoke the orchestrator.\n"
            f"Allowed paths:\n{allowed}\nProtected paths:\n{protected}\n"
            "Do not change acceptance conditions. The Control Plane will capture the actual diff "
            "and run authoritative verification separately."
        )


def provider_for(contract: TaskContract) -> Provider:
    """Construct the single provider selected by a validated TaskContract."""

    provider_type = contract.provider.get("type", "fake")
    if provider_type == "fake":
        return FakeProvider()
    if provider_type == "codex":
        return CodexProvider()
    raise ValueError(f"unsupported provider type: {provider_type}")


def validate_codex_launch_arguments(argv: list[str]) -> None:
    """Reject weakened Codex sandbox arguments and require fixed safe settings."""

    lowered = [argument.lower() for argument in argv]
    forbidden = {
        "--dangerously-bypass-approvals-and-sandbox",
        "--yolo",
        "danger-full-access",
    }
    if forbidden.intersection(lowered):
        raise RuntimeError("unsafe Codex launch arguments are forbidden")
    joined = "\0".join(lowered)
    required = (
        "--sandbox\0workspace-write",
        'approval_policy="never"',
        'web_search="disabled"',
        "allow_login_shell=false",
        "sandbox_workspace_write.network_access=false",
        "--ignore-user-config",
    )
    if any(setting not in joined for setting in required):
        raise RuntimeError("required Codex security setting is missing")


def validate_codex_review_launch_arguments(argv: list[str]) -> None:
    """Require the same noninteractive security controls and a read-only filesystem."""

    try:
        index = argv.index("--sandbox")
    except ValueError as exc:
        raise RuntimeError("reviewer sandbox setting is missing") from exc
    comparable = list(argv)
    if index + 1 >= len(comparable):
        raise RuntimeError("reviewer sandbox setting is missing")
    comparable[index + 1] = "workspace-write"
    validate_codex_launch_arguments(comparable)
    joined = "\0".join(item.lower() for item in argv)
    if "--sandbox\0read-only" not in joined:
        raise RuntimeError("reviewer must use a read-only sandbox")


def process_exists(pid: int) -> bool:
    """Return whether a process PID still exists, including zombies."""

    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            if stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                return False
        except (OSError, IndexError):
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
