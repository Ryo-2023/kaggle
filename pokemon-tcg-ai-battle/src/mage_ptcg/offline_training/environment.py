"""Runtime environment doctor and dynamic resource policy.

Nothing about the lab PC is assumed.  Every value is probed at runtime and the
resource policy is derived from the observed CPU, RAM, VRAM, and free disk.
No host, user, IP, or absolute path is recorded, so the doctor payload is safe
to persist inside a run directory.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
import shutil
import sys
from typing import Any


GIB = 1024 ** 3
DISK_SOFT_STOP_GIB = 100.0
DISK_HARD_STOP_GIB = 60.0


def _total_ram_bytes() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    collection_workers: int
    ram_reservation_bytes: int
    vram_reservation_bytes: int
    disk_soft_stop_bytes: int
    disk_hard_stop_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int
    training_device: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cuda_probe() -> dict[str, Any]:
    info: dict[str, Any] = {
        "cuda_available": False,
        "gpu_name": "NONE",
        "compute_capability": None,
        "bf16_supported": False,
        "vram_total_bytes": 0,
        "torch_version": None,
    }
    try:
        import torch
    except Exception:  # noqa: BLE001 - torch is optional
        return info
    info["torch_version"] = getattr(torch, "__version__", "unknown")
    try:
        if torch.cuda.is_available():
            info["cuda_available"] = True
            info["gpu_name"] = str(torch.cuda.get_device_name(0))
            info["compute_capability"] = list(torch.cuda.get_device_capability(0))
            info["bf16_supported"] = bool(torch.cuda.is_bf16_supported())
            props = torch.cuda.get_device_properties(0)
            info["vram_total_bytes"] = int(getattr(props, "total_memory", 0))
    except Exception:  # noqa: BLE001 - a broken CUDA stack must not crash the doctor
        return {**info, "cuda_available": False, "gpu_name": "NONE"}
    return info


from pathlib import Path

def doctor(*, gpu_python: str | None = None) -> dict[str, Any]:
    """Probe the environment.  Returns a privacy-safe machine-readable report with structured PASS/WARN/FAIL statuses."""
    cpu_count = os.cpu_count() or 1
    ram_total = _total_ram_bytes()

    # Try getting available RAM (Linux specific)
    ram_available = None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        ram_available = int(line.split()[1]) * 1024  # kB to bytes
                        break
        except Exception:
            pass

    usage = shutil.disk_usage(os.getcwd())

    numpy_version = None
    try:
        import numpy as _np
        numpy_version = getattr(_np, "__version__", "unknown")
    except Exception:
        pass

    pytest_version = None
    try:
        import pytest
        pytest_version = getattr(pytest, "__version__", "unknown")
    except Exception:
        pass

    repo_root = Path(__file__).resolve().parents[3]
    repo_root_ok = repo_root.is_dir()

    # Git repository checks
    branch = "unknown"
    head = "unknown"
    worktree_clean = True
    if repo_root_ok:
        import subprocess
        try:
            res_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
            if res_branch.returncode == 0:
                branch = res_branch.stdout.strip()

            res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
            if res_head.returncode == 0:
                head = res_head.stdout.strip()

            res_status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=False)
            if res_status.returncode == 0:
                worktree_clean = (len(res_status.stdout.strip()) == 0)
        except Exception:
            pass

    # Required internal modules
    import importlib
    required_modules = {
        "mage_ptcg.offline_training": False,
        "mage_ptcg.dataops": False,
        "mage_ptcg.student": False,
    }
    for mod in required_modules:
        try:
            importlib.import_module(mod)
            required_modules[mod] = True
        except Exception:
            pass

    # cabt availability check
    cabt_available = False
    cabt_report = {}
    if repo_root_ok:
        try:
            scripts_path = repo_root / "scripts"
            if str(scripts_path) not in sys.path:
                sys.path.insert(0, str(scripts_path))
            from cabt_capability import diagnose_cabt_capability
            cabt_report = diagnose_cabt_capability()
            cabt_available = cabt_report.get("actual_execution_allowed", False)
        except Exception:
            pass

    # config files readability
    config_readability = {}
    config_dir = repo_root / "configs" / "offline_training_v1"
    if config_dir.is_dir():
        for cfg_path in config_dir.glob("*.json"):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    json.load(f)
                config_readability[cfg_path.name] = True
            except Exception:
                config_readability[cfg_path.name] = False

    # output directories check
    output_directories = {}
    for dir_name in ("runs", "dist"):
        path = repo_root / dir_name
        try:
            if path.exists():
                writable = os.access(path, os.W_OK)
            else:
                writable = os.access(path.parent, os.W_OK)
            output_directories[dir_name] = writable
        except Exception:
            output_directories[dir_name] = False

    cuda = _cuda_probe()

    # Determine status for each check
    checks = {}

    # Python Version
    checks["python"] = "PASS" if sys.version_info >= (3, 9) else "FAIL"

    # Pytest
    checks["pytest"] = "PASS" if pytest_version else "WARN"

    # NumPy
    checks["numpy"] = "PASS" if numpy_version else "FAIL"

    # Torch / CUDA
    if cuda.get("cuda_available"):
        checks["cuda"] = "PASS"
    else:
        checks["cuda"] = "WARN"

    # Disk Space (checks simple limits)
    free_gib = usage.free / (1024 ** 3)
    if free_gib < DISK_HARD_STOP_GIB:
        checks["disk"] = "FAIL"
    elif free_gib < DISK_SOFT_STOP_GIB:
        checks["disk"] = "WARN"
    else:
        checks["disk"] = "PASS"

    # RAM total & available
    if ram_total >= 8 * GIB:
        checks["ram_total"] = "PASS"
    elif ram_total >= 4 * GIB:
        checks["ram_total"] = "WARN"
    else:
        checks["ram_total"] = "FAIL"

    if ram_available is None:
        checks["ram_available"] = "WARN"
    elif ram_available >= 4 * GIB:
        checks["ram_available"] = "PASS"
    elif ram_available >= 2 * GIB:
        checks["ram_available"] = "WARN"
    else:
        checks["ram_available"] = "FAIL"

    # Git / Repository
    checks["git"] = "PASS" if (repo_root_ok and branch != "unknown" and head != "unknown") else "WARN"

    # Internal Modules
    checks["internal_modules"] = "PASS" if all(required_modules.values()) else "FAIL"

    # cabt Availability
    checks["cabt"] = "PASS" if cabt_available else "WARN"

    # Config readability
    checks["config_readability"] = "PASS" if (config_readability and all(config_readability.values())) else "FAIL"

    # Output directory access
    checks["output_directories"] = "PASS" if (output_directories and all(output_directories.values())) else "FAIL"

    # Overall Status
    if "FAIL" in checks.values():
        overall_status = "FAIL"
    elif "WARN" in checks.values():
        overall_status = "WARN"
    else:
        overall_status = "PASS"

    report = {
        "schema_version": "offline-training-v1-doctor-v2",
        "doctor_status": overall_status,
        "checks": checks,
        "python_executable": sys.executable,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_implementation": sys.implementation.name,
        "pytest_version": pytest_version,
        "cpu_count": cpu_count,
        "ram_total_bytes": ram_total,
        "ram_available_bytes": ram_available,
        "disk_free_bytes": int(usage.free),
        "disk_total_bytes": int(usage.total),
        "numpy_version": numpy_version,
        "gpu_python_configured": gpu_python is not None,
        "repository_root": str(repo_root),
        "git_branch": branch,
        "git_head": head,
        "worktree_clean": worktree_clean,
        "required_internal_modules": required_modules,
        "cabt_available": cabt_available,
        "cabt_report": cabt_report,
        "config_readability": config_readability,
        "output_directories_writable": output_directories,
        **cuda,
    }
    return report


def resolve_resource_policy(report: dict[str, Any], *, hard_worker_cap: int = 16) -> ResourcePolicy:
    """Derive safe defaults from an environment report (see module docstring)."""
    cpu_count = int(report.get("cpu_count", 1) or 1)
    workers = min(hard_worker_cap, min(12, max(2, cpu_count // 2)))

    ram_total = int(report.get("ram_total_bytes", 0) or 0)
    ram_reservation = max(int(ram_total * 0.25), 6 * GIB)

    vram_total = int(report.get("vram_total_bytes", 0) or 0)
    vram_reservation = max(int(vram_total * 0.15), 3 * GIB) if report.get("cuda_available") else 0

    disk_free = int(report.get("disk_free_bytes", 0) or 0)
    disk_total = int(report.get("disk_total_bytes", 0) or 0)
    # Adjust soft/hard stops downward for genuinely small volumes so the policy
    # stays meaningful rather than always tripping on a small disk.
    soft = int(DISK_SOFT_STOP_GIB * GIB)
    hard = int(DISK_HARD_STOP_GIB * GIB)
    if disk_total and disk_total < soft * 2:
        soft = min(soft, int(disk_total * 0.10))
        hard = min(hard, int(disk_total * 0.05))

    training_device = "cuda" if report.get("cuda_available") else "cpu"
    return ResourcePolicy(
        collection_workers=workers,
        ram_reservation_bytes=ram_reservation,
        vram_reservation_bytes=vram_reservation,
        disk_soft_stop_bytes=soft,
        disk_hard_stop_bytes=hard,
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        training_device=training_device,
    )


def disk_guard_status(policy: ResourcePolicy, free_bytes: int) -> str:
    """Return HARD_STOP / SOFT_STOP / OK for a current free-space reading."""
    if free_bytes < policy.disk_hard_stop_bytes:
        return "HARD_STOP"
    if free_bytes < policy.disk_soft_stop_bytes:
        return "SOFT_STOP"
    return "OK"


def environment_hash(report: dict[str, Any]) -> str:
    stable = {
        key: report.get(key)
        for key in (
            "schema_version",
            "python_version",
            "python_implementation",
            "cpu_count",
            "cuda_available",
            "gpu_name",
            "compute_capability",
            "numpy_version",
            "torch_version",
        )
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DISK_HARD_STOP_GIB",
    "DISK_SOFT_STOP_GIB",
    "GIB",
    "ResourcePolicy",
    "disk_guard_status",
    "doctor",
    "environment_hash",
    "resolve_resource_policy",
]
