"""Read-only resource governance for research evaluators.

The governor is deliberately independent from every evaluator and scheduler:
it observes the host, recommends a bounded worker count, and emits a sealed
telemetry record.  It never starts or stops work and never sends a signal to an
unrelated process.  ``psutil`` and ``nvidia-smi`` are optional; missing GPU
tools only disable GPU admission while leaving CPU-only evaluation usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence


RESOURCE_GOVERNOR_SCHEMA_V1 = "meta-specialist-resource-governor-v1"
_HEX64 = frozenset("0123456789abcdef")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_GIB = 1024**3


class ResourceGovernorError(ValueError):
    """Raised when a resource budget, snapshot, or telemetry artifact is open."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResourceGovernorError(f"value is not canonical JSON: {exc}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ResourceGovernorError(f"{field} must be an integer >= {minimum}")
    return value


def _require_float(value: object, *, field: str, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ResourceGovernorError(f"{field} must be a finite number >= {minimum}")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum:
        raise ResourceGovernorError(f"{field} must be a finite number >= {minimum}")
    return parsed


def _require_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise ResourceGovernorError(f"{field} must be a bool")
    return value


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ResourceGovernorError(f"{field} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """Closed resource policy used by :class:`ResourceGovernor`."""

    safe_free_gib: float = 10.0
    safe_free_fraction: float = 0.20
    critical_free_gib: float = 6.0
    worker_memory_gib: float = 2.0
    initial_workers: int = 2
    max_workers: int = 12
    ramp_workers: tuple[int, ...] = (1, 2, 4, 8, 12)
    recycle_games: int = 16
    gpu_max: int = 1

    def __post_init__(self) -> None:
        safe = _require_float(self.safe_free_gib, field="safe_free_gib")
        fraction = _require_float(self.safe_free_fraction, field="safe_free_fraction")
        critical = _require_float(self.critical_free_gib, field="critical_free_gib")
        worker_memory = _require_float(self.worker_memory_gib, field="worker_memory_gib")
        if safe <= 0.0 or worker_memory <= 0.0:
            raise ResourceGovernorError("safe_free_gib and worker_memory_gib must be positive")
        if not 0.0 < fraction < 1.0:
            raise ResourceGovernorError("safe_free_fraction must be in (0, 1)")
        if critical <= 0.0 or critical >= safe:
            raise ResourceGovernorError("critical_free_gib must be positive and below safe_free_gib")
        initial = _require_int(self.initial_workers, field="initial_workers", minimum=1)
        maximum = _require_int(self.max_workers, field="max_workers", minimum=1)
        recycle = _require_int(self.recycle_games, field="recycle_games", minimum=1)
        gpu_max = _require_int(self.gpu_max, field="gpu_max", minimum=0)
        ramp = self.ramp_workers
        if type(ramp) is not tuple or not ramp:
            raise ResourceGovernorError("ramp_workers must be a nonempty tuple")
        if any(type(item) is not int or item < 1 for item in ramp):
            raise ResourceGovernorError("ramp_workers must contain positive integers")
        if tuple(sorted(set(ramp))) != ramp:
            raise ResourceGovernorError("ramp_workers must be strictly increasing")
        if ramp[-1] != maximum or initial > maximum or initial not in ramp:
            raise ResourceGovernorError("initial/max workers must be represented by ramp_workers")
        # Re-run the numeric checks against the normalized values so subclasses
        # or hostile mappings cannot smuggle booleans through comparisons.
        if safe != float(self.safe_free_gib) or fraction != float(self.safe_free_fraction):
            raise ResourceGovernorError("budget numeric values are malformed")
        _ = (critical, worker_memory, recycle, gpu_max)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ResourceBudget":
        if type(payload) is not dict:
            raise ResourceGovernorError("resource budget must be a JSON object")
        expected = {
            "safe_free_gib", "safe_free_fraction", "critical_free_gib",
            "worker_memory_gib", "initial_workers", "max_workers",
            "ramp_workers", "recycle_games", "gpu_max",
        }
        unknown = set(payload).difference(expected)
        missing = expected.difference(payload)
        if unknown or missing:
            raise ResourceGovernorError(
                f"resource budget has unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        ramp = payload["ramp_workers"]
        if type(ramp) is not list:
            raise ResourceGovernorError("ramp_workers must be a JSON list")
        return cls(
            safe_free_gib=payload["safe_free_gib"],
            safe_free_fraction=payload["safe_free_fraction"],
            critical_free_gib=payload["critical_free_gib"],
            worker_memory_gib=payload["worker_memory_gib"],
            initial_workers=payload["initial_workers"],
            max_workers=payload["max_workers"],
            ramp_workers=tuple(ramp),
            recycle_games=payload["recycle_games"],
            gpu_max=payload["gpu_max"],
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "ResourceBudget":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResourceGovernorError(f"cannot load resource budget: {path}") from exc
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "safe_free_gib": self.safe_free_gib,
            "safe_free_fraction": self.safe_free_fraction,
            "critical_free_gib": self.critical_free_gib,
            "worker_memory_gib": self.worker_memory_gib,
            "initial_workers": self.initial_workers,
            "max_workers": self.max_workers,
            "ramp_workers": list(self.ramp_workers),
            "recycle_games": self.recycle_games,
            "gpu_max": self.gpu_max,
        }

    @property
    def safe_free_bytes(self) -> int:
        return int(self.safe_free_gib * _GIB)

    @property
    def critical_free_bytes(self) -> int:
        return int(self.critical_free_gib * _GIB)

    @property
    def worker_memory_bytes(self) -> int:
        return int(self.worker_memory_gib * _GIB)


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """One immutable read-only observation of the current host."""

    collected_at_utc: str
    logical_cpus: int
    load1: float | None
    memory_total_bytes: int
    memory_available_bytes: int
    memory_free_bytes: int
    process_rss_bytes: int
    swap_total_bytes: int
    swap_free_bytes: int
    gpu_count: int
    gpu_compute_processes: tuple[int, ...]
    nvidia_smi_available: bool
    source_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.collected_at_utc) is not str or _UTC_RE.fullmatch(self.collected_at_utc) is None:
            raise ResourceGovernorError("collected_at_utc must be canonical UTC")
        _require_int(self.logical_cpus, field="logical_cpus", minimum=1)
        if self.load1 is not None:
            _require_float(self.load1, field="load1")
        for field in (
            "memory_total_bytes", "memory_available_bytes", "memory_free_bytes",
            "process_rss_bytes", "swap_total_bytes", "swap_free_bytes", "gpu_count",
        ):
            _require_int(getattr(self, field), field=field, minimum=0)
        if self.memory_total_bytes and self.memory_available_bytes > self.memory_total_bytes:
            raise ResourceGovernorError("memory_available_bytes exceeds total")
        if self.swap_total_bytes and self.swap_free_bytes > self.swap_total_bytes:
            raise ResourceGovernorError("swap_free_bytes exceeds total")
        if type(self.gpu_compute_processes) is not tuple or any(
            type(pid) is not int or pid < 0 for pid in self.gpu_compute_processes
        ):
            raise ResourceGovernorError("gpu_compute_processes must be a tuple of PIDs")
        if len(set(self.gpu_compute_processes)) != len(self.gpu_compute_processes):
            raise ResourceGovernorError("gpu_compute_processes must be unique")
        _require_bool(self.nvidia_smi_available, field="nvidia_smi_available")
        if type(self.source_errors) is not tuple or any(
            type(error) is not str or not error for error in self.source_errors
        ):
            raise ResourceGovernorError("source_errors must be a tuple of nonempty strings")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ResourceSnapshot":
        if type(payload) is not dict:
            raise ResourceGovernorError("resource snapshot must be a JSON object")
        expected = {
            "collected_at_utc", "logical_cpus", "load1", "memory_total_bytes",
            "memory_available_bytes", "memory_free_bytes", "process_rss_bytes",
            "swap_total_bytes", "swap_free_bytes", "gpu_count",
            "gpu_compute_processes", "nvidia_smi_available", "source_errors",
        }
        unknown = set(payload).difference(expected)
        missing = expected.difference(payload)
        if unknown or missing:
            raise ResourceGovernorError(
                f"resource snapshot has unknown={sorted(unknown)} missing={sorted(missing)}"
            )
        processes = payload["gpu_compute_processes"]
        errors = payload["source_errors"]
        if type(processes) is not list or type(errors) is not list:
            raise ResourceGovernorError("snapshot process/error fields must be JSON lists")
        return cls(
            collected_at_utc=payload["collected_at_utc"],
            logical_cpus=payload["logical_cpus"],
            load1=payload["load1"],
            memory_total_bytes=payload["memory_total_bytes"],
            memory_available_bytes=payload["memory_available_bytes"],
            memory_free_bytes=payload["memory_free_bytes"],
            process_rss_bytes=payload["process_rss_bytes"],
            swap_total_bytes=payload["swap_total_bytes"],
            swap_free_bytes=payload["swap_free_bytes"],
            gpu_count=payload["gpu_count"],
            gpu_compute_processes=tuple(processes),
            nvidia_smi_available=payload["nvidia_smi_available"],
            source_errors=tuple(errors),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "collected_at_utc": self.collected_at_utc,
            "logical_cpus": self.logical_cpus,
            "load1": self.load1,
            "memory_total_bytes": self.memory_total_bytes,
            "memory_available_bytes": self.memory_available_bytes,
            "memory_free_bytes": self.memory_free_bytes,
            "process_rss_bytes": self.process_rss_bytes,
            "swap_total_bytes": self.swap_total_bytes,
            "swap_free_bytes": self.swap_free_bytes,
            "gpu_count": self.gpu_count,
            "gpu_compute_processes": list(self.gpu_compute_processes),
            "nvidia_smi_available": self.nvidia_smi_available,
            "source_errors": list(self.source_errors),
        }

    @classmethod
    def collect(cls) -> "ResourceSnapshot":
        errors: list[str] = []
        logical_cpus = os.cpu_count() or 0
        if logical_cpus < 1:
            errors.append("cpu_count unavailable")
            logical_cpus = 1
        load1: float | None
        try:
            load1 = float(os.getloadavg()[0])
        except (AttributeError, OSError, IndexError, TypeError, ValueError):
            load1 = None
            errors.append("loadavg unavailable")
        memory = _collect_memory(errors)
        rss = _collect_rss(errors)
        gpu_count, gpu_processes, gpu_available, gpu_error = _collect_gpu()
        if gpu_error is not None:
            # Missing nvidia-smi is a supported CPU-only state, while a
            # malformed command result is retained as a diagnostic error.
            if gpu_error != "nvidia-smi unavailable":
                errors.append(gpu_error)
        return cls(
            collected_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            logical_cpus=logical_cpus,
            load1=load1,
            memory_total_bytes=memory[0],
            memory_available_bytes=memory[1],
            memory_free_bytes=memory[2],
            process_rss_bytes=rss,
            swap_total_bytes=memory[3],
            swap_free_bytes=memory[4],
            gpu_count=gpu_count,
            gpu_compute_processes=gpu_processes,
            nvidia_smi_available=gpu_available,
            source_errors=tuple(errors),
        )


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    """Fail-closed admission result derived from one snapshot."""

    state: str
    recommended_workers: int
    initial_workers: int
    max_workers: int
    cpu_worker_cap: int
    memory_worker_cap: int
    task_cap: int
    gpu_admitted: bool
    gpu_compute_processes: tuple[int, ...]
    kills_performed: int
    reasons: tuple[str, ...]
    snapshot: ResourceSnapshot

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "recommended_workers": self.recommended_workers,
            "initial_workers": self.initial_workers,
            "max_workers": self.max_workers,
            "cpu_worker_cap": self.cpu_worker_cap,
            "memory_worker_cap": self.memory_worker_cap,
            "task_cap": self.task_cap,
            "gpu_admitted": self.gpu_admitted,
            "gpu_compute_processes": list(self.gpu_compute_processes),
            "kills_performed": self.kills_performed,
            "reasons": list(self.reasons),
        }


def _parse_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ResourceGovernorError(f"cannot read meminfo: {path}") from exc
    parsed: dict[str, int] = {}
    for line in lines:
        if not line or ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if not key or not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError as exc:
            raise ResourceGovernorError(f"malformed meminfo value for {key}") from exc
        if value < 0:
            raise ResourceGovernorError(f"negative meminfo value for {key}")
        # /proc/meminfo values are kB when a unit is present.
        parsed[key] = value * 1024 if len(parts) > 1 and parts[1].lower() == "kb" else value
    return parsed


def _collect_memory(errors: list[str]) -> tuple[int, int, int, int, int]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            virtual = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return (
                int(virtual.total), int(virtual.available), int(virtual.free),
                int(swap.total), int(swap.free),
            )
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            errors.append(f"psutil memory unavailable: {type(exc).__name__}")
    try:
        values = _parse_meminfo()
        total = values["MemTotal"]
        available = values.get("MemAvailable", values.get("MemFree", 0))
        free = values.get("MemFree", available)
        swap_total = values.get("SwapTotal", 0)
        swap_free = values.get("SwapFree", 0)
        return total, available, free, swap_total, swap_free
    except (KeyError, ResourceGovernorError) as exc:
        errors.append(f"meminfo unavailable: {type(exc).__name__}")
        return 0, 0, 0, 0, 0


def _collect_rss(errors: list[str]) -> int:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            return int(psutil.Process(os.getpid()).memory_info().rss)
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            errors.append(f"psutil rss unavailable: {type(exc).__name__}")
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
        return resident_pages * int(page_size)
    except (AttributeError, IndexError, OSError, TypeError, ValueError) as exc:
        errors.append(f"rss unavailable: {type(exc).__name__}")
        return 0


def _parse_nvidia_lines(text: str, *, field: str) -> tuple[int, ...]:
    values: list[int] = []
    for raw in text.splitlines():
        token = raw.strip().split(",", 1)[0].strip()
        if not token or token.lower() in {"no running processes found", "n/a"}:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ResourceGovernorError(f"malformed nvidia-smi {field} value") from exc
        if value < 0:
            raise ResourceGovernorError(f"negative nvidia-smi {field} value")
        values.append(value)
    return tuple(values)


def _run_nvidia_smi(args: Sequence[str]) -> str:
    command = shutil.which("nvidia-smi")
    if command is None:
        raise FileNotFoundError("nvidia-smi unavailable")
    result = subprocess.run(
        [command, *args], capture_output=True, text=True, timeout=2.0, check=False,
    )
    if result.returncode != 0:
        raise ResourceGovernorError(f"nvidia-smi failed with code {result.returncode}")
    return result.stdout


def _collect_gpu() -> tuple[int, tuple[int, ...], bool, str | None]:
    try:
        gpu_lines = _run_nvidia_smi(["--query-gpu=index", "--format=csv,noheader,nounits"])
        indices = _parse_nvidia_lines(gpu_lines, field="gpu")
        process_lines = _run_nvidia_smi(["--query-compute-apps=pid", "--format=csv,noheader,nounits"])
        processes = _parse_nvidia_lines(process_lines, field="pid")
        return len(indices), tuple(sorted(set(processes))), True, None
    except FileNotFoundError:
        return 0, (), False, "nvidia-smi unavailable"
    except (OSError, ResourceGovernorError, subprocess.TimeoutExpired):
        # GPU telemetry is optional for CPU-only evaluation.  WSL can expose
        # an nvidia-smi executable while blocking NVML initialization; that
        # must not turn an otherwise healthy CPU host into an emergency with
        # zero workers.  A GPU-required caller still receives admission=false
        # from the normal ``gpu_count == 0`` branch in ``decide``.
        return 0, (), False, None


class ResourceGovernor:
    """Observe resources and calculate bounded worker/GPU admission."""

    def __init__(
        self,
        budget: ResourceBudget | None = None,
        *,
        snapshot_provider: Callable[[], ResourceSnapshot] | None = None,
    ) -> None:
        if budget is not None and type(budget) is not ResourceBudget:
            raise ResourceGovernorError("budget must be ResourceBudget")
        if snapshot_provider is not None and not callable(snapshot_provider):
            raise ResourceGovernorError("snapshot_provider must be callable")
        self.budget = budget or ResourceBudget()
        self._snapshot_provider = snapshot_provider

    def snapshot(self) -> ResourceSnapshot:
        value = self._snapshot_provider() if self._snapshot_provider is not None else ResourceSnapshot.collect()
        if type(value) is not ResourceSnapshot:
            raise ResourceGovernorError("snapshot provider returned malformed telemetry")
        return value

    def ramp_for_capacity(self, capacity: int) -> int:
        capacity = _require_int(capacity, field="capacity")
        return max((step for step in self.budget.ramp_workers if step <= capacity), default=0)

    def decide(
        self,
        *,
        task_cap: int | None = None,
        gpu_required: bool = False,
        snapshot: ResourceSnapshot | None = None,
    ) -> ResourceDecision:
        if type(gpu_required) is not bool:
            raise ResourceGovernorError("gpu_required must be a bool")
        task_limit = self.budget.max_workers if task_cap is None else _require_int(task_cap, field="task_cap")
        observed = self.snapshot() if snapshot is None else snapshot
        if type(observed) is not ResourceSnapshot:
            raise ResourceGovernorError("snapshot must be ResourceSnapshot")
        reasons: list[str] = []
        if observed.source_errors:
            state = "emergency"
            reasons.extend(observed.source_errors)
        elif observed.memory_total_bytes <= 0 or observed.memory_available_bytes <= 0:
            state = "emergency"
            reasons.append("memory telemetry is unavailable or non-positive")
        elif observed.memory_available_bytes < self.budget.critical_free_bytes:
            state = "critical"
            reasons.append("available memory is below critical_free_gib")
        elif observed.memory_available_bytes < max(
            self.budget.safe_free_bytes,
            int(observed.memory_total_bytes * self.budget.safe_free_fraction),
        ):
            state = "warning"
            reasons.append("available memory is below safe free threshold")
        else:
            state = "normal"
        reserve = max(
            self.budget.safe_free_bytes,
            int(observed.memory_total_bytes * self.budget.safe_free_fraction),
        )
        if state == "warning":
            reserve = self.budget.critical_free_bytes
        memory_cap = max(0, (observed.memory_available_bytes - reserve) // self.budget.worker_memory_bytes)
        cpu_cap = min(observed.logical_cpus, self.budget.max_workers)
        gpu_admitted = True
        if gpu_required:
            if observed.gpu_count <= 0:
                gpu_admitted = False
                reasons.append("GPU required but no GPU was observed")
            elif observed.gpu_compute_processes:
                gpu_admitted = False
                reasons.append("GPU compute process detected; admission is refused")
            elif observed.gpu_count > self.budget.gpu_max:
                gpu_admitted = False
                reasons.append("observed GPU count exceeds configured gpu_max")
        if state in {"critical", "emergency"}:
            recommended = 0
        else:
            recommended = min(cpu_cap, memory_cap, task_limit, self.budget.max_workers)
            if gpu_required and not gpu_admitted:
                recommended = 0
        return ResourceDecision(
            state=state,
            recommended_workers=max(0, int(recommended)),
            initial_workers=self.budget.initial_workers,
            max_workers=self.budget.max_workers,
            cpu_worker_cap=cpu_cap,
            memory_worker_cap=memory_cap,
            task_cap=task_limit,
            gpu_admitted=gpu_admitted,
            gpu_compute_processes=observed.gpu_compute_processes,
            kills_performed=0,
            reasons=tuple(reasons),
            snapshot=observed,
        )

    def recommend_workers(
        self, *, task_cap: int | None = None, gpu_required: bool = False,
        snapshot: ResourceSnapshot | None = None,
    ) -> int:
        return self.decide(task_cap=task_cap, gpu_required=gpu_required, snapshot=snapshot).recommended_workers

    def telemetry_payload(
        self, *, task_cap: int | None = None, gpu_required: bool = False,
        snapshot: ResourceSnapshot | None = None,
    ) -> dict[str, object]:
        decision = self.decide(task_cap=task_cap, gpu_required=gpu_required, snapshot=snapshot)
        payload: dict[str, object] = {
            "schema_version": RESOURCE_GOVERNOR_SCHEMA_V1,
            "budget": self.budget.to_dict(),
            "snapshot": decision.snapshot.to_dict(),
            "decision": decision.to_dict(),
            "no_process_kill": True,
        }
        payload["payload_sha256"] = _sha256_bytes(_canonical_bytes(payload))
        return payload

    def write_telemetry(
        self, path: str | Path, *, task_cap: int | None = None,
        gpu_required: bool = False, snapshot: ResourceSnapshot | None = None,
    ) -> str:
        destination = Path(path)
        if destination.exists():
            raise FileExistsError(f"telemetry destination already exists: {destination}")
        payload = self.telemetry_payload(
            task_cap=task_cap, gpu_required=gpu_required, snapshot=snapshot,
        )
        claimed = str(payload["payload_sha256"])
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            # link() is the no-clobber publication point: unlike replace(), it
            # cannot overwrite a destination won by another writer.
            os.link(temporary, destination, follow_symlinks=False)
            try:
                directory_fd = os.open(destination.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            os.unlink(temporary)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return claimed


__all__ = [
    "RESOURCE_GOVERNOR_SCHEMA_V1",
    "ResourceBudget",
    "ResourceDecision",
    "ResourceGovernor",
    "ResourceGovernorError",
    "ResourceSnapshot",
]
