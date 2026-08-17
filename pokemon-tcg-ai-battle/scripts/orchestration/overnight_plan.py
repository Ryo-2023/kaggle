"""Strict, versioned input contract for unattended overnight sessions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .contract_validation import validate_task_contract
from .policy import normalize_relative
from .schemas import TaskContract


class OvernightPlanError(ValueError):
    """Raised before a session or Git ref is created for an unsafe plan."""


_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COMPLEXITIES = frozenset({"simple", "normal", "complex", "algorithm"})
_RISKS = frozenset({"low", "medium", "high"})
_TIERS = ("economy", "standard", "deep")
_EXPECTED_EFFORT = {"economy": "low", "standard": "medium", "deep": "high"}
_LIMIT_CAPS = {
    "max_tasks": 32,
    "max_provider_calls": 96,
    "max_repair_attempts_per_task": 3,
    "max_elapsed_seconds": 86_400,
    "max_prompt_bytes_per_call": 1_000_000,
    "max_diff_lines_for_auto_integration": 20_000,
}
_ROOT_FIELDS = {
    "version",
    "name",
    "base_branch",
    "session_branch_prefix",
    "auto_integrate_low_risk",
    "limits",
    "routing",
    "protected_paths",
    "low_risk_allowlist",
    "tasks",
}
_TASK_FIELDS = {
    "id",
    "title",
    "contract",
    "depends_on",
    "complexity",
    "risk_hint",
    "max_repair_attempts",
}


def _object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OvernightPlanError(f"{where} must be an object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise OvernightPlanError(
            f"{where} fields are invalid (missing={sorted(missing)}, unknown={sorted(extra)})"
        )


def _safe_relative(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise OvernightPlanError(f"{where} must be a non-empty relative path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise OvernightPlanError(f"{where} is unsafe")
    try:
        return normalize_relative(candidate.as_posix())
    except Exception as exc:
        raise OvernightPlanError(f"{where} is unsafe") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ModelRoute:
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class OvernightTask:
    id: str
    title: str
    contract: str
    depends_on: tuple[str, ...]
    complexity: str
    risk_hint: str
    max_repair_attempts: int
    contract_sha256: str


@dataclass(frozen=True)
class OvernightPlan:
    version: int
    name: str
    base_branch: str
    session_branch_prefix: str
    auto_integrate_low_risk: bool
    limits: dict[str, int]
    routing: dict[str, ModelRoute]
    protected_paths: tuple[str, ...]
    low_risk_allowlist: tuple[str, ...]
    tasks: tuple[OvernightTask, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, raw: Mapping[str, Any]) -> "OvernightPlan":
        """Rebuild an already validated snapshot without consulting mutable files."""

        try:
            routing = {
                tier: ModelRoute(**dict(raw["routing"][tier])) for tier in _TIERS
            }
            tasks = tuple(OvernightTask(**dict(item)) for item in raw["tasks"])
            return cls(
                version=int(raw["version"]),
                name=str(raw["name"]),
                base_branch=str(raw["base_branch"]),
                session_branch_prefix=str(raw["session_branch_prefix"]),
                auto_integrate_low_risk=bool(raw["auto_integrate_low_risk"]),
                limits={str(key): int(value) for key, value in raw["limits"].items()},
                routing=routing,
                protected_paths=tuple(raw["protected_paths"]),
                low_risk_allowlist=tuple(raw["low_risk_allowlist"]),
                tasks=tasks,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OvernightPlanError("plan snapshot schema is invalid") from exc


def _parse_limits(raw: object) -> dict[str, int]:
    value = _object(raw, "limits")
    _exact_fields(value, set(_LIMIT_CAPS), "limits")
    result: dict[str, int] = {}
    for name, cap in _LIMIT_CAPS.items():
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int) or not 0 < item <= cap:
            raise OvernightPlanError(f"limits.{name} must be between 1 and {cap}")
        result[name] = item
    return result


def _parse_routing(
    raw: object,
    available_models: set[str],
    available_profiles: set[tuple[str, str]] | None = None,
) -> dict[str, ModelRoute]:
    value = _object(raw, "routing")
    _exact_fields(value, set(_TIERS), "routing")
    result: dict[str, ModelRoute] = {}
    for tier in _TIERS:
        route = _object(value[tier], f"routing.{tier}")
        _exact_fields(route, {"model", "reasoning_effort"}, f"routing.{tier}")
        model = route["model"]
        effort = route["reasoning_effort"]
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
            raise OvernightPlanError(f"routing.{tier}.model is invalid")
        if model not in available_models:
            raise OvernightPlanError(f"requested model is unavailable: {model}")
        if effort != _EXPECTED_EFFORT[tier]:
            raise OvernightPlanError(
                f"routing.{tier}.reasoning_effort must be {_EXPECTED_EFFORT[tier]}"
            )
        if available_profiles is not None and (model, str(effort)) not in available_profiles:
            raise OvernightPlanError(
                f"requested model profile is unavailable: {model}/{effort}"
            )
        result[tier] = ModelRoute(model, str(effort))
    return result


def _parse_paths(raw: object, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise OvernightPlanError(f"{where} must be a list of paths")
    values = tuple(_safe_relative(item, f"{where}[]") for item in raw)
    if len(values) != len(set(values)):
        raise OvernightPlanError(f"{where} contains duplicates")
    return values


def load_plan(
    path: Path,
    repository_root: Path,
    available_models: set[str],
    available_profiles: set[tuple[str, str]] | None = None,
) -> OvernightPlan:
    """Validate the complete plan before creating a session, ref, or worktree."""

    if not available_models:
        raise OvernightPlanError("no available models")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OvernightPlanError("cannot read a valid UTF-8 JSON plan") from exc
    value = _object(raw, "plan")
    _exact_fields(value, _ROOT_FIELDS, "plan")
    if value["version"] != 1:
        raise OvernightPlanError("only overnight plan version 1 is supported")
    name = value["name"]
    if not isinstance(name, str) or not _ID.fullmatch(name):
        raise OvernightPlanError("name is invalid")
    branch = value["base_branch"]
    if (
        not isinstance(branch, str)
        or not branch
        or branch.startswith("-")
        or any(character.isspace() for character in branch)
        or ".." in branch
    ):
        raise OvernightPlanError("base_branch is invalid")
    prefix = value["session_branch_prefix"]
    if (
        not isinstance(prefix, str)
        or not prefix.startswith("overnight/")
        or not prefix.endswith("/")
        or ".." in prefix
        or any(character.isspace() for character in prefix)
    ):
        raise OvernightPlanError("session_branch_prefix must be a safe overnight/ prefix")
    if not isinstance(value["auto_integrate_low_risk"], bool):
        raise OvernightPlanError("auto_integrate_low_risk must be boolean")

    limits = _parse_limits(value["limits"])
    routing = _parse_routing(value["routing"], available_models, available_profiles)
    protected_paths = _parse_paths(value["protected_paths"], "protected_paths")
    low_risk_allowlist = _parse_paths(
        value["low_risk_allowlist"], "low_risk_allowlist"
    )
    tasks_raw = value["tasks"]
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise OvernightPlanError("tasks must be a non-empty list")
    if len(tasks_raw) > limits["max_tasks"]:
        raise OvernightPlanError("task count exceeds max_tasks")

    root = repository_root.resolve()
    tasks: list[OvernightTask] = []
    identifiers: set[str] = set()
    for index, raw_task in enumerate(tasks_raw):
        task = _object(raw_task, f"tasks[{index}]")
        _exact_fields(task, _TASK_FIELDS, f"tasks[{index}]")
        task_id = task["id"]
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise OvernightPlanError(f"tasks[{index}].id is invalid")
        if task_id in identifiers:
            raise OvernightPlanError("task IDs must be unique")
        identifiers.add(task_id)
        title = task["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise OvernightPlanError(f"tasks[{index}].title is invalid")
        contract_relative = _safe_relative(task["contract"], f"tasks[{index}].contract")
        contract_path = (root / contract_relative).resolve(strict=False)
        if (
            not contract_path.is_relative_to(root)
            or not contract_path.is_file()
            or contract_path.is_symlink()
        ):
            raise OvernightPlanError(
                f"tasks[{index}].contract must name a safe existing regular file"
            )
        try:
            contract = TaskContract.from_json_file(contract_path)
            validate_task_contract(root, contract)
        except Exception as exc:
            raise OvernightPlanError(f"invalid task contract: {contract_relative}") from exc
        dependencies = task["depends_on"]
        if (
            not isinstance(dependencies, list)
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in dependencies)
            or len(dependencies) != len(set(dependencies))
            or task_id in dependencies
        ):
            raise OvernightPlanError(f"tasks[{index}].depends_on is invalid")
        complexity = task["complexity"]
        risk = task["risk_hint"]
        if complexity not in _COMPLEXITIES or risk not in _RISKS:
            raise OvernightPlanError(f"tasks[{index}] complexity or risk is invalid")
        repairs = task["max_repair_attempts"]
        if (
            isinstance(repairs, bool)
            or not isinstance(repairs, int)
            or repairs < 0
            or repairs > limits["max_repair_attempts_per_task"]
        ):
            raise OvernightPlanError(f"tasks[{index}].max_repair_attempts is invalid")
        tasks.append(
            OvernightTask(
                id=task_id,
                title=title.strip(),
                contract=contract_relative,
                depends_on=tuple(dependencies),
                complexity=str(complexity),
                risk_hint=str(risk),
                max_repair_attempts=repairs,
                contract_sha256=_sha256(contract_path),
            )
        )

    graph = {task.id: task.depends_on for task in tasks}
    for task in tasks:
        if any(dependency not in identifiers for dependency in task.depends_on):
            raise OvernightPlanError(f"task {task.id} has a missing dependency")
    visiting: set[str] = set()
    complete: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise OvernightPlanError("task dependencies contain a cycle")
        if node in complete:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        complete.add(node)

    for node in graph:
        visit(node)
    return OvernightPlan(
        version=1,
        name=name,
        base_branch=branch,
        session_branch_prefix=prefix,
        auto_integrate_low_risk=value["auto_integrate_low_risk"],
        limits=limits,
        routing=routing,
        protected_paths=protected_paths,
        low_risk_allowlist=low_risk_allowlist,
        tasks=tuple(tasks),
    )
