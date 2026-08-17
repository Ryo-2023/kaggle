from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.orchestration.kernel import Kernel
from scripts.orchestration.policy import PolicyViolation, validate_command
from scripts.orchestration.process import run_process
from scripts.orchestration.provider import process_exists


@pytest.mark.parametrize(
    "command",
    [
        ["git", "commit", "-m", "no"],
        ["git", "push"],
        ["git", "tag", "v1"],
        ["git", "-C", "/tmp", "commit", "-m", "no"],
        ["kaggle", "competitions", "submit", "-c", "x"],
        ["python", "-c", "from kaggle.api import api; api.competition_submit('x')"],
        ["bash", "-c", "git commit -m no"],
        ["sh", "-c", "git push"],
        ["xargs", "git", "commit"],
        ["python", "-c", "import subprocess; subprocess.run(['git', 'tag', 'x'])"],
        ["python", "scripts/orchestrate.py", "status"],
        ["printenv"],
    ],
)
def test_dangerous_commands_are_rejected(command: list[str]) -> None:
    with pytest.raises(PolicyViolation):
        validate_command(command)


def test_git_argument_containing_commit_is_not_a_false_positive() -> None:
    validate_command(["git", "log", "--grep", "commit"])


def test_normal_process_does_not_receive_child_marker(tmp_path: Path) -> None:
    (tmp_path / "marker.py").write_text(
        "import os\nprint(os.environ.get('MAGE_ORCHESTRATOR_CHILD'))\n", encoding="utf-8"
    )
    result = run_process(
        ["python3", "marker.py"],
        cwd=tmp_path,
        timeout_seconds=2,
        environment_allowlist=(),
    )

    assert result.passed
    assert result.stdout.strip() == "None"
    assert Path(result.argv[0]).is_absolute()


def test_provider_boundary_forces_marker_and_child_inherits_it(tmp_path: Path) -> None:
    (tmp_path / "grandchild.py").write_text(
        "import os\nprint(os.environ.get('MAGE_ORCHESTRATOR_CHILD'))\n",
        encoding="utf-8",
    )
    (tmp_path / "provider.py").write_text(
        "import subprocess, sys\n"
        "result = subprocess.run([sys.executable, 'grandchild.py'], capture_output=True, text=True, check=True)\n"
        "print(result.stdout.strip())\n",
        encoding="utf-8",
    )

    result = run_process(
        ["python3", "provider.py"],
        cwd=tmp_path,
        timeout_seconds=2,
        inject_orchestrator_child=True,
    )

    assert result.passed
    assert result.stdout.strip() == "1"


def test_marker_cannot_be_overridden_and_is_removed_for_verification(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "marker.py").write_text(
        "import os\nprint(os.environ.get('MAGE_ORCHESTRATOR_CHILD'))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGE_ORCHESTRATOR_CHILD", "parent-value")

    provider = run_process(
        ["python3", "marker.py"],
        cwd=tmp_path,
        timeout_seconds=2,
        environment_allowlist=("MAGE_ORCHESTRATOR_CHILD",),
        extra_environment={"MAGE_ORCHESTRATOR_CHILD": ""},
        inject_orchestrator_child=True,
    )
    verification = run_process(
        ["python3", "marker.py"],
        cwd=tmp_path,
        timeout_seconds=2,
        environment_allowlist=("MAGE_ORCHESTRATOR_CHILD",),
        extra_environment={"MAGE_ORCHESTRATOR_CHILD": "overridden"},
        inject_orchestrator_child=False,
    )

    assert provider.stdout.strip() == "1"
    assert verification.stdout.strip() == "None"


def test_timeout_kills_child_process_group(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    state = kernel.start(
        make_contract(scenario="child_process_leak", writes={"fixture.py": "VALUE = 2\n"})
    )
    invocation_dirs = list(
        (repository / ".orchestrator" / "runs" / state.run_id / "invocations").iterdir()
    )
    pid = int((invocation_dirs[0] / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while process_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.02)

    assert state.state.value == "BLOCKED"
    assert not process_exists(pid)


def test_child_orchestrator_marker_is_rejected(repository: Path, make_contract, monkeypatch) -> None:
    monkeypatch.setenv("MAGE_ORCHESTRATOR_CHILD", "1")
    with pytest.raises(RuntimeError, match="recursive"):
        Kernel(repository).start(make_contract())
