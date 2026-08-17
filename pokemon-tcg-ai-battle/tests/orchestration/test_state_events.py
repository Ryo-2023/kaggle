from __future__ import annotations

from pathlib import Path

import pytest

from scripts.orchestration.events import EventStore, RunBusyError, RunLock
from scripts.orchestration.state import InvalidTransition, RunStatus, rebuild_state, validate_transition


def test_events_rebuild_state_after_materialized_state_loss(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "run-1")
    store.append("run_created", {"run_id": "run-1", "snapshot_id": "snap-1"})
    store.append("state_transition", {"from": "INTAKE", "to": "IMPLEMENTATION"})
    store.state_path.unlink()

    rebuilt = store.rebuild()

    assert rebuilt.state == RunStatus.IMPLEMENTATION
    assert rebuilt.event_count == 2
    assert store.state_path.exists()


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(RunStatus.INTAKE, RunStatus.DONE)


def test_event_stream_rejects_forged_transition() -> None:
    with pytest.raises(InvalidTransition):
        rebuild_state(
            [
                {"kind": "run_created", "at": "now", "payload": {"run_id": "r"}},
                {
                    "kind": "state_transition",
                    "at": "later",
                    "payload": {"from": "INTAKE", "to": "DONE"},
                },
            ]
        )


def test_same_run_lock_is_non_reentrant(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with RunLock(path):
        with pytest.raises(RunBusyError):
            with RunLock(path):
                pass
