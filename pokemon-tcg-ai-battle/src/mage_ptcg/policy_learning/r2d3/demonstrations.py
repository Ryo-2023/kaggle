"""Manifest-backed demonstrations; they are replay sources, not labels at runtime."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DemonstrationRecord:
    sequence_id: str
    source_policy_hash: str
    source_lineage: str
    deck_hash: str
    side: int
    outcome: str
    strength_evidence: str


def write_registry(path: str | Path, records: Iterable[DemonstrationRecord]) -> dict[str, object]:
    values = [asdict(value) for value in records]; payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")); result = {"schema": "r2d3-demonstration-registry-v1", "records": values, "hash": hashlib.sha256(payload.encode()).hexdigest()}
    Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); return result
