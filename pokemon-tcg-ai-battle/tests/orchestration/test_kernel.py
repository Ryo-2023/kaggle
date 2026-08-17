from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.orchestration.events import RunLock
from scripts.orchestration.kernel import Kernel
from scripts.orchestration.state import RunStatus


def _root_value(repository: Path) -> str:
    return (repository / "fixture.py").read_text(encoding="utf-8")


def test_fake_valid_reaches_approval_then_approve_applies(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)

    waiting = kernel.start(make_contract())

    assert waiting.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert _root_value(repository) == "VALUE = 1\n"
    done = kernel.approve(waiting.run_id, "integration")
    assert done.state == RunStatus.DONE
    assert _root_value(repository) == "VALUE = 2\n"
    assert not (
        repository / ".orchestrator" / "worktrees" / waiting.run_id
    ).exists()


def test_dirty_tracked_snapshot_is_not_duplicated_in_captured_patch(
    repository: Path, make_contract
) -> None:
    (repository / "fixture.py").write_text("VALUE = 5\n", encoding="utf-8")
    kernel = Kernel(repository)

    waiting = kernel.start(make_contract())

    assert waiting.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert _root_value(repository) == "VALUE = 5\n"
    done = kernel.approve(waiting.run_id, "integration")
    assert done.state == RunStatus.DONE
    assert _root_value(repository) == "VALUE = 2\n"


def test_allowed_new_file_is_captured_as_patch(repository: Path, make_contract) -> None:
    command = [
        sys.executable,
        "verify_new.py",
    ]
    kernel = Kernel(repository)
    waiting = kernel.start(
        make_contract(
            writes={"new_module.py": "NEW = 1\n"},
            allowed_paths=["new_module.py"],
            command=command,
        )
    )

    assert waiting.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert not (repository / "new_module.py").exists()
    assert kernel.approve(waiting.run_id, "integration").state == RunStatus.DONE
    assert (repository / "new_module.py").read_text(encoding="utf-8") == "NEW = 1\n"


def test_allowed_path_violation_blocks_without_root_change(repository: Path, make_contract) -> None:
    state = Kernel(repository).start(
        make_contract(writes={"README.md": "worker\n"}, allowed_paths=["fixture.py"])
    )

    assert state.state == RunStatus.BLOCKED
    assert (repository / "README.md").read_text(encoding="utf-8") == "baseline\n"


def test_protected_path_change_is_rejected(repository: Path, make_contract) -> None:
    state = Kernel(repository).start(
        make_contract(
            scenario="protected_write",
            allowed_paths=["fixture.py", "protected_test.py"],
        )
    )

    assert state.state == RunStatus.BLOCKED
    assert (repository / "protected_test.py").read_text(encoding="utf-8") == "ORACLE = 1\n"


def test_symlink_escape_is_rejected_before_write(repository: Path, make_contract, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("SAFE\n", encoding="utf-8")
    (repository / "escape.py").symlink_to(outside)
    subprocess.run(["git", "add", "escape.py"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add symlink fixture"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    state = Kernel(repository).start(
        make_contract(writes={"escape.py": "ESCAPED\n"}, allowed_paths=["escape.py"])
    )

    assert state.state == RunStatus.BLOCKED
    assert any("symlink path escape" in finding for finding in state.findings)
    assert outside.read_text(encoding="utf-8") == "SAFE\n"


def test_excluded_required_symlink_is_rejected_at_intake(
    repository: Path, make_contract, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("SAFE\n", encoding="utf-8")
    (repository / "untracked_escape.py").symlink_to(outside)

    with pytest.raises(RuntimeError, match="snapshot excluded required paths"):
        Kernel(repository).start(
            make_contract(
                writes={"untracked_escape.py": "ESCAPED\n"},
                allowed_paths=["untracked_escape.py"],
            )
        )


def test_authoritative_failure_overrides_provider_success(repository: Path, make_contract) -> None:
    state = Kernel(repository).start(
        make_contract(command=[sys.executable, "fail_verification.py"])
    )

    assert state.state == RunStatus.BLOCKED
    assert any("authoritative verification failed" in item for item in state.findings)
    assert _root_value(repository) == "VALUE = 1\n"


def test_authoritative_verification_can_start_kernel_tests_without_child_marker(
    repository: Path, make_contract
) -> None:
    state = Kernel(repository).start(
        make_contract(command=[sys.executable, "verify_no_marker.py"])
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    evidence = json.loads(
        (
            repository
            / ".orchestrator"
            / "runs"
            / state.run_id
            / "evidence"
            / "authoritative"
            / "command-000.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["exit_code"] == 0


def test_authoritative_verification_uses_safe_default_environment(
    repository: Path, make_contract, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "parent-pythonpath")
    state = Kernel(repository).start(
        make_contract(command=[sys.executable, "verify_default_environment.py"])
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_authoritative_verification_inherits_harmless_legacy_environment(
    repository: Path, make_contract, monkeypatch
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    state = Kernel(repository).start(
        make_contract(
            command=[sys.executable, "verify_legacy_environment.py"],
            environment_allowlist=["TZ"],
        )
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_authoritative_verification_blocks_legacy_python_environment(
    repository: Path, make_contract, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "parent-pythonpath")
    monkeypatch.setenv("PYTHONHOME", "parent-pythonhome")
    monkeypatch.setenv("VIRTUAL_ENV", "parent-virtual-env")
    state = Kernel(repository).start(
        make_contract(
            command=[sys.executable, "verify_python_environment.py"],
            environment_allowlist=["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"],
        )
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_authoritative_verification_explicit_pythonpath_is_worktree_relative(
    repository: Path, make_contract
) -> None:
    state = Kernel(repository).start(
        make_contract(
            command=[sys.executable, "verify_src_import.py"],
            verification_environment={"PYTHONPATH": "src"},
        )
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_authoritative_verification_explicit_environment_overrides_legacy_without_leaking(
    repository: Path, make_contract, monkeypatch
) -> None:
    monkeypatch.setenv("LC_ALL", "C")
    state = Kernel(repository).start(
        make_contract(
            command=[sys.executable, "verify_explicit_environment.py"],
            environment_allowlist=["LC_ALL", "PATH"],
            verification_environment={
                "LC_ALL": "C.UTF-8",
                "PATH": "private-verification-path",
            },
        )
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    evidence_dir = repository / ".orchestrator" / "runs" / state.run_id / "evidence"
    evidence = "\n".join(
        path.read_text(encoding="utf-8") for path in evidence_dir.rglob("*.json")
    )
    assert "private-verification-path" not in evidence
    command_evidence = json.loads(
        (evidence_dir / "authoritative" / "command-000.json").read_text(encoding="utf-8")
    )
    assert isinstance(command_evidence["environment_digest"], str)


def test_clean_verification_receives_patch_not_worker_cache(repository: Path, make_contract) -> None:
    command = [
        sys.executable,
        "verify_clean.py",
    ]
    state = Kernel(repository).start(
        make_contract(
            command=command,
            provider_extra={"worker_artifacts": {"worker-cache.tmp": "cache\n"}},
        )
    )

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_source_change_blocks_approval(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    state = kernel.start(make_contract())
    (repository / "fixture.py").write_text("VALUE = 99\n", encoding="utf-8")

    blocked = kernel.approve(state.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    assert _root_value(repository) == "VALUE = 99\n"
    assert any("source_changed" in finding for finding in blocked.findings)


def test_partial_write_never_reaches_root(repository: Path, make_contract) -> None:
    state = Kernel(repository).start(make_contract(scenario="partial_write"))

    assert state.state == RunStatus.BLOCKED
    assert _root_value(repository) == "VALUE = 1\n"


@pytest.mark.parametrize("scenario", ["timeout", "nonzero_exit", "forbidden_write"])
def test_fake_failure_scenarios_block_without_root_change(
    repository: Path, make_contract, scenario: str
) -> None:
    state = Kernel(repository).start(make_contract(scenario=scenario))

    assert state.state == RunStatus.BLOCKED
    assert _root_value(repository) == "VALUE = 1\n"


def test_crash_recovery_rebuilds_state_and_resume(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    state = kernel.start(make_contract())
    state_path = repository / ".orchestrator" / "runs" / state.run_id / "state.json"
    state_path.write_text("{broken", encoding="utf-8")

    resumed = kernel.resume(state.run_id)

    assert resumed.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert json.loads(state_path.read_text(encoding="utf-8"))["state"] == resumed.state.value


def test_first_resume_cleans_stale_interrupted_worktree(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    blocked = kernel.start(make_contract(scenario="nonzero_exit"))
    run_dir = repository / ".orchestrator" / "runs" / blocked.run_id
    contract_path = run_dir / "task_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["provider"] = {
        "type": "fake",
        "scenario": "valid",
        "writes": {"fixture.py": "VALUE = 2\n"},
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    from scripts.orchestration.events import EventStore

    EventStore(run_dir).append(
        "state_transition", {"from": "BLOCKED", "to": "IMPLEMENTATION"}
    )
    stale = (
        repository
        / ".orchestrator"
        / "worktrees"
        / blocked.run_id
        / "worker-interrupted"
    )
    kernel.snapshot_manager.materialize(blocked.snapshot_id, stale)

    resumed = kernel.resume(blocked.run_id)

    assert resumed.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert not stale.exists()
    assert not stale.parent.exists()


def test_concurrent_resume_is_rejected(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    state = kernel.start(make_contract())
    lock_path = repository / ".orchestrator" / "locks" / f"{state.run_id}.lock"

    with RunLock(lock_path):
        with pytest.raises(RuntimeError, match="already active"):
            kernel.resume(state.run_id)


def test_reject_and_abort_do_not_apply_patch(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    rejected = kernel.reject(waiting.run_id, "integration", "human declined")
    assert rejected.state == RunStatus.REJECTED
    assert _root_value(repository) == "VALUE = 1\n"

    second = kernel.start(make_contract())
    aborted = kernel.abort(second.run_id)
    assert aborted.state == RunStatus.ABORTED
    assert _root_value(repository) == "VALUE = 1\n"


def test_doctor_reports_provider_and_environment_isolation(repository: Path) -> None:
    report = Kernel(repository).doctor()

    assert "codex_provider" in report["checks"]
    assert report["checks"]["environment_policy"] == {
        "passed": True,
        "value": [],
    }
