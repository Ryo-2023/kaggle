"""Run state machine and event-derived materialized state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping


class RunStatus(StrEnum):
    """All states supported by the Bootstrap Kernel."""

    INTAKE = "INTAKE"
    IMPLEMENTATION = "IMPLEMENTATION"
    VERIFICATION_DETERMINISTIC = "VERIFICATION_DETERMINISTIC"
    WAITING_INTEGRATION_APPROVAL = "WAITING_INTEGRATION_APPROVAL"
    APPLIED = "APPLIED"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    ABORTED = "ABORTED"


TERMINAL_STATES = {RunStatus.DONE, RunStatus.REJECTED, RunStatus.ABORTED}
_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.INTAKE: {RunStatus.IMPLEMENTATION, RunStatus.BLOCKED, RunStatus.ABORTED},
    RunStatus.IMPLEMENTATION: {
        RunStatus.VERIFICATION_DETERMINISTIC,
        RunStatus.BLOCKED,
        RunStatus.ABORTED,
    },
    RunStatus.VERIFICATION_DETERMINISTIC: {
        RunStatus.WAITING_INTEGRATION_APPROVAL,
        RunStatus.BLOCKED,
        RunStatus.ABORTED,
    },
    RunStatus.WAITING_INTEGRATION_APPROVAL: {
        RunStatus.APPLIED,
        RunStatus.REJECTED,
        RunStatus.ABORTED,
        RunStatus.BLOCKED,
    },
    RunStatus.APPLIED: {RunStatus.DONE, RunStatus.BLOCKED},
    RunStatus.BLOCKED: {RunStatus.IMPLEMENTATION, RunStatus.ABORTED},
    RunStatus.DONE: set(),
    RunStatus.REJECTED: set(),
    RunStatus.ABORTED: set(),
}


class InvalidTransition(ValueError):
    """Raised when a requested run state transition is invalid."""


def validate_transition(current: RunStatus, target: RunStatus) -> None:
    """Raise if *current* cannot transition to *target*."""

    if target not in _TRANSITIONS[current]:
        raise InvalidTransition(f"invalid transition: {current} -> {target}")


@dataclass
class RunState:
    """Materialized run view rebuilt exclusively from append-only events."""

    run_id: str
    state: RunStatus
    updated_at: str
    snapshot_id: str | None = None
    patch_ref: str | None = None
    findings: list[str] = field(default_factory=list)
    event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable state document."""

        value = asdict(self)
        value["state"] = self.state.value
        return value


def rebuild_state(events: Iterable[Mapping[str, Any]]) -> RunState:
    """Reconstruct and validate a run state from ordered events."""

    state: RunState | None = None
    for index, event in enumerate(events, start=1):
        kind = event.get("kind")
        payload = event.get("payload", {})
        at = event.get("at")
        if not isinstance(payload, Mapping) or not isinstance(at, str):
            raise ValueError("invalid event envelope")
        if kind == "run_created":
            if state is not None:
                raise ValueError("duplicate run_created event")
            state = RunState(
                run_id=str(payload["run_id"]),
                state=RunStatus.INTAKE,
                updated_at=at,
                snapshot_id=payload.get("snapshot_id"),
            )
        elif state is None:
            raise ValueError("first event must be run_created")
        elif kind == "state_transition":
            target = RunStatus(str(payload["to"]))
            if payload.get("from") != state.state.value:
                raise InvalidTransition("event source state does not match materialized state")
            validate_transition(state.state, target)
            state.state = target
            state.updated_at = at
            reason = payload.get("reason")
            if isinstance(reason, str) and reason:
                state.findings.append(reason)
        elif kind == "patch_captured":
            state.patch_ref = str(payload["patch_ref"])
            state.updated_at = at
        state.event_count = index
    if state is None:
        raise ValueError("event stream is empty")
    return state
