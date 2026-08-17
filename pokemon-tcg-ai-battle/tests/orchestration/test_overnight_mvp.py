from __future__ import annotations

import json
import fcntl
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.orchestration import overnight as overnight_module
from scripts.orchestration.authorization import ProviderCapability, repository_identity
from scripts.orchestration.overnight import (
    CleanupError,
    OvernightError,
    OvernightRunner as _OvernightRunner,
)
from scripts.orchestration.overnight_plan import OvernightPlanError, load_plan
from scripts.orchestration.provider import FakeProvider, ReadOnlyReviewResult
from scripts.orchestration.provider import build_codex_review_prompt
from scripts.orchestration.risk import PatchMetadata, RiskAssessment


def _fake_capabilities(
    repository: Path,
    profiles: dict[str, set[str]] | None = None,
    *,
    provider: str = "fake",
) -> tuple[ProviderCapability, ...]:
    selected = profiles or {"gpt-5.6-terra": {"low", "medium", "high"}}
    identity = repository_identity(repository)
    return tuple(
        ProviderCapability(identity, provider, model, frozenset(efforts))
        for model, efforts in selected.items()
    )


def OvernightRunner(repository: Path, **kwargs):
    """Construct a runner with explicit repository-bound fake capabilities."""

    kwargs.setdefault("provider_capabilities", _fake_capabilities(repository))
    return _OvernightRunner(repository, **kwargs)


class Crash(BaseException):
    pass


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def _contract(
    *,
    writes: dict[str, str] | None = None,
    allowed: list[str] | None = None,
    verification: str = "verify_fixture.py",
    scenario: str = "valid",
    auto_integrate: bool = True,
    command: list[str] | None = None,
) -> dict[str, object]:
    return {
        "task_id": "overnight-contract",
        "role": "implementation",
        "read_paths": [],
        "allowed_paths": allowed or ["fixture.py"],
        "forbidden_paths": ["forbidden/**"],
        "protected_paths": ["protected_test.py"],
        "verification_commands": [command or [sys.executable, verification]],
        "environment_allowlist": [],
        "resource_budget": {
            "provider_timeout_seconds": 1,
            "verification_timeout_seconds": 5,
        },
        "expected_outputs": {
            "allow_overnight_auto_integration": auto_integrate,
        },
        "provider": {
            "type": "fake",
            "scenario": scenario,
            "writes": writes or {"fixture.py": "VALUE = 2\n"},
        },
        "external_model": {},
    }


def _prepare(
    repository: Path,
    tmp_path: Path,
    *,
    task_values: list[dict[str, object]] | None = None,
    contracts: list[dict[str, object]] | None = None,
    auto_integrate: bool = True,
    limits: dict[str, int] | None = None,
    routing: dict[str, dict[str, str]] | None = None,
    plan_extra: dict[str, object] | None = None,
) -> Path:
    contracts = contracts or [_contract()]
    contract_dir = repository / "contracts"
    contract_dir.mkdir(exist_ok=True)
    for index, value in enumerate(contracts, 1):
        (contract_dir / f"task-{index}.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
    _git(repository, "add", "contracts")
    _git(repository, "commit", "-m", "add overnight contracts")
    tasks = task_values or [
        {
            "id": "task-01",
            "title": "Fixture update",
            "contract": "contracts/task-1.json",
            "depends_on": [],
            "complexity": "simple",
            "risk_hint": "low",
            "max_repair_attempts": 1,
        }
    ]
    value: dict[str, object] = {
        "version": 1,
        "name": "overnight-test",
        "base_branch": _git(repository, "branch", "--show-current"),
        "session_branch_prefix": "overnight/",
        "auto_integrate_low_risk": auto_integrate,
        "limits": limits
        or {
            "max_tasks": 8,
            "max_provider_calls": 20,
            "max_repair_attempts_per_task": 2,
            "max_elapsed_seconds": 100,
            "max_prompt_bytes_per_call": 100_000,
            "max_diff_lines_for_auto_integration": 800,
        },
        "routing": routing
        or {
            "economy": {"model": "gpt-5.6-terra", "reasoning_effort": "low"},
            "standard": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
            "deep": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
        },
        "protected_paths": ["protected_test.py"],
        "low_risk_allowlist": ["fixture.py", "new_module.py"],
        "tasks": tasks,
    }
    if plan_extra:
        value.update(plan_extra)
    path = tmp_path / "overnight-plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class RecordingFactory:
    def __init__(self) -> None:
        self.invocations: list[dict[str, object]] = []
        self.reviews: list[dict[str, object]] = []
        self.review_payload = json.dumps(
            {
                "verdict": "PASS",
                "risk": "LOW",
                "findings": [],
                "auto_integration_allowed": True,
            }
        )
        self.review_mutates = False
        self.review_timed_out = False
        self.repair_writes: dict[str, str] | None = None
        self.invoke_exception: BaseException | None = None
        self.review_mutation_path: str | None = None
        self.invocation_tokens: tuple[int, int] | None = None

    def __call__(self, _contract_value):
        factory = self

        class Provider:
            def invoke(self, worktree, contract, invocation_dir):
                factory.invocations.append(
                    {
                        "worktree": str(worktree),
                        "model": contract.provider.get("model"),
                        "effort": contract.provider.get("effort"),
                        "prompt": contract.provider.get("prompt", ""),
                    }
                )
                if factory.invoke_exception is not None:
                    raise factory.invoke_exception
                delegated = contract
                if (
                    factory.repair_writes is not None
                    and str(contract.provider.get("prompt", "")).startswith("Repair task")
                ):
                    delegated = replace(
                        contract,
                        provider={**contract.provider, "writes": factory.repair_writes},
                    )
                result = FakeProvider().invoke(worktree, delegated, invocation_dir)
                if factory.invocation_tokens is None:
                    return result
                return replace(
                    result,
                    invocation=replace(
                        result.invocation,
                        input_tokens=factory.invocation_tokens[0],
                        output_tokens=factory.invocation_tokens[1],
                    ),
                )

            def review(self, worktree, context, invocation_dir):
                factory.reviews.append(
                    {
                        "worktree": str(worktree),
                        "model": context["model"],
                        "effort": context["effort"],
                        "keys": set(context),
                        "context": context,
                    }
                )
                if factory.review_mutates:
                    (Path(worktree) / "fixture.py").write_text(
                        "VALUE = 99\n", encoding="utf-8"
                    )
                if factory.review_mutation_path is not None:
                    (Path(worktree) / factory.review_mutation_path).write_text(
                        "reviewer mutation\n", encoding="utf-8"
                    )
                return ReadOnlyReviewResult(
                    not factory.review_timed_out,
                    factory.review_payload,
                    timed_out=factory.review_timed_out,
                )

        return Provider()


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_invalid_plan_has_zero_session_branch_worktree_and_provider(
    repository: Path, tmp_path: Path
) -> None:
    plan = _prepare(repository, tmp_path, plan_extra={"unknown": True})
    before_branches = _git(repository, "branch", "--format=%(refname)")
    before_worktrees = _git(repository, "worktree", "list", "--porcelain")
    factory = RecordingFactory()
    with pytest.raises(OvernightPlanError, match="unknown"):
        OvernightRunner(repository, provider_factory=factory).start(plan)
    assert factory.invocations == []
    assert before_branches == _git(repository, "branch", "--format=%(refname)")
    assert before_worktrees == _git(repository, "worktree", "list", "--porcelain")
    assert not (repository / ".orchestrator" / "overnight").exists()


def test_command_policy_and_authorization_fail_before_session(
    repository: Path, tmp_path: Path
) -> None:
    unsafe = _contract(command=["bash", "-c", "true"])
    plan = _prepare(repository, tmp_path, contracts=[unsafe])
    with pytest.raises(OvernightPlanError):
        load_plan(plan, repository, {"gpt-5.6-terra"})
    assert not (repository / ".orchestrator" / "overnight").exists()

    codex = _contract()
    codex["provider"] = {"type": "codex", "prompt": "change fixture"}
    codex["read_paths"] = ["fixture.py"]
    codex["external_model"] = {}
    (repository / "contracts" / "task-1.json").write_text(
        json.dumps(codex), encoding="utf-8"
    )
    _git(repository, "add", "contracts/task-1.json")
    _git(repository, "commit", "-m", "replace contract")
    with pytest.raises(OvernightPlanError):
        load_plan(plan, repository, {"gpt-5.6-terra"})


def test_task_worker_isolated_and_root_fully_unchanged(
    repository: Path, tmp_path: Path
) -> None:
    plan = _prepare(repository, tmp_path)
    root_before = (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "status", "--porcelain"),
        _git(repository, "diff", "--cached", "--binary"),
    )
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(plan)
    assert state["status"] == "DONE"
    record = state["tasks"]["task-01"]
    assert record["status"] == "INTEGRATED"
    assert "/tasks/task-01/implementation" in factory.invocations[0]["worktree"]
    assert factory.invocations[0]["worktree"] != state["integration_worktree"]
    assert not Path(factory.invocations[0]["worktree"]).exists()
    assert (repository / "fixture.py").read_text() == "VALUE = 1\n"
    assert root_before == (
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "status", "--porcelain"),
        _git(repository, "diff", "--cached", "--binary"),
    )


def test_waiting_human_keeps_integration_clean_and_dependency_is_skipped(
    repository: Path, tmp_path: Path
) -> None:
    tasks = [
        {
            "id": "task-01",
            "title": "Needs human",
            "contract": "contracts/task-1.json",
            "depends_on": [],
            "complexity": "simple",
            "risk_hint": "low",
            "max_repair_attempts": 0,
        },
        {
            "id": "task-02",
            "title": "Dependent",
            "contract": "contracts/task-2.json",
            "depends_on": ["task-01"],
            "complexity": "simple",
            "risk_hint": "low",
            "max_repair_attempts": 0,
        },
    ]
    plan = _prepare(
        repository,
        tmp_path,
        task_values=tasks,
        contracts=[_contract(auto_integrate=False), _contract()],
    )
    state = OvernightRunner(repository).start(plan)
    assert state["tasks"]["task-01"]["status"] == "WAITING_HUMAN"
    assert state["tasks"]["task-02"]["status"] == "SKIPPED_DEPENDENCY"
    assert state["status"] == "BLOCKED"
    integration = Path(state["integration_worktree"])
    assert _git(integration, "status", "--porcelain") == ""
    assert _git(integration, "rev-parse", "HEAD") == state["source_head"]


def test_failed_dependency_is_skipped_and_session_is_not_done(
    repository: Path, tmp_path: Path
) -> None:
    tasks = [
        {"id": "task-01", "title": "Fails", "contract": "contracts/task-1.json", "depends_on": [], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
        {"id": "task-02", "title": "Blocked", "contract": "contracts/task-2.json", "depends_on": ["task-01"], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
    ]
    plan = _prepare(
        repository,
        tmp_path,
        task_values=tasks,
        contracts=[_contract(scenario="nonzero_exit"), _contract()],
    )
    state = OvernightRunner(repository).start(plan)
    assert state["tasks"]["task-01"]["status"] == "FAILED"
    assert state["tasks"]["task-02"]["status"] == "SKIPPED_DEPENDENCY"
    assert state["status"] != "DONE"


def test_changed_path_outside_allowed_is_blocked_before_review(
    repository: Path, tmp_path: Path
) -> None:
    contract = _contract(writes={"outside.py": "bad\n"}, allowed=["fixture.py"])
    plan = _prepare(repository, tmp_path, contracts=[contract])
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(plan)
    assert state["tasks"]["task-01"]["status"] == "BLOCKED"
    assert factory.reviews == []
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_two_integrated_tasks_use_updated_session_head(
    repository: Path, tmp_path: Path
) -> None:
    (repository / "verify_second.py").write_text(
        "from pathlib import Path\n"
        "assert Path('fixture.py').read_text() == 'VALUE = 2\\n'\n"
        "assert Path('new_module.py').read_text() == 'NEW = 1\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "verify_second.py")
    _git(repository, "commit", "-m", "add chained verifier")
    tasks = [
        {"id": "task-01", "title": "First", "contract": "contracts/task-1.json", "depends_on": [], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
        {"id": "task-02", "title": "Second", "contract": "contracts/task-2.json", "depends_on": ["task-01"], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
    ]
    contracts = [
        _contract(),
        _contract(writes={"new_module.py": "NEW = 1\n"}, allowed=["new_module.py"], verification="verify_second.py"),
    ]
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, task_values=tasks, contracts=contracts)
    )
    first = state["tasks"]["task-01"]
    second = state["tasks"]["task-02"]
    assert state["status"] == "DONE"
    assert second["task_source_head"] == first["commit"]
    assert first["task_source_head"] == state["source_head"]
    assert first["commit"] != second["commit"]


def test_routing_profiles_reach_actual_provider_and_repair_escalates(
    repository: Path, tmp_path: Path
) -> None:
    contract = _contract(writes={"fixture.py": "VALUE = 0\n"})
    factory = RecordingFactory()
    factory.repair_writes = {"fixture.py": "VALUE = 2\n"}
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path, contracts=[contract])
    )
    assert state["status"] == "DONE"
    assert [(item["model"], item["effort"]) for item in factory.invocations] == [
        ("gpt-5.6-terra", "low"),
        ("gpt-5.6-terra", "medium"),
    ]
    repair_prompt = str(factory.invocations[1]["prompt"])
    assert "command_index" in repair_prompt
    assert "stdout" not in repair_prompt and "stderr" not in repair_prompt
    assert factory.reviews[0]["effort"] == "low"
    assert factory.reviews[0]["keys"] == {
        "contract", "patch", "changed_paths", "verification", "routing", "model", "effort"
    }


def test_review_malformed_or_digest_change_never_integrates(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    factory.review_payload = "not-json"
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path)
    )
    assert state["tasks"]["task-01"]["status"] == "WAITING_HUMAN"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_review_digest_change_never_integrates(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    factory.review_mutates = True
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path)
    )
    record = state["tasks"]["task-01"]
    assert record["status"] == "WAITING_HUMAN"
    assert record["review"]["status"] == "DIGEST_CHANGED"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_review_timeout_never_integrates(repository: Path, tmp_path: Path) -> None:
    factory = RecordingFactory()
    factory.review_timed_out = True
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path)
    )
    assert state["tasks"]["task-01"]["status"] == "WAITING_HUMAN"
    assert state["tasks"]["task-01"]["review"]["status"] == "PROVIDER_FAILED"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_economy_standard_and_deep_profiles_reach_provider(
    repository: Path, tmp_path: Path
) -> None:
    tasks = []
    contracts = []
    for index, complexity in enumerate(("simple", "normal", "complex"), 1):
        tasks.append(
            {
                "id": f"task-0{index}",
                "title": complexity,
                "contract": f"contracts/task-{index}.json",
                "depends_on": [],
                "complexity": complexity,
                "risk_hint": "low",
                "max_repair_attempts": 0,
            }
        )
        contracts.append(_contract(auto_integrate=False))
    routing = {
        "economy": {"model": "economy-model", "reasoning_effort": "low"},
        "standard": {"model": "standard-model", "reasoning_effort": "medium"},
        "deep": {"model": "deep-model", "reasoning_effort": "high"},
    }
    factory = RecordingFactory()
    state = OvernightRunner(
        repository,
        provider_factory=factory,
        provider_capabilities=_fake_capabilities(
            repository,
            {
                "economy-model": {"low"},
                "standard-model": {"medium"},
                "deep-model": {"high"},
            },
        ),
    ).start(
        _prepare(
            repository,
            tmp_path,
            task_values=tasks,
            contracts=contracts,
            routing=routing,
        )
    )
    assert state["status"] == "WAITING_HUMAN"
    assert [(item["model"], item["effort"]) for item in factory.invocations] == [
        ("economy-model", "low"),
        ("standard-model", "medium"),
        ("deep-model", "high"),
    ]


def test_control_plane_contract_forces_deep_and_never_auto_integrates(
    repository: Path, tmp_path: Path
) -> None:
    target = "scripts/orchestration/provider.py"
    (repository / "verify_control.py").write_text(
        "from pathlib import Path\n"
        f"assert Path({target!r}).read_text() == 'changed\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "verify_control.py")
    _git(repository, "commit", "-m", "add control verifier")
    contract = _contract(
        writes={target: "changed\n"},
        allowed=[target],
        verification="verify_control.py",
    )
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path, contracts=[contract])
    )
    record = state["tasks"]["task-01"]
    assert factory.invocations[0]["effort"] == "high"
    assert record["risk"]["level"] == "HIGH"
    assert record["status"] == "WAITING_HUMAN"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_unavailable_routing_model_has_no_side_effect(
    repository: Path, tmp_path: Path
) -> None:
    routing = {
        "economy": {"model": "missing-model", "reasoning_effort": "low"},
        "standard": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
        "deep": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
    }
    plan = _prepare(repository, tmp_path, routing=routing)
    with pytest.raises(OvernightPlanError, match="unavailable"):
        OvernightRunner(repository).start(plan)
    assert not (repository / ".orchestrator" / "overnight").exists()


@pytest.mark.parametrize(
    "operation",
    ["_implementation_attempt", "_review_patch", "_integrate"],
)
def test_elapsed_cap_is_checked_before_sensitive_operations(
    repository: Path, tmp_path: Path, monkeypatch, operation: str
) -> None:
    clock = MutableClock()
    factory = RecordingFactory()
    limits = {
        "max_tasks": 2,
        "max_provider_calls": 5,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 1,
        "max_prompt_bytes_per_call": 100_000,
        "max_diff_lines_for_auto_integration": 800,
    }
    runner = OvernightRunner(
        repository, provider_factory=factory, clock=clock
    )
    original = getattr(runner, operation)

    def expire(*args, **kwargs):
        clock.value = 1.0
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, operation, expire)
    state = runner.start(_prepare(repository, tmp_path, limits=limits))
    assert state["status"] == "STOPPED_BUDGET"
    assert state["stop_reason"] == "MAX_ELAPSED_SECONDS"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_task_repair_cap_is_enforced_per_task(
    repository: Path, tmp_path: Path
) -> None:
    task = {
        "id": "task-01",
        "title": "No repair",
        "contract": "contracts/task-1.json",
        "depends_on": [],
        "complexity": "simple",
        "risk_hint": "low",
        "max_repair_attempts": 0,
    }
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(
            repository,
            tmp_path,
            task_values=[task],
            contracts=[_contract(writes={"fixture.py": "VALUE = 0\n"})],
        )
    )
    assert state["tasks"]["task-01"]["status"] == "FAILED"
    assert state["tasks"]["task-01"]["repair_count"] == 0
    assert len(factory.invocations) == 1


def test_provider_call_cap_stops_before_review(
    repository: Path, tmp_path: Path
) -> None:
    limits = {
        "max_tasks": 2,
        "max_provider_calls": 1,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 100,
        "max_prompt_bytes_per_call": 100_000,
        "max_diff_lines_for_auto_integration": 800,
    }
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path, limits=limits)
    )
    assert state["status"] == "STOPPED_BUDGET"
    assert state["stop_reason"] == "MAX_PROVIDER_CALLS"
    assert len(factory.invocations) == 1
    assert factory.reviews == []


def test_prompt_byte_cap_stops_before_provider(
    repository: Path, tmp_path: Path
) -> None:
    contract = _contract()
    contract["provider"]["prompt"] = "x" * 20
    limits = {
        "max_tasks": 2,
        "max_provider_calls": 5,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 100,
        "max_prompt_bytes_per_call": 10,
        "max_diff_lines_for_auto_integration": 800,
    }
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path, contracts=[contract], limits=limits)
    )
    assert state["status"] == "STOPPED_BUDGET"
    assert state["stop_reason"] == "MAX_PROMPT_BYTES_PER_CALL"
    assert factory.invocations == []


def test_diff_line_cap_prevents_auto_integration(
    repository: Path, tmp_path: Path
) -> None:
    contract = _contract(writes={"fixture.py": "A\nB\nC\n"})
    limits = {
        "max_tasks": 2,
        "max_provider_calls": 5,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 100,
        "max_prompt_bytes_per_call": 100_000,
        "max_diff_lines_for_auto_integration": 1,
    }
    # Verification is deliberately made compatible with the larger low-risk patch.
    (repository / "verify_three.py").write_text(
        "from pathlib import Path\nassert Path('fixture.py').read_text() == 'A\\nB\\nC\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "verify_three.py")
    _git(repository, "commit", "-m", "add line cap verifier")
    contract["verification_commands"] = [[sys.executable, "verify_three.py"]]
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, contracts=[contract], limits=limits)
    )
    assert state["tasks"]["task-01"]["diff_lines"] > 1
    assert state["tasks"]["task-01"]["status"] == "WAITING_HUMAN"


def test_plan_and_source_head_drift_stop_resume(
    repository: Path, tmp_path: Path
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    session = repository / ".orchestrator" / "overnight" / "sessions" / state["session_id"]
    snapshot = session / "plan.snapshot.json"
    snapshot.write_text(snapshot.read_text() + " ", encoding="utf-8")
    resumed = OvernightRunner(repository).resume(state["session_id"])
    assert resumed["status"] == "STOPPED_ERROR"
    assert resumed["stop_reason"] == "RESUME_VALIDATION_FAILED"


def test_session_head_drift_stops_resume(repository: Path, tmp_path: Path) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    integration = Path(state["integration_worktree"])
    _git(integration, "commit", "--allow-empty", "-m", "unexpected drift")
    resumed = OvernightRunner(repository).resume(state["session_id"])
    assert resumed["status"] == "STOPPED_ERROR"
    assert resumed["stop_reason"] == "RESUME_VALIDATION_FAILED"


def test_dirty_integration_worktree_stops_resume(
    repository: Path, tmp_path: Path
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    integration = Path(state["integration_worktree"])
    (integration / "unexpected.txt").write_text("dirty\n", encoding="utf-8")
    resumed = OvernightRunner(repository).resume(state["session_id"])
    assert resumed["status"] == "STOPPED_ERROR"
    assert resumed["stop_reason"] == "RESUME_VALIDATION_FAILED"


@pytest.mark.parametrize("artifact", ["patch", "verification", "review"])
def test_resume_rejects_durable_artifact_digest_drift(
    repository: Path, tmp_path: Path, artifact: str
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    record = state["tasks"]["task-01"]
    if artifact == "patch":
        path = Path(record["patch_path"])
    elif artifact == "verification":
        path = Path(record["verification"]["path"])
    else:
        path = Path(record["review"]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    resumed = OvernightRunner(repository).resume(state["session_id"])
    assert resumed["status"] == "STOPPED_ERROR"
    assert resumed["stop_reason"] == "RESUME_VALIDATION_FAILED"


def test_resume_rejects_task_contract_digest_drift_hidden_from_git_status(
    repository: Path, tmp_path: Path
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    contract = repository / "contracts" / "task-1.json"
    _git(repository, "update-index", "--assume-unchanged", "contracts/task-1.json")
    contract.write_text(contract.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert _git(repository, "status", "--porcelain") == ""
    resumed = OvernightRunner(repository).resume(state["session_id"])
    assert resumed["status"] == "STOPPED_ERROR"
    assert resumed["stop_reason"] == "RESUME_VALIDATION_FAILED"


def test_provider_crash_is_not_reinvoked_on_resume(
    repository: Path, tmp_path: Path
) -> None:
    plan = _prepare(repository, tmp_path)
    crashing = RecordingFactory()
    crashing.invoke_exception = Crash()
    with pytest.raises(Crash):
        OvernightRunner(repository, provider_factory=crashing).start(plan)
    sessions = repository / ".orchestrator" / "overnight" / "sessions"
    session_id = next(sessions.iterdir()).name
    recovery = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=recovery).resume(session_id)
    assert recovery.invocations == []
    assert state["tasks"]["task-01"]["status"] == "WAITING_HUMAN"


def test_verification_crash_reconciles_without_duplicate_provider(
    repository: Path, tmp_path: Path
) -> None:
    plan = _prepare(repository, tmp_path)
    factory = RecordingFactory()

    def crash(stage, _state):
        if stage == "verification-started":
            raise Crash()

    with pytest.raises(Crash):
        OvernightRunner(
            repository, provider_factory=factory, fault_injector=crash
        ).start(plan)
    session_id = next(
        (repository / ".orchestrator" / "overnight" / "sessions").iterdir()
    ).name
    state = OvernightRunner(repository, provider_factory=factory).resume(session_id)
    assert len(factory.invocations) == 1
    assert state["tasks"]["task-01"]["verification_completed"] is True


def test_commit_crash_recovers_without_duplicate_commit(
    repository: Path, tmp_path: Path
) -> None:
    plan = _prepare(repository, tmp_path)

    def crash(stage, _state):
        if stage == "commit-created":
            raise Crash()

    with pytest.raises(Crash):
        OvernightRunner(repository, fault_injector=crash).start(plan)
    session_id = next(
        (repository / ".orchestrator" / "overnight" / "sessions").iterdir()
    ).name
    integration = next(
        (repository / ".orchestrator" / "overnight" / "worktrees" / session_id).glob("integration")
    )
    head = _git(integration, "rev-parse", "HEAD")
    state = OvernightRunner(repository).resume(session_id)
    assert state["status"] == "DONE"
    assert state["tasks"]["task-01"]["commit"] == head
    assert _git(integration, "rev-list", "--count", f"{state['source_head']}..HEAD") == "1"


def test_cleanup_failure_and_keyboard_interrupt_are_durable(
    repository: Path, tmp_path: Path, monkeypatch
) -> None:
    runner = OvernightRunner(repository)
    original = runner._remove_worktree

    def fail_after_cleanup(path):
        original(path)
        raise CleanupError("injected")

    monkeypatch.setattr(runner, "_remove_worktree", fail_after_cleanup)
    state = runner.start(_prepare(repository, tmp_path))
    assert state["status"] == "BLOCKED"
    assert state["tasks"]["task-01"]["failure_code"] == "CLEANUP_FAILED"


def test_keyboard_interrupt_stops_after_task_worker_cleanup(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    factory.invoke_exception = KeyboardInterrupt()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path)
    )
    assert state["status"] == "STOPPED_INTERRUPT"
    assert not Path(factory.invocations[0]["worktree"]).exists()


def test_runtime_never_issues_push(repository: Path, tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    original = overnight_module._git

    def recording(root, *args, **kwargs):
        calls.append(tuple(args))
        return original(root, *args, **kwargs)

    monkeypatch.setattr(overnight_module, "_git", recording)
    OvernightRunner(repository).start(_prepare(repository, tmp_path))
    assert all("push" not in call for call in calls)


def test_reverse_order_dependencies_are_scheduled_from_the_ready_dag(
    repository: Path, tmp_path: Path
) -> None:
    (repository / "verify_reverse_second.py").write_text(
        "from pathlib import Path\n"
        "assert Path('fixture.py').read_text() == 'VALUE = 2\\n'\n"
        "assert Path('new_module.py').read_text() == 'NEW = 1\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "verify_reverse_second.py")
    _git(repository, "commit", "-m", "add reverse dependency verifier")
    tasks = [
        {"id": "task-02", "title": "Dependent second", "contract": "contracts/task-2.json", "depends_on": ["task-01"], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
        {"id": "task-01", "title": "Dependency first", "contract": "contracts/task-1.json", "depends_on": [], "complexity": "simple", "risk_hint": "low", "max_repair_attempts": 0},
    ]
    state = OvernightRunner(repository).start(
        _prepare(
            repository,
            tmp_path,
            task_values=tasks,
            contracts=[
                _contract(),
                _contract(
                    writes={"new_module.py": "NEW = 1\n"},
                    allowed=["new_module.py"],
                    verification="verify_reverse_second.py",
                ),
            ],
        )
    )
    assert state["status"] == "DONE"
    assert state["tasks"]["task-02"]["task_source_head"] == state["tasks"]["task-01"]["commit"]


def test_concurrent_resume_lock_has_zero_provider_event_and_commit_side_effects(
    repository: Path, tmp_path: Path
) -> None:
    initial = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    session = repository / ".orchestrator" / "overnight" / "sessions" / initial["session_id"]
    state_before = (session / "state.json").read_bytes()
    events_before = (session / "events.jsonl").read_bytes()
    head_before = _git(Path(initial["integration_worktree"]), "rev-parse", "HEAD")
    lock = (session / "session.lock").open("r+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    factory = RecordingFactory()
    try:
        with pytest.raises(OvernightError, match="SESSION_LOCKED"):
            OvernightRunner(repository, provider_factory=factory).resume(initial["session_id"])
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    assert factory.invocations == []
    assert (session / "state.json").read_bytes() == state_before
    assert (session / "events.jsonl").read_bytes() == events_before
    assert _git(Path(initial["integration_worktree"]), "rev-parse", "HEAD") == head_before


def test_control_plane_self_change_is_never_auto_integrated(
    repository: Path, tmp_path: Path
) -> None:
    target = "scripts/orchestrate.py"
    (repository / "scripts").mkdir()
    (repository / "scripts" / "orchestrate.py").write_text("baseline\n", encoding="utf-8")
    (repository / "verify_control_entry.py").write_text(
        "from pathlib import Path\n"
        f"assert Path({target!r}).read_text() == 'changed\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "scripts", "verify_control_entry.py")
    _git(repository, "commit", "-m", "add control entry verifier")
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(
            repository,
            tmp_path,
            contracts=[_contract(writes={target: "changed\n"}, allowed=[target], verification="verify_control_entry.py")],
        )
    )
    record = state["tasks"]["task-01"]
    assert factory.invocations[0]["effort"] == "high"
    assert record["risk"]["level"] == "HIGH"
    assert record["status"] == "WAITING_HUMAN"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("target", "replacement"),
    [
        (".gitmodules", '[submodule "fixture"]\n\tpath = fixture\n\turl = local\n'),
        (".gitattributes", "*.txt text\n"),
        (".gitignore", ".orchestrator/\nworker-cache.tmp\nnew-ignore\n"),
    ],
)
def test_git_control_files_are_high_risk_and_never_auto_integrated(
    repository: Path, tmp_path: Path, target: str, replacement: str
) -> None:
    path = repository / target
    if not path.exists():
        path.write_text("", encoding="utf-8")
        _git(repository, "add", target)
        _git(repository, "commit", "-m", f"add {target} fixture")
    verifier = repository / "verify_git_control.py"
    verifier.write_text(
        "from pathlib import Path\n"
        f"assert Path({target!r}).read_text() == {replacement!r}\n",
        encoding="utf-8",
    )
    _git(repository, "add", "verify_git_control.py")
    _git(repository, "commit", "-m", "add Git control verifier")
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(
            repository,
            tmp_path,
            contracts=[
                _contract(
                    writes={target: replacement},
                    allowed=[target],
                    verification="verify_git_control.py",
                )
            ],
        )
    )
    record = state["tasks"]["task-01"]
    assert factory.invocations[0]["effort"] == "high"
    assert record["risk"]["level"] == "HIGH"
    assert record["review"]["status"] == "VALID"
    assert record["review"]["risk"] == "LOW"
    assert record["status"] == "WAITING_HUMAN"


@pytest.mark.parametrize("allowed", ["scripts/**", "scripts/**/*.py", "**/*.py"])
def test_control_plane_allowed_globs_route_deep_and_remain_high_risk(
    repository: Path, tmp_path: Path, allowed: str
) -> None:
    target = "scripts/orchestration/provider.py"
    (repository / "scripts" / "orchestration").mkdir(parents=True)
    (repository / target).write_text("baseline\n", encoding="utf-8")
    (repository / "verify_control_glob.py").write_text(
        "from pathlib import Path\n"
        f"assert Path({target!r}).read_text() == 'changed\\n'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "scripts", "verify_control_glob.py")
    _git(repository, "commit", "-m", "add broad Control Plane verifier")
    factory = RecordingFactory()
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(
            repository,
            tmp_path,
            contracts=[
                _contract(
                    writes={target: "changed\n"},
                    allowed=[allowed],
                    verification="verify_control_glob.py",
                )
            ],
        )
    )
    record = state["tasks"]["task-01"]
    assert factory.invocations[0]["effort"] == "high"
    assert record["risk"]["level"] == "HIGH"
    assert record["status"] == "WAITING_HUMAN"


def test_routed_model_reauthorization_failure_prevents_provider(
    repository: Path, tmp_path: Path, write_authorization_policy
) -> None:
    policy = write_authorization_policy(
        repository,
        allowed_models={"model-a": ["low", "medium", "high"]},
    )
    contract = _contract()
    contract["provider"] = {"type": "codex", "prompt": "edit fixture"}
    contract["read_paths"] = ["fixture.py"]
    contract["external_model"] = {
        "enabled": True,
        "provider": "codex",
        "authorization_policy_path": ".orchestrator/policies/external_model_authorization.json",
        "authorization_id": policy["authorization_id"],
        "policy_hash": policy["policy_hash"],
        "read_scope": ["fixture.py"],
    }
    routing = {
        "economy": {"model": "model-b", "reasoning_effort": "low"},
        "standard": {"model": "model-b", "reasoning_effort": "medium"},
        "deep": {"model": "model-b", "reasoning_effort": "high"},
    }
    factory = RecordingFactory()
    runner = OvernightRunner(
        repository,
        provider_factory=factory,
        provider_capabilities=_fake_capabilities(
            repository,
            {"model-b": {"low", "medium", "high"}},
            provider="codex",
        ),
    )
    state = runner.start(
        _prepare(repository, tmp_path, contracts=[contract], routing=routing)
    )
    assert state["tasks"]["task-01"]["status"] == "BLOCKED"
    assert factory.invocations == []
    assert factory.reviews == []


def test_empty_available_model_set_has_no_session_or_provider_side_effect(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    with pytest.raises(OvernightPlanError, match="no available models"):
        OvernightRunner(repository, provider_factory=factory, provider_capabilities=()).start(
            _prepare(repository, tmp_path)
        )
    assert factory.invocations == []
    assert not (repository / ".orchestrator" / "overnight").exists()


def test_task_source_prohibited_data_scan_prevents_external_provider(
    repository: Path, tmp_path: Path, monkeypatch, write_authorization_policy
) -> None:
    policy = write_authorization_policy(
        repository,
        allowed_models={"gpt-5.6-terra": ["low", "medium", "high"]},
    )
    contract = _contract()
    contract["provider"] = {"type": "codex", "prompt": "edit fixture"}
    contract["read_paths"] = ["fixture.py"]
    contract["external_model"] = {
        "enabled": True,
        "provider": "codex",
        "authorization_policy_path": ".orchestrator/policies/external_model_authorization.json",
        "authorization_id": policy["authorization_id"],
        "policy_hash": policy["policy_hash"],
        "read_scope": ["fixture.py"],
    }
    factory = RecordingFactory()
    runner = OvernightRunner(
        repository,
        provider_factory=factory,
        provider_capabilities=_fake_capabilities(
            repository,
            {"gpt-5.6-terra": {"low", "medium", "high"}},
            provider="codex",
        ),
    )
    original = runner._add_detached_worktree

    def add_secret(path, head):
        original(path, head)
        if path.name == "implementation":
            (path / "source-secret.key").write_text("not for external use\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_add_detached_worktree", add_secret)
    state = runner.start(_prepare(repository, tmp_path, contracts=[contract]))
    assert state["tasks"]["task-01"]["status"] == "BLOCKED"
    assert factory.invocations == []


def test_reviewer_mutation_outside_allowed_paths_invalidates_review(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    factory.review_mutation_path = "README.md"
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path)
    )
    record = state["tasks"]["task-01"]
    assert record["status"] == "WAITING_HUMAN"
    assert record["review"]["status"] == "DIGEST_CHANGED"
    assert _git(Path(state["integration_worktree"]), "status", "--porcelain") == ""


def test_session_commit_disables_repository_hooks_and_never_pushes(
    repository: Path, tmp_path: Path, monkeypatch
) -> None:
    plan = _prepare(repository, tmp_path)
    commit_marker = repository / "commit-hook-ran"
    integration_marker = repository / "integration-checkout-hook-ran"
    task_marker = repository / "task-checkout-hook-ran"
    pre_commit = repository / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text(
        f"#!/bin/sh\ntouch {str(commit_marker)!r}\ngit push origin forbidden\nexit 1\n",
        encoding="utf-8",
    )
    os.chmod(pre_commit, 0o755)
    post_checkout = repository / ".git" / "hooks" / "post-checkout"
    post_checkout.write_text(
        "#!/bin/sh\n"
        f"case \"$PWD\" in\n  */integration) touch {str(integration_marker)!r} ;;\n"
        f"  */tasks/*) touch {str(task_marker)!r} ;;\nesac\n"
        "git push origin forbidden\nexit 1\n",
        encoding="utf-8",
    )
    os.chmod(post_checkout, 0o755)
    calls: list[tuple[str, ...]] = []
    original = overnight_module._git

    def recording(root, *args, **kwargs):
        calls.append(tuple(args))
        return original(root, *args, **kwargs)

    monkeypatch.setattr(overnight_module, "_git", recording)
    state = OvernightRunner(repository).start(plan)
    assert state["status"] == "DONE"
    assert not commit_marker.exists()
    assert not integration_marker.exists()
    assert not task_marker.exists()
    assert all("push" not in call for call in calls)
    worktree_adds = [
        call
        for call in calls
        if "worktree" in call and "add" in call
    ]
    assert len(worktree_adds) >= 2
    assert all(
        call[:2] == ("-c", "core.hooksPath=/dev/null")
        for call in worktree_adds
    )


def test_review_budget_counts_the_exact_prefixed_prompt_before_provider(
    repository: Path, tmp_path: Path
) -> None:
    patch = (
        b"diff --git a/fixture.py b/fixture.py\n"
        b"--- a/fixture.py\n"
        b"+++ b/fixture.py\n"
        b"@@ -1 +1 @@\n"
        b"-VALUE = 1\n"
        b"+VALUE = 2\n"
    )
    metadata = PatchMetadata(1, 1, 2, False, False, False, False)
    assessment = RiskAssessment("LOW", (), metadata)

    def synthetic_implementation(*_args, **_kwargs):
        return patch, ("fixture.py",), metadata, assessment

    first_contract = _contract(auto_integrate=False)
    first_contract["provider"]["nonce"] = "first"
    first_factory = RecordingFactory()
    first_runner = OvernightRunner(repository, provider_factory=first_factory)
    first_runner._implementation_attempt = synthetic_implementation
    first_runner.start(
        _prepare(
            repository,
            tmp_path,
            contracts=[first_contract],
            auto_integrate=False,
        )
    )
    assert first_factory.invocations == []
    context = first_factory.reviews[0]["context"]
    assert isinstance(context, dict)
    context_bytes = len(
        json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    exact_prompt_bytes = len(build_codex_review_prompt(context).encode("utf-8"))
    assert context_bytes < exact_prompt_bytes

    second_contract = _contract(auto_integrate=False)
    second_contract["provider"]["nonce"] = "second"
    limits = {
        "max_tasks": 2,
        "max_provider_calls": 5,
        "max_repair_attempts_per_task": 1,
        "max_elapsed_seconds": 100,
        "max_prompt_bytes_per_call": context_bytes,
        "max_diff_lines_for_auto_integration": 800,
    }
    second_factory = RecordingFactory()
    second_runner = OvernightRunner(repository, provider_factory=second_factory)
    second_runner._implementation_attempt = synthetic_implementation
    state = second_runner.start(
        _prepare(
            repository,
            tmp_path,
            contracts=[second_contract],
            auto_integrate=False,
            limits=limits,
        )
    )
    assert state["status"] == "STOPPED_BUDGET"
    assert state["stop_reason"] == "MAX_PROMPT_BYTES_PER_CALL"
    assert second_factory.invocations == []
    assert second_factory.reviews == []
    assert state["budget"]["provider_calls"] == 0
    assert state["budget"]["prompt_bytes"] == 0


def test_mixed_exact_and_unknown_provider_usage_is_not_reported_as_exact(
    repository: Path, tmp_path: Path
) -> None:
    factory = RecordingFactory()
    factory.invocation_tokens = (3, 5)
    state = OvernightRunner(repository, provider_factory=factory).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    assert state["budget"]["token_usage"] == "unknown"
    assert state["budget"]["known_measured_usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
    }


def test_invalid_generated_session_ref_has_zero_side_effects(
    repository: Path, tmp_path: Path, monkeypatch
) -> None:
    class InvalidUuid:
        hex = "invalid..ref"

    plan = _prepare(repository, tmp_path)
    monkeypatch.setattr(overnight_module.uuid, "uuid4", lambda: InvalidUuid())
    before_branches = _git(repository, "branch", "--format=%(refname)")
    before_worktrees = _git(repository, "worktree", "list", "--porcelain")
    factory = RecordingFactory()
    with pytest.raises(OvernightError, match="INVALID_SESSION_BRANCH"):
        OvernightRunner(repository, provider_factory=factory).start(plan)
    assert factory.invocations == []
    assert before_branches == _git(repository, "branch", "--format=%(refname)")
    assert before_worktrees == _git(repository, "worktree", "list", "--porcelain")
    assert not (repository / ".orchestrator" / "overnight").exists()
