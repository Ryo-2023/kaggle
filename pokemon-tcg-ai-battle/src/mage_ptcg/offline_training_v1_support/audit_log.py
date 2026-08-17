"""Audit logging and event sourcing module.

Implements sequential hash-chained logs tracking support operations atomically,
shielding credentials and private details.
"""

from __future__ import annotations

import os
import time
import json
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    FileLock,
    SupportContractError,
    digest,
    walk_safe,
    load_records,
)

class AuditLogger:
    """Manages an append-only, hash-chained audit trail of operations."""

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.log_path.parent / "audit_log.lock"

    def _get_last_event_hash(self) -> str:
        """Find the hash of the last successfully written event in the chain."""
        if not self.log_path.exists():
            return "0" * 64
        try:
            records = load_records(self.log_path)
            if not records:
                return "0" * 64
            # Return hash of the last record
            return records[-1].get("event_hash", "0" * 64)
        except Exception:
            return "0" * 64

    def log_event(
        self,
        operation: str,
        actor_type: str,
        artifact_type: str,
        artifact_id: str,
        input_hashes: list[str],
        output_hashes: list[str],
        status: str,
        safe_summary: str,
    ) -> str:
        """Atomically append a new event to the audit trail chain."""
        with FileLock(self.lock_path):
            prev_hash = self._get_last_event_hash()

            event = {
                "schema_version": "support-audit-event-v1",
                "event_id": digest(f"{operation}:{time.time()}", domain="audit-event-id"),
                "timestamp": time.time(),
                "operation": operation,
                "actor_type": actor_type,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "input_hashes": sorted(input_hashes),
                "output_hashes": sorted(output_hashes),
                "status": status,
                "safe_summary": safe_summary,
                "previous_event_hash": prev_hash,
            }

            # Safety check: redact any credentials or private data from safety summary
            walk_safe(event)

            # Compute actual self event_hash
            event_hash = digest({k: v for k, v in event.items() if k != "event_hash"}, domain="audit-event-hash")
            event["event_hash"] = event_hash

            # Atomic append write under lock
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

            return event_hash

    def verify_chain(self) -> list[str]:
        """Verify the integrity of the audit log chain, detecting truncations or corruptions."""
        corruptions = []
        if not self.log_path.exists():
            return corruptions

        try:
            records = load_records(self.log_path)
        except Exception as exc:
            corruptions.append(f"Failed to parse audit log records: {exc}")
            return corruptions

        expected_prev_hash = "0" * 64
        for number, event in enumerate(records, 1):
            event_hash = event.get("event_hash")
            prev_hash = event.get("previous_event_hash")

            if prev_hash != expected_prev_hash:
                corruptions.append(
                    f"Chain broken at record {number}: expected previous hash {expected_prev_hash}, got {prev_hash}"
                )

            # Compute and verify hash
            clean_event = {k: v for k, v in event.items() if k != "event_hash"}
            calc_hash = digest(clean_event, domain="audit-event-hash")
            if calc_hash != event_hash:
                corruptions.append(
                    f"Hash mismatch at record {number}: recorded {event_hash}, calculated {calc_hash}"
                )

            expected_prev_hash = event_hash

        return corruptions
