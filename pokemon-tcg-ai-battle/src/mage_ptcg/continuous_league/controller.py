"""学習と分離された event-driven orchestration controller。"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from .contracts import LeagueContractError, atomic_write_json, load_json
from .scheduler import DurableScheduler, QueueItem, ResourceRequest


TaskHandler = Callable[[QueueItem], Mapping[str, Any]]
StatusReporter = Callable[[DurableScheduler], None]


def _percent(value: object) -> str:
    return "--" if value is None else f"{float(value) * 100:.2f}%"


def _interval(value: object) -> str:
    if not isinstance(value, list) or len(value) != 2:
        return "--"
    return f"{float(value[0]) * 100:.2f}–{float(value[1]) * 100:.2f}%"


def _delta(value: object) -> str:
    return "--" if value is None else f"{float(value) * 100:+.2f}pp"


def format_checkpoint_benchmark_terminal_summary(
    *, history_root: Path, scheduler: DurableScheduler, max_rows: int = 10
) -> str:
    """Build one compact terminal view from persisted benchmark artifacts."""
    if max_rows < 1:
        raise LeagueContractError("checkpoint benchmark terminal rows must be positive")
    summary_path = Path(history_root) / "evaluation_summary.json"
    history = load_json(summary_path).get("history", []) if summary_path.exists() else []
    if not isinstance(history, list):
        raise LeagueContractError("checkpoint evaluation summary history is invalid")
    counts = {
        state: sum(item.state == state for item in scheduler.items.values())
        for state in ("COMPLETE", "RUNNING", "PENDING", "FAILED")
    }
    running_steps = [
        str(item.payload["training_step"])
        for item in scheduler.items.values()
        if item.state == "RUNNING" and "training_step" in item.payload
    ]
    lines = [
        "checkpoint benchmark",
        "queue: "
        f"complete={counts['COMPLETE']} running={counts['RUNNING']} "
        f"pending={counts['PENDING']} failed={counts['FAILED']}"
        + (f" | running step: {', '.join(running_steps)}" if running_steps else ""),
        "step | score | 95% CI | worst | fault | delta",
    ]
    rows = history[-max_rows:]
    if not rows:
        lines.append("(completed checkpoint evaluation is not available yet)")
    for row in rows:
        eligible = (
            row.get("aggregate_status") == "COMPLETE"
            and row.get("fault_count") == 0
            and row.get("is_schedule_complete") is True
        )
        lines.append(
            f"{int(row['training_step']):,} | "
            f"{_percent(row.get('game_weighted_score_rate')) if eligible else '--'} | "
            f"{_interval(row.get('game_weighted_wilson_95')) if eligible else '--'} | "
            f"{_percent(row.get('worst_opponent_score_rate')) if eligible else '--'} | "
            f"{int(row.get('fault_count', 0))} | "
            f"{_delta(row.get('score_delta_from_previous_complete')) if eligible else '--'}"
        )
    return "\n".join(lines)


def render_checkpoint_benchmark_terminal_summary(
    *, history_root: Path, scheduler: DurableScheduler, stream: TextIO
) -> None:
    """Redraw the compact view only on an interactive terminal."""
    if not stream.isatty():
        return
    stream.write("\x1b[2J\x1b[H")
    stream.write(
        format_checkpoint_benchmark_terminal_summary(
            history_root=history_root, scheduler=scheduler
        )
        + "\n\n"
    )
    stream.flush()


class SubprocessTaskHandler:
    """task request file を別 process へ渡し、model を controller に載せない。"""

    def __init__(
        self,
        command: Sequence[str],
        request_root: Path,
        *,
        forward_output: bool = False,
        quiet_result: bool = False,
    ) -> None:
        if not command:
            raise LeagueContractError("subprocess handler command is empty")
        self.command = tuple(command)
        self.request_root = Path(request_root)
        self.forward_output = forward_output
        self.quiet_result = quiet_result

    def __call__(self, item: QueueItem) -> Mapping[str, Any]:
        request_path = self.request_root / f"{item.task_id}.json"
        result_path = self.request_root / f"{item.task_id}.result.json"
        atomic_write_json(request_path, item.to_dict())
        command = [
            *self.command,
            "--task-request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        if self.quiet_result:
            command.append("--quiet")
        options: dict[str, Any] = {"text": True, "check": False}
        if not self.forward_output:
            options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        completed = subprocess.run(command, **options)
        if completed.returncode:
            detail = (
                completed.stderr.strip()[-500:]
                if not self.forward_output
                else "see forwarded task output"
            )
            raise LeagueContractError(
                f"task subprocess failed ({completed.returncode}): "
                f"{detail}"
            )
        result = load_json(result_path)
        if result.get("task_id") != item.task_id:
            raise LeagueContractError("task subprocess result identity mismatch")
        return result


class ContinuousLeagueController:
    def __init__(
        self,
        *,
        root: Path,
        checkpoint_event_dir: Path,
        task_event_dir: Path | None = None,
        visible_benchmark_id: str,
        exposure_snapshot_id: str,
        handlers: Mapping[str, TaskHandler],
        cpu_slots: int = 1,
        gpu_slots: int = 0,
        max_pending_evaluations: int | None = None,
    ) -> None:
        self.root = Path(root)
        self.checkpoint_event_dir = Path(checkpoint_event_dir)
        self.task_event_dir = Path(task_event_dir) if task_event_dir else None
        self.visible_benchmark_id = visible_benchmark_id
        self.exposure_snapshot_id = exposure_snapshot_id
        self.handlers = dict(handlers)
        self.status_reporter: StatusReporter | None = None
        self.scheduler = DurableScheduler(
            self.root / "scheduler_state.json",
            cpu_slots=cpu_slots,
            gpu_slots=gpu_slots,
            max_pending_evaluations=max_pending_evaluations,
        )
        self.discovery_path = self.root / "discovery_state.json"
        self.discovered = (
            set(load_json(self.discovery_path).get("training_checkpoint_ids", []))
            if self.discovery_path.exists()
            else set()
        )
        self.discovered_task_events = (
            set(load_json(self.discovery_path).get("task_event_ids", []))
            if self.discovery_path.exists()
            else set()
        )

    def _report_status(self) -> None:
        if self.status_reporter is not None:
            self.status_reporter(self.scheduler)

    def discover_checkpoints(self) -> int:
        discovered_now = 0
        events = [
            (event_path, load_json(event_path))
            for event_path in self.checkpoint_event_dir.glob("*.json")
        ]
        events.sort(
            key=lambda item: (
                item[1].get("training_step")
                if type(item[1].get("training_step")) is int
                and item[1]["training_step"] >= 0
                else float("inf"),
                item[0].name,
            )
        )
        for event_path, event in events:
            training_checkpoint_id = event.get("training_checkpoint_id")
            runtime_policy_id = event.get("runtime_policy_id")
            if not training_checkpoint_id or not runtime_policy_id:
                raise LeagueContractError(f"invalid checkpoint event: {event_path}")
            if training_checkpoint_id in self.discovered:
                continue
            payload = {
                "training_checkpoint_id": training_checkpoint_id,
                "runtime_policy_id": runtime_policy_id,
                "benchmark_id": self.visible_benchmark_id,
                "exposure_snapshot_id": self.exposure_snapshot_id,
            }
            training_step = event.get("training_step")
            if type(training_step) is int and training_step >= 0:
                payload["training_step"] = training_step
            self.scheduler.enqueue(
                "VISIBLE_EVALUATION",
                payload,
                resources=ResourceRequest(cpu_slots=1),
            )
            self.discovered.add(training_checkpoint_id)
            discovered_now += 1
        atomic_write_json(
            self.discovery_path,
            {
                "schema_version": 1,
                "training_checkpoint_ids": sorted(self.discovered),
                "task_event_ids": sorted(self.discovered_task_events),
            },
        )
        return discovered_now

    def discover_task_events(self) -> int:
        if self.task_event_dir is None:
            return 0
        discovered_now = 0
        for event_path in sorted(self.task_event_dir.glob("*.json")):
            event = load_json(event_path)
            event_id = str(event.get("task_event_id") or event_path.stem)
            if event_id in self.discovered_task_events:
                continue
            resources = ResourceRequest(**event.get("resources", {}))
            self.scheduler.enqueue(
                str(event["task_type"]),
                dict(event["payload"]),
                resources=resources,
            )
            self.discovered_task_events.add(event_id)
            discovered_now += 1
        atomic_write_json(
            self.discovery_path,
            {
                "schema_version": 1,
                "training_checkpoint_ids": sorted(self.discovered),
                "task_event_ids": sorted(self.discovered_task_events),
            },
        )
        return discovered_now

    def submit_task(
        self,
        task_type: str,
        payload: Mapping[str, Any],
        *,
        resources: ResourceRequest = ResourceRequest(),
    ) -> QueueItem:
        return self.scheduler.enqueue(
            task_type, payload, resources=resources
        )

    def run_once(self) -> dict[str, Any]:
        discovered = self.discover_checkpoints() + self.discover_task_events()
        item = self.scheduler.next_runnable()
        if item is None:
            return {"discovered": discovered, "executed": None, "status": "IDLE"}
        handler = self.handlers.get(item.task_type)
        if handler is None:
            return {
                "discovered": discovered,
                "executed": None,
                "status": "WAITING_FOR_HANDLER",
                "task_id": item.task_id,
                "task_type": item.task_type,
            }
        self.scheduler.transition(item.task_id, "RUNNING")
        self._report_status()
        try:
            result = dict(handler(item))
            atomic_write_json(self.root / "task_results" / f"{item.task_id}.json", result)
            self.scheduler.transition(item.task_id, "COMPLETE")
        except (LeagueContractError, OSError, RuntimeError, ValueError) as exc:
            self.scheduler.transition(
                item.task_id,
                "FAILED",
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            self._report_status()
            return {
                "discovered": discovered,
                "executed": item.task_id,
                "status": "FAILED",
                "error": str(exc),
            }
        self._report_status()
        return {
            "discovered": discovered,
            "executed": item.task_id,
            "status": "COMPLETE",
        }

    def run(self, *, poll_seconds: float = 10.0) -> None:
        if poll_seconds <= 0:
            raise LeagueContractError("poll_seconds must be positive")
        self._report_status()
        while True:
            result = self.run_once()
            if result["status"] in {"IDLE", "WAITING_FOR_HANDLER"}:
                time.sleep(poll_seconds)
