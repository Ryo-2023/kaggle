"""Resumable, task-isolated, local-only Overnight MVP control plane."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

import fcntl

from .authorization import (
    ExternalAuthorizationError,
    ProviderCapability,
    repository_identity,
)
from .budget import Budget, BudgetExceeded
from .contract_validation import validate_task_contract
from .events import atomic_write_json, utc_now
from .model_router import RoutingDecision, route_task
from .overnight_plan import OvernightPlan, OvernightPlanError, OvernightTask, load_plan
from .overnight_state import (
    TASK_STATES,
    TERMINAL_SESSION_STATES,
    OvernightStateError,
    append_event,
    current_progress_record,
    load_state,
    read_events,
    save_state,
)
from .policy import (
    PolicyViolation,
    path_matches,
    touches_control_plane,
    validate_changed_paths,
)
from .provider import Provider, build_codex_review_prompt, provider_for
from .report import write_report
from .reviewer import parse_review, review_allows_integration
from .risk import PatchMetadata, RiskAssessment, classify_risk, inspect_patch_metadata
from .schemas import TaskContract
from .snapshot import review_workspace_state, tree_digest
from .verify import verify
from .worktree import apply_patch, capture_patch, changed_paths


class OvernightError(RuntimeError):
    """Controlled overnight failure without untrusted detail."""


class CleanupError(OvernightError):
    """A task worktree could not be removed safely."""


class TaskTerminated(OvernightError):
    """A task reached a durable terminal state without stopping independent tasks."""


_DEPENDENCY_FAILURE_STATES = {
    "WAITING_HUMAN",
    "FAILED",
    "BLOCKED",
    "SKIPPED_DEPENDENCY",
}
def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise OvernightError("GIT_OPERATION_FAILED")
    return result.stdout


def _git_text(root: Path, *args: str) -> str:
    return _git(root, *args).decode("utf-8", "replace").strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise OvernightError("DURABLE_ARTIFACT_MISSING") from exc


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_session_branch(root: Path, branch: str) -> None:
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise OvernightError("INVALID_SESSION_BRANCH")


@contextmanager
def _exclusive_session_lock(session: Path):
    """Hold a non-blocking OS advisory lock for one durable session."""

    descriptor = os.open(session / "session.lock", os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise OvernightError("SESSION_LOCKED") from exc
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _contract_subset(contract: TaskContract) -> dict[str, object]:
    return {
        "task_id": contract.task_id,
        "role": contract.role,
        "allowed_paths": list(contract.allowed_paths),
        "forbidden_paths": list(contract.forbidden_paths),
        "protected_paths": list(contract.protected_paths),
        "acceptance_digest": contract.acceptance_digest,
    }


def _decision_dict(decision: RoutingDecision, purpose: str) -> dict[str, object]:
    return {
        "purpose": purpose,
        "tier": decision.tier,
        "model": decision.model,
        "reasoning_effort": decision.reasoning_effort,
        "reasons": list(decision.reasons),
        "fallback": decision.fallback,
    }


def _new_task_record(task: OvernightTask) -> dict[str, Any]:
    return {
        "status": "PENDING",
        "contract_sha256": task.contract_sha256,
        "task_source_head": None,
        "task_source_tree_digest": None,
        "session_branch": None,
        "provider_calls": 0,
        "repair_count": 0,
        "repair_started_count": 0,
        "repair_completed_count": 0,
        "repair_patch_captured_count": 0,
        "routing_decisions": [],
        "provider_started": False,
        "provider_completed": False,
        "patch_captured": False,
        "verification_started": False,
        "verification_completed": False,
        "review_started": False,
        "review_completed": False,
        "integration_started": False,
        "integration_completed": False,
        "changed_paths": [],
        "patch_path": None,
        "patch_digest": None,
        "diff_lines": 0,
        "verification": None,
        "review": None,
        "risk": None,
        "integration": None,
        "commit": None,
        "failure_code": None,
    }


class OvernightRunner:
    """Run validated tasks through isolated workers and a session-only branch."""

    def __init__(
        self,
        repository_root: Path,
        *,
        provider_factory: Callable[[TaskContract], Provider] = provider_for,
        provider_capabilities: Iterable[ProviderCapability] = (),
        clock: Callable[[], float] = time.monotonic,
        fault_injector: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.root = repository_root.resolve()
        self.provider_factory = provider_factory
        self.repository_identity = repository_identity(self.root)
        self.provider_capabilities = tuple(provider_capabilities)
        if any(
            capability.repository_identity != self.repository_identity
            for capability in self.provider_capabilities
        ):
            raise OvernightError("PROVIDER_CAPABILITY_REPOSITORY_MISMATCH")
        self.capability_models = {
            capability.model for capability in self.provider_capabilities
        }
        self.capability_profiles = {
            (capability.model, effort)
            for capability in self.provider_capabilities
            for effort in capability.reasoning_efforts
        }
        self.clock = clock
        self.fault_injector = fault_injector
        self.sessions_root = self.root / ".orchestrator" / "overnight" / "sessions"
        self.worktrees_root = self.root / ".orchestrator" / "overnight" / "worktrees"
        self._run_clock_start: float | None = None
        self._elapsed_base = 0.0

    def start(self, plan_path: Path) -> dict[str, Any]:
        """Validate without side effects, then create one durable session."""

        if os.environ.get("MAGE_ORCHESTRATOR_CHILD") == "1":
            raise OvernightError("RECURSIVE_ORCHESTRATOR_FORBIDDEN")
        plan = load_plan(
            plan_path,
            self.root,
            self.capability_models,
            self.capability_profiles,
        )
        current_branch = _git_text(self.root, "branch", "--show-current")
        source_head = _git_text(self.root, "rev-parse", "HEAD")
        base_head = _git_text(self.root, "rev-parse", plan.base_branch)
        if current_branch != plan.base_branch or source_head != base_head:
            raise OvernightError("SOURCE_HEAD_MISMATCH")
        if _git(self.root, "status", "--porcelain"):
            raise OvernightError("ROOT_WORKTREE_DIRTY")

        session_id = f"{plan.name}-{uuid.uuid4().hex[:12]}"
        session_branch = (
            f"{plan.session_branch_prefix}{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
        )
        _validate_session_branch(self.root, session_branch)
        session = self.sessions_root / session_id
        session.mkdir(parents=True, exist_ok=False)
        with _exclusive_session_lock(session):
            for child in ("task-results", "reviews", "reports"):
                (session / child).mkdir(parents=True, exist_ok=False)
            snapshot_path = session / "plan.snapshot.json"
            atomic_write_json(snapshot_path, plan.to_dict())
            snapshot_sha256 = _sha256_file(snapshot_path)
            budget = Budget(dict(plan.limits))
            state: dict[str, Any] = {
                "version": 1,
                "session_id": session_id,
                "name": plan.name,
                "status": "RUNNING",
                "source_head": source_head,
                "source_branch": plan.base_branch,
                "session_branch": session_branch,
                "integration_worktree": str(
                    self.worktrees_root / session_id / "integration"
                ),
                "plan_snapshot_sha256": snapshot_sha256,
                "started_at": utc_now(),
                "updated_at": utc_now(),
                "current_task": None,
                "tasks": {task.id: _new_task_record(task) for task in plan.tasks},
                "budget": budget.to_dict(),
                "stop_reason": None,
                "integration_results": [],
                "report_paths": {
                    "json": str(session / "reports" / "morning-report.json"),
                    "markdown": str(session / "reports" / "morning-report.md"),
                },
            }
            save_state(session, state)
            atomic_write_json(session / "budget.json", state["budget"])
            append_event(
                session,
                session_id=session_id,
                stage="session",
                status="RUNNING",
                message="session created",
                digest=snapshot_sha256,
            )
            integration = Path(state["integration_worktree"])
            integration.parent.mkdir(parents=True, exist_ok=True)
            try:
                _git(
                    self.root,
                    "-c",
                    "core.hooksPath=/dev/null",
                    "worktree",
                    "add",
                    "-b",
                    session_branch,
                    str(integration),
                    source_head,
                )
            except Exception:
                state["status"] = "STOPPED_ERROR"
                state["stop_reason"] = "INTEGRATION_WORKTREE_CREATE_FAILED"
                self._save(session, state, budget)
                write_report(session, state)
                return state
            return self._run(session, plan, state, budget)

    def resume(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        with _exclusive_session_lock(session):
            state = load_state(session)
            if state["status"] == "DONE":
                return state
            try:
                plan = self._load_snapshot(session, state)
                self._validate_resume(session, plan, state)
                budget = Budget.from_dict(state["budget"])
                return self._run(session, plan, state, budget)
            except (OvernightError, OvernightPlanError, OvernightStateError, ValueError):
                state["status"] = "STOPPED_ERROR"
                state["stop_reason"] = "RESUME_VALIDATION_FAILED"
                state["updated_at"] = utc_now()
                save_state(session, state)
                write_report(session, state)
                return state

    def status(self, session_id: str) -> dict[str, Any]:
        return load_state(self._session(session_id))

    def events(self, session_id: str) -> list[dict[str, object]]:
        return read_events(self._session(session_id))

    def progress_record(self, session_id: str) -> dict[str, object]:
        return current_progress_record(self.status(session_id))

    def _session(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or ".." in session_id:
            raise OvernightError("INVALID_SESSION_ID")
        session = self.sessions_root / session_id
        if not (session / "state.json").is_file():
            raise OvernightError("UNKNOWN_SESSION")
        return session

    def _load_snapshot(
        self, session: Path, state: dict[str, Any]
    ) -> OvernightPlan:
        snapshot = session / "plan.snapshot.json"
        if _sha256_file(snapshot) != state["plan_snapshot_sha256"]:
            raise OvernightError("PLAN_SNAPSHOT_DRIFT")
        try:
            raw = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OvernightError("PLAN_SNAPSHOT_INVALID") from exc
        if not isinstance(raw, dict):
            raise OvernightError("PLAN_SNAPSHOT_INVALID")
        return OvernightPlan.from_snapshot(raw)

    def _validate_resume(
        self, session: Path, plan: OvernightPlan, state: dict[str, Any]
    ) -> None:
        if state["source_branch"] != plan.base_branch:
            raise OvernightError("SOURCE_BRANCH_DRIFT")
        _git(self.root, "cat-file", "-e", f"{state['source_head']}^{{commit}}")
        if (
            _git_text(self.root, "branch", "--show-current") != state["source_branch"]
            or _git_text(self.root, "rev-parse", "HEAD") != state["source_head"]
            or _git(self.root, "status", "--porcelain")
        ):
            raise OvernightError("SOURCE_WORKTREE_DRIFT")
        integration = Path(state["integration_worktree"])
        if not integration.is_dir():
            raise OvernightError("INTEGRATION_WORKTREE_MISSING")
        if _git_text(integration, "branch", "--show-current") != state["session_branch"]:
            raise OvernightError("INTEGRATION_BRANCH_DRIFT")
        if _git(integration, "status", "--porcelain"):
            raise OvernightError("INTEGRATION_WORKTREE_DIRTY")
        branch_head = _git_text(integration, "rev-parse", "HEAD")
        expected_branch_head = state["source_head"]
        for task in plan.tasks:
            record = state["tasks"][task.id]
            contract_path = self.root / task.contract
            if _sha256_file(contract_path) != record["contract_sha256"]:
                raise OvernightError("TASK_CONTRACT_DRIFT")
            if record["patch_path"] is not None:
                if _sha256_file(Path(record["patch_path"])) != record["patch_digest"]:
                    raise OvernightError("PATCH_DIGEST_DRIFT")
            verification = record.get("verification")
            if verification and _sha256_file(Path(verification["path"])) != verification["digest"]:
                raise OvernightError("VERIFICATION_EVIDENCE_DRIFT")
            review = record.get("review")
            if review and _sha256_file(Path(review["path"])) != review["digest"]:
                raise OvernightError("REVIEW_EVIDENCE_DRIFT")
            if record.get("provider_started") and not record.get("provider_completed"):
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "PROVIDER_OUTCOME_UNKNOWN"
            if record.get("provider_completed") and not record.get("patch_captured"):
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "PATCH_CAPTURE_OUTCOME_UNKNOWN"
            if record.get("repair_started_count", 0) > record.get(
                "repair_completed_count", 0
            ):
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "REPAIR_OUTCOME_UNKNOWN"
            if record.get("repair_completed_count", 0) > record.get(
                "repair_patch_captured_count", 0
            ):
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "REPAIR_PATCH_CAPTURE_OUTCOME_UNKNOWN"
            if record.get("review_started") and not record.get("review_completed"):
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "REVIEW_OUTCOME_UNKNOWN"
            integration_state = record.get("integration")
            if (
                record.get("task_source_head") is not None
                and record["task_source_head"] != expected_branch_head
            ):
                raise OvernightError("TASK_SOURCE_HEAD_DRIFT")
            if record.get("integration_started") and not record.get("integration_completed"):
                if not isinstance(integration_state, dict):
                    raise OvernightError("INTEGRATION_CHECKPOINT_INVALID")
                source = record["task_source_head"]
                expected_tree = integration_state.get("expected_tree")
                if branch_head == source:
                    record["integration_started"] = False
                    record["integration"] = None
                elif (
                    expected_tree
                    and _git_text(integration, "rev-parse", "HEAD^{tree}") == expected_tree
                    and _git_text(integration, "rev-parse", "HEAD^") == source
                ):
                    record["commit"] = branch_head
                    record["integration_completed"] = True
                    record["status"] = "INTEGRATED"
                    state["integration_results"].append(
                        {"task_id": task.id, "commit": branch_head}
                    )
                    expected_branch_head = branch_head
                else:
                    raise OvernightError("INTEGRATION_COMMIT_IDENTITY_DRIFT")
            if record.get("commit") is not None:
                commit = str(record["commit"])
                _git(self.root, "cat-file", "-e", f"{commit}^{{commit}}")
                _git(
                    self.root,
                    "merge-base",
                    "--is-ancestor",
                    commit,
                    state["session_branch"],
                )
                expected_branch_head = commit
        if branch_head != expected_branch_head:
            raise OvernightError("SESSION_HEAD_DRIFT")
        self._save(session, state, Budget.from_dict(state["budget"]))

    def _run(
        self,
        session: Path,
        plan: OvernightPlan,
        state: dict[str, Any],
        budget: Budget,
    ) -> dict[str, Any]:
        self._run_clock_start = self.clock()
        self._elapsed_base = budget.elapsed_seconds
        integration = Path(state["integration_worktree"])
        state["status"] = "RUNNING"
        state["stop_reason"] = None
        try:
            terminal = {
                "INTEGRATED",
                "WAITING_HUMAN",
                "FAILED",
                "BLOCKED",
                "SKIPPED_DEPENDENCY",
            }
            stalled = False
            while True:
                progressed = False
                for task in plan.tasks:
                    record = state["tasks"][task.id]
                    if record["status"] in terminal:
                        continue
                    dependency_states = [
                        state["tasks"][item]["status"] for item in task.depends_on
                    ]
                    if any(
                        item in _DEPENDENCY_FAILURE_STATES or item.startswith("STOPPED_")
                        for item in dependency_states
                    ):
                        record["status"] = "SKIPPED_DEPENDENCY"
                        record["failure_code"] = "DEPENDENCY_NOT_INTEGRATED"
                        self._checkpoint(
                            session,
                            state,
                            budget,
                            stage="dependency",
                            task_id=task.id,
                            message="task skipped by dependency",
                        )
                        progressed = True

                ready = [
                    task
                    for task in plan.tasks
                    if state["tasks"][task.id]["status"] not in terminal
                    and all(
                        state["tasks"][dependency]["status"] == "INTEGRATED"
                        for dependency in task.depends_on
                    )
                ]
                if ready:
                    task = ready[0]
                    record = state["tasks"][task.id]
                    record["status"] = "READY"
                    state["current_task"] = task.id
                    self._checkpoint(
                        session,
                        state,
                        budget,
                        stage="task",
                        task_id=task.id,
                        message="task ready",
                    )
                    try:
                        self._execute_task(session, plan, task, state, budget, integration)
                    except TaskTerminated:
                        pass
                    except PolicyViolation:
                        record["status"] = "BLOCKED"
                        record["failure_code"] = "POLICY_VIOLATION"
                    except CleanupError:
                        record["status"] = "BLOCKED"
                        record["failure_code"] = "CLEANUP_FAILED"
                    self._checkpoint(
                        session,
                        state,
                        budget,
                        stage="task",
                        task_id=task.id,
                        message="task checkpoint",
                    )
                    progressed = True
                if progressed:
                    continue
                if any(record["status"] not in terminal for record in state["tasks"].values()):
                    stalled = True
                break
            self._update_elapsed(budget)
            statuses = [record["status"] for record in state["tasks"].values()]
            if stalled:
                state["status"] = "STOPPED_ERROR"
                state["stop_reason"] = "DEPENDENCY_PROGRESS_STALLED"
            elif all(status == "INTEGRATED" for status in statuses):
                state["status"] = "DONE"
            elif "BLOCKED" in statuses or "SKIPPED_DEPENDENCY" in statuses:
                state["status"] = "BLOCKED"
            elif "FAILED" in statuses:
                state["status"] = "FAILED"
            elif "WAITING_HUMAN" in statuses:
                state["status"] = "WAITING_HUMAN"
            else:
                state["status"] = "STOPPED_ERROR"
                state["stop_reason"] = "INCOMPLETE_TASK_STATE"
        except BudgetExceeded as exc:
            state["status"] = "STOPPED_BUDGET"
            state["stop_reason"] = str(exc)
        except KeyboardInterrupt:
            state["status"] = "STOPPED_INTERRUPT"
            state["stop_reason"] = "KEYBOARD_INTERRUPT"
        except Exception:
            state["status"] = "STOPPED_ERROR"
            state["stop_reason"] = "CONTROLLED_OPERATION_FAILED"
        state["current_task"] = None
        self._update_elapsed(budget)
        self._save(session, state, budget)
        append_event(
            session,
            session_id=state["session_id"],
            stage="session",
            status=state["status"],
            message="session stopped",
        )
        write_report(session, state)
        return state

    def _execute_task(
        self,
        session: Path,
        plan: OvernightPlan,
        task: OvernightTask,
        state: dict[str, Any],
        budget: Budget,
        integration: Path,
    ) -> None:
        record = state["tasks"][task.id]
        if _git_text(integration, "branch", "--show-current") != state["session_branch"]:
            raise OvernightError("INTEGRATION_BRANCH_DRIFT")
        if _git(integration, "status", "--porcelain"):
            raise OvernightError("INTEGRATION_WORKTREE_DIRTY")
        task_source_head = _git_text(integration, "rev-parse", "HEAD")
        if record["task_source_head"] is None:
            record["task_source_head"] = task_source_head
            record["task_source_tree_digest"] = _git_text(
                integration, "rev-parse", "HEAD^{tree}"
            )
            record["session_branch"] = state["session_branch"]
        elif record["task_source_head"] != task_source_head:
            raise OvernightError("TASK_SOURCE_HEAD_DRIFT")

        contract_path = self.root / task.contract
        if _sha256_file(contract_path) != record["contract_sha256"]:
            raise OvernightError("TASK_CONTRACT_DRIFT")
        contract = TaskContract.from_json_file(contract_path)
        validate_task_contract(self.root, contract)
        control_plane = touches_control_plane(contract.allowed_paths)
        decision = route_task(
            plan.routing,
            complexity=task.complexity,
            risk=task.risk_hint,
            control_plane=control_plane,
        )
        self._record_decision(record, decision, "implementation")

        if record["patch_path"] is None:
            record["status"] = "RUNNING"
            patch, paths, metadata, assessment = self._implementation_attempt(
                session,
                plan,
                task,
                state,
                budget,
                contract,
                decision,
            )
            self._store_patch(session, task.id, record, patch, paths, metadata, assessment)
            record["patch_captured"] = True
            self._checkpoint(
                session,
                state,
                budget,
                stage="patch",
                task_id=task.id,
                message="implementation patch captured",
                digest=record["patch_digest"],
                fault_stage="patch-captured",
            )
        else:
            patch = Path(record["patch_path"]).read_bytes()
            paths = tuple(record["changed_paths"])
            metadata = PatchMetadata(**record["risk"]["metadata"])
            assessment = RiskAssessment(
                record["risk"]["level"], tuple(record["risk"]["findings"]), metadata
            )

        while True:
            if not record["verification_completed"]:
                record["status"] = "VERIFYING"
                record["verification_started"] = True
                self._checkpoint(
                    session,
                    state,
                    budget,
                    stage="verification",
                    task_id=task.id,
                    message="verification started",
                    fault_stage="verification-started",
                )
                passed, verification = self._verify_patch(
                    session, plan, state, budget, task.id, patch, contract
                )
                record["verification"] = verification
                record["verification_completed"] = True
                self._checkpoint(
                    session,
                    state,
                    budget,
                    stage="verification",
                    task_id=task.id,
                    message="verification completed",
                    digest=verification["digest"],
                    fault_stage="verification-completed",
                )
                if passed:
                    break
            elif record["verification"] and record["verification"]["passed"]:
                break
            if record["repair_count"] >= task.max_repair_attempts:
                record["status"] = "FAILED"
                record["failure_code"] = "REPAIR_LIMIT_REACHED"
                return
            record["verification_started"] = False
            record["verification_completed"] = False
            patch, paths, metadata, assessment = self._repair_attempt(
                session,
                plan,
                task,
                state,
                budget,
                contract,
                patch,
                control_plane,
            )
            self._store_patch(session, task.id, record, patch, paths, metadata, assessment)
            record["repair_patch_captured_count"] = record["repair_count"]
            self._checkpoint(
                session,
                state,
                budget,
                stage="patch",
                task_id=task.id,
                message="repair patch captured",
                digest=record["patch_digest"],
                count=record["repair_count"],
                fault_stage="repair-patch-captured",
            )

        review_decision = route_task(
            plan.routing,
            complexity=task.complexity,
            risk=task.risk_hint,
            control_plane=control_plane,
            review=True,
            large_diff=(metadata.diff_lines > plan.limits["max_diff_lines_for_auto_integration"]),
        )
        self._record_decision(record, review_decision, "review")
        if not record["review_completed"]:
            self._review_patch(
                session,
                plan,
                task,
                state,
                budget,
                contract,
                patch,
                paths,
                review_decision,
            )

        review_value = record["review"]
        review_ok = bool(review_value and review_value.get("allows_integration"))
        low_risk_paths = all(
            path_matches(path, plan.low_risk_allowlist) for path in paths
        )
        contract_allows = (
            contract.expected_outputs.get("allow_overnight_auto_integration") is True
        )
        diff_within_limit = (
            metadata.diff_lines
            <= plan.limits["max_diff_lines_for_auto_integration"]
        )
        eligible = all(
            (
                plan.auto_integrate_low_risk,
                contract_allows,
                task.risk_hint == "low",
                assessment.level == "LOW",
                low_risk_paths,
                review_ok,
                diff_within_limit,
                not control_plane,
            )
        )
        if not eligible:
            record["status"] = "WAITING_HUMAN"
            record["failure_code"] = "AUTO_INTEGRATION_NOT_ALLOWED"
            return
        record["status"] = "APPROVED_LOW_RISK"
        self._checkpoint(
            session,
            state,
            budget,
            stage="integration",
            task_id=task.id,
            message="low risk approved",
        )
        self._integrate(
            session, plan, task, state, budget, contract, patch, paths, integration
        )

    def _implementation_attempt(
        self,
        session: Path,
        plan: OvernightPlan,
        task: OvernightTask,
        state: dict[str, Any],
        budget: Budget,
        contract: TaskContract,
        decision: RoutingDecision,
    ) -> tuple[bytes, tuple[str, ...], PatchMetadata, RiskAssessment]:
        record = state["tasks"][task.id]
        provider_contract = self._effective_provider_contract(contract, decision)
        prompt_bytes = len(str(provider_contract.provider.get("prompt", "")).encode())
        worker = self._task_worktree(state, task.id, "implementation")
        self._add_detached_worktree(worker, record["task_source_head"])
        try:
            self._reauthorize_provider_call(worker, provider_contract, decision)
            self._guard(budget, additional_provider_calls=1, prompt_bytes=prompt_bytes)
            budget.charge_provider(prompt_bytes)
            record["provider_calls"] += 1
            record["provider_started"] = True
            self._checkpoint(
                session,
                state,
                budget,
                stage="provider",
                task_id=task.id,
                message="implementation provider started",
                fault_stage="provider-started",
            )
            result = self.provider_factory(provider_contract).invoke(
                worker,
                provider_contract,
                session / "task-results" / task.id / "invocations" / "implementation",
            )
            record["provider_completed"] = True
            budget.record_usage(
                result.invocation.input_tokens, result.invocation.output_tokens
            )
            self._checkpoint(
                session,
                state,
                budget,
                stage="provider",
                task_id=task.id,
                message="implementation provider completed",
                fault_stage="provider-completed",
            )
            if not result.succeeded:
                record["status"] = "FAILED"
                record["failure_code"] = "PROVIDER_FAILED"
                raise TaskTerminated("PROVIDER_FAILED")
            return self._capture_worker_patch(worker, plan, contract)
        finally:
            self._remove_worktree(worker)

    def _repair_attempt(
        self,
        session: Path,
        plan: OvernightPlan,
        task: OvernightTask,
        state: dict[str, Any],
        budget: Budget,
        contract: TaskContract,
        current_patch: bytes,
        control_plane: bool,
    ) -> tuple[bytes, tuple[str, ...], PatchMetadata, RiskAssessment]:
        record = state["tasks"][task.id]
        if record["repair_count"] >= task.max_repair_attempts:
            raise BudgetExceeded("MAX_REPAIR_ATTEMPTS_PER_TASK")
        decision = route_task(
            plan.routing,
            complexity=task.complexity,
            risk=task.risk_hint,
            control_plane=control_plane,
            repair=True,
        )
        self._record_decision(record, decision, "repair")
        repair_number = record["repair_count"] + 1
        repair_prompt = (
            f"Repair task {contract.task_id}; verification failed. "
            f"Changed paths: {', '.join(record['changed_paths'])}. "
            f"Patch digest: {record['patch_digest']}. "
            f"Failure: {json.dumps(record['verification'].get('failure'), sort_keys=True)}. "
            f"Allowed paths: {', '.join(contract.allowed_paths)}. "
            "Use only the visible current patch and rerun no orchestration commands."
        )
        prompt_bytes = len(repair_prompt.encode())
        record["repair_count"] = repair_number
        worker = self._task_worktree(state, task.id, f"repair-{repair_number}")
        self._add_detached_worktree(worker, record["task_source_head"])
        try:
            apply_patch(worker, current_patch)
            repair_contract = self._effective_provider_contract(
                contract, decision, prompt=repair_prompt
            )
            self._reauthorize_provider_call(worker, repair_contract, decision)
            self._guard(budget, additional_provider_calls=1, prompt_bytes=prompt_bytes)
            budget.charge_provider(prompt_bytes)
            record["provider_calls"] += 1
            record["repair_started_count"] = repair_number
            record["status"] = "REPAIRING"
            self._checkpoint(
                session,
                state,
                budget,
                stage="repair",
                task_id=task.id,
                message="repair provider started",
                count=repair_number,
                fault_stage="repair-started",
            )
            result = self.provider_factory(repair_contract).invoke(
                worker,
                repair_contract,
                session
                / "task-results"
                / task.id
                / "invocations"
                / f"repair-{repair_number}",
            )
            budget.record_usage(
                result.invocation.input_tokens, result.invocation.output_tokens
            )
            record["repair_completed_count"] = repair_number
            self._checkpoint(
                session,
                state,
                budget,
                stage="repair",
                task_id=task.id,
                message="repair provider completed",
                count=repair_number,
                fault_stage="repair-completed",
            )
            if not result.succeeded:
                record["status"] = "FAILED"
                record["failure_code"] = "REPAIR_PROVIDER_FAILED"
                raise TaskTerminated("REPAIR_PROVIDER_FAILED")
            return self._capture_worker_patch(worker, plan, contract)
        finally:
            self._remove_worktree(worker)

    def _capture_worker_patch(
        self, worker: Path, plan: OvernightPlan, contract: TaskContract
    ) -> tuple[bytes, tuple[str, ...], PatchMetadata, RiskAssessment]:
        paths = changed_paths(worker)
        if not paths:
            raise OvernightError("EMPTY_PATCH")
        validate_changed_paths(
            worker,
            paths,
            contract.allowed_paths,
            contract.forbidden_paths,
            (*plan.protected_paths, *contract.protected_paths),
        )
        patch = capture_patch(worker)
        metadata = inspect_patch_metadata(worker)
        assessment = classify_risk(
            paths,
            allowed_paths=contract.allowed_paths,
            forbidden_paths=contract.forbidden_paths,
            protected_paths=(*plan.protected_paths, *contract.protected_paths),
            metadata=metadata,
        )
        return patch, paths, metadata, assessment

    def _store_patch(
        self,
        session: Path,
        task_id: str,
        record: dict[str, Any],
        patch: bytes,
        paths: tuple[str, ...],
        metadata: PatchMetadata,
        assessment: RiskAssessment,
    ) -> None:
        patch_path = session / "task-results" / task_id / "implementation.patch"
        _atomic_bytes(patch_path, patch)
        record["patch_path"] = str(patch_path)
        record["patch_digest"] = _sha256_bytes(patch)
        record["changed_paths"] = list(paths)
        record["diff_lines"] = metadata.diff_lines
        record["risk"] = {
            "level": assessment.level,
            "findings": list(assessment.findings),
            "metadata": {
                "added_lines": metadata.added_lines,
                "deleted_lines": metadata.deleted_lines,
                "diff_lines": metadata.diff_lines,
                "binary": metadata.binary,
                "symlink": metadata.symlink,
                "submodule": metadata.submodule,
                "large_deletion": metadata.large_deletion,
            },
        }

    def _verify_patch(
        self,
        session: Path,
        plan: OvernightPlan,
        state: dict[str, Any],
        budget: Budget,
        task_id: str,
        patch: bytes,
        contract: TaskContract,
        *,
        label: str = "clean",
    ) -> tuple[bool, dict[str, object]]:
        self._guard(budget)
        record = state["tasks"][task_id]
        target = self._task_worktree(state, task_id, f"verification-{label}")
        self._add_detached_worktree(target, record["task_source_head"])
        try:
            expected_protected = tree_digest(
                target, (*plan.protected_paths, *contract.protected_paths)
            )
            apply_patch(target, patch)
            evidence_dir = session / "task-results" / task_id / f"verification-{label}"
            passed, _ = verify(
                target,
                contract,
                evidence_dir,
                expected_protected,
                record["task_source_tree_digest"],
                _sha256_bytes(patch),
            )
            summary = evidence_dir / "summary.json"
            summary_value = json.loads(summary.read_text(encoding="utf-8"))
            failure: dict[str, object] | None = None
            for index, item in enumerate(summary_value.get("evidence", [])):
                if (
                    isinstance(item, dict)
                    and item.get("kind") == "command"
                    and (item.get("exit_code") != 0 or item.get("timed_out") is True)
                ):
                    failure = {
                        "command_index": index,
                        "exit_code": item.get("exit_code"),
                        "timed_out": item.get("timed_out") is True,
                    }
                    break
            result = {
                "passed": passed,
                "path": str(summary),
                "digest": _sha256_file(summary),
                "command_count": len(contract.verification_commands),
                "failure": failure,
            }
            return passed, result
        finally:
            self._remove_worktree(target)

    def _review_patch(
        self,
        session: Path,
        plan: OvernightPlan,
        task: OvernightTask,
        state: dict[str, Any],
        budget: Budget,
        contract: TaskContract,
        patch: bytes,
        paths: tuple[str, ...],
        decision: RoutingDecision,
    ) -> None:
        record = state["tasks"][task.id]
        context: dict[str, object] = {
            "contract": _contract_subset(contract),
            "patch": patch.decode("utf-8", "replace"),
            "changed_paths": list(paths),
            "verification": {
                "passed": record["verification"]["passed"],
                "command_count": record["verification"]["command_count"],
                "evidence_digest": record["verification"]["digest"],
            },
            "routing": _decision_dict(decision, "review"),
            "model": decision.model,
            "effort": decision.reasoning_effort,
        }
        review_prompt = build_codex_review_prompt(context)
        prompt_bytes = len(review_prompt.encode("utf-8"))
        target = self._task_worktree(state, task.id, "review")
        self._add_detached_worktree(target, record["task_source_head"])
        review_payload: dict[str, object]
        try:
            apply_patch(target, patch)
            before = review_workspace_state(target)
            provider_contract = self._effective_provider_contract(contract, decision)
            self._reauthorize_provider_call(target, provider_contract, decision)
            self._guard(budget, additional_provider_calls=1, prompt_bytes=prompt_bytes)
            budget.charge_provider(prompt_bytes)
            record["provider_calls"] += 1
            record["status"] = "REVIEWING"
            record["review_started"] = True
            self._checkpoint(
                session,
                state,
                budget,
                stage="review",
                task_id=task.id,
                message="independent review started",
                fault_stage="review-started",
            )
            provider = self.provider_factory(provider_contract)
            try:
                raw = provider.review(  # type: ignore[attr-defined]
                    target, context, session / "reviews" / task.id / "invocation"
                )
                budget.record_usage(
                    getattr(raw, "input_tokens", None),
                    getattr(raw, "output_tokens", None),
                )
                after = review_workspace_state(target)
                if before != after:
                    review_payload = {
                        "status": "DIGEST_CHANGED",
                        "before_worktree_digest": before["worktree_digest"],
                        "after_worktree_digest": after["worktree_digest"],
                    }
                elif not raw.succeeded or raw.timed_out:
                    review_payload = {"status": "PROVIDER_FAILED"}
                else:
                    parsed = parse_review(raw.payload)
                    review_payload = {
                        "status": "VALID",
                        "verdict": parsed.verdict,
                        "risk": parsed.risk,
                        "findings_count": len(parsed.findings),
                        "auto_integration_allowed": parsed.auto_integration_allowed,
                        "allows_integration": review_allows_integration(parsed),
                        "worktree_digest": before["worktree_digest"],
                    }
            except (ValueError, RuntimeError, OSError):
                review_payload = {"status": "MALFORMED_OR_FAILED"}
        finally:
            self._remove_worktree(target)
        review_path = session / "reviews" / task.id / "review.json"
        atomic_write_json(review_path, review_payload)
        record["review"] = {
            **review_payload,
            "path": str(review_path),
            "digest": _sha256_file(review_path),
        }
        record["review_completed"] = True
        self._checkpoint(
            session,
            state,
            budget,
            stage="review",
            task_id=task.id,
            message="independent review completed",
            digest=record["review"]["digest"],
            fault_stage="review-completed",
        )

    def _integrate(
        self,
        session: Path,
        plan: OvernightPlan,
        task: OvernightTask,
        state: dict[str, Any],
        budget: Budget,
        contract: TaskContract,
        patch: bytes,
        paths: tuple[str, ...],
        integration: Path,
    ) -> None:
        record = state["tasks"][task.id]
        self._guard(budget)
        if _git(integration, "status", "--porcelain"):
            raise OvernightError("INTEGRATION_WORKTREE_DIRTY")
        if _git_text(integration, "rev-parse", "HEAD") != record["task_source_head"]:
            raise OvernightError("SESSION_HEAD_DRIFT")
        record["integration_started"] = True
        record["integration"] = {
            "patch_digest": record["patch_digest"],
            "source_head": record["task_source_head"],
            "expected_tree": None,
        }
        self._checkpoint(
            session,
            state,
            budget,
            stage="integration",
            task_id=task.id,
            message="integration started",
            digest=record["patch_digest"],
            fault_stage="integration-started",
        )
        applied = False
        try:
            apply_patch(integration, patch)
            applied = True
            actual_paths = changed_paths(integration)
            validate_changed_paths(
                integration,
                actual_paths,
                contract.allowed_paths,
                contract.forbidden_paths,
                (*plan.protected_paths, *contract.protected_paths),
            )
            if capture_patch(integration) != patch:
                raise OvernightError("INTEGRATION_PATCH_DRIFT")
            self._guard(budget)
            passed, verification = self._verify_patch(
                session,
                plan,
                state,
                budget,
                task.id,
                patch,
                contract,
                label="precommit",
            )
            if not passed:
                record["status"] = "WAITING_HUMAN"
                record["failure_code"] = "PRECOMMIT_VERIFICATION_FAILED"
                self._rollback_integration(integration, patch)
                applied = False
                return
            record["integration"]["verification_digest"] = verification["digest"]
            self._guard(budget)
            _git(integration, "add", "--", *paths)
            expected_tree = _git_text(integration, "write-tree")
            record["integration"]["expected_tree"] = expected_tree
            self._checkpoint(
                session,
                state,
                budget,
                stage="integration",
                task_id=task.id,
                message="integration prepared",
                digest=expected_tree,
                fault_stage="integration-prepared",
            )
            self._guard(budget)
            _git(
                integration,
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-m",
                f"feat(overnight): {task.id} の安全な自動統合",
            )
            commit = _git_text(integration, "rev-parse", "HEAD")
            self._fault("commit-created", state)
            record["commit"] = commit
            record["integration_completed"] = True
            record["integration"]["commit"] = commit
            record["status"] = "INTEGRATED"
            state["integration_results"].append({"task_id": task.id, "commit": commit})
            applied = False
            self._checkpoint(
                session,
                state,
                budget,
                stage="integration",
                task_id=task.id,
                message="integration completed",
                digest=commit,
                fault_stage="integration-completed",
            )
        except Exception:
            if applied:
                try:
                    self._rollback_integration(integration, patch)
                except Exception as exc:
                    raise CleanupError("INTEGRATION_ROLLBACK_FAILED") from exc
            raise

    def _rollback_integration(self, integration: Path, patch: bytes) -> None:
        _git(integration, "reset", "--quiet")
        _git(integration, "apply", "--reverse", "--binary", "-", input_bytes=patch)
        if _git(integration, "status", "--porcelain"):
            raise CleanupError("INTEGRATION_ROLLBACK_DIRTY")

    def _task_worktree(self, state: dict[str, Any], task_id: str, stage: str) -> Path:
        return (
            self.worktrees_root
            / state["session_id"]
            / "tasks"
            / task_id
            / stage
        )

    def _add_detached_worktree(self, path: Path, head: str) -> None:
        if path.exists():
            raise CleanupError("STALE_TASK_WORKTREE")
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(
            self.root,
            "-c",
            "core.hooksPath=/dev/null",
            "worktree",
            "add",
            "--detach",
            str(path),
            head,
        )

    def _remove_worktree(self, path: Path) -> None:
        try:
            _git(self.root, "worktree", "remove", "--force", str(path))
            _git(self.root, "worktree", "prune")
        except Exception as exc:
            raise CleanupError("TASK_WORKTREE_CLEANUP_FAILED") from exc

    def _record_decision(
        self, record: dict[str, Any], decision: RoutingDecision, purpose: str
    ) -> None:
        value = _decision_dict(decision, purpose)
        if value not in record["routing_decisions"]:
            record["routing_decisions"].append(value)

    def _effective_provider_contract(
        self,
        contract: TaskContract,
        decision: RoutingDecision,
        *,
        prompt: str | None = None,
    ) -> TaskContract:
        provider = {
            **contract.provider,
            "model": decision.model,
            "effort": decision.reasoning_effort,
        }
        if prompt is not None:
            provider["prompt"] = prompt
        return replace(contract, provider=provider)

    def _reauthorize_provider_call(
        self,
        task_source_worktree: Path,
        effective_contract: TaskContract,
        decision: RoutingDecision,
    ) -> None:
        """Fail closed against the exact source tree immediately before a call."""

        provider = str(effective_contract.provider.get("type", "fake"))
        if not any(
            capability.allows(
                self.repository_identity,
                provider,
                decision.model,
                decision.reasoning_effort,
            )
            for capability in self.provider_capabilities
        ):
            raise PolicyViolation("ROUTED_PROFILE_UNAVAILABLE")
        if (
            effective_contract.provider.get("model") != decision.model
            or effective_contract.provider.get("effort") != decision.reasoning_effort
        ):
            raise PolicyViolation("EFFECTIVE_PROVIDER_CONTRACT_INVALID")
        try:
            validate_task_contract(
                self.root,
                effective_contract,
                read_scope_root=task_source_worktree,
            )
        except (ExternalAuthorizationError, RuntimeError, ValueError) as exc:
            raise PolicyViolation("PROVIDER_REAUTHORIZATION_FAILED") from exc

    def _guard(
        self,
        budget: Budget,
        *,
        additional_provider_calls: int = 0,
        prompt_bytes: int = 0,
    ) -> None:
        self._update_elapsed(budget)
        budget.check(
            additional_provider_calls=additional_provider_calls,
            prompt_bytes_for_call=prompt_bytes,
            elapsed_seconds=budget.elapsed_seconds,
        )

    def _update_elapsed(self, budget: Budget) -> None:
        if self._run_clock_start is not None:
            budget.record_elapsed(
                self._elapsed_base + max(0.0, self.clock() - self._run_clock_start)
            )

    def _save(self, session: Path, state: dict[str, Any], budget: Budget) -> None:
        self._update_elapsed(budget)
        state["budget"] = budget.to_dict()
        state["updated_at"] = utc_now()
        save_state(session, state)
        atomic_write_json(session / "budget.json", state["budget"])

    def _checkpoint(
        self,
        session: Path,
        state: dict[str, Any],
        budget: Budget,
        *,
        stage: str,
        task_id: str,
        message: str,
        digest: str | None = None,
        count: int | None = None,
        fault_stage: str | None = None,
    ) -> None:
        self._save(session, state, budget)
        append_event(
            session,
            session_id=state["session_id"],
            stage=stage,
            status=state["tasks"][task_id]["status"],
            message=message,
            task_id=task_id,
            digest=digest,
            count=count,
        )
        if fault_stage:
            self._fault(fault_stage, state)

    def _fault(self, stage: str, state: dict[str, Any]) -> None:
        if self.fault_injector is not None:
            self.fault_injector(stage, state)


__all__ = [
    "CleanupError",
    "OvernightError",
    "OvernightRunner",
    "TASK_STATES",
    "TERMINAL_SESSION_STATES",
]
