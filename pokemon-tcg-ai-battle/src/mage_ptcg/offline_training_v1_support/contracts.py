"""Contracts and utility functions for Offline Training support.

Provides JSON validation, atomic file writes, canonical hashing, and locking.
"""

from __future__ import annotations

import os
import sys
import math
import time
import json
import socket
import hashlib
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

SUPPORT_SCHEMA_VERSION = "support-v1"

# Privacy-sensitive keys based on repository standards
PRIVATE_KEYS = frozenset({
    "token", "email", "cookie", "header", "authorization", "signed_url",
    "search_begin_input", "private_action_key_digest", "action_key_core",
    "opponent_hand", "opponent_hand_ids", "opponent_deck", "raw_observation",
    "oauth", "api_key", "Bearer", "Authorization", "private_hand", "deck_order"
})


class SupportContractError(ValueError):
    """Raised when an operation or data violates the support contract."""


def dict_raise_on_duplicates(ordered_pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Raise SupportContractError if duplicate keys are detected in JSON parsing."""
    d = {}
    for k, v in ordered_pairs:
        if k in d:
            raise SupportContractError(f"Duplicate JSON key detected: {k}")
        d[k] = v
    return d


def safe_json_loads(s: str) -> Any:
    """Parse JSON string and reject duplicate keys."""
    try:
        return json.loads(s, object_pairs_hook=dict_raise_on_duplicates)
    except SupportContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise SupportContractError("Invalid or duplicate key JSON string") from exc


def canonical_json(value: object) -> str:
    """Serialize value to a stable, sorted JSON string without non-finite values."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SupportContractError("Value is not finite canonical JSON") from exc


def digest(value: object, *, domain: str = "support") -> str:
    """Generate SHA-256 hash of canonical JSON with domain salt."""
    serialized = canonical_json(value)
    return hashlib.sha256(f"mage_ptcg:{domain}:v1\0".encode("utf-8") + serialized.encode("utf-8")).hexdigest()


def walk_safe(value: object, *, path: str = "$") -> None:
    """Ensure no private keys, paths, or non-finite values exist in the object."""
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < -9223372036854775808 or value > 9223372036854775807:
            raise SupportContractError(f"Integer out of 64-bit signed range at {path}")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SupportContractError(f"Non-finite value at {path}")
        if value == 0.0 and math.copysign(1, value) < 0:
            raise SupportContractError(f"Negative zero not allowed at {path}")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise SupportContractError(f"Non-string key at {path}")
            lowered = key.lower()
            if key in PRIVATE_KEYS or lowered in PRIVATE_KEYS:
                raise SupportContractError(f"Forbidden key {key!r} at {path}")
            walk_safe(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            walk_safe(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        # Prevent surrogate pair issues in JSON
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise SupportContractError(f"Surrogate character detected in string at {path}")
        if not unicodedata.is_normalized('NFC', value):
            raise SupportContractError(f"Unicode string not normalized to NFC at {path}")
        # Prevent leakage of raw system paths
        if any(pat in value for pat in ("/home/", "/mnt/", "/Users/", "C:\\")):
            raise SupportContractError(f"Path-like private value at {path}")
        # Prevent common secret tokens
        for forbidden in ("oauth", "token", "cookie", "Authorization", "Bearer", "api_key"):
            if forbidden.lower() in value.lower():
                raise SupportContractError(f"Potential secret leaked in value at {path}: {forbidden}")
    else:
        raise SupportContractError(f"Unsupported type {type(value)} at {path}")


def atomic_write_json(path: str | Path, value: object) -> None:
    """Atomically write value as canonical JSON to destination path."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    walk_safe(value)
    encoded = canonical_json(value) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            dir_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass
    except Exception as exc:
        if temporary and temporary.exists():
            try:
                temporary.unlink()
            except Exception:
                pass
        raise exc


def atomic_write_records(path: str | Path, records: Iterable[dict[str, object]]) -> int:
    """Atomically write records as JSONL to destination path."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    values = list(records)
    for record in values:
        walk_safe(record)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            for record in values:
                handle.write(canonical_json(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            dir_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass
    except Exception as exc:
        if temporary and temporary.exists():
            try:
                temporary.unlink()
            except Exception:
                pass
        raise exc
    return len(values)


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL records from path safely."""
    path = Path(path)
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SupportContractError(f"invalid JSONL line {number}") from exc
            if not isinstance(item, dict):
                raise SupportContractError(f"invalid record at line {number}")
            walk_safe(item)
            values.append(item)
    return values


class FileLockError(TimeoutError):
    """Raised when file lock acquisition times out."""


def is_pid_active(pid: int) -> bool:
    """Check if a process with given PID is running on local system."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


class FileLock:
    """PID/Hostname/Time based file locking mechanism."""

    def __init__(self, lock_path: str | Path, timeout: float = 10.0, stale_threshold: float = 30.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.stale_threshold = stale_threshold
        self.lock_info = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": time.time()
        }

    def __enter__(self) -> FileLock:
        start_time = time.time()
        while True:
            try:
                payload = json.dumps(self.lock_info).encode("utf-8")
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, payload)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return self
            except FileExistsError:
                # Check for stale lock
                try:
                    if self.lock_path.exists():
                        content = self.lock_path.read_text(encoding="utf-8")
                        info = json.loads(content)
                        pid = info.get("pid", 0)
                        hostname = info.get("hostname", "")
                        created_at = info.get("created_at", 0)

                        # Detect reentrancy on the same process/thread
                        if pid == self.lock_info["pid"] and hostname == self.lock_info["hostname"]:
                            raise FileLockError("Reentrant lock acquisition not supported")

                        # A lock is stale if:
                        # 1. it has expired based on stale_threshold AND/OR
                        # 2. the PID is no longer active on the same hostname
                        is_stale = False
                        if time.time() - created_at > self.stale_threshold:
                            is_stale = True
                        elif hostname == self.lock_info["hostname"] and not is_pid_active(pid):
                            is_stale = True

                        if is_stale:
                            self.lock_path.unlink(missing_ok=True)
                            continue
                except FileLockError:
                    raise
                except Exception:
                    # Clean up corrupt lock files
                    self.lock_path.unlink(missing_ok=True)
                    continue

                if time.time() - start_time > self.timeout:
                    raise FileLockError(f"Failed to acquire lock on {self.lock_path}: timeout exceeded")
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if self.lock_path.exists():
                content = self.lock_path.read_text(encoding="utf-8")
                info = json.loads(content)
                if info.get("pid") == self.lock_info["pid"] and info.get("hostname") == self.lock_info["hostname"]:
                    self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
