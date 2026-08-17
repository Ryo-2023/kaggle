from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import orchestrate
from scripts.orchestration.overnight_state import OvernightStateError
from .test_overnight_mvp import OvernightRunner, _contract, _prepare


def test_follow_prints_current_once_without_replaying_history(
    repository: Path, tmp_path: Path, monkeypatch
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    runner = OvernightRunner(repository)
    records: list[dict[str, object]] = []
    monkeypatch.setattr(orchestrate, "_print_progress", lambda value: records.append(value) or True)
    monkeypatch.setattr(
        orchestrate.time,
        "sleep",
        lambda _interval: pytest.fail("terminal follow must not poll"),
    )
    assert orchestrate._follow_overnight(runner, state["session_id"], 0.01) == 0
    assert len(records) == 1
    assert records[0]["stage"] == "current"
    assert records[0]["status"] == "WAITING_HUMAN"


def test_follow_emits_only_new_events_then_stops(monkeypatch) -> None:
    old = {"stage": "old", "status": "RUNNING"}
    new = {"stage": "new", "status": "DONE"}

    class Runner:
        calls = 0

        def progress_record(self, _session_id):
            return {"stage": "current", "status": "RUNNING"}

        def events(self, _session_id):
            self.calls += 1
            return [old] if self.calls == 1 else [old, new]

        def status(self, _session_id):
            return {"status": "DONE"}

    records: list[dict[str, object]] = []
    monkeypatch.setattr(orchestrate, "_print_progress", lambda value: records.append(value) or True)
    monkeypatch.setattr(orchestrate.time, "sleep", lambda _interval: None)
    assert orchestrate._follow_overnight(Runner(), "session", 0.01) == 0
    assert records == [
        {"stage": "current", "status": "RUNNING"},
        new,
    ]


@pytest.mark.parametrize("mutation", ["missing", "truncated", "malformed"])
def test_event_stream_failures_are_controlled(
    repository: Path, tmp_path: Path, mutation: str
) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    runner = OvernightRunner(repository)
    session = runner._session(state["session_id"])
    path = session / "events.jsonl"
    if mutation == "missing":
        path.unlink()
    elif mutation == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    else:
        path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(OvernightStateError):
        runner.events(state["session_id"])


def test_state_events_and_reports_are_redacted_and_report_is_complete(
    repository: Path, tmp_path: Path
) -> None:
    secret = "TOP-SECRET-PROMPT-VALUE"
    contract = _contract(auto_integrate=False)
    contract["provider"]["prompt"] = secret
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, contracts=[contract], auto_integrate=False)
    )
    session = (
        repository
        / ".orchestrator"
        / "overnight"
        / "sessions"
        / state["session_id"]
    )
    report = json.loads(
        (session / "reports" / "morning-report.json").read_text(encoding="utf-8")
    )
    public = "\n".join(
        (
            (session / "state.json").read_text(encoding="utf-8"),
            (session / "events.jsonl").read_text(encoding="utf-8"),
            json.dumps(report),
            (session / "reports" / "morning-report.md").read_text(encoding="utf-8"),
        )
    )
    assert secret not in public
    for forbidden in ('"prompt"', '"argv"', '"stdout"', '"stderr"', '"environment"'):
        assert forbidden not in public
    assert report["session_branch"] == state["session_branch"]
    assert report["budget"]["token_usage"] == "unknown"
    assert set(report["budget"]["proxy_usage"]) == {
        "provider_calls",
        "prompt_bytes",
        "elapsed_seconds",
    }
    assert report["human_review_tasks"] == ["task-01"]
    assert state["session_id"] in report["next_command"]


def test_state_schema_drift_is_rejected(repository: Path, tmp_path: Path) -> None:
    state = OvernightRunner(repository).start(
        _prepare(repository, tmp_path, auto_integrate=False)
    )
    runner = OvernightRunner(repository)
    session = runner._session(state["session_id"])
    path = session / "state.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("version")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(OvernightStateError):
        runner.status(state["session_id"])
