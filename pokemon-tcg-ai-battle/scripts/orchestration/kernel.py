"""End-to-end deterministic Bootstrap Kernel control plane."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .authorization import (
    AuthorizationSummary,
    inspect_default_authorization,
)
from .contract_validation import validate_task_contract
from .events import EventStore, RunLock, atomic_write_json, utc_now
from .integration import (
    IntegrationClassification,
    IntegrationFaultController,
    IntegrationHookPoint,
    IntegrationRecoveryClass,
    hook_evidence,
)
from .policy import PolicyViolation, path_matches, validate_changed_paths
from .process import allowlisted_environment
from .progress import Progress
from .provider import CodexProvider, provider_for
from .schemas import ApprovalRecord, RunManifest, StageResult, TaskContract
from .snapshot import (
    SnapshotError,
    WorkspaceSnapshot,
    compose_content_digest,
    compose_workspace_digest,
    path_manifest,
    tree_digest,
    workspace_state,
)
from .state import RunState, RunStatus, TERMINAL_STATES, validate_transition
from .verify import verify
from .worktree import apply_patch, capture_patch, changed_paths, prepare_snapshot_baseline
from .worktree import cleanup_run_worktrees, remove_worktree


class KernelError(RuntimeError):
    """Raised for invalid Kernel operations or failed preconditions."""


class Kernel:
    """Run one explicit TaskContract through isolation, verification, and approval."""

    def __init__(
        self,
        repository_root: Path,
        *,
        integration_fault_controller: IntegrationFaultController | None = None,
        progress: Progress | None = None,
    ):
        self.root = repository_root.resolve()
        self.control_root = self.root / ".orchestrator"
        self.runs_root = self.control_root / "runs"
        self.snapshots_root = self.control_root / "snapshots"
        self.locks_root = self.control_root / "locks"
        self.worktrees_root = self.control_root / "worktrees"
        self.snapshot_manager = WorkspaceSnapshot(self.root, self.snapshots_root)
        self._integration_fault_controller = integration_fault_controller
        self._progress = progress if progress is not None else Progress()

    def start(self, contract_path: Path) -> RunState:
        """Create a run and advance it through authoritative verification."""

        if os.environ.get("MAGE_ORCHESTRATOR_CHILD") == "1":
            raise KernelError("recursive child orchestrator invocation is forbidden")
        contract = TaskContract.from_json_file(contract_path)
        validation = validate_task_contract(self.root, contract)
        authorization: AuthorizationSummary | None = validation.authorization
        snapshot_id, snapshot_manifest = self.snapshot_manager.create(contract.allowed_paths)
        required_paths = (*contract.allowed_paths, *contract.read_paths)
        excluded_required = [
            item["path"]
            for item in snapshot_manifest["excluded_files"]
            if path_matches(str(item["path"]), required_paths)
        ]
        if excluded_required:
            raise KernelError(
                f"snapshot excluded required paths: {sorted(excluded_required)}"
            )
        run_id = f"run-{utc_now()[:10]}-{uuid.uuid4().hex[:12]}"
        run_dir = self._run_dir(run_id)
        for child in ("approvals", "invocations", "evidence", "patches", "logs"):
            (run_dir / child).mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "task_contract.json", contract.to_dict())
        if authorization is not None:
            atomic_write_json(
                run_dir / "evidence" / "external-model-authorization.json",
                authorization.to_dict(),
            )
        now = utc_now()
        manifest = RunManifest(
            run_id=run_id,
            request=contract.task_id,
            state=RunStatus.INTAKE.value,
            snapshot_id=snapshot_id,
            task_contract_ref=str(run_dir / "task_contract.json"),
            risk_level="R1",
            created_at=now,
            updated_at=now,
        )
        atomic_write_json(run_dir / "manifest.json", asdict(manifest))
        store = EventStore(run_dir)
        store.append("run_created", {"run_id": run_id, "snapshot_id": snapshot_id})
        self._emit_progress(run_id, RunStatus.INTAKE, "intake", "run created")
        self._emit_progress(run_id, RunStatus.INTAKE, "snapshot", "snapshot ready")
        with RunLock(self._lock_path(run_id)):
            return self._advance(run_id)

    def resume(self, run_id: str) -> RunState:
        """Rebuild run state from events and resume the current resumable stage."""

        self._require_run(run_id)
        with RunLock(self._lock_path(run_id)):
            store = EventStore(self._run_dir(run_id))
            state = store.rebuild()
            if state.state in TERMINAL_STATES:
                return state
            if (
                state.state == RunStatus.BLOCKED
                and self._has_integration_stage_evidence(run_id, state)
            ):
                return state
            if state.state == RunStatus.WAITING_INTEGRATION_APPROVAL:
                try:
                    approval = self._inspect_durable_approval(run_id)
                except KernelError as exc:
                    return self._block_integration(
                        run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
                    )
                if approval is None:
                    return state
                if approval.get("decision") == "rejected":
                    try:
                        self._validate_integration_artifacts(run_id, approval)
                    except KernelError as exc:
                        return self._block_integration(
                            run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
                        )
                    reason = approval.get("reason")
                    return self._transition(
                        run_id,
                        RunStatus.REJECTED,
                        (
                            str(reason)
                            if isinstance(reason, str) and reason
                            else "recovered durable rejection"
                        ),
                    )
                return self._recover_integration(run_id, approval)
            return self._advance(run_id)

    def status(self, run_id: str | None = None) -> RunState | list[RunState]:
        """Return one run status or all run statuses ordered by run identifier."""

        if run_id is not None:
            self._require_run(run_id)
            return EventStore(self._run_dir(run_id)).rebuild()
        if not self.runs_root.exists():
            return []
        return [
            EventStore(path).rebuild()
            for path in sorted(self.runs_root.iterdir())
            if path.is_dir() and (path / "events.jsonl").exists()
        ]

    def approve(self, run_id: str, gate: str) -> RunState:
        """Apply a verified patch to root only after integration approval."""

        if gate != "integration":
            raise KernelError("Bootstrap Kernel supports only integration approval")
        self._require_run(run_id)
        with RunLock(self._lock_path(run_id)):
            state = EventStore(self._run_dir(run_id)).rebuild()
            if state.state != RunStatus.WAITING_INTEGRATION_APPROVAL:
                raise KernelError(f"run is not waiting for integration approval: {state.state}")
            try:
                chain = self._validate_integration_artifacts(run_id)
                prior = self._inspect_durable_approval(run_id)
            except KernelError as exc:
                return self._block_integration(
                    run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
                )
            subject_digest = str(chain["patch_sha256"])
            if prior is None:
                classification = self._classify_integration_root(run_id, chain, None)
                self._write_integration_evidence(run_id, classification)
                if classification.classification != IntegrationRecoveryClass.UNAPPLIED:
                    return self._block_integration(
                        run_id, classification.classification, classification.reason, classification
                    )
                approval, created = self._record_or_reuse_approval(
                    run_id, gate, "approved", subject_digest, None
                )
            else:
                if (
                    prior.get("decision") != "approved"
                    or prior.get("subject_digest") != subject_digest
                ):
                    return self._block_integration(
                        run_id,
                        IntegrationRecoveryClass.UNKNOWN,
                        "existing integration approval conflicts with requested approval",
                    )
                approval, created = prior, False
            if created:
                self._integration_hook(
                    IntegrationHookPoint.AFTER_APPROVAL_EVENT_DURABLE,
                    run_id,
                    subject_digest,
                    {"classification": IntegrationRecoveryClass.UNAPPLIED.value},
                )
            return self._recover_integration(run_id, approval)

    def reject(self, run_id: str, gate: str, reason: str) -> RunState:
        """Record a human rejection without applying the patch."""

        if gate != "integration" or not reason.strip():
            raise KernelError("integration rejection requires a non-empty reason")
        self._require_run(run_id)
        with RunLock(self._lock_path(run_id)):
            state = EventStore(self._run_dir(run_id)).rebuild()
            if state.state != RunStatus.WAITING_INTEGRATION_APPROVAL:
                raise KernelError("run is not waiting for integration approval")
            try:
                chain = self._validate_integration_artifacts(run_id)
                prior = self._inspect_durable_approval(run_id)
                self._record_or_reuse_approval(
                    run_id,
                    gate,
                    "rejected",
                    str(chain["patch_sha256"]),
                    reason,
                    prior=prior,
                )
            except KernelError as exc:
                return self._block_integration(
                    run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
                )
            return self._transition(run_id, RunStatus.REJECTED, reason)

    def abort(self, run_id: str) -> RunState:
        """Abort a non-terminal run."""

        self._require_run(run_id)
        with RunLock(self._lock_path(run_id)):
            state = EventStore(self._run_dir(run_id)).rebuild()
            if state.state in TERMINAL_STATES:
                return state
            if state.state == RunStatus.APPLIED:
                return self._transition(run_id, RunStatus.DONE, "recovered applied state")
            return self._transition(run_id, RunStatus.ABORTED, "aborted by user")

    def doctor(self) -> dict[str, Any]:
        """Report deterministic local prerequisites without changing external state."""

        git_directory = self.root / ".git"
        try:
            codex_probe = CodexProvider().probe(("HOME", "PATH", "CODEX_HOME"))
        except (OSError, RuntimeError, ValueError) as exc:
            codex_probe = {"available": False, "error": str(exc)}
        isolated_environment = allowlisted_environment(())
        authorization_check = inspect_default_authorization(self.root)
        codex_security = CodexProvider.security_configuration()
        checks = {
            "python": {"passed": sys.version_info >= (3, 12), "value": sys.version.split()[0]},
            "git_repository": {"passed": git_directory.exists(), "value": str(self.root)},
            "fake_provider": {"passed": True, "value": "builtin"},
            "codex_provider": {
                "passed": bool(codex_probe.get("available")),
                "value": codex_probe,
            },
            "external_model_authorization": {
                "passed": bool(authorization_check.get("passed")),
                "value": authorization_check,
            },
            "codex_security": {
                "passed": codex_security
                == {
                    "approval_policy": "never",
                    "sandbox_mode": "workspace-write",
                    "web_search": "disabled",
                    "allow_login_shell": False,
                    "network_access": False,
                    "ignore_user_config": True,
                    "dangerous_flags": False,
                },
                "value": codex_security,
            },
            "integration_approval": {
                "passed": True,
                "value": "required per run",
            },
            "environment_policy": {
                "passed": isolated_environment == {},
                "value": sorted(isolated_environment),
            },
            "control_root_writable": {
                "passed": os.access(self.root, os.W_OK),
                "value": str(self.control_root),
            },
        }
        return {"passed": all(check["passed"] for check in checks.values()), "checks": checks}

    def _advance(self, run_id: str) -> RunState:
        run_dir = self._run_dir(run_id)
        run_worktrees = self.worktrees_root / run_id
        cleanup_run_worktrees(self.root, run_worktrees)
        store = EventStore(run_dir)
        state = store.rebuild()
        contract = self._load_contract(run_id)
        manifest = self._load_manifest(run_id)
        snapshot_id = str(manifest["snapshot_id"])
        if state.state == RunStatus.BLOCKED:
            state = self._transition(run_id, RunStatus.IMPLEMENTATION, "resume after blocked stage")
        elif state.state == RunStatus.INTAKE:
            state = self._transition(run_id, RunStatus.IMPLEMENTATION)
        if state.state == RunStatus.IMPLEMENTATION:
            attempt = f"{store.rebuild().event_count}-{uuid.uuid4().hex[:8]}"
            worker = run_worktrees / f"worker-{attempt}"
            try:
                self.snapshot_manager.materialize(snapshot_id, worker)
                protected_digest = tree_digest(worker, contract.protected_paths)
                prepare_snapshot_baseline(worker)
                configured_writes = contract.provider.get("writes", {})
                if isinstance(configured_writes, dict):
                    validate_changed_paths(
                        worker,
                        configured_writes,
                        contract.allowed_paths,
                        contract.forbidden_paths,
                        contract.protected_paths,
                    )
                atomic_write_json(
                    run_dir / "evidence" / "baseline.json",
                    {"protected_digest": protected_digest},
                )
                self._emit_progress(
                    run_id, RunStatus.IMPLEMENTATION, "provider", "provider running"
                )
                provider_result = provider_for(contract).invoke(
                    worker, contract, run_dir / "invocations" / f"implementation-{attempt}"
                )
                atomic_write_json(
                    run_dir / "evidence" / "provider-result.json",
                    {
                        "succeeded": provider_result.succeeded,
                        "reported_evidence": provider_result.reported_evidence,
                        "finding": provider_result.finding,
                    },
                )
                if not provider_result.succeeded:
                    return self._transition(
                        run_id, RunStatus.BLOCKED, provider_result.finding or "provider_failed"
                    )
                paths = changed_paths(worker)
                if not paths:
                    return self._transition(run_id, RunStatus.BLOCKED, "provider produced no patch")
                validate_changed_paths(
                    worker,
                    paths,
                    contract.allowed_paths,
                    contract.forbidden_paths,
                    contract.protected_paths,
                )
                patch = capture_patch(worker)
                patch_path = run_dir / "patches" / "implementation.patch"
                patch_path.write_bytes(patch)
                with patch_path.open("rb") as handle:
                    os.fsync(handle.fileno())
                store.append(
                    "patch_captured",
                    {
                        "patch_ref": str(patch_path),
                        "sha256": hashlib.sha256(patch).hexdigest(),
                        "changed_paths": list(paths),
                    },
                )
                self._emit_progress(
                    run_id, RunStatus.IMPLEMENTATION, "patch", "patch captured"
                )
            except (PolicyViolation, SnapshotError, ValueError) as exc:
                return self._transition(run_id, RunStatus.BLOCKED, str(exc))
            finally:
                if worker.exists():
                    remove_worktree(self.root, worker)
            state = self._transition(run_id, RunStatus.VERIFICATION_DETERMINISTIC)
        if state.state == RunStatus.VERIFICATION_DETERMINISTIC:
            attempt = f"{store.rebuild().event_count}-{uuid.uuid4().hex[:8]}"
            verification = run_worktrees / f"verification-{attempt}"
            try:
                self.snapshot_manager.materialize(snapshot_id, verification)
                patch = (run_dir / "patches" / "implementation.patch").read_bytes()
                apply_patch(verification, patch)
                baseline = self._load_json(run_dir / "evidence" / "baseline.json")
                snapshot_manifest = self.snapshot_manager.load_manifest(snapshot_id)
                passed, evidence = verify(
                    verification,
                    contract,
                    run_dir / "evidence" / "authoritative",
                    str(baseline["protected_digest"]),
                    str(snapshot_manifest["tracked_patch_sha256"]),
                    hashlib.sha256(patch).hexdigest(),
                    command_progress=self._command_progress(run_id),
                )
                atomic_write_json(
                    run_dir / "evidence" / "integration-target.json",
                    self._integration_target_evidence(
                        verification, contract, snapshot_manifest
                    ),
                )
                result = StageResult(
                    stage="VERIFICATION_DETERMINISTIC",
                    status="passed" if passed else "blocked",
                    authoritative_evidence=evidence,
                    patch_ref=str(run_dir / "patches" / "implementation.patch"),
                )
                atomic_write_json(run_dir / "evidence" / "stage-result.json", asdict(result))
                if not passed:
                    return self._transition(
                        run_id, RunStatus.BLOCKED, "authoritative verification failed"
                    )
            except (PolicyViolation, SnapshotError, ValueError) as exc:
                return self._transition(run_id, RunStatus.BLOCKED, str(exc))
            finally:
                if verification.exists():
                    remove_worktree(self.root, verification)
            return self._transition(run_id, RunStatus.WAITING_INTEGRATION_APPROVAL)
        state = store.rebuild()
        if state.state == RunStatus.APPLIED:
            try:
                approval = self._inspect_durable_approval(run_id)
                if approval is None or approval.get("decision") != "approved":
                    raise KernelError("APPLIED state lacks a durable approved integration record")
                chain = self._validate_integration_artifacts(run_id, approval)
                classification = self._classify_integration_root(run_id, chain, approval)
                self._write_integration_evidence(run_id, classification)
                if classification.classification != IntegrationRecoveryClass.FULLY_APPLIED:
                    return self._block_integration(
                        run_id,
                        classification.classification,
                        "APPLIED state root no longer matches the verified target",
                        classification,
                    )
            except KernelError as exc:
                return self._block_integration(
                    run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
                )
            return self._transition(run_id, RunStatus.DONE, "recovered applied state")
        return state

    def _integration_target_evidence(
        self,
        verification: Path,
        contract: TaskContract,
        snapshot_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        source_workspace = snapshot_manifest.get("source_workspace")
        if not isinstance(source_workspace, dict):
            raise SnapshotError("snapshot workspace baseline is missing")
        target_workspace = workspace_state(verification, contract.allowed_paths)
        source_untracked = {
            str(item["path"]): item
            for item in source_workspace["untracked_manifest"]
            if not path_matches(str(item["path"]), contract.allowed_paths)
        }
        target_allowed_untracked = {
            str(item["path"]): item
            for item in target_workspace["untracked_manifest"]
            if path_matches(str(item["path"]), contract.allowed_paths)
        }
        expected_untracked = [
            {**item}
            for _, item in sorted({**source_untracked, **target_allowed_untracked}.items())
        ]
        expected_content_digest = compose_content_digest(
            str(source_workspace["head"]),
            str(target_workspace["tracked_patch_sha256"]),
            expected_untracked,
        )
        expected_workspace_digest = compose_workspace_digest(
            str(source_workspace["head"]),
            str(source_workspace["index_digest"]),
            expected_content_digest,
        )
        return {
            "source_digest": tree_digest(verification, contract.allowed_paths),
            "expected_workspace_digest": expected_workspace_digest,
            "target_content_digest": expected_content_digest,
            "target_non_allowed_digest": source_workspace["non_allowed_digest"],
            "target_allowed_manifest": path_manifest(verification, contract.allowed_paths),
        }

    def _inspect_durable_approval(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self._run_dir(run_id)
        approval_path = run_dir / "approvals" / "integration.json"
        events = [
            event.get("payload")
            for event in EventStore(run_dir).read_events()
            if event.get("kind") == "approval_recorded"
        ]
        if not approval_path.exists() and not events:
            return None
        if approval_path.exists() != bool(events):
            raise KernelError("integration approval JSON/event presence mismatch")
        if len(events) != 1 or not isinstance(events[0], dict):
            raise KernelError("integration approval event count is not exactly one")
        approval = self._load_json(approval_path)
        event = dict(events[0])
        fields = (
            "run_id",
            "gate",
            "decision",
            "subject_digest",
            "reason",
            "recorded_at",
        )
        if any(approval.get(field) != event.get(field) for field in fields):
            raise KernelError("integration approval JSON/event fields mismatch")
        if approval.get("run_id") != run_id or approval.get("gate") != "integration":
            raise KernelError("integration approval run or gate mismatch")
        if approval.get("decision") not in {"approved", "rejected"}:
            raise KernelError("integration approval decision is invalid")
        if not isinstance(approval.get("subject_digest"), str):
            raise KernelError("integration approval subject digest is invalid")
        return approval

    def _record_or_reuse_approval(
        self,
        run_id: str,
        gate: str,
        decision: str,
        subject_digest: str,
        reason: str | None,
        *,
        prior: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        existing = self._inspect_durable_approval(run_id) if prior is None else prior
        if existing is not None:
            if (
                existing.get("run_id") == run_id
                and existing.get("gate") == gate
                and existing.get("decision") == decision
                and existing.get("subject_digest") == subject_digest
            ):
                return existing, False
            raise KernelError("existing integration approval conflicts with requested decision")
        record = ApprovalRecord(
            run_id=run_id,
            gate=gate,
            decision=decision,
            subject_digest=subject_digest,
            reason=reason,
            recorded_at=utc_now(),
        )
        value = asdict(record)
        run_dir = self._run_dir(run_id)
        atomic_write_json(run_dir / "approvals" / "integration.json", value)
        EventStore(run_dir).append("approval_recorded", value)
        return value, True

    def _validate_integration_artifacts(
        self, run_id: str, approval: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        patch_path = run_dir / "patches" / "implementation.patch"
        try:
            patch = patch_path.read_bytes()
            patch_sha256 = hashlib.sha256(patch).hexdigest()
            patch_events = [
                event["payload"]
                for event in EventStore(run_dir).read_events()
                if event.get("kind") == "patch_captured"
            ]
            if len(patch_events) != 1 or not isinstance(patch_events[0], dict):
                raise KernelError("patch capture event count is not exactly one")
            if patch_events[0].get("sha256") != patch_sha256:
                raise KernelError("captured patch artifact/event hash mismatch")
            manifest = self._load_manifest(run_id)
            snapshot = self.snapshot_manager.load_manifest(str(manifest["snapshot_id"]))
            snapshot_digest = snapshot.get("tracked_patch_sha256")
            if not isinstance(snapshot_digest, str):
                raise KernelError("snapshot manifest tracked patch digest is invalid")
            summary = self._load_json(run_dir / "evidence" / "authoritative" / "summary.json")
            stage = self._load_json(run_dir / "evidence" / "stage-result.json")
            if summary.get("passed") is not True or stage.get("status") != "passed":
                raise KernelError("authoritative verification did not pass")
            for label, source in (
                ("authoritative summary", summary.get("evidence")),
                ("stage result", stage.get("authoritative_evidence")),
            ):
                verification_digests = (
                    {
                        str(item["patch_digest"])
                        for item in source
                        if isinstance(item, dict) and "patch_digest" in item
                    }
                    if isinstance(source, list)
                    else set()
                )
                if verification_digests != {patch_sha256}:
                    raise KernelError(f"{label} patch hash is missing or mismatched")
                snapshot_digests = (
                    [
                        item["snapshot_digest"]
                        for item in source
                        if isinstance(item, dict) and "snapshot_digest" in item
                    ]
                    if isinstance(source, list)
                    else []
                )
                if not snapshot_digests or any(
                    not isinstance(value, str) or value != snapshot_digest
                    for value in snapshot_digests
                ):
                    raise KernelError(f"{label} snapshot hash is missing or mismatched")
            if approval is not None and approval.get("subject_digest") != patch_sha256:
                raise KernelError("integration approval subject/patch hash mismatch")
            target = self._load_json(run_dir / "evidence" / "integration-target.json")
            source_workspace = snapshot.get("source_workspace")
            required_target = (
                "source_digest",
                "expected_workspace_digest",
                "target_non_allowed_digest",
                "target_allowed_manifest",
            )
            if not isinstance(source_workspace, dict) or any(
                field not in target for field in required_target
            ):
                raise KernelError("integration workspace evidence is incomplete")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise KernelError(f"integration artifact validation failed: {exc}") from exc
        return {
            "patch": patch,
            "patch_sha256": patch_sha256,
            "patch_event": patch_events[0],
            "snapshot": snapshot,
            "source_workspace": source_workspace,
            "target": target,
        }

    def _has_integration_stage_evidence(self, run_id: str, state: RunState) -> bool:
        run_dir = self._run_dir(run_id)
        if (run_dir / "approvals" / "integration.json").exists() or (
            run_dir / "evidence" / "integration-recovery.json"
        ).exists():
            return True
        if any(finding.startswith("integration_recovery_") for finding in state.findings):
            return True
        for event in EventStore(run_dir).read_events():
            kind = event.get("kind")
            payload = event.get("payload")
            if kind == "approval_recorded" and isinstance(payload, dict):
                if payload.get("gate") == "integration":
                    return True
            if kind in {"integration_apply_started", "integration_fault_hook_reached"}:
                return True
            if isinstance(kind, str) and kind.startswith("integration_recovery_"):
                return True
            if kind == "state_transition" and isinstance(payload, dict):
                if payload.get("to") in {RunStatus.APPLIED.value, RunStatus.DONE.value}:
                    return True
        return False

    def _integration_apply_started(self, run_id: str, patch_sha256: str) -> bool:
        return any(
            event.get("kind") == "integration_apply_started"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("patch_sha256") == patch_sha256
            for event in EventStore(self._run_dir(run_id)).read_events()
        )

    def _classify_integration_root(
        self,
        run_id: str,
        chain: dict[str, Any],
        approval: dict[str, Any] | None,
    ) -> IntegrationClassification:
        contract = self._load_contract(run_id)
        snapshot = chain["snapshot"]
        source_workspace = chain["source_workspace"]
        target = chain["target"]
        current_workspace = workspace_state(self.root, contract.allowed_paths)
        current_allowed = tree_digest(self.root, contract.allowed_paths)
        source_allowed = str(snapshot.get("source_digest"))
        target_allowed = str(target.get("source_digest"))
        patch_sha256 = str(chain["patch_sha256"])
        approval_subject = (
            str(approval.get("subject_digest")) if approval is not None else None
        )
        started = self._integration_apply_started(run_id, patch_sha256)
        expected_head = str(source_workspace.get("head"))
        expected_index = str(source_workspace.get("index_digest"))
        expected_non_allowed = str(source_workspace.get("non_allowed_digest"))
        current_manifest = path_manifest(self.root, contract.allowed_paths)
        external = (
            current_workspace["head"] != expected_head
            or current_workspace["index_digest"] != expected_index
            or current_workspace["non_allowed_digest"] != expected_non_allowed
        )
        if external:
            classification = IntegrationRecoveryClass.SOURCE_CHANGED_EXTERNALLY
            reason = "HEAD, index, or non-allowed workspace differs from snapshot baseline"
        elif (
            current_allowed == source_allowed
            and current_workspace["workspace_digest"]
            == source_workspace.get("workspace_digest")
        ):
            classification = IntegrationRecoveryClass.UNAPPLIED
            reason = "root exactly matches the captured source workspace"
        elif (
            current_allowed == target_allowed
            and current_workspace["workspace_digest"]
            == target.get("expected_workspace_digest")
            and current_manifest == target.get("target_allowed_manifest")
            and current_workspace["non_allowed_digest"]
            == target.get("target_non_allowed_digest")
        ):
            classification = IntegrationRecoveryClass.FULLY_APPLIED
            reason = "root exactly matches the verified integration target"
        elif started:
            classification = IntegrationRecoveryClass.PARTIALLY_APPLIED
            reason = "integration started and root matches neither source nor target"
        else:
            classification = IntegrationRecoveryClass.SOURCE_CHANGED_EXTERNALLY
            reason = "source_changed: allowed root content differs before integration started"
        return IntegrationClassification(
            classification=classification,
            current_allowed_digest=current_allowed,
            source_allowed_digest=source_allowed,
            target_allowed_digest=target_allowed,
            current_workspace_digest=str(current_workspace["workspace_digest"]),
            source_workspace_digest=str(source_workspace.get("workspace_digest")),
            target_workspace_digest=str(target.get("expected_workspace_digest")),
            current_head=str(current_workspace["head"]),
            expected_head=expected_head,
            current_index_digest=str(current_workspace["index_digest"]),
            expected_index_digest=expected_index,
            current_non_allowed_digest=str(current_workspace["non_allowed_digest"]),
            expected_non_allowed_digest=expected_non_allowed,
            patch_sha256=patch_sha256,
            approval_subject_digest=approval_subject,
            integration_apply_started=started,
            reason=reason,
        )

    def _write_integration_evidence(
        self, run_id: str, classification: IntegrationClassification
    ) -> None:
        atomic_write_json(
            self._run_dir(run_id) / "evidence" / "integration-recovery.json",
            classification.to_dict(),
        )

    def _block_integration(
        self,
        run_id: str,
        classification: IntegrationRecoveryClass,
        reason: str,
        evidence: IntegrationClassification | None = None,
    ) -> RunState:
        if evidence is None:
            evidence = IntegrationClassification(
                classification=classification,
                current_allowed_digest=None,
                source_allowed_digest=None,
                target_allowed_digest=None,
                current_workspace_digest=None,
                source_workspace_digest=None,
                target_workspace_digest=None,
                current_head=None,
                expected_head=None,
                current_index_digest=None,
                expected_index_digest=None,
                current_non_allowed_digest=None,
                expected_non_allowed_digest=None,
                patch_sha256=None,
                approval_subject_digest=None,
                integration_apply_started=False,
                reason=reason,
            )
        self._write_integration_evidence(run_id, evidence)
        return self._transition(
            run_id,
            RunStatus.BLOCKED,
            f"integration_recovery_{classification.value.lower()}: {reason}",
        )

    def _integration_hook(
        self,
        point: IntegrationHookPoint,
        run_id: str,
        patch_sha256: str,
        classification: dict[str, Any],
    ) -> None:
        if self._integration_fault_controller is not None:
            evidence = hook_evidence(
                run_id=run_id,
                patch_sha256=patch_sha256,
                classification=classification,
            )
            EventStore(self._run_dir(run_id)).append(
                "integration_fault_hook_reached",
                {"point": point.value, **evidence},
            )
            self._integration_fault_controller.reached(
                point,
                evidence,
            )

    def _recover_integration(
        self, run_id: str, approval: dict[str, Any]
    ) -> RunState:
        try:
            chain = self._validate_integration_artifacts(run_id, approval)
        except KernelError as exc:
            return self._block_integration(
                run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
            )
        classification = self._classify_integration_root(run_id, chain, approval)
        self._write_integration_evidence(run_id, classification)
        if classification.classification == IntegrationRecoveryClass.FULLY_APPLIED:
            self._transition(run_id, RunStatus.APPLIED, "recovered fully applied patch")
            return self._transition(run_id, RunStatus.DONE)
        if classification.classification != IntegrationRecoveryClass.UNAPPLIED:
            return self._block_integration(
                run_id, classification.classification, classification.reason, classification
            )
        patch = chain["patch"]
        patch_sha256 = str(chain["patch_sha256"])
        try:
            apply_patch(self.root, patch, check_only=True)
            if not self._integration_apply_started(run_id, patch_sha256):
                EventStore(self._run_dir(run_id)).append(
                    "integration_apply_started",
                    {
                        "patch_sha256": patch_sha256,
                        "approval_subject_digest": approval["subject_digest"],
                    },
                )
            apply_patch(self.root, patch)
        except SnapshotError as exc:
            after_failure = self._classify_integration_root(run_id, chain, approval)
            return self._block_integration(
                run_id,
                after_failure.classification,
                f"integration_apply_failed: {exc}",
                after_failure,
            )
        try:
            chain_after = self._validate_integration_artifacts(run_id, approval)
        except KernelError as exc:
            return self._block_integration(
                run_id, IntegrationRecoveryClass.UNKNOWN, str(exc)
            )
        after = self._classify_integration_root(run_id, chain_after, approval)
        self._write_integration_evidence(run_id, after)
        if after.classification != IntegrationRecoveryClass.FULLY_APPLIED:
            return self._block_integration(
                run_id, after.classification, "post-apply target verification failed", after
            )
        self._integration_hook(
            IntegrationHookPoint.AFTER_PATCH_TARGET_VERIFIED_BEFORE_APPLIED_EVENT,
            run_id,
            patch_sha256,
            after.to_dict(),
        )
        self._transition(run_id, RunStatus.APPLIED)
        return self._transition(run_id, RunStatus.DONE)

    def _transition(
        self, run_id: str, target: RunStatus, reason: str | None = None
    ) -> RunState:
        store = EventStore(self._run_dir(run_id))
        current = store.rebuild()
        validate_transition(current.state, target)
        payload: dict[str, Any] = {"from": current.state.value, "to": target.value}
        if reason:
            payload["reason"] = reason
        state = store.append("state_transition", payload)
        manifest = self._load_manifest(run_id)
        manifest["state"] = target.value
        manifest["updated_at"] = state.updated_at
        atomic_write_json(self._run_dir(run_id) / "manifest.json", manifest)
        milestones = {
            RunStatus.IMPLEMENTATION: ("implementation", "implementation started"),
            RunStatus.VERIFICATION_DETERMINISTIC: (
                "verification",
                "deterministic verification started",
            ),
            RunStatus.WAITING_INTEGRATION_APPROVAL: (
                "approval",
                "waiting for integration approval",
            ),
            RunStatus.BLOCKED: ("terminal", "blocked"),
            RunStatus.DONE: ("terminal", "done"),
            RunStatus.REJECTED: ("terminal", "rejected"),
            RunStatus.ABORTED: ("terminal", "aborted"),
        }
        milestone = milestones.get(target)
        if milestone is not None:
            self._emit_progress(run_id, target, *milestone)
        return state

    def _emit_progress(
        self,
        run_id: str,
        state: RunStatus,
        stage: str,
        message: str,
        *,
        command_index: int | None = None,
        command_total: int | None = None,
        status: str | None = None,
    ) -> None:
        """Emit observer-only progress without changing control-plane behavior."""

        try:
            self._progress.emit(
                self._run_dir(run_id),
                run_id,
                state,
                stage,
                message,
                command_index=command_index,
                command_total=command_total,
                status=status,
            )
        except Exception:
            # Progress is intentionally non-authoritative, including if an injected
            # renderer or stream implementation itself fails unexpectedly.
            try:
                self._progress._disable()
            except Exception:
                pass

    def _command_progress(self, run_id: str) -> Callable[[int, int, str], None]:
        """Return a best-effort observer callback for authoritative commands."""

        def emit(command_index: int, command_total: int, status: str) -> None:
            self._emit_progress(
                run_id,
                RunStatus.VERIFICATION_DETERMINISTIC,
                "command",
                f"command {status}",
                command_index=command_index,
                command_total=command_total,
                status=status,
            )

        return emit

    def _validate_contract_policy(self, contract: TaskContract) -> None:
        """Backward-compatible private wrapper around the public preflight."""

        validate_task_contract(
            self.root,
            contract,
            validate_external=False,
            validate_provider_security=False,
        )

    def _run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("run-") or "/" in run_id or ".." in run_id:
            raise KernelError("invalid run identifier")
        return self.runs_root / run_id

    def _lock_path(self, run_id: str) -> Path:
        return self.locks_root / f"{run_id}.lock"

    def _require_run(self, run_id: str) -> None:
        if not self._run_dir(run_id).is_dir():
            raise KernelError(f"run does not exist: {run_id}")

    def _load_contract(self, run_id: str) -> TaskContract:
        return TaskContract.from_json_file(self._run_dir(run_id) / "task_contract.json")

    def _load_manifest(self, run_id: str) -> dict[str, Any]:
        return self._load_json(self._run_dir(run_id) / "manifest.json")

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise KernelError(f"JSON document must be an object: {path}")
        return value
