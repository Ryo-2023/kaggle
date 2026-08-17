"""Observer-only progress integration tests.

The assertions intentionally exercise the public Kernel and CLI boundary: the
progress stream is useful for following a run, but cannot alter its state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from scripts import orchestrate
from scripts.orchestration import kernel as kernel_module
from scripts.orchestration import verify as verify_module
from scripts.orchestration.kernel import Kernel
from scripts.orchestration.process import ProcessResult
from scripts.orchestration.progress import Progress, ProgressDataError, new_records
from scripts.orchestration.schemas import TaskContract
from scripts.orchestration.state import RunStatus


def _records(repository: Path, run_id: str) -> list[dict[str, object]]:
    return Progress.read(repository / ".orchestrator" / "runs" / run_id)


def test_kernel_emits_sanitized_early_milestones_and_keeps_verify_truth(
    repository: Path, make_contract
) -> None:
    state = Kernel(repository).start(make_contract())

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    records = _records(repository, state.run_id)
    messages = [str(record["message"]) for record in records]
    assert messages[:2] == ["run created", "snapshot ready"]
    for required in (
        "implementation started",
        "provider running",
        "patch captured",
        "deterministic verification started",
        "command started",
        "command passed",
        "waiting for integration approval",
    ):
        assert required in messages
    for record in records:
        assert set(record).issubset(
            {
                "timestamp",
                "run_id",
                "state",
                "stage",
                "message",
                "command_index",
                "command_total",
                "status",
            }
        )
        assert record["state"] in {item.value for item in RunStatus}
        assert "RunStatus." not in str(record["state"])
    signatures = [
        (
            record["state"],
            record["stage"],
            record["message"],
            record.get("command_index"),
            record.get("status"),
        )
        for record in records
    ]
    assert len(signatures) == len(set(signatures))
    assert "fixture.py" not in json.dumps(records)
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
    assert evidence["timed_out"] is False


def test_verify_reports_command_progress_at_the_real_execution_boundaries(
    tmp_path: Path, monkeypatch
) -> None:
    contract = TaskContract.from_dict(
        {
            "task_id": "command-progress",
            "allowed_paths": ["fixture.py"],
            "verification_commands": [
                ["command-one"],
                ["command-two"],
                ["command-three"],
            ],
            "resource_budget": {"verification_timeout_seconds": 1},
        }
    )
    evidence_dir = tmp_path / "evidence"
    calls: list[tuple[object, ...]] = []
    results = [
        ProcessResult(
            argv=("command-one",),
            cwd=str(tmp_path),
            environment_digest="digest-one",
            exit_code=0,
            stdout="secret verification stdout",
            stderr="secret verification stderr",
            started_at="start-one",
            ended_at="end-one",
            duration_seconds=0.1,
            timed_out=False,
        ),
        ProcessResult(
            argv=("command-two",),
            cwd=str(tmp_path),
            environment_digest="digest-two",
            exit_code=3,
            stdout="secret verification stdout",
            stderr="secret verification stderr",
            started_at="start-two",
            ended_at="end-two",
            duration_seconds=0.1,
            timed_out=False,
        ),
    ]

    def run_process(*_args: object, **_kwargs: object) -> ProcessResult:
        index = len([call for call in calls if call[0] == "run"])
        calls.append(("run", index + 1))
        return results[index]

    def command_progress(index: int, total: int, status: str) -> None:
        calls.append(
            (
                "progress",
                index,
                total,
                status,
                (evidence_dir / f"command-{index - 1:03d}.json").exists(),
            )
        )

    monkeypatch.setattr(verify_module, "run_process", run_process)
    monkeypatch.setattr(verify_module, "tree_digest", lambda *_args: "protected")

    passed, _ = verify_module.verify(
        tmp_path,
        contract,
        evidence_dir,
        "protected",
        "snapshot",
        "patch",
        command_progress=command_progress,
    )

    assert passed is False
    assert calls == [
        ("progress", 1, 3, "started", False),
        ("run", 1),
        ("progress", 1, 3, "passed", True),
        ("progress", 2, 3, "started", False),
        ("run", 2),
        ("progress", 2, 3, "failed", True),
    ]


def test_verify_ignores_progress_callback_failure(tmp_path: Path, monkeypatch) -> None:
    contract = TaskContract.from_dict(
        {
            "task_id": "failed-command-progress",
            "allowed_paths": ["fixture.py"],
            "verification_commands": [["command"]],
        }
    )
    result = ProcessResult(
        argv=("command",),
        cwd=str(tmp_path),
        environment_digest="digest",
        exit_code=0,
        stdout="",
        stderr="",
        started_at="start",
        ended_at="end",
        duration_seconds=0.1,
        timed_out=False,
    )
    monkeypatch.setattr(verify_module, "run_process", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(verify_module, "tree_digest", lambda *_args: "protected")

    passed, _ = verify_module.verify(
        tmp_path,
        contract,
        tmp_path / "evidence",
        "protected",
        "snapshot",
        "patch",
        command_progress=lambda *_args: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    assert passed is True


def test_cli_start_streams_sanitized_stderr_before_provider_and_one_final_json(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    original_provider_for = kernel_module.provider_for
    observed: dict[str, str] = {}

    class InspectingProvider:
        def __init__(self, delegate: object):
            self.delegate = delegate

        def invoke(self, *args: object, **kwargs: object) -> object:
            captured = capsys.readouterr()
            observed["stderr_before_provider"] = captured.err
            observed["stdout_before_provider"] = captured.out
            return self.delegate.invoke(*args, **kwargs)

    def provider_for(contract: TaskContract) -> InspectingProvider:
        return InspectingProvider(original_provider_for(contract))

    monkeypatch.setattr(kernel_module, "provider_for", provider_for)
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["start", "--contract", str(make_contract())]) == 0
    completed = capsys.readouterr()
    early_lines = observed["stderr_before_provider"].splitlines()
    later_lines = completed.err.splitlines()
    records = [json.loads(line) for line in (*early_lines, *later_lines)]
    final_state = json.loads(completed.out)

    assert observed["stdout_before_provider"] == ""
    assert any(record["message"] == "run created" for record in records)
    assert {"snapshot ready", "provider running", "patch captured"}.issubset(
        {record["message"] for record in records}
    )
    assert final_state["state"] == "WAITING_INTEGRATION_APPROVAL"
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
    assert all(set(record).issubset(allowed) for record in records)
    rendered = "\n".join((*early_lines, *later_lines))
    for secret in (
        "payload",
        "argv",
        "stdout",
        "stderr",
        "environment_digest",
        "VALUE = 2",
    ):
        assert secret not in rendered


def test_cli_live_sink_failure_does_not_change_state_or_durable_progress(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    def fail_sink(_record: dict[str, object]) -> None:
        raise OSError("secret live-renderer failure")

    monkeypatch.setattr(orchestrate, "_print_live_progress", fail_sink)
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["start", "--contract", str(make_contract())]) == 0
    captured = capsys.readouterr()
    state = json.loads(captured.out)
    records = _records(repository, state["run_id"])

    assert state["state"] == "WAITING_INTEGRATION_APPROVAL"
    assert records[-1]["state"] == "WAITING_INTEGRATION_APPROVAL"
    assert captured.err.count("warning: progress observer disabled") == 1
    assert "secret live-renderer failure" not in captured.err


def test_progress_parse_and_renderer_failures_do_not_change_kernel_state(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    kernel = Kernel(repository)

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise OSError("secret-path-and-error-must-not-be-rendered")

    monkeypatch.setattr(kernel._progress, "_append", fail_append)
    state = kernel.start(make_contract())
    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert capsys.readouterr().err.count("warning: progress observer disabled") == 1

    path = repository / ".orchestrator" / "runs" / state.run_id / "progress.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(ProgressDataError):
        Progress.read(path.parent)
    assert kernel.status(state.run_id).state == RunStatus.WAITING_INTEGRATION_APPROVAL


def test_cleanup_failure_still_propagates(repository: Path, make_contract, monkeypatch) -> None:
    def fail_cleanup(*_args: object) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(kernel_module, "cleanup_run_worktrees", fail_cleanup)
    with pytest.raises(OSError, match="cleanup failed"):
        Kernel(repository).start(make_contract())


def test_worktree_removal_failure_still_propagates(
    repository: Path, make_contract, monkeypatch
) -> None:
    def fail_remove(*_args: object) -> None:
        raise OSError("remove failed")

    monkeypatch.setattr(kernel_module, "remove_worktree", fail_remove)
    with pytest.raises(OSError, match="remove failed"):
        Kernel(repository).start(make_contract())


@pytest.mark.parametrize("terminal", ["DONE", "REJECTED", "ABORTED"])
def test_status_follow_prints_current_once_and_no_full_state(
    terminal: str,
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    if terminal == "DONE":
        kernel.approve(waiting.run_id, "integration")
    elif terminal == "REJECTED":
        kernel.reject(waiting.run_id, "integration", "declined")
    else:
        kernel.abort(waiting.run_id)
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", waiting.run_id, "--follow"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert len(output) == 1
    record = json.loads(output[0])
    assert record["state"] == terminal
    assert "event_count" not in record
    assert "snapshot_id" not in record
    assert new_records([record], 1) == []


def test_blocked_follow_prints_once_and_never_sleeps(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    blocked = Kernel(repository).start(make_contract(scenario="nonzero_exit"))
    assert blocked.state == RunStatus.BLOCKED
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)
    monkeypatch.setattr(
        orchestrate.time,
        "sleep",
        lambda _interval: pytest.fail("BLOCKED follow must not sleep"),
    )

    assert orchestrate.main(["status", blocked.run_id, "--follow"]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0])["state"] == "BLOCKED"


def test_follow_emits_current_then_only_new_sanitized_records(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    state = Kernel(repository).start(make_contract())
    existing = _records(repository, state.run_id)
    terminal = {
        "timestamp": "2026-07-13T00:00:00+00:00",
        "run_id": state.run_id,
        "state": "BLOCKED",
        "stage": "terminal",
        "message": "blocked",
    }
    snapshots = iter((existing, [*existing, terminal]))
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)
    monkeypatch.setattr(orchestrate.Progress, "read", lambda _run_dir: next(snapshots))
    monkeypatch.setattr(orchestrate.time, "sleep", lambda _interval: None)

    assert orchestrate.main(["status", state.run_id, "--follow"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert [record["message"] for record in records] == [
        existing[-1]["message"],
        "blocked",
    ]
    assert all("payload" not in record for record in records)


def test_status_without_follow_prints_one_normal_json(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    state = Kernel(repository).start(make_contract())
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", state.run_id]) == 0
    output = capsys.readouterr().out

    assert json.loads(output)["run_id"] == state.run_id
    assert output.count('"run_id"') == 1


def test_status_errors_are_controlled_without_sleep(repository: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", "run-missing", "--follow"]) == 2
    assert orchestrate.main(["status", "--follow"]) == 2
    assert orchestrate.main(["status", "run-missing", "--follow", "--interval", "0"]) == 2
    for interval in ("-1", "nan", "inf", "-inf"):
        assert (
            orchestrate.main(
                ["status", "run-missing", "--follow", "--interval", interval]
            )
            == 2
        )
    assert "Traceback" not in capsys.readouterr().err


def test_follow_rejects_malformed_complete_progress_data(
    repository: Path, make_contract, monkeypatch
) -> None:
    state = Kernel(repository).start(make_contract())
    path = repository / ".orchestrator" / "runs" / state.run_id / "progress.jsonl"
    path.write_text("{malformed}\n", encoding="utf-8")
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", state.run_id, "--follow"]) == 2


def test_follow_rejects_missing_progress_data(
    repository: Path, make_contract, monkeypatch
) -> None:
    state = Kernel(repository).start(make_contract())
    Progress.path(repository / ".orchestrator" / "runs" / state.run_id).unlink()
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", state.run_id, "--follow"]) == 2


def test_follow_rejects_truncated_progress_data(
    repository: Path, make_contract, monkeypatch
) -> None:
    state = Kernel(repository).start(make_contract())
    path = repository / ".orchestrator" / "runs" / state.run_id / "progress.jsonl"
    data = path.read_bytes()
    path.write_bytes(data.rstrip(b"\n"))
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)

    assert orchestrate.main(["status", state.run_id, "--follow"]) == 2


def test_follow_keyboard_interrupt_is_controlled(
    repository: Path, make_contract, monkeypatch, capsys
) -> None:
    state = Kernel(repository).start(make_contract())
    monkeypatch.setattr(orchestrate, "_repository_root", lambda: repository)
    monkeypatch.setattr(
        orchestrate.time,
        "sleep",
        lambda _interval: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert orchestrate.main(["status", state.run_id, "--follow"]) == 130
    assert "Traceback" not in capsys.readouterr().err


def test_kernel_progress_leaves_no_background_thread(repository: Path, make_contract) -> None:
    before = {thread.ident for thread in threading.enumerate()}

    Kernel(repository).start(make_contract())

    assert {thread.ident for thread in threading.enumerate()} == before
