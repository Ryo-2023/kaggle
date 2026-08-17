from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

import scripts.orchestration.kernel as kernel_module
from scripts.orchestration.events import EventStore, atomic_write_json, utc_now
from scripts.orchestration.integration import IntegrationHookPoint
from scripts.orchestration.kernel import Kernel
from scripts.orchestration.schemas import ApprovalRecord
from scripts.orchestration.snapshot import workspace_state
from scripts.orchestration.state import RunStatus
from scripts.orchestration.worktree import apply_patch


class InjectedCrash(BaseException):
    pass


class CrashController:
    def __init__(self, point: IntegrationHookPoint):
        self.point = point
        self.observed: list[tuple[IntegrationHookPoint, dict[str, object]]] = []

    def reached(self, point: IntegrationHookPoint, evidence: dict[str, object]) -> None:
        self.observed.append((point, evidence))
        if point == self.point:
            raise InjectedCrash(point.value)


def _run_dir(repository: Path, run_id: str) -> Path:
    return repository / ".orchestrator" / "runs" / run_id


def _events(repository: Path, run_id: str) -> list[dict[str, object]]:
    return EventStore(_run_dir(repository, run_id)).read_events()


def _approval_events(repository: Path, run_id: str) -> list[dict[str, object]]:
    return [event for event in _events(repository, run_id) if event["kind"] == "approval_recorded"]


def _provider_invocations(repository: Path, run_id: str) -> list[Path]:
    return sorted((_run_dir(repository, run_id) / "invocations").glob("*/invocation.json"))


def _event_count(repository: Path, run_id: str, kind: str) -> int:
    return sum(event["kind"] == kind for event in _events(repository, run_id))


def _patch(repository: Path, run_id: str) -> bytes:
    return (_run_dir(repository, run_id) / "patches" / "implementation.patch").read_bytes()


def _crash_after_approval(repository: Path, make_contract) -> tuple[Kernel, str]:
    controller = CrashController(IntegrationHookPoint.AFTER_APPROVAL_EVENT_DURABLE)
    kernel = Kernel(repository, integration_fault_controller=controller)
    waiting = kernel.start(make_contract())
    with pytest.raises(InjectedCrash):
        kernel.approve(waiting.run_id, "integration")
    assert len(_approval_events(repository, waiting.run_id)) == 1
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    return kernel, waiting.run_id


def _write_approval_side(
    repository: Path,
    run_id: str,
    *,
    write_json: bool,
    write_event: bool,
    decision: str = "approved",
    subject_digest: str | None = None,
) -> ApprovalRecord:
    subject = subject_digest or hashlib.sha256(_patch(repository, run_id)).hexdigest()
    record = ApprovalRecord(
        run_id=run_id,
        gate="integration",
        decision=decision,
        subject_digest=subject,
        reason=None,
        recorded_at=utc_now(),
    )
    run_dir = _run_dir(repository, run_id)
    if write_json:
        atomic_write_json(run_dir / "approvals" / "integration.json", asdict(record))
    if write_event:
        EventStore(run_dir).append("approval_recorded", asdict(record))
    return record


def test_resume_unapplied_reuses_one_approval_and_reaches_done(repository: Path, make_contract) -> None:
    kernel, run_id = _crash_after_approval(repository, make_contract)

    resumed = kernel.resume(run_id)

    assert resumed.state == RunStatus.DONE
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert len(_approval_events(repository, run_id)) == 1


def test_git_ignored_group_write_mode_does_not_block_full_application(
    repository: Path, make_contract
) -> None:
    untouched = repository / "README.md"
    untouched.chmod(0o664)
    git_mode = subprocess.run(
        ["git", "ls-files", "-s", "--", "README.md"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    assert git_mode == "100644"
    kernel = Kernel(repository)
    waiting = kernel.start(
        make_contract(allowed_paths=["fixture.py", "README.md"])
    )

    done = kernel.approve(waiting.run_id, "integration")

    assert done.state == RunStatus.DONE
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert untouched.stat().st_mode & 0o777 == 0o664
    assert not any("integration_recovery" in finding for finding in done.findings)


def test_resume_fully_applied_does_not_apply_patch_again(
    repository: Path, make_contract, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = CrashController(
        IntegrationHookPoint.AFTER_PATCH_TARGET_VERIFIED_BEFORE_APPLIED_EVENT
    )
    kernel = Kernel(repository, integration_fault_controller=controller)
    waiting = kernel.start(make_contract())
    with pytest.raises(InjectedCrash):
        kernel.approve(waiting.run_id, "integration")
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    def forbidden_apply(*args: object, **kwargs: object) -> None:
        raise AssertionError("git apply must not run for FULLY_APPLIED recovery")

    monkeypatch.setattr(kernel_module, "apply_patch", forbidden_apply)
    resumed = Kernel(repository).resume(waiting.run_id)

    assert resumed.state == RunStatus.DONE
    assert len(_approval_events(repository, waiting.run_id)) == 1


def test_resume_partial_application_blocks_without_reapply(
    repository: Path, make_contract, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id = _crash_after_approval(repository, make_contract)
    EventStore(_run_dir(repository, run_id)).append(
        "integration_apply_started", {"patch_sha256": hashlib.sha256(_patch(repository, run_id)).hexdigest()}
    )
    (repository / "fixture.py").write_text("VALUE = ", encoding="utf-8")

    monkeypatch.setattr(
        kernel_module,
        "apply_patch",
        lambda *args, **kwargs: pytest.fail("partial root must not be reapplied"),
    )
    resumed = Kernel(repository).resume(run_id)

    assert resumed.state == RunStatus.BLOCKED
    evidence = json.loads(
        (_run_dir(repository, run_id) / "evidence" / "integration-recovery.json").read_text()
    )
    assert evidence["classification"] == "PARTIALLY_APPLIED"
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = "


def test_non_allowed_change_is_external_and_blocks(repository: Path, make_contract) -> None:
    _, run_id = _crash_after_approval(repository, make_contract)
    (repository / "README.md").write_text("external\n", encoding="utf-8")

    resumed = Kernel(repository).resume(run_id)

    assert resumed.state == RunStatus.BLOCKED
    evidence = json.loads(
        (_run_dir(repository, run_id) / "evidence" / "integration-recovery.json").read_text()
    )
    assert evidence["classification"] == "SOURCE_CHANGED_EXTERNALLY"


def test_tampered_patch_artifact_is_rejected(repository: Path, make_contract) -> None:
    _, run_id = _crash_after_approval(repository, make_contract)
    patch_path = _run_dir(repository, run_id) / "patches" / "implementation.patch"
    patch_path.write_bytes(patch_path.read_bytes() + b"\n")

    resumed = Kernel(repository).resume(run_id)

    assert resumed.state == RunStatus.BLOCKED
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_patch_capture_event_hash_mismatch_is_rejected(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    run_dir = _run_dir(repository, waiting.run_id)
    events = EventStore(run_dir).read_events()
    for event in events:
        if event["kind"] == "patch_captured":
            event["payload"]["sha256"] = "0" * 64
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events), encoding="utf-8"
    )

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    assert not (run_dir / "approvals" / "integration.json").exists()


def test_verification_patch_hash_mismatch_is_rejected(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    summary_path = _run_dir(repository, waiting.run_id) / "evidence" / "authoritative" / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["evidence"][0]["patch_digest"] = "0" * 64
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_missing_summary_patch_hash_is_rejected(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    summary_path = (
        _run_dir(repository, waiting.run_id)
        / "evidence"
        / "authoritative"
        / "summary.json"
    )
    summary = json.loads(summary_path.read_text())
    for item in summary["evidence"]:
        item.pop("patch_digest", None)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.parametrize(
    ("artifact", "mutation"),
    [
        ("summary", "missing"),
        ("summary", "wrong_type"),
        ("summary", "mismatch"),
        ("stage", "mismatch"),
    ],
)
def test_verification_snapshot_digest_must_match_run_snapshot(
    repository: Path, make_contract, artifact: str, mutation: str
) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    run_dir = _run_dir(repository, waiting.run_id)
    if artifact == "summary":
        evidence_path = run_dir / "evidence" / "authoritative" / "summary.json"
        value = json.loads(evidence_path.read_text())
        evidence = value["evidence"]
    else:
        evidence_path = run_dir / "evidence" / "stage-result.json"
        value = json.loads(evidence_path.read_text())
        evidence = value["authoritative_evidence"]
    protected = next(item for item in evidence if item["kind"] == "protected_hash")
    if mutation == "missing":
        protected.pop("snapshot_digest")
    elif mutation == "wrong_type":
        protected["snapshot_digest"] = 7
    else:
        protected["snapshot_digest"] = "0" * 64
    evidence_path.write_text(json.dumps(value), encoding="utf-8")

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_approval_subject_mismatch_is_rejected(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    _write_approval_side(
        repository,
        waiting.run_id,
        write_json=True,
        write_event=True,
        subject_digest="0" * 64,
    )

    blocked = kernel.resume(waiting.run_id)

    assert blocked.state == RunStatus.BLOCKED
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_post_apply_target_mismatch_never_records_applied(
    repository: Path, make_contract, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_apply = kernel_module.apply_patch

    def corrupting_apply(root: Path, patch: bytes, check_only: bool = False) -> None:
        if check_only:
            real_apply(root, patch, check_only=True)
        elif root.resolve() != repository.resolve():
            real_apply(root, patch)
        else:
            (root / "fixture.py").write_text("CORRUPT\n", encoding="utf-8")

    monkeypatch.setattr(kernel_module, "apply_patch", corrupting_apply)
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED
    transitions = [event["payload"]["to"] for event in _events(repository, waiting.run_id) if event["kind"] == "state_transition"]
    assert "APPLIED" not in transitions


@pytest.mark.parametrize(
    ("write_json", "write_event"),
    [(True, False), (False, True)],
)
def test_one_sided_approval_artifact_blocks(
    repository: Path, make_contract, write_json: bool, write_event: bool
) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    _write_approval_side(
        repository, waiting.run_id, write_json=write_json, write_event=write_event
    )

    blocked = kernel.resume(waiting.run_id)

    assert blocked.state == RunStatus.BLOCKED


def test_repeated_approve_does_not_duplicate_approval_event(repository: Path, make_contract) -> None:
    kernel, run_id = _crash_after_approval(repository, make_contract)

    done = kernel.approve(run_id, "integration")

    assert done.state == RunStatus.DONE
    assert len(_approval_events(repository, run_id)) == 1


def test_existing_approval_decision_mismatch_blocks(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    _write_approval_side(
        repository,
        waiting.run_id,
        write_json=True,
        write_event=True,
        decision="rejected",
    )

    blocked = kernel.approve(waiting.run_id, "integration")

    assert blocked.state == RunStatus.BLOCKED


def test_resume_recovers_durable_rejection_without_reinvocation(
    repository: Path, make_contract
) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    _write_approval_side(
        repository,
        waiting.run_id,
        write_json=True,
        write_event=True,
        decision="rejected",
    )
    invocations = len(_provider_invocations(repository, waiting.run_id))
    patch_events = _event_count(repository, waiting.run_id, "patch_captured")
    root_before = workspace_state(repository, ["fixture.py"])["workspace_digest"]

    rejected = kernel.resume(waiting.run_id)

    assert rejected.state == RunStatus.REJECTED
    assert len(_provider_invocations(repository, waiting.run_id)) == invocations
    assert _event_count(repository, waiting.run_id, "patch_captured") == patch_events == 1
    assert len(_approval_events(repository, waiting.run_id)) == 1
    assert workspace_state(repository, ["fixture.py"])["workspace_digest"] == root_before


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_id", "run-wrong"), ("gate", "wrong-gate")],
)
def test_approval_identity_mismatch_blocks(
    repository: Path, make_contract, field: str, value: str
) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())
    _write_approval_side(
        repository, waiting.run_id, write_json=True, write_event=True
    )
    run_dir = _run_dir(repository, waiting.run_id)
    approval_path = run_dir / "approvals" / "integration.json"
    approval = json.loads(approval_path.read_text())
    approval[field] = value
    atomic_write_json(approval_path, approval)
    events = EventStore(run_dir).read_events()
    next(event for event in events if event["kind"] == "approval_recorded")["payload"][field] = value
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    blocked = kernel.resume(waiting.run_id)

    assert blocked.state == RunStatus.BLOCKED


def test_mode_change_after_full_apply_is_not_accepted(
    repository: Path, make_contract
) -> None:
    controller = CrashController(
        IntegrationHookPoint.AFTER_PATCH_TARGET_VERIFIED_BEFORE_APPLIED_EVENT
    )
    kernel = Kernel(repository, integration_fault_controller=controller)
    waiting = kernel.start(make_contract())
    with pytest.raises(InjectedCrash):
        kernel.approve(waiting.run_id, "integration")
    (repository / "fixture.py").chmod(0o755)

    blocked = Kernel(repository).resume(waiting.run_id)

    assert blocked.state == RunStatus.BLOCKED
    transitions = [
        event["payload"]["to"]
        for event in _events(repository, waiting.run_id)
        if event["kind"] == "state_transition"
    ]
    assert "APPLIED" not in transitions


def test_integration_blocked_resume_never_reenters_implementation(
    repository: Path, make_contract
) -> None:
    _, run_id = _crash_after_approval(repository, make_contract)
    EventStore(_run_dir(repository, run_id)).append(
        "integration_apply_started",
        {"patch_sha256": hashlib.sha256(_patch(repository, run_id)).hexdigest()},
    )
    (repository / "fixture.py").write_text("VALUE = ", encoding="utf-8")
    kernel = Kernel(repository)
    blocked = kernel.resume(run_id)
    assert blocked.state == RunStatus.BLOCKED
    invocations = len(_provider_invocations(repository, run_id))
    event_count = blocked.event_count
    root_before = workspace_state(repository, ["fixture.py"])["workspace_digest"]

    for _ in range(3):
        still_blocked = kernel.resume(run_id)
        assert still_blocked.state == RunStatus.BLOCKED
        assert still_blocked.event_count == event_count
        assert len(_provider_invocations(repository, run_id)) == invocations
        assert _event_count(repository, run_id, "patch_captured") == 1
        assert len(_approval_events(repository, run_id)) == 1
        assert workspace_state(repository, ["fixture.py"])["workspace_digest"] == root_before


def test_applied_event_recovers_done_when_materialized_state_is_broken(
    repository: Path, make_contract
) -> None:
    kernel, run_id = _crash_after_approval(repository, make_contract)
    apply_patch(repository, _patch(repository, run_id))
    run_dir = _run_dir(repository, run_id)
    EventStore(run_dir).append(
        "state_transition", {"from": "WAITING_INTEGRATION_APPROVAL", "to": "APPLIED"}
    )
    (run_dir / "state.json").write_text("{broken", encoding="utf-8")

    resumed = Kernel(repository).resume(run_id)

    assert resumed.state == RunStatus.DONE


def test_resume_without_approval_still_waits(repository: Path, make_contract) -> None:
    kernel = Kernel(repository)
    waiting = kernel.start(make_contract())

    resumed = kernel.resume(waiting.run_id)

    assert resumed.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert not (_run_dir(repository, waiting.run_id) / "approvals" / "integration.json").exists()


def test_fault_hook_is_constructor_only(repository: Path, make_contract, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGE_INTEGRATION_FAULT_HOOK", "AFTER_APPROVAL_EVENT_DURABLE")
    contract_path = make_contract(
        provider_extra={"integration_fault_hook": "AFTER_APPROVAL_EVENT_DURABLE"}
    )
    kernel = Kernel(repository)
    waiting = kernel.start(contract_path)

    done = kernel.approve(waiting.run_id, "integration")

    assert done.state == RunStatus.DONE
    assert _event_count(repository, waiting.run_id, "integration_fault_hook_reached") == 0
