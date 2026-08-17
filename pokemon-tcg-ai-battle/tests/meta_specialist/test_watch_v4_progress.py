"""Tests for the single-bar V4 progress monitor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_monitor():
    script = Path(__file__).resolve().parents[2] / "scripts" / "watch_v4_progress.py"
    spec = importlib.util.spec_from_file_location("watch_v4_progress_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_format_snapshot_contains_aggregate_fields() -> None:
    monitor = _load_monitor()
    payload = {
        "status": "running", "completed": 2, "total": 9,
        "rate_per_second": 0.12, "eta_seconds": 58.0,
        "fields": {
            "stage": "evaluate", "seed": 0, "partition": "validation",
            "recurrence": "carry", "complete_action_nll": 0.51,
            "complete_action_top1": 0.73,
        },
    }
    text = monitor._format_snapshot(payload)
    assert "2/9" in text
    assert "evaluate" in text
    assert "validation" in text
    assert "carry" in text
    assert "nll=0.51" in text
    assert "top1=0.73" in text


def test_read_progress_rejects_non_object(tmp_path: Path) -> None:
    monitor = _load_monitor()
    path = tmp_path / "progress.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    try:
        monitor._read_progress(path)
    except ValueError:
        return
    raise AssertionError("non-object progress must fail closed")
