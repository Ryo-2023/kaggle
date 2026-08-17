"""durable priority queue、dedupe、backpressure、resource budget。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import LeagueContractError, atomic_write_json, content_id, load_json, utc_now


TASK_PRIORITIES = {
    "SEALED_EVALUATION": 0,
    "VISIBLE_EVALUATION": 10,
    "CHECKPOINT_PUBLISH": 20,
    "ROLLOVER_BOOTSTRAP": 30,
    "PSRO_EXPANSION": 35,
    "EXPERIENCE_COLLECTION": 40,
    "CATALOG_REFRESH": 45,
    "SOURCE_REFRESH": 50,
    "CALIBRATION": 60,
    "REPORT": 70,
}


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    cpu_slots: int = 1
    gpu_slots: int = 0

    def __post_init__(self) -> None:
        if self.cpu_slots < 0 or self.gpu_slots < 0:
            raise LeagueContractError("resource slots must be non-negative")


@dataclass(frozen=True, slots=True)
class QueueItem:
    task_id: str
    task_type: str
    payload: dict[str, Any]
    resources: ResourceRequest
    priority: int
    state: str
    attempts: int
    created_at: str
    updated_at: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        return document

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QueueItem":
        return cls(
            task_id=str(payload["task_id"]),
            task_type=str(payload["task_type"]),
            payload=dict(payload["payload"]),
            resources=ResourceRequest(**payload["resources"]),
            priority=int(payload["priority"]),
            state=str(payload["state"]),
            attempts=int(payload["attempts"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            error=payload.get("error"),
        )


class DurableScheduler:
    def __init__(
        self,
        state_path: Path,
        *,
        cpu_slots: int = 1,
        gpu_slots: int = 0,
        max_pending_evaluations: int | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.cpu_slots = cpu_slots
        self.gpu_slots = gpu_slots
        self.max_pending_evaluations = max_pending_evaluations
        if max_pending_evaluations is not None and max_pending_evaluations < 1:
            raise LeagueContractError(
                "max_pending_evaluations must be positive or unlimited"
            )
        self.items: dict[str, QueueItem] = {}
        if self.state_path.exists():
            payload = load_json(self.state_path)
            if payload.get("schema_version") != 1:
                raise LeagueContractError("unsupported scheduler state")
            self.items = {
                item["task_id"]: QueueItem.from_dict(item)
                for item in payload["items"]
            }

    def _save(self) -> None:
        atomic_write_json(
            self.state_path,
            {
                "schema_version": 1,
                "cpu_slots": self.cpu_slots,
                "gpu_slots": self.gpu_slots,
                "max_pending_evaluations": self.max_pending_evaluations,
                "items": [
                    item.to_dict()
                    for item in sorted(
                        self.items.values(),
                        key=lambda value: (value.priority, value.created_at, value.task_id),
                    )
                ],
            },
        )

    def enqueue(
        self,
        task_type: str,
        payload: Mapping[str, Any],
        *,
        resources: ResourceRequest = ResourceRequest(),
        priority: int | None = None,
    ) -> QueueItem:
        if task_type not in TASK_PRIORITIES:
            raise LeagueContractError(f"unknown task type: {task_type}")
        identity = {"task_type": task_type, "payload": dict(payload)}
        task_id = content_id("continuous-task-v1", identity)
        existing = self.items.get(task_id)
        if existing is not None:
            return existing
        if task_type == "VISIBLE_EVALUATION":
            pending = sorted(
                (
                    item
                    for item in self.items.values()
                    if item.task_type == task_type and item.state == "PENDING"
                ),
                key=lambda item: item.created_at,
            )
            if (
                self.max_pending_evaluations is not None
                and len(pending) >= self.max_pending_evaluations
            ):
                raise LeagueContractError("visible evaluation backpressure limit reached")
        now = utc_now()
        item = QueueItem(
            task_id=task_id,
            task_type=task_type,
            payload=dict(payload),
            resources=resources,
            priority=TASK_PRIORITIES[task_type] if priority is None else priority,
            state="PENDING",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self.items[task_id] = item
        self._save()
        return item

    def next_runnable(
        self,
        *,
        running: Iterable[QueueItem] = (),
    ) -> QueueItem | None:
        used_cpu = sum(item.resources.cpu_slots for item in running)
        used_gpu = sum(item.resources.gpu_slots for item in running)
        for item in sorted(
            self.items.values(),
            key=lambda value: (value.priority, value.created_at, value.task_id),
        ):
            if item.state != "PENDING":
                continue
            if (
                used_cpu + item.resources.cpu_slots <= self.cpu_slots
                and used_gpu + item.resources.gpu_slots <= self.gpu_slots
            ):
                return item
        return None

    def recover_interrupted(self) -> int:
        """Return tasks left RUNNING by a confirmed-stopped controller to PENDING.

        This is intentionally explicit: automatically reclaiming a RUNNING task
        could duplicate work while a previous controller process is still alive.
        """
        interrupted = [
            item for item in self.items.values() if item.state == "RUNNING"
        ]
        if not interrupted:
            return 0
        now = utc_now()
        for item in interrupted:
            self.items[item.task_id] = QueueItem(
                task_id=item.task_id,
                task_type=item.task_type,
                payload=item.payload,
                resources=item.resources,
                priority=item.priority,
                state="PENDING",
                attempts=item.attempts,
                created_at=item.created_at,
                updated_at=now,
                error="requeued after explicit interrupted-controller recovery",
            )
        self._save()
        return len(interrupted)

    def transition(
        self, task_id: str, state: str, *, error: str | None = None
    ) -> QueueItem:
        if state not in {"RUNNING", "COMPLETE", "FAILED", "PENDING"}:
            raise LeagueContractError(f"unsupported task state: {state}")
        item = self.items.get(task_id)
        if item is None:
            raise LeagueContractError(f"unknown task: {task_id}")
        attempts = item.attempts + int(state == "RUNNING")
        updated = QueueItem(
            task_id=item.task_id,
            task_type=item.task_type,
            payload=item.payload,
            resources=item.resources,
            priority=item.priority,
            state=state,
            attempts=attempts,
            created_at=item.created_at,
            updated_at=utc_now(),
            error=error,
        )
        self.items[task_id] = updated
        self._save()
        return updated
