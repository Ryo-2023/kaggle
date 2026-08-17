"""Measure this host, then sweep worker counts and keep only what measured better (Slice L6).

The design's rule for ``--compute auto`` is that a setting is adopted because it
was *measured* on this host, and abandoned the moment throughput falls, faults
rise, or a headroom bound is crossed.  Two things follow, and this module exists
to make both structural:

**No hard-coded worker count.**  The plan's own words: "the locally observed
eight-worker setting is only the first measured point; 12+ requires a
host-memory soak.  Do not hard-code the old proxy's 20 actors."  So there is no
default actor count here.  A plan comes from :meth:`ComputePlannerV1.sweep`,
which starts at a setting that is always runnable and climbs only while each
larger setting actually wins.

**Rollback is a normal outcome, not an error.**  Climbing stops at the first
setting that is worse, and the *previous* setting is what gets returned --
together with the observation that rejected the larger one, so the decision is
auditable rather than a bare number.

What this module does not do
-----------------------------
It never runs the work itself; a caller supplies a ``probe`` that measures one
candidate setting and returns a :class:`ComputeObservationV1`.  That separates
the policy (when to climb, when to roll back, what counts as headroom) from the
cost of a real collection or training run, and lets the policy be tested against
measured observations rather than against a simulated host.

Precision, BF16, pinned memory, and compilation are deliberately absent from the
plan this module produces.  The design gates them behind "numerical and resume
parity", which is a property of a *learner* run, not of a host probe; emitting a
precision recommendation here would assert a parity result nobody measured.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
from typing import Callable, Sequence


COMPUTE_PLAN_SCHEMA_V1 = "meta-specialist-compute-plan-v1"

# Fraction of a resource that must remain free for a setting to be adoptable.
# The design fixes the VRAM reserve at 15%; the same floor is applied to host
# RAM, because a learner that fills host memory fails exactly as hard.
_REQUIRED_HEADROOM_FRACTION_V1 = 0.15
# A larger setting must beat the incumbent by more than measurement noise to be
# worth its extra memory; equal-within-noise counts as "not better".
_MIN_RELATIVE_THROUGHPUT_GAIN_V1 = 0.05


class ComputePlannerV1Error(ValueError):
    """Raised when a host cannot be measured or a plan cannot be justified."""


@dataclass(frozen=True, slots=True)
class HostCapabilitiesV1:
    """What this host actually reports, measured rather than assumed."""

    cpu_threads: int
    total_ram_bytes: int
    available_ram_bytes: int
    free_disk_bytes: int
    gpu_count: int
    total_vram_bytes: int

    def __post_init__(self) -> None:
        for name in (
            "cpu_threads", "total_ram_bytes", "available_ram_bytes",
            "free_disk_bytes", "gpu_count", "total_vram_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ComputePlannerV1Error(f"{name} must be a nonnegative int")
        if self.cpu_threads < 1:
            raise ComputePlannerV1Error("cpu_threads must be at least 1")
        if self.available_ram_bytes > self.total_ram_bytes:
            raise ComputePlannerV1Error("available_ram_bytes cannot exceed total_ram_bytes")
        if self.gpu_count == 0 and self.total_vram_bytes != 0:
            raise ComputePlannerV1Error("total_vram_bytes must be 0 when gpu_count is 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "cpu_threads": self.cpu_threads,
            "total_ram_bytes": self.total_ram_bytes,
            "available_ram_bytes": self.available_ram_bytes,
            "free_disk_bytes": self.free_disk_bytes,
            "gpu_count": self.gpu_count,
            "total_vram_bytes": self.total_vram_bytes,
        }


@dataclass(frozen=True, slots=True)
class ComputeObservationV1:
    """One measured candidate setting.

    ``throughput`` is whatever unit the caller's probe measures (games/s for a
    collection sweep, steps/s for a learner sweep); the planner only compares
    observations to each other, never to an absolute expectation.
    """

    workers: int
    throughput: float
    faults: int
    peak_rss_bytes: int
    peak_vram_bytes: int = 0

    def __post_init__(self) -> None:
        if type(self.workers) is not int or self.workers < 1:
            raise ComputePlannerV1Error("workers must be a positive int")
        if type(self.throughput) is not float or self.throughput != self.throughput:
            raise ComputePlannerV1Error("throughput must be a real float")
        if self.throughput < 0.0:
            raise ComputePlannerV1Error("throughput must be nonnegative")
        for name in ("faults", "peak_rss_bytes", "peak_vram_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ComputePlannerV1Error(f"{name} must be a nonnegative int")

    def to_dict(self) -> dict[str, object]:
        return {
            "workers": self.workers,
            "throughput": self.throughput,
            "faults": self.faults,
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_vram_bytes": self.peak_vram_bytes,
        }


@dataclass(frozen=True, slots=True)
class ComputePlanV1:
    """The adopted setting, plus the evidence for adopting it and stopping there."""

    schema_version: str
    workers: int
    host: HostCapabilitiesV1
    observations: tuple[ComputeObservationV1, ...]
    rejected: ComputeObservationV1 | None
    rejection_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "workers": self.workers,
            "host": self.host.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
            "rejected": None if self.rejected is None else self.rejected.to_dict(),
            "rejection_reason": self.rejection_reason,
        }


def _read_host_memory_v1() -> tuple[int, int]:
    """Return ``(total_bytes, available_bytes)`` for host RAM.

    Prefers ``/proc/meminfo``: ``MemAvailable`` is the kernel's own estimate of
    what a new allocation can actually get (it counts reclaimable page cache),
    which is the number a headroom decision needs.  ``SC_AV_PHYS_PAGES`` is only
    a fallback because it is absent on some kernels this runs on, including
    WSL2, and it undercounts by ignoring reclaimable cache.
    """
    try:
        fields: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                if name in ("MemTotal", "MemAvailable"):
                    fields[name] = int(rest.split()[0]) * 1024
                if len(fields) == 2:
                    break
        if len(fields) == 2:
            return fields["MemTotal"], fields["MemAvailable"]
    except (OSError, ValueError, IndexError):
        pass

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = page_size * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError) as exc:  # pragma: no cover - non-POSIX
        raise ComputePlannerV1Error(f"could not read host memory: {exc}") from exc
    try:
        available = page_size * os.sysconf("SC_AV_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        # Refusing here would be wrong: total is known and is the bound the
        # headroom rule actually uses. Report available as unknown-but-bounded
        # rather than inventing a fraction.
        available = total
    return int(total), int(available)


def measure_host_capabilities_v1(
    *, disk_path: str | os.PathLike[str] = ".",
) -> HostCapabilitiesV1:
    """Read this host's real limits. Never substitutes a guess for an unreadable value."""
    try:
        cpu_threads = len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - non-Linux
        cpu_threads = os.cpu_count() or 1

    total_ram, available_ram = _read_host_memory_v1()

    try:
        free_disk = shutil.disk_usage(os.fspath(disk_path)).free
    except OSError as exc:
        raise ComputePlannerV1Error(f"could not read free disk at {disk_path}: {exc}") from exc

    gpu_count, total_vram = 0, 0
    try:
        import torch
    except ImportError:
        pass
    else:
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            total_vram = sum(
                torch.cuda.get_device_properties(index).total_memory
                for index in range(gpu_count)
            )
    return HostCapabilitiesV1(
        cpu_threads=int(cpu_threads),
        total_ram_bytes=int(total_ram),
        available_ram_bytes=int(available_ram),
        free_disk_bytes=int(free_disk),
        gpu_count=int(gpu_count),
        total_vram_bytes=int(total_vram),
    )


def _headroom_violation_v1(
    observation: ComputeObservationV1, host: HostCapabilitiesV1,
) -> str | None:
    """Return why this observation crossed a headroom bound, or ``None``."""
    ram_ceiling = host.total_ram_bytes * (1.0 - _REQUIRED_HEADROOM_FRACTION_V1)
    if observation.peak_rss_bytes > ram_ceiling:
        return (
            f"peak RSS {observation.peak_rss_bytes} crossed the "
            f"{int(_REQUIRED_HEADROOM_FRACTION_V1 * 100)}% host-RAM reserve "
            f"(ceiling {int(ram_ceiling)} of {host.total_ram_bytes})"
        )
    if host.total_vram_bytes:
        vram_ceiling = host.total_vram_bytes * (1.0 - _REQUIRED_HEADROOM_FRACTION_V1)
        if observation.peak_vram_bytes > vram_ceiling:
            return (
                f"peak VRAM {observation.peak_vram_bytes} crossed the "
                f"{int(_REQUIRED_HEADROOM_FRACTION_V1 * 100)}% VRAM reserve "
                f"(ceiling {int(vram_ceiling)} of {host.total_vram_bytes})"
            )
    elif observation.peak_vram_bytes:
        return "observation reports VRAM use on a host that reports no GPU"
    return None


def _regression_reason_v1(
    candidate: ComputeObservationV1, incumbent: ComputeObservationV1,
) -> str | None:
    """Return why this candidate is not an improvement, or ``None``."""
    if candidate.faults > incumbent.faults:
        return (
            f"faults rose from {incumbent.faults} to {candidate.faults} at "
            f"{candidate.workers} workers"
        )
    if incumbent.throughput <= 0.0:
        return None if candidate.throughput > 0.0 else "throughput is still zero"
    gain = (candidate.throughput - incumbent.throughput) / incumbent.throughput
    if gain <= _MIN_RELATIVE_THROUGHPUT_GAIN_V1:
        return (
            f"throughput gain {gain:+.1%} at {candidate.workers} workers did not clear the "
            f"{_MIN_RELATIVE_THROUGHPUT_GAIN_V1:.0%} threshold over {incumbent.workers} "
            f"workers ({incumbent.throughput:.4f} -> {candidate.throughput:.4f})"
        )
    return None


class ComputePlannerV1:
    """Sweep candidate worker counts upward, adopting only measured improvements."""

    __slots__ = ("_host", "_ladder")

    def __init__(
        self, host: HostCapabilitiesV1, *, ladder: Sequence[int] | None = None,
    ) -> None:
        if type(host) is not HostCapabilitiesV1:
            raise ComputePlannerV1Error("host must be a HostCapabilitiesV1")
        self._host = host
        resolved = tuple(ladder) if ladder is not None else self.default_ladder(host)
        if not resolved:
            raise ComputePlannerV1Error("the sweep ladder must have at least one setting")
        if any(type(item) is not int or item < 1 for item in resolved):
            raise ComputePlannerV1Error("every ladder setting must be a positive int")
        if list(resolved) != sorted(set(resolved)):
            raise ComputePlannerV1Error("the sweep ladder must be strictly increasing")
        self._ladder = resolved

    @property
    def host(self) -> HostCapabilitiesV1:
        return self._host

    @property
    def ladder(self) -> tuple[int, ...]:
        return self._ladder

    @staticmethod
    def default_ladder(host: HostCapabilitiesV1) -> tuple[int, ...]:
        """A baseline that is always runnable, doubling up to this host's thread count.

        Starts at 1.  The design forbids assuming a previously observed count is
        right for this host, so nothing here is seeded from an earlier run.
        """
        settings: list[int] = []
        candidate = 1
        while candidate <= host.cpu_threads:
            settings.append(candidate)
            candidate *= 2
        if settings and settings[-1] != host.cpu_threads:
            settings.append(host.cpu_threads)
        return tuple(settings)

    def sweep(self, probe: Callable[[int], ComputeObservationV1]) -> ComputePlanV1:
        """Measure each setting in turn; stop at the first that is not better.

        The returned plan's ``workers`` is the last setting that both stayed
        inside every headroom bound and improved on its predecessor.  The
        setting that ended the climb is reported in ``rejected`` with its reason,
        so a later reader can tell a measured stop from the ladder running out.
        """
        if not callable(probe):
            raise ComputePlannerV1Error("probe must be callable")

        observations: list[ComputeObservationV1] = []
        adopted: ComputeObservationV1 | None = None
        for workers in self._ladder:
            observation = probe(workers)
            if type(observation) is not ComputeObservationV1:
                raise ComputePlannerV1Error("probe must return a ComputeObservationV1")
            if observation.workers != workers:
                raise ComputePlannerV1Error(
                    f"probe measured {observation.workers} workers when asked for {workers}"
                )
            observations.append(observation)

            violation = _headroom_violation_v1(observation, self._host)
            if violation is not None:
                if adopted is None:
                    raise ComputePlannerV1Error(
                        f"even the smallest setting ({workers} workers) crossed a headroom "
                        f"bound: {violation}"
                    )
                return ComputePlanV1(
                    schema_version=COMPUTE_PLAN_SCHEMA_V1, workers=adopted.workers,
                    host=self._host, observations=tuple(observations),
                    rejected=observation, rejection_reason=violation,
                )

            if adopted is None:
                adopted = observation
                continue
            regression = _regression_reason_v1(observation, adopted)
            if regression is not None:
                return ComputePlanV1(
                    schema_version=COMPUTE_PLAN_SCHEMA_V1, workers=adopted.workers,
                    host=self._host, observations=tuple(observations),
                    rejected=observation, rejection_reason=regression,
                )
            adopted = observation

        assert adopted is not None  # the ladder is nonempty, so the first setting adopts
        return ComputePlanV1(
            schema_version=COMPUTE_PLAN_SCHEMA_V1, workers=adopted.workers, host=self._host,
            observations=tuple(observations), rejected=None, rejection_reason=None,
        )


__all__ = [
    "COMPUTE_PLAN_SCHEMA_V1",
    "ComputeObservationV1",
    "ComputePlanV1",
    "ComputePlannerV1",
    "ComputePlannerV1Error",
    "HostCapabilitiesV1",
    "measure_host_capabilities_v1",
]
