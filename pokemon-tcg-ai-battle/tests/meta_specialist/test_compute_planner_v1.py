"""A compute plan must be measured on this host, and must roll back on its own evidence.

The design forbids carrying a worker count over from another host or another
run.  These tests hold the sweep to that: it climbs only while a larger setting
measurably wins, stops at the first regression or headroom crossing, and reports
the observation that stopped it.
"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.compute_planner_v1 import (
    COMPUTE_PLAN_SCHEMA_V1,
    ComputeObservationV1,
    ComputePlannerV1,
    ComputePlannerV1Error,
    HostCapabilitiesV1,
    measure_host_capabilities_v1,
)


GIB = 1024**3


def _host(*, cpu_threads: int = 8, ram_gib: int = 64, gpu: bool = False) -> HostCapabilitiesV1:
    return HostCapabilitiesV1(
        cpu_threads=cpu_threads,
        total_ram_bytes=ram_gib * GIB,
        available_ram_bytes=ram_gib * GIB // 2,
        free_disk_bytes=500 * GIB,
        gpu_count=1 if gpu else 0,
        total_vram_bytes=24 * GIB if gpu else 0,
    )


def _observation(workers: int, throughput: float, **overrides) -> ComputeObservationV1:
    payload = {"faults": 0, "peak_rss_bytes": GIB, "peak_vram_bytes": 0}
    payload.update(overrides)
    return ComputeObservationV1(workers=workers, throughput=throughput, **payload)


# -- host measurement -------------------------------------------------------


def test_the_host_is_measured_not_assumed() -> None:
    host = measure_host_capabilities_v1()
    assert host.cpu_threads >= 1
    assert host.total_ram_bytes > 0
    assert host.available_ram_bytes <= host.total_ram_bytes
    assert host.free_disk_bytes >= 0
    if host.gpu_count == 0:
        assert host.total_vram_bytes == 0
    else:
        assert host.total_vram_bytes > 0


def test_an_incoherent_host_is_refused() -> None:
    with pytest.raises(ComputePlannerV1Error, match="available_ram_bytes"):
        HostCapabilitiesV1(
            cpu_threads=4, total_ram_bytes=GIB, available_ram_bytes=2 * GIB,
            free_disk_bytes=0, gpu_count=0, total_vram_bytes=0,
        )
    with pytest.raises(ComputePlannerV1Error, match="total_vram_bytes"):
        HostCapabilitiesV1(
            cpu_threads=4, total_ram_bytes=GIB, available_ram_bytes=GIB,
            free_disk_bytes=0, gpu_count=0, total_vram_bytes=GIB,
        )


# -- the ladder -------------------------------------------------------------


def test_the_default_ladder_starts_at_one_and_is_bounded_by_this_host() -> None:
    """Never seeded from a count observed on some other machine."""
    ladder = ComputePlannerV1.default_ladder(_host(cpu_threads=12))
    assert ladder[0] == 1
    assert max(ladder) == 12
    assert list(ladder) == sorted(set(ladder))
    # The old proxy's 20 actors must not appear on a 12-thread host.
    assert all(setting <= 12 for setting in ladder)


def test_a_non_increasing_ladder_is_refused() -> None:
    for bad in ([4, 2], [1, 1, 2], [0, 1], [1, -2]):
        with pytest.raises(ComputePlannerV1Error):
            ComputePlannerV1(_host(), ladder=bad)


# -- climbing and rollback --------------------------------------------------


def test_the_sweep_adopts_the_last_setting_that_actually_improved() -> None:
    planner = ComputePlannerV1(_host(), ladder=[1, 2, 4, 8])
    measured = {1: 10.0, 2: 19.0, 4: 36.0, 8: 36.5}  # 8 barely beats 4

    plan = planner.sweep(lambda workers: _observation(workers, measured[workers]))

    assert plan.schema_version == COMPUTE_PLAN_SCHEMA_V1
    assert plan.workers == 4
    assert plan.rejected is not None and plan.rejected.workers == 8
    assert "did not clear" in plan.rejection_reason
    # The whole climb is retained as evidence, including the rejected point.
    assert [item.workers for item in plan.observations] == [1, 2, 4, 8]


def test_the_sweep_rolls_back_when_throughput_falls() -> None:
    planner = ComputePlannerV1(_host(), ladder=[1, 2, 4])
    measured = {1: 10.0, 2: 20.0, 4: 6.0}

    plan = planner.sweep(lambda workers: _observation(workers, measured[workers]))

    assert plan.workers == 2
    assert plan.rejected.workers == 4
    assert "throughput gain" in plan.rejection_reason


def test_the_sweep_rolls_back_when_faults_rise_even_if_throughput_rose() -> None:
    """Speed never buys the right to fail more games."""
    planner = ComputePlannerV1(_host(), ladder=[1, 2])
    observations = {
        1: _observation(1, 10.0, faults=0),
        2: _observation(2, 100.0, faults=3),
    }

    plan = planner.sweep(lambda workers: observations[workers])

    assert plan.workers == 1
    assert "faults rose from 0 to 3" in plan.rejection_reason


def test_the_sweep_rolls_back_when_the_ram_reserve_is_crossed() -> None:
    host = _host(ram_gib=64)
    planner = ComputePlannerV1(host, ladder=[1, 2])
    observations = {
        1: _observation(1, 10.0, peak_rss_bytes=8 * GIB),
        # 60 GiB of 64 GiB leaves less than the 15% reserve.
        2: _observation(2, 99.0, peak_rss_bytes=60 * GIB),
    }

    plan = planner.sweep(lambda workers: observations[workers])

    assert plan.workers == 1
    assert "host-RAM reserve" in plan.rejection_reason


def test_the_sweep_rolls_back_when_the_vram_reserve_is_crossed() -> None:
    host = _host(gpu=True)
    planner = ComputePlannerV1(host, ladder=[1, 2])
    observations = {
        1: _observation(1, 10.0, peak_vram_bytes=4 * GIB),
        2: _observation(2, 99.0, peak_vram_bytes=23 * GIB),  # of 24 GiB
    }

    plan = planner.sweep(lambda workers: observations[workers])

    assert plan.workers == 1
    assert "VRAM reserve" in plan.rejection_reason


def test_the_whole_ladder_is_adopted_when_every_setting_wins() -> None:
    planner = ComputePlannerV1(_host(), ladder=[1, 2, 4])
    measured = {1: 10.0, 2: 20.0, 4: 40.0}

    plan = planner.sweep(lambda workers: _observation(workers, measured[workers]))

    assert plan.workers == 4
    assert plan.rejected is None and plan.rejection_reason is None


def test_a_host_that_cannot_run_even_the_smallest_setting_fails_closed() -> None:
    """Returning a setting known to exceed memory would be worse than refusing."""
    host = _host(ram_gib=8)
    planner = ComputePlannerV1(host, ladder=[1, 2])

    with pytest.raises(ComputePlannerV1Error, match="smallest setting"):
        planner.sweep(lambda workers: _observation(workers, 1.0, peak_rss_bytes=8 * GIB))


def test_a_probe_that_measured_a_different_setting_is_refused() -> None:
    """Otherwise the plan would report a number nobody measured."""
    planner = ComputePlannerV1(_host(), ladder=[1, 2])

    with pytest.raises(ComputePlannerV1Error, match="when asked for"):
        planner.sweep(lambda _workers: _observation(99, 10.0))


def test_a_probe_returning_the_wrong_type_is_refused() -> None:
    planner = ComputePlannerV1(_host(), ladder=[1])
    with pytest.raises(ComputePlannerV1Error, match="ComputeObservationV1"):
        planner.sweep(lambda _workers: {"workers": 1, "throughput": 1.0})


def test_the_plan_serializes_every_observation_for_audit() -> None:
    planner = ComputePlannerV1(_host(), ladder=[1, 2])
    measured = {1: 10.0, 2: 10.1}

    payload = planner.sweep(lambda workers: _observation(workers, measured[workers])).to_dict()

    assert payload["workers"] == 1
    assert len(payload["observations"]) == 2
    assert payload["rejected"]["workers"] == 2
    assert payload["host"]["cpu_threads"] == 8
    assert isinstance(payload["rejection_reason"], str)


def test_a_nan_throughput_is_refused_rather_than_compared() -> None:
    with pytest.raises(ComputePlannerV1Error, match="throughput"):
        ComputeObservationV1(workers=1, throughput=float("nan"), faults=0, peak_rss_bytes=0)
