"""Teacher cache management module.

Implements content-addressed caching for teacher outputs, including integrity validation
and corrupt cache quarantining.
"""

from __future__ import annotations

import time
import json
import shutil
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    atomic_write_json,
    digest,
    walk_safe,
)


class TeacherCache:
    """Content-addressed cache storing teacher action selections and margins."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.entry_dir = self.cache_dir / "entries"
        self.quarantine_dir = self.cache_dir / "quarantine"
        self.manifest_path = self.cache_dir / "cache_manifest.json"

        self.entry_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        self._initialize_manifest()

    def _initialize_manifest(self) -> None:
        if not self.manifest_path.exists():
            manifest = {
                "query_count": 0,
                "hit_count": 0,
                "miss_count": 0,
                "corruption_count": 0,
                "failure_count": 0,
                "updated_at": time.time(),
            }
            atomic_write_json(self.manifest_path, manifest)

    def _update_manifest_stats(self, key: str, increment: int = 1) -> None:
        try:
            with self.manifest_path.open("r", encoding="utf-8") as f:
                stats = json.load(f)
            stats[key] = stats.get(key, 0) + increment
            stats["updated_at"] = time.time()
            atomic_write_json(self.manifest_path, stats)
        except Exception:
            pass

    def get_public_stats(self) -> dict[str, int]:
        """Return public stats.

        Safely excludes any private hashes, state digests, or credentials.
        """
        try:
            with self.manifest_path.open("r", encoding="utf-8") as f:
                stats = json.load(f)
            return {
                "query_count": stats.get("query_count", 0),
                "hit_count": stats.get("hit_count", 0),
                "miss_count": stats.get("miss_count", 0),
                "corruption_count": stats.get("corruption_count", 0),
                "failure_count": stats.get("failure_count", 0),
            }
        except Exception:
            return {}

    def make_cache_key(
        self,
        teacher_id: str,
        teacher_version: str,
        teacher_config_hash: str,
        input_schema_version: str,
        state_digest: str,
        candidate_digest: str,
    ) -> str:
        """Create a stable hash cache key, shielding raw systems paths or private hand states."""
        payload = {
            "teacher_id": teacher_id,
            "teacher_version": teacher_version,
            "teacher_config_hash": teacher_config_hash,
            "input_schema_version": input_schema_version,
            "state_digest": state_digest,
            "candidate_digest": candidate_digest,
        }
        return digest(payload, domain="teacher-cache-key")

    def lookup(self, cache_key: str) -> dict[str, Any] | None:
        """Retrieve output from cache.

        Validates checksum and unlinks/quarantines corrupt records.
        """
        self._update_manifest_stats("query_count")
        target = self.entry_dir / f"tc_{cache_key}.json"
        if not target.exists():
            self._update_manifest_stats("miss_count")
            return None

        try:
            with target.open("r", encoding="utf-8") as f:
                entry = json.load(f)

            # Check privacy constraints
            walk_safe(entry)

            # Check checksum
            expected_sha = entry.get("checksum")
            actual_sha = digest({k: v for k, v in entry.items() if k != "checksum"}, domain="teacher-cache-val")
            if expected_sha != actual_sha:
                raise SupportContractError("Cache entry checksum mismatch")

            self._update_manifest_stats("hit_count")
            return entry.get("output")

        except Exception as exc:
            # Quarantine corruption
            self._update_manifest_stats("corruption_count")
            dest = self.quarantine_dir / target.name
            try:
                if dest.exists():
                    dest.unlink(missing_ok=True)
                shutil.move(str(target), str(dest))
            except Exception:
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass
            return None

    def store(self, cache_key: str, teacher_info: dict[str, str], output: dict[str, Any]) -> None:
        """Store teacher output in the cache atomically."""
        # Check output privacy
        walk_safe(output)

        entry = {
            "schema_version": "support-teacher-cache-entry-v1",
            "cache_key_hash": cache_key,
            "teacher_id": teacher_info.get("teacher_id"),
            "teacher_version": teacher_info.get("teacher_version"),
            "output": output,
            "created_at": time.time(),
        }

        checksum = digest(entry, domain="teacher-cache-val")
        entry["checksum"] = checksum

        target = self.entry_dir / f"tc_{cache_key}.json"
        atomic_write_json(target, entry)
