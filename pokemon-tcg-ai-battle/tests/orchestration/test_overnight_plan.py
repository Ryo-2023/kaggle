from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.orchestration.overnight_plan import OvernightPlanError, load_plan
from .test_overnight_mvp import _prepare


def _rewrite(path: Path, mutate) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("case", ["duplicate-id", "missing", "self", "duplicate-dep", "cycle"])
def test_dependency_graph_is_strict(
    repository: Path, tmp_path: Path, case: str
) -> None:
    plan = _prepare(repository, tmp_path)

    def mutate(value):
        first = value["tasks"][0]
        second = {**first, "id": "task-02", "title": "second"}
        if case == "duplicate-id":
            second["id"] = first["id"]
        elif case == "missing":
            second["depends_on"] = ["missing-task"]
        elif case == "self":
            second["depends_on"] = ["task-02"]
        elif case == "duplicate-dep":
            second["depends_on"] = ["task-01", "task-01"]
        else:
            first["depends_on"] = ["task-02"]
            second["depends_on"] = ["task-01"]
        value["tasks"].append(second)

    _rewrite(plan, mutate)
    with pytest.raises(OvernightPlanError):
        load_plan(plan, repository, {"gpt-5.6-terra"})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["limits"].update({"max_tasks": 0}),
        lambda value: value["limits"].update({"max_provider_calls": -1}),
        lambda value: value["routing"]["economy"].update({"reasoning_effort": "high"}),
        lambda value: value["tasks"][0].update({"contract": "../unsafe.json"}),
        lambda value: value["tasks"][0].update({"contract": "missing.json"}),
    ],
)
def test_limits_routing_and_contract_paths_fail_closed(
    repository: Path, tmp_path: Path, mutation
) -> None:
    plan = _prepare(repository, tmp_path)
    _rewrite(plan, mutation)
    with pytest.raises(OvernightPlanError):
        load_plan(plan, repository, {"gpt-5.6-terra"})


def test_repository_sample_plan_matches_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    plan = load_plan(
        root / "examples" / "overnight-plan.example.json",
        root,
        {"gpt-5.6-terra"},
    )
    assert plan.version == 1
    assert plan.auto_integrate_low_risk is False
