"""Deduplication and conflict quarantine module.

Filters out duplicates, detects label conflicts, and isolates faulty records.
"""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    digest,
    walk_safe,
)


def process_and_deduplicate(
    file_path: str | Path,
    max_record_size: int = 1024 * 1024,  # 1MB
    required_keys: set[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read a JSONL file, separate clean deduplicated records, and quarantine issues."""
    if required_keys is None:
        required_keys = {"episode_id", "decision_id", "state_digest", "teacher_action_key"}

    path = Path(file_path)
    clean_records: list[dict[str, Any]] = []
    quarantine_records: list[dict[str, Any]] = []

    # Map to track state groupings and detect conflicting labels
    # Key: (state_digest, selection_type) -> (record_id, teacher_action_key)
    state_to_label: dict[tuple[str, str], tuple[str, str]] = {}

    # Map to track decision_id -> record_id
    decision_id_to_record: dict[str, str] = {}

    # Set to track exact duplicate record_ids
    seen_record_ids: set[str] = set()

    if not path.exists():
        return [], []

    with path.open("r", encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            record_hash = hashlib.sha256(line.encode("utf-8")).hexdigest() if line else "empty"

            # 1. Size guard
            if len(line.encode("utf-8")) > max_record_size:
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(record_hash)[:16]}",
                    "reason": "oversized record rejection",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [],
                    "safe_summary": {"size_bytes": len(line)},
                    "timestamp": time.time(),
                })
                continue

            if not line.strip():
                continue

            # 2. Corrupt JSON
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(record_hash)[:16]}",
                    "reason": f"corrupt JSON: {exc}",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [],
                    "safe_summary": {},
                    "timestamp": time.time(),
                })
                continue

            if not isinstance(record, dict):
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(record_hash)[:16]}",
                    "reason": "record must be a JSON object",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [],
                    "safe_summary": {},
                    "timestamp": time.time(),
                })
                continue

            # Generate stable record hash based on content (or record_id if available)
            rec_id = record.get("record_id") or record.get("decision_id") or record_hash

            # 3. Missing required fields
            missing = required_keys - set(record)
            if missing:
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(rec_id)[:16]}",
                    "reason": f"missing required field: {sorted(missing)}",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [],
                    "safe_summary": {},
                    "timestamp": time.time(),
                })
                continue

            # 4. Safe values (non-finite and private key validation)
            try:
                walk_safe(record)
            except SupportContractError as exc:
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(rec_id)[:16]}",
                    "reason": f"invalid value/forbidden data: {exc}",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [],
                    "safe_summary": {},
                    "timestamp": time.time(),
                })
                continue

            # 5. Exact duplicates
            if rec_id in seen_record_ids:
                # Silently drop exact duplicates as they do not constitute anomaly
                continue
            seen_record_ids.add(rec_id)

            # 6. Decision ID mapping validation (Same decision ID, different payload)
            dec_id = record["decision_id"]
            if dec_id in decision_id_to_record:
                quarantine_records.append({
                    "schema_version": "support-quarantine-v1",
                    "quarantine_id": f"q_{digest(rec_id)[:16]}",
                    "reason": "same decision ID/different payload",
                    "source_file": str(path),
                    "source_line": line_num,
                    "record_hash": record_hash,
                    "conflicting_record_hashes": [decision_id_to_record[dec_id]],
                    "safe_summary": {"decision_id": dec_id},
                    "timestamp": time.time(),
                })
                continue
            decision_id_to_record[dec_id] = rec_id

            # 7. Same state, conflicting label
            state_dig = record["state_digest"]
            sel_type = str(record.get("selection_type", "default"))
            state_key = (state_dig, sel_type)
            teacher_lbl = str(record.get("teacher_action_key", ""))

            if state_key in state_to_label:
                prev_rec_id, prev_label = state_to_label[state_key]
                if prev_label != teacher_lbl:
                    # Conflicting label conflict quarantine
                    quarantine_records.append({
                        "schema_version": "support-quarantine-v1",
                        "quarantine_id": f"q_{digest(rec_id)[:16]}",
                        "reason": "same state/candidates/conflicting label",
                        "source_file": str(path),
                        "source_line": line_num,
                        "record_hash": record_hash,
                        "conflicting_record_hashes": [prev_rec_id],
                        "safe_summary": {"state_digest": state_dig, "conflict_labels": [prev_label, teacher_lbl]},
                        "timestamp": time.time(),
                    })
                    continue
            else:
                state_to_label[state_key] = (rec_id, teacher_lbl)

            clean_records.append(record)

    return clean_records, quarantine_records


import hashlib
