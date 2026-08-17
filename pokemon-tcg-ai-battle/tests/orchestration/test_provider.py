from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.orchestration.provider import (
    CodexProvider,
    build_codex_review_prompt,
    validate_codex_launch_arguments,
    validate_codex_review_launch_arguments,
)
from scripts.orchestration.kernel import Kernel
from scripts.orchestration.schemas import SchemaError, TaskContract
from scripts.orchestration.state import RunStatus


def test_codex_provider_invokes_process_with_child_marker(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import os, pathlib, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('codex-cli test')\n"
        "else:\n"
        "    value = ('VALUE = 2\\n' if "
        "os.environ.get('MAGE_ORCHESTRATOR_CHILD') == '1' else 'VALUE = 0\\n')\n"
        "    pathlib.Path('fixture.py').write_text(value)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    worktree = tmp_path / "worker"
    worktree.mkdir()
    (worktree / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    contract = TaskContract.from_dict(
        {
            "task_id": "real-provider-test",
            "allowed_paths": ["fixture.py"],
            "verification_commands": [["python3", "verify.py"]],
            "environment_allowlist": [],
            "resource_budget": {"provider_timeout_seconds": 5},
            "provider": {"type": "codex", "prompt": "Change fixture.py."},
        }
    )

    result = CodexProvider(str(executable)).invoke(
        worktree, contract, tmp_path / "invocation"
    )

    assert result.succeeded
    assert (worktree / "fixture.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    invocation = json.loads((tmp_path / "invocation" / "invocation.json").read_text())
    assert invocation["provider"] == "codex"
    assert invocation["cli_version"] == "codex-cli test"
    assert invocation["input_tokens"] is None


def test_kernel_routes_codex_provider_through_isolated_pipeline(
    repository: Path,
    make_contract,
    tmp_path: Path,
    monkeypatch,
    write_authorization_policy,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "codex"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import os, pathlib, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('codex-cli kernel-test')\n"
        "else:\n"
        "    assert os.environ.get('MAGE_ORCHESTRATOR_CHILD') == '1'\n"
        "    pathlib.Path('fixture.py').write_text('VALUE = 2\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    policy = write_authorization_policy(repository)
    contract = make_contract(
        provider_extra={"type": "codex", "prompt": "Change fixture.py to VALUE = 2."},
        read_paths=["fixture.py"],
        external_model={
            "enabled": True,
            "authorization_id": policy["authorization_id"],
            "authorization_policy_path": ".orchestrator/policies/external_model_authorization.json",
            "policy_hash": policy["policy_hash"],
            "provider": "codex",
            "read_scope": ["fixture.py"],
        },
    )

    waiting = Kernel(repository).start(contract)

    assert waiting.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    invocation = json.loads(
        next(
            (
                repository
                / ".orchestrator"
                / "runs"
                / waiting.run_id
                / "invocations"
            ).glob("*/invocation.json")
        ).read_text(encoding="utf-8")
    )
    assert invocation["provider"] == "codex"
    assert invocation["cli_version"] == "codex-cli kernel-test"


def test_codex_launch_enforces_noninteractive_workspace_sandbox(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/usr/bin/python3\n", encoding="utf-8")
    executable.chmod(0o755)
    contract = TaskContract.from_dict(
        {
            "task_id": "security-args",
            "allowed_paths": ["fixture.py"],
            "provider": {"type": "codex", "prompt": "Edit fixture."},
        }
    )
    provider = CodexProvider(str(executable))
    argv = [
        provider.executable,
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
    ]

    validate_codex_launch_arguments(argv)
    assert provider.security_configuration()["network_access"] is False


def test_codex_review_uses_read_only_sandbox_and_routed_model_effort(
    tmp_path: Path,
) -> None:
    arguments_path = tmp_path / "arguments.json"
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(arguments_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "print('{\"verdict\":\"PASS\",\"risk\":\"LOW\",\"findings\":[],\"auto_integration_allowed\":true}')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    worktree = tmp_path / "review"
    worktree.mkdir()
    result = CodexProvider(str(executable)).review(
        worktree,
        {"model": "review-model", "effort": "high", "patch": "digest-bound"},
        tmp_path / "invocation",
    )
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    assert result.succeeded is True
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert arguments[arguments.index("--model") + 1] == "review-model"
    assert 'model_reasoning_effort="high"' in arguments
    assert arguments[-1] == build_codex_review_prompt(
        {"model": "review-model", "effort": "high", "patch": "digest-bound"}
    )
    validate_codex_review_launch_arguments([str(executable), *arguments[:-1]])
    assert not (tmp_path / "invocation" / "stdout.txt").exists()


@pytest.mark.parametrize(
    "dangerous", ["--dangerously-bypass-approvals-and-sandbox", "--yolo", "danger-full-access"]
)
def test_codex_launch_rejects_dangerous_settings(dangerous: str) -> None:
    with pytest.raises(RuntimeError, match="unsafe Codex"):
        validate_codex_launch_arguments([dangerous])


def test_task_contract_without_verification_environment_is_backward_compatible() -> None:
    contract = TaskContract.from_dict(
        {
            "task_id": "legacy-contract",
            "allowed_paths": ["fixture.py"],
        }
    )

    assert contract.verification_environment == {}


@pytest.mark.parametrize(
    "verification_environment",
    [
        {"UNSAFE": "value"},
        {"LANG": 1},
        {"LANG": "contains\x00nul"},
    ],
)
def test_task_contract_rejects_invalid_verification_environment(
    verification_environment: dict[str, object],
) -> None:
    with pytest.raises(SchemaError):
        TaskContract.from_dict(
            {
                "task_id": "invalid-verification-environment",
                "allowed_paths": ["fixture.py"],
                "verification_environment": verification_environment,
            }
        )
