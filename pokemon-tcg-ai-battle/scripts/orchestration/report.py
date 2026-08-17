"""Sanitized JSON and concise Markdown morning reports."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .events import atomic_write_json


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _task_report(record: dict[str, Any]) -> dict[str, object]:
    allowed = {
        "status",
        "task_source_head",
        "task_source_tree_digest",
        "provider_calls",
        "repair_count",
        "routing_decisions",
        "changed_paths",
        "patch_digest",
        "diff_lines",
        "verification",
        "review",
        "risk",
        "integration",
        "commit",
        "failure_code",
    }
    return {key: value for key, value in record.items() if key in allowed}


def write_report(session: Path, state: dict[str, Any]) -> dict[str, object]:
    tasks = {name: _task_report(record) for name, record in state["tasks"].items()}
    human_tasks = [
        name for name, record in tasks.items() if record.get("status") == "WAITING_HUMAN"
    ]
    commits = {
        name: record["commit"]
        for name, record in tasks.items()
        if record.get("commit") is not None
    }
    report: dict[str, object] = {
        "version": 1,
        "session_id": state["session_id"],
        "name": state["name"],
        "status": state["status"],
        "source_head": state["source_head"],
        "source_branch": state["source_branch"],
        "session_branch": state["session_branch"],
        "started_at": state["started_at"],
        "updated_at": state["updated_at"],
        "elapsed_seconds": state["budget"]["elapsed_seconds"],
        "tasks": tasks,
        "budget": state["budget"],
        "integrated_commits": commits,
        "human_review_tasks": human_tasks,
        "stop_reason": state["stop_reason"],
        "next_command": (
            None
            if state["status"] == "DONE"
            else f"/usr/bin/python3 scripts/orchestrate.py overnight --resume {state['session_id']}"
        ),
        "residual_risk": (
            "human review or recovery is required"
            if state["status"] != "DONE"
            else None
        ),
    }
    json_path = session / "reports" / "morning-report.json"
    markdown_path = session / "reports" / "morning-report.md"
    atomic_write_json(json_path, report)
    lines = [
        "# Overnight morning report",
        f"- Session: {state['session_id']}",
        f"- Status: {state['status']}",
        f"- Branch: {state['session_branch']}",
        f"- Source: {state['source_head']}",
        f"- Elapsed: {state['budget']['elapsed_seconds']:.3f}s",
    ]
    lines.extend(f"- {name}: {record['status']}" for name, record in tasks.items())
    if human_tasks:
        lines.append("- Human review: " + ", ".join(human_tasks))
    _atomic_text(markdown_path, "\n".join(lines) + "\n")
    return report
