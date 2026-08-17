"""The pipeline graph must be idempotent, durable, and bound to one deck.

These tests exercise the three refusals the design requires: identical work is
recognised rather than re-run, a journal survives the process that wrote it, and
a lineage cannot be resumed against a different deck.
"""

from __future__ import annotations

import json

import pytest

from mage_ptcg.meta_specialist.orchestrator_v1 import (
    LineageBindingV1,
    OrchestratorV1,
    OrchestratorV1Error,
    build_lineage_pipeline_v1,
    declare_all_v1,
    define_task_v1,
    derive_task_id_v1,
)


BINDING = LineageBindingV1(deck_identity="deck-abc123", policy_lineage_id="a" * 64)
OTHER_DECK = LineageBindingV1(deck_identity="deck-different", policy_lineage_id="a" * 64)


def _pipeline():
    return build_lineage_pipeline_v1(
        collect_inputs={"lane": "alakazam", "games": 2000},
        train_inputs={"steps": 1000},
        evaluate_inputs={"cells": 4},
        promote_inputs={"gate": "promotion-v1"},
    )


def _orchestrator(tmp_path, binding=BINDING):
    return OrchestratorV1(tmp_path / "journal.jsonl", binding=binding)


def _drive(orchestrator, task, result=None):
    orchestrator.mark_running(task.task_id)
    orchestrator.mark_completed(task.task_id, result or {"ok": True})


# -- identity ---------------------------------------------------------------


def test_the_same_work_gets_the_same_id_regardless_of_declaration_order() -> None:
    left = derive_task_id_v1(stage="train", inputs={"a": 1, "b": 2}, depends_on=["x", "y"])
    right = derive_task_id_v1(stage="train", inputs={"b": 2, "a": 1}, depends_on=["y", "x"])
    assert left == right


def test_different_upstream_work_is_a_different_task() -> None:
    """Identical train inputs on a different collection is not the same task."""
    base = define_task_v1(stage="collect", inputs={"games": 100})
    other = define_task_v1(stage="collect", inputs={"games": 200})
    train_a = define_task_v1(stage="train", inputs={"steps": 10}, depends_on=(base.task_id,))
    train_b = define_task_v1(stage="train", inputs={"steps": 10}, depends_on=(other.task_id,))
    assert train_a.task_id != train_b.task_id


def test_a_forged_task_id_is_refused() -> None:
    from mage_ptcg.meta_specialist.orchestrator_v1 import OrchestrationTaskV1

    with pytest.raises(OrchestratorV1Error, match="content address"):
        OrchestrationTaskV1(
            task_id="0" * 64, stage="train", inputs={"steps": 1}, depends_on=(),
        )


def test_redeclaring_identical_work_does_not_duplicate_it(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    tasks = _pipeline()
    declare_all_v1(orchestrator, tasks)
    declare_all_v1(orchestrator, tasks)  # a resumed run re-declares the same graph

    assert orchestrator.summary()["tasks"] == 4


# -- dependency ordering ----------------------------------------------------


def test_only_the_first_stage_is_ready_before_anything_completes(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    collect, _train, _evaluate, _promote = declare_all_v1(orchestrator, _pipeline())

    ready = orchestrator.ready_tasks()
    assert [task.task_id for task in ready] == [collect.task_id]


def test_the_pipeline_drains_in_order(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    tasks = declare_all_v1(orchestrator, _pipeline())

    for expected in tasks:
        ready = orchestrator.ready_tasks()
        assert [task.task_id for task in ready] == [expected.task_id]
        _drive(orchestrator, expected)

    assert orchestrator.ready_tasks() == ()
    assert orchestrator.is_complete()


def test_independent_lanes_are_offered_together(tmp_path) -> None:
    """Parallelism is expressed by several tasks being ready at once."""
    orchestrator = _orchestrator(tmp_path)
    lanes = [
        define_task_v1(stage="collect", inputs={"lane": name})
        for name in ("alakazam", "grimmsnarl", "rocket_mewtwo")
    ]
    declare_all_v1(orchestrator, lanes)

    ready = orchestrator.ready_tasks()
    assert {task.task_id for task in ready} == {task.task_id for task in lanes}


def test_a_task_cannot_start_before_its_dependency_completes(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    _collect, train, _evaluate, _promote = declare_all_v1(orchestrator, _pipeline())

    with pytest.raises(OrchestratorV1Error, match="dependency"):
        orchestrator.mark_running(train.task_id)


def test_depending_on_an_undeclared_task_is_refused(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    orphan = define_task_v1(stage="train", inputs={"steps": 1}, depends_on=("f" * 64,))

    with pytest.raises(OrchestratorV1Error, match="not declared"):
        orchestrator.declare(orphan)


def test_a_stage_cannot_depend_on_a_later_stage(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    promote = orchestrator.declare(define_task_v1(stage="promote", inputs={"g": 1}))
    backwards = define_task_v1(stage="collect", inputs={"x": 1}, depends_on=(promote.task_id,))

    with pytest.raises(OrchestratorV1Error, match="cannot depend on a later"):
        orchestrator.declare(backwards)


# -- completion discipline --------------------------------------------------


def test_a_task_that_never_ran_cannot_be_completed(tmp_path) -> None:
    """A completion must come from work that actually reported success."""
    orchestrator = _orchestrator(tmp_path)
    collect, *_ = declare_all_v1(orchestrator, _pipeline())

    with pytest.raises(OrchestratorV1Error, match="only a running task can complete"):
        orchestrator.mark_completed(collect.task_id, {"ok": True})


def test_a_failed_dependency_blocks_the_rest_rather_than_letting_it_run(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    collect, train, evaluate, promote = declare_all_v1(orchestrator, _pipeline())

    orchestrator.mark_running(collect.task_id)
    orchestrator.mark_failed(collect.task_id, "engine faulted on every game")

    assert orchestrator.ready_tasks() == ()
    blocked = {task.task_id for task in orchestrator.blocked_tasks()}
    assert train.task_id in blocked
    assert not orchestrator.is_complete()
    # evaluate/promote are blocked transitively, not directly, so they are not
    # listed until their own dependency has failed.
    assert evaluate.task_id not in blocked and promote.task_id not in blocked


# -- durability -------------------------------------------------------------


def test_state_survives_a_new_process(tmp_path) -> None:
    first = _orchestrator(tmp_path)
    collect, train, _evaluate, _promote = declare_all_v1(first, _pipeline())
    _drive(first, collect, {"games": 2000})

    reopened = _orchestrator(tmp_path)

    assert reopened.state_of(collect.task_id) == "completed"
    assert reopened.result_of(collect.task_id) == {"games": 2000}
    assert [task.task_id for task in reopened.ready_tasks()] == [train.task_id]


def test_a_task_interrupted_while_running_returns_to_pending(tmp_path) -> None:
    """A "running" record is not evidence the work finished."""
    first = _orchestrator(tmp_path)
    collect, *_ = declare_all_v1(first, _pipeline())
    first.mark_running(collect.task_id)  # process dies here

    reopened = _orchestrator(tmp_path)

    assert reopened.state_of(collect.task_id) == "pending"
    assert [task.task_id for task in reopened.ready_tasks()] == [collect.task_id]


def test_resuming_with_a_different_deck_is_refused(tmp_path) -> None:
    first = _orchestrator(tmp_path)
    declare_all_v1(first, _pipeline())

    with pytest.raises(OrchestratorV1Error, match="different deck"):
        _orchestrator(tmp_path, binding=OTHER_DECK)


def test_a_corrupt_journal_raises_instead_of_being_partially_replayed(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    declare_all_v1(orchestrator, _pipeline())
    path = tmp_path / "journal.jsonl"
    path.write_text(path.read_text() + "{not json\n", encoding="utf-8")

    with pytest.raises(OrchestratorV1Error, match="valid JSON"):
        _orchestrator(tmp_path)


def test_the_journal_is_one_canonical_record_per_line(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    collect, *_ = declare_all_v1(orchestrator, _pipeline())
    _drive(orchestrator, collect)

    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in lines if line.strip()]
    assert events[0] == "open"
    assert events.count("declare") == 4
    assert events[-1] == "state"
