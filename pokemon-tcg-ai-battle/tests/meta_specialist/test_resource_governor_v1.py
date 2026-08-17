"""TDD contract for read-only resource-aware parallelization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.resource_governor_v1 import (
    ResourceBudget,
    ResourceGovernor,
    ResourceGovernorError,
    ResourceSnapshot,
)


def test_gpu_access_blocked_is_cpu_only_not_emergency(monkeypatch) -> None:
    import mage_ptcg.meta_specialist.resource_governor_v1 as module

    def blocked(_args):
        raise ResourceGovernorError(
            "nvidia-smi failed: Failed to initialize NVML: GPU access blocked by the operating system"
        )

    monkeypatch.setattr(module, "_run_nvidia_smi", blocked)
    count, processes, available, error = module._collect_gpu()

    assert (count, processes, available, error) == (0, (), False, None)


GiB = 1024**3


def _snapshot(
    *,
    available_gib: float = 32,
    total_gib: float = 64,
    logical_cpus: int = 16,
    rss_gib: float = 1,
    swap_total_gib: float = 8,
    swap_free_gib: float = 8,
    gpu_count: int = 0,
    gpu_compute_processes: tuple[int, ...] = (),
    source_errors: tuple[str, ...] = (),
) -> ResourceSnapshot:
    return ResourceSnapshot(
        collected_at_utc="2026-08-13T13:00:00Z",
        logical_cpus=logical_cpus,
        load1=0.5,
        memory_total_bytes=int(total_gib * GiB),
        memory_available_bytes=int(available_gib * GiB),
        memory_free_bytes=int(available_gib * GiB),
        process_rss_bytes=int(rss_gib * GiB),
        swap_total_bytes=int(swap_total_gib * GiB),
        swap_free_bytes=int(swap_free_gib * GiB),
        gpu_count=gpu_count,
        gpu_compute_processes=gpu_compute_processes,
        nvidia_smi_available=gpu_count > 0,
        source_errors=source_errors,
    )


def test_default_budget_seals_requested_parallelization_policy() -> None:
    budget = ResourceBudget()

    assert budget.safe_free_gib == 10.0
    assert budget.safe_free_fraction == 0.20
    assert budget.critical_free_gib == 6.0
    assert budget.initial_workers == 2
    assert budget.max_workers == 12
    assert budget.ramp_workers == (1, 2, 4, 8, 12)
    assert budget.recycle_games == 16
    assert budget.gpu_max == 1


def test_normal_snapshot_recommends_min_of_cpu_memory_and_task_caps() -> None:
    governor = ResourceGovernor(
        ResourceBudget(worker_memory_gib=2.0),
        snapshot_provider=lambda: _snapshot(available_gib=32, logical_cpus=16),
    )

    decision = governor.decide(task_cap=6)

    assert decision.state == "normal"
    assert decision.recommended_workers == 6
    assert decision.initial_workers == 2
    assert decision.memory_worker_cap == 9
    assert decision.gpu_admitted is True


def test_warning_and_critical_memory_states_are_fail_closed_without_killing_processes() -> None:
    warning = ResourceGovernor(
        ResourceBudget(worker_memory_gib=1.0),
        snapshot_provider=lambda: _snapshot(available_gib=8),
    ).decide(task_cap=12)
    critical = ResourceGovernor(
        ResourceBudget(worker_memory_gib=1.0),
        snapshot_provider=lambda: _snapshot(available_gib=5),
    ).decide(task_cap=12)

    assert warning.state == "warning"
    assert warning.recommended_workers == 2
    assert critical.state == "critical"
    assert critical.recommended_workers == 0
    assert warning.kills_performed == 0
    assert critical.kills_performed == 0


def test_unknown_telemetry_is_emergency_and_recommends_zero() -> None:
    snapshot = _snapshot(source_errors=("/proc/meminfo: unavailable",))
    decision = ResourceGovernor(snapshot_provider=lambda: snapshot).decide(task_cap=12)

    assert decision.state == "emergency"
    assert decision.recommended_workers == 0
    assert decision.reasons


def test_gpu_compute_process_blocks_gpu_admission_but_does_not_kill_it() -> None:
    governor = ResourceGovernor(
        snapshot_provider=lambda: _snapshot(gpu_count=1, gpu_compute_processes=(1234,)),
    )

    decision = governor.decide(task_cap=12, gpu_required=True)

    assert decision.gpu_admitted is False
    assert decision.recommended_workers == 0
    assert decision.gpu_compute_processes == (1234,)
    assert decision.kills_performed == 0


def test_ramp_only_returns_configured_steps() -> None:
    governor = ResourceGovernor(
        ResourceBudget(), snapshot_provider=lambda: _snapshot(logical_cpus=64, available_gib=64)
    )

    assert governor.ramp_for_capacity(3) == 2
    assert governor.ramp_for_capacity(10) == 8
    assert governor.ramp_for_capacity(100) == 12
    with pytest.raises(ResourceGovernorError, match="capacity"):
        governor.ramp_for_capacity(-1)


def test_snapshot_mapping_rejects_unknown_or_malformed_fields() -> None:
    payload = _snapshot().to_dict()

    with pytest.raises(ResourceGovernorError, match="unknown"):
        ResourceSnapshot.from_dict({**payload, "unexpected": 1})
    with pytest.raises(ResourceGovernorError, match="logical_cpus"):
        ResourceSnapshot.from_dict({**payload, "logical_cpus": 0})


def test_telemetry_is_canonical_hash_bound_and_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    governor = ResourceGovernor(snapshot_provider=lambda: _snapshot())
    written = governor.write_telemetry(path, task_cap=8)
    payload = json.loads(path.read_text(encoding="utf-8"))
    claimed = payload.pop("payload_sha256")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    assert written == hashlib.sha256(canonical).hexdigest()
    assert claimed == written
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        governor.write_telemetry(path, task_cap=8)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp-*"))


def test_budget_from_json_is_strict_and_config_matches_checked_in_policy() -> None:
    config_path = Path(__file__).resolve().parents[2] / "configs/meta_specialist/resource_budget_v1.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    budget = ResourceBudget.from_dict(payload)

    assert budget.to_dict()["safe_free_gib"] == 10.0
    with pytest.raises(ResourceGovernorError, match="unknown"):
        ResourceBudget.from_dict({**payload, "typo": 1})
