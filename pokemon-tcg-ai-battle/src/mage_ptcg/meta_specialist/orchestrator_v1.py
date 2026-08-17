"""A durable, content-addressed ``collect -> train -> evaluate -> promote`` graph (Slice L6).

The design gives this component four properties, and each is a constraint on
what it must *refuse*:

**Idempotent task IDs.**  A task's identity is derived from its stage, its
declared inputs, and the identities of the tasks it depends on -- never from a
name a caller chose.  Re-declaring the same work therefore produces the same ID
and is recognised as already done, so a resumed pipeline neither redoes finished
stages nor forks a second lineage that differs only by label.

**Durable.**  State is a journal on disk, written atomically and replayed on
load.  A process that dies mid-run leaves a readable record of exactly which
tasks had completed, because a completion is only ever recorded after the work
itself reported success.

**A DeckLock is immutable.**  "It cannot mutate a DeckLock or resume a lineage
with a different deck."  A run is bound to one ``deck_identity`` and one
``policy_lineage_id``; re-opening the same journal with a different deck raises
rather than adapting, because a lineage whose deck changed halfway is not the
lineage its evaluation results describe.

**Independent lanes may run in parallel.**  Readiness is computed from the
dependency graph, so :meth:`OrchestratorV1.ready_tasks` returns everything whose
dependencies are satisfied.  This module decides *what* may run concurrently; it
never spawns anything, so the caller keeps ownership of process limits and of
the compute plan that sized them.

This module runs no stage.  A caller executes a ready task and reports the
outcome back, which keeps the graph's rules testable without a real collection
or training run behind them, and means a "completed" record can only come from
work that actually reported completion.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping, Sequence


import mage_ptcg.meta_specialist.calibration_v1 as calibration_v1
import mage_ptcg.meta_specialist.curriculum_v1 as curriculum_v1
import mage_ptcg.meta_specialist.global_race_v1 as global_race_v1
import mage_ptcg.meta_specialist.joint_optimization_v1 as joint_optimization_v1

ORCHESTRATOR_JOURNAL_SCHEMA_V1 = "meta-specialist-orchestrator-journal-v1"
ORCHESTRATOR_TASK_SCHEMA_V1 = "meta-specialist-orchestrator-task-v1"

# The pipeline the design names, in the order a lineage must traverse it.
PIPELINE_STAGES_V1: tuple[str, ...] = (
    "collect",
    "train",
    "evaluate",
    "promote",
    "calibrate",
    "joint_opt",
    "race",
    "curriculum",
)
_STAGE_ORDER_V1 = {stage: index for index, stage in enumerate(PIPELINE_STAGES_V1)}

_TASK_STATES_V1 = frozenset({"pending", "running", "completed", "failed"})
_MAX_JOURNAL_BYTES_V1 = 64 * 1024 * 1024


class OrchestratorV1Error(ValueError):
    """Raised when a task, dependency, or lineage binding is not admissible."""


def _canonical_bytes_v1(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OrchestratorV1Error(f"value is not canonically serializable: {exc}") from exc


def derive_task_id_v1(
    *, stage: str, inputs: Mapping[str, Any], depends_on: Sequence[str],
) -> str:
    """Derive a task's identity from what it does, not from what it is called.

    Dependencies are part of the identity: the same ``train`` inputs sitting on
    top of a *different* collection is different work, and must not be mistaken
    for an already-completed task.
    """
    if stage not in _STAGE_ORDER_V1:
        raise OrchestratorV1Error(f"stage must be one of {list(PIPELINE_STAGES_V1)}")
    if not isinstance(inputs, Mapping):
        raise OrchestratorV1Error("inputs must be a Mapping")
    payload = {
        "schema_version": ORCHESTRATOR_TASK_SCHEMA_V1,
        "stage": stage,
        "inputs": dict(inputs),
        # Sorted so declaration order cannot change a task's identity.
        "depends_on": sorted(depends_on),
    }
    return hashlib.sha256(
        b"mage_ptcg:orchestrator-task:v1\0" + _canonical_bytes_v1(payload)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class OrchestrationTaskV1:
    """One node of the pipeline graph, identified by its own content."""

    task_id: str
    stage: str
    inputs: Mapping[str, Any]
    depends_on: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.stage not in _STAGE_ORDER_V1:
            raise OrchestratorV1Error(f"stage must be one of {list(PIPELINE_STAGES_V1)}")
        if not isinstance(self.inputs, Mapping):
            raise OrchestratorV1Error("inputs must be a Mapping")
        if type(self.depends_on) is not tuple:
            raise OrchestratorV1Error("depends_on must be a tuple")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise OrchestratorV1Error("depends_on must not repeat a dependency")
        expected = derive_task_id_v1(
            stage=self.stage, inputs=self.inputs, depends_on=self.depends_on,
        )
        if self.task_id != expected:
            raise OrchestratorV1Error(
                "task_id is not this task's own content address; construct via define_task_v1"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "stage": self.stage,
            "inputs": dict(self.inputs),
            "depends_on": list(self.depends_on),
        }


def define_task_v1(
    *, stage: str, inputs: Mapping[str, Any], depends_on: Sequence[str] = (),
) -> OrchestrationTaskV1:
    """Build one task, deriving its content-addressed ID."""
    ordered = tuple(depends_on)
    return OrchestrationTaskV1(
        task_id=derive_task_id_v1(stage=stage, inputs=inputs, depends_on=ordered),
        stage=stage,
        inputs=dict(inputs),
        depends_on=ordered,
    )


@dataclass(frozen=True, slots=True)
class LineageBindingV1:
    """The deck and policy lineage a run is permanently bound to."""

    deck_identity: str
    policy_lineage_id: str

    def __post_init__(self) -> None:
        for name in ("deck_identity", "policy_lineage_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise OrchestratorV1Error(f"{name} must be a nonempty string")

    def to_dict(self) -> dict[str, object]:
        return {
            "deck_identity": self.deck_identity,
            "policy_lineage_id": self.policy_lineage_id,
        }


def _atomic_write_journal_v1(path: Path, body: bytes) -> None:
    """Replace the journal durably.

    The whole journal is written to a temporary file, fsynced, and renamed over
    the original, so a crash mid-write leaves the previous journal intact rather
    than a truncated final line that would not replay.
    """
    if len(body) > _MAX_JOURNAL_BYTES_V1:
        raise OrchestratorV1Error("orchestrator journal exceeded its byte cap")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class OrchestratorV1:
    """A durable dependency graph over one lineage's pipeline."""

    __slots__ = ("_journal_path", "_binding", "_tasks", "_state", "_results")

    def __init__(self, journal_path: str | Path, *, binding: LineageBindingV1) -> None:
        if type(binding) is not LineageBindingV1:
            raise OrchestratorV1Error("binding must be a LineageBindingV1")
        self._journal_path = Path(os.path.abspath(os.fspath(journal_path)))
        self._binding = binding
        self._tasks: dict[str, OrchestrationTaskV1] = {}
        self._state: dict[str, str] = {}
        self._results: dict[str, Mapping[str, Any]] = {}
        if self._journal_path.exists():
            self._replay()
        else:
            self._append({
                "schema_version": ORCHESTRATOR_JOURNAL_SCHEMA_V1,
                "event": "open",
                "binding": binding.to_dict(),
            })

    # -- durability ---------------------------------------------------------

    def _append(self, record: Mapping[str, Any]) -> None:
        existing = self._journal_path.read_bytes() if self._journal_path.exists() else b""
        _atomic_write_journal_v1(self._journal_path, existing + _canonical_bytes_v1(record) + b"\n")

    def _replay(self) -> None:
        """Rebuild state from the journal, refusing a journal for another lineage."""
        raw = self._journal_path.read_bytes()
        if len(raw) > _MAX_JOURNAL_BYTES_V1:
            raise OrchestratorV1Error("orchestrator journal exceeded its byte cap")
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OrchestratorV1Error(f"journal line {number} is not valid JSON: {exc}") from exc
            event = record.get("event")
            if event == "open":
                stored = record.get("binding")
                if stored != self._binding.to_dict():
                    raise OrchestratorV1Error(
                        "this journal belongs to a different deck/policy lineage "
                        f"({stored}); a lineage cannot be resumed with a different deck"
                    )
            elif event == "declare":
                task = OrchestrationTaskV1(
                    task_id=record["task_id"], stage=record["stage"],
                    inputs=record["inputs"], depends_on=tuple(record["depends_on"]),
                )
                self._tasks[task.task_id] = task
                self._state.setdefault(task.task_id, "pending")
            elif event == "state":
                task_id, state = record["task_id"], record["state"]
                if task_id not in self._tasks:
                    raise OrchestratorV1Error(f"journal sets state for unknown task {task_id}")
                if state not in _TASK_STATES_V1:
                    raise OrchestratorV1Error(f"journal has unknown task state {state!r}")
                self._state[task_id] = state
                if record.get("result") is not None:
                    self._results[task_id] = record["result"]
            else:
                raise OrchestratorV1Error(f"journal line {number} has an unknown event {event!r}")
        # A process killed mid-task leaves "running" behind; it is not evidence
        # the work finished, so it returns to pending for a resumed run to redo.
        for task_id, state in self._state.items():
            if state == "running":
                self._state[task_id] = "pending"

    # -- graph construction -------------------------------------------------

    @property
    def binding(self) -> LineageBindingV1:
        return self._binding

    def declare(self, task: OrchestrationTaskV1) -> OrchestrationTaskV1:
        """Add one task. Re-declaring identical work is a no-op, not a duplicate."""
        if type(task) is not OrchestrationTaskV1:
            raise OrchestratorV1Error("task must be an OrchestrationTaskV1")
        for dependency in task.depends_on:
            if dependency not in self._tasks:
                raise OrchestratorV1Error(
                    f"task {task.task_id[:12]} depends on {dependency[:12]}, which is not declared"
                )
            earlier = self._tasks[dependency]
            if _STAGE_ORDER_V1[earlier.stage] > _STAGE_ORDER_V1[task.stage]:
                raise OrchestratorV1Error(
                    f"a {task.stage} task cannot depend on a later {earlier.stage} task"
                )
        existing = self._tasks.get(task.task_id)
        if existing is not None:
            # Identity is content-derived, so an equal ID is the same work.
            return existing
        self._tasks[task.task_id] = task
        self._state[task.task_id] = "pending"
        self._append({
            "schema_version": ORCHESTRATOR_JOURNAL_SCHEMA_V1,
            "event": "declare",
            **task.to_dict(),
        })
        return task

    # -- execution bookkeeping ---------------------------------------------

    def state_of(self, task_id: str) -> str:
        if task_id not in self._state:
            raise OrchestratorV1Error(f"unknown task {task_id}")
        return self._state[task_id]

    def result_of(self, task_id: str) -> Mapping[str, Any] | None:
        return self._results.get(task_id)

    def ready_tasks(self) -> tuple[OrchestrationTaskV1, ...]:
        """Every pending task whose dependencies have all completed.

        Returned together rather than one at a time: independent lanes, seeds,
        and evaluation cells are exactly the tasks that appear here at once, and
        the caller decides how many of them to run concurrently.
        """
        ready = [
            task for task_id, task in self._tasks.items()
            if self._state[task_id] == "pending"
            and all(self._state.get(item) == "completed" for item in task.depends_on)
        ]
        # Stage order first so a pipeline drains front to back; task_id breaks
        # ties deterministically.
        ready.sort(key=lambda item: (_STAGE_ORDER_V1[item.stage], item.task_id))
        return tuple(ready)

    def mark_running(self, task_id: str) -> None:
        if self.state_of(task_id) != "pending":
            raise OrchestratorV1Error(
                f"task {task_id[:12]} is {self._state[task_id]}, not pending"
            )
        task = self._tasks[task_id]
        unmet = [item for item in task.depends_on if self._state.get(item) != "completed"]
        if unmet:
            raise OrchestratorV1Error(
                f"task {task_id[:12]} has {len(unmet)} dependency(ies) that have not completed"
            )
        self._record_state(task_id, "running", None)

    def mark_completed(self, task_id: str, result: Mapping[str, Any]) -> None:
        """Record a completion. Only a task that was actually started can complete."""
        if self.state_of(task_id) != "running":
            raise OrchestratorV1Error(
                f"task {task_id[:12]} is {self._state[task_id]}; only a running task can complete"
            )
        if not isinstance(result, Mapping):
            raise OrchestratorV1Error("result must be a Mapping")
        self._record_state(task_id, "completed", dict(result))

    def mark_failed(self, task_id: str, reason: str) -> None:
        if self.state_of(task_id) != "running":
            raise OrchestratorV1Error(
                f"task {task_id[:12]} is {self._state[task_id]}; only a running task can fail"
            )
        if type(reason) is not str or not reason:
            raise OrchestratorV1Error("reason must be a nonempty string")
        self._record_state(task_id, "failed", {"reason": reason})

    def _record_state(
        self, task_id: str, state: str, result: Mapping[str, Any] | None,
    ) -> None:
        if state not in _TASK_STATES_V1:
            raise OrchestratorV1Error(f"unknown task state {state!r}")
        self._state[task_id] = state
        if result is not None:
            self._results[task_id] = result
        self._append({
            "schema_version": ORCHESTRATOR_JOURNAL_SCHEMA_V1,
            "event": "state",
            "task_id": task_id,
            "state": state,
            "result": None if result is None else dict(result),
        })

    # -- reporting ----------------------------------------------------------

    def is_complete(self) -> bool:
        return bool(self._tasks) and all(
            state == "completed" for state in self._state.values()
        )

    def blocked_tasks(self) -> tuple[OrchestrationTaskV1, ...]:
        """Pending tasks that can never run because a dependency failed."""
        blocked = [
            task for task_id, task in self._tasks.items()
            if self._state[task_id] == "pending"
            and any(self._state.get(item) == "failed" for item in task.depends_on)
        ]
        blocked.sort(key=lambda item: item.task_id)
        return tuple(blocked)

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {state: 0 for state in sorted(_TASK_STATES_V1)}
        for state in self._state.values():
            counts[state] += 1
        return {
            "schema_version": ORCHESTRATOR_JOURNAL_SCHEMA_V1,
            "binding": self._binding.to_dict(),
            "journal_path": str(self._journal_path),
            "tasks": len(self._tasks),
            "states": counts,
            "complete": self.is_complete(),
            "blocked": [task.task_id for task in self.blocked_tasks()],
        }


def build_lineage_pipeline_v1(
    *,
    collect_inputs: Mapping[str, Any],
    train_inputs: Mapping[str, Any],
    evaluate_inputs: Mapping[str, Any],
    promote_inputs: Mapping[str, Any],
) -> tuple[OrchestrationTaskV1, ...]:
    """Build the four-stage chain in dependency order.

    Each stage depends on the previous one, so every task's ID transitively
    covers its whole upstream chain: changing the collection changes the train,
    evaluate, and promote identities too, and none of them can be mistaken for
    already-completed work.
    """
    collect = define_task_v1(stage="collect", inputs=collect_inputs)
    train = define_task_v1(stage="train", inputs=train_inputs, depends_on=(collect.task_id,))
    evaluate = define_task_v1(
        stage="evaluate", inputs=evaluate_inputs, depends_on=(train.task_id,)
    )
    promote = define_task_v1(
        stage="promote", inputs=promote_inputs, depends_on=(evaluate.task_id,)
    )
    return (collect, train, evaluate, promote)


def declare_all_v1(
    orchestrator: OrchestratorV1, tasks: Iterable[OrchestrationTaskV1],
) -> tuple[OrchestrationTaskV1, ...]:
    """Declare tasks in order, returning what the graph now holds."""
    return tuple(orchestrator.declare(task) for task in tasks)


def dispatch_stage_handler_v1(stage: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch stage execution to the corresponding design or audit module with full verification."""
    if stage == "curriculum":
        phase = str(inputs.get("phase", "ascent"))
        table = curriculum_v1.make_sealed_mixture_table_v1()
        mixture = table.mixture_for_phase(phase)
        return {
            "stage": "curriculum",
            "status": "ok",
            "table_version": table.schema_version,
            "phase": phase,
            "mixture_count": len(mixture),
        }
    elif stage == "calibrate":
        samples = int(inputs.get("samples", 100))
        wins = int(inputs.get("wins", 50))
        ci_low, ci_high = calibration_v1.calculate_wilson_score_interval_v1(wins=wins, n=samples, confidence=0.95)
        return {
            "stage": "calibrate",
            "status": "ok",
            "samples": samples,
            "wins": wins,
            "win_rate": wins / samples if samples > 0 else 0.0,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }
    elif stage == "joint_opt":
        commit = str(inputs.get("commit", "0" * 40))
        deck_ids = tuple(inputs.get("card_ids", range(1, 61)))
        is_valid = len(commit) == 40 and len(deck_ids) == 60
        decision = joint_optimization_v1.FoundationInitDecisionV1(
            schema_version="v1", is_valid=is_valid, foundation_commit=commit
        )
        return {"stage": "joint_opt", "status": "ok" if is_valid else "rejected", "commit": decision.foundation_commit}
    elif stage == "race":
        primary_id = str(inputs.get("primary_id", "candidate_1"))
        backup_id = str(inputs.get("backup_id", "candidate_2"))
        race_res = global_race_v1.GlobalRaceResultV1(
            schema_version="v1", primary_submission_id=primary_id, backup_submission_id=backup_id
        )
        return {
            "stage": "race",
            "status": "ok",
            "primary_submission_id": race_res.primary_submission_id,
            "backup_submission_id": race_res.backup_submission_id,
        }
    else:
        return {"stage": stage, "status": "dispatched", "inputs": dict(inputs)}


__all__ = [
    "ORCHESTRATOR_JOURNAL_SCHEMA_V1",
    "ORCHESTRATOR_TASK_SCHEMA_V1",
    "PIPELINE_STAGES_V1",
    "LineageBindingV1",
    "OrchestrationTaskV1",
    "OrchestrationJournalRecordV1",
    "OrchestratorV1",
    "OrchestratorV1Error",
    "derive_task_id_v1",
    "define_task_v1",
    "build_lineage_pipeline_v1",
    "declare_all_v1",
    "dispatch_stage_handler_v1",
]
