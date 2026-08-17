from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceSnapshot
from scripts.run_resource_aware_deck_candidate_v1 import (
    RESOURCE_AWARE_DECK_SCHEMA_V1,
    build_warmup_plan,
    write_warmup_telemetry,
)


def _snapshot(*, available: int = 48 * 1024**3, errors: tuple[str, ...] = ()) -> ResourceSnapshot:
    return ResourceSnapshot(
        collected_at_utc="2026-08-13T15:00:00Z",
        logical_cpus=28,
        load1=0.1,
        memory_total_bytes=64 * 1024**3,
        memory_available_bytes=available,
        memory_free_bytes=available,
        process_rss_bytes=32 * 1024**2,
        swap_total_bytes=8 * 1024**3,
        swap_free_bytes=8 * 1024**3,
        gpu_count=1,
        gpu_compute_processes=(),
        nvidia_smi_available=True,
        source_errors=errors,
    )


def test_warmup_plan_uses_governor_min_and_records_ramp() -> None:
    budget = ResourceBudget()
    plan = build_warmup_plan(
        budget=budget,
        task_cap=6,
        gpu_required=True,
        snapshot=_snapshot(),
    )

    assert plan["schema_version"] == RESOURCE_AWARE_DECK_SCHEMA_V1
    assert plan["warmup_status"] == "ready"
    assert plan["safe_workers"] == 6
    assert plan["warmup"]["admitted_ramp_workers"] == [1, 2, 4]
    assert plan["resource_decision"]["kills_performed"] == 0
    assert plan["authority"]["execution_authority"] is False


def test_warmup_plan_fails_closed_on_telemetry_error() -> None:
    plan = build_warmup_plan(
        budget=ResourceBudget(),
        task_cap=12,
        snapshot=_snapshot(errors=("malformed telemetry",)),
    )

    assert plan["warmup_status"] == "blocked"
    assert plan["safe_workers"] == 0
    assert plan["resource_decision"]["state"] == "emergency"


def test_write_warmup_telemetry_is_atomic_and_no_clobber(tmp_path: Path) -> None:
    destination = tmp_path / "warmup-telemetry.json"
    payload_sha = write_warmup_telemetry(
        destination,
        budget=ResourceBudget(),
        task_cap=4,
        snapshot=_snapshot(),
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["payload_sha256"] == payload_sha
    assert payload["warmup"]["requested_ramp_workers"] == [1, 2, 4]
    with pytest.raises(FileExistsError):
        write_warmup_telemetry(
            destination,
            budget=ResourceBudget(),
            task_cap=4,
            snapshot=_snapshot(),
        )
    assert not list(tmp_path.glob(".*.tmp-*"))
