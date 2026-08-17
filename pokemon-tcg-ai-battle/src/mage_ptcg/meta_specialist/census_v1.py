"""§5 Census seal and missing attribute sensitivity analysis module.

Enforces dataset coverage invariants:
- Gold tier coverage seal: 100% complete
- Total dataset coverage seal: >= 98% complete
- Missing attribute sensitivity quantification
- Census metadata persistence and validation
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class CensusV1Error(ValueError):
    """Raised when census invariants or sensitivity bounds are violated."""


@dataclass(frozen=True, slots=True)
class CensusRecordV1:
    record_id: str
    tier: str  # "Gold", "Silver", "Bronze"
    is_complete: bool
    missing_fields: tuple[str, ...] = ()
    deck_hash: str = ""
    replay_hash: str = ""

    def __post_init__(self) -> None:
        if self.tier not in ("Gold", "Silver", "Bronze"):
            raise CensusV1Error(f"invalid tier: {self.tier}")
        if not self.record_id:
            raise CensusV1Error("record_id cannot be empty")


@dataclass(frozen=True, slots=True)
class CensusSealReportV1:
    gold_count: int
    gold_complete_count: int
    gold_coverage_rate: float
    total_count: int
    total_complete_count: int
    total_coverage_rate: float
    is_sealed: bool
    missing_sensitivity: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "gold_count": self.gold_count,
            "gold_complete_count": self.gold_complete_count,
            "gold_coverage_rate": round(self.gold_coverage_rate, 4),
            "total_count": self.total_count,
            "total_complete_count": self.total_complete_count,
            "total_coverage_rate": round(self.total_coverage_rate, 4),
            "is_sealed": self.is_sealed,
            "missing_sensitivity": self.missing_sensitivity,
        }


def verify_census_seal_v1(records: Sequence[CensusRecordV1]) -> CensusSealReportV1:
    """Verify Gold 100% and Total >= 98% census seal constraints."""
    if not records:
        raise CensusV1Error("records collection cannot be empty")

    gold_records = [r for r in records if r.tier == "Gold"]
    gold_count = len(gold_records)
    gold_complete = sum(1 for r in gold_records if r.is_complete)
    gold_rate = (gold_complete / gold_count) if gold_count > 0 else 1.0

    total_count = len(records)
    total_complete = sum(1 for r in records if r.is_complete)
    total_rate = total_complete / total_count

    # Constraints: Gold == 100%, Total >= 98%
    is_sealed = (gold_rate >= 1.0) and (total_rate >= 0.98)
    sensitivity = calculate_missing_sensitivity_v1(records)

    return CensusSealReportV1(
        gold_count=gold_count,
        gold_complete_count=gold_complete,
        gold_coverage_rate=gold_rate,
        total_count=total_count,
        total_complete_count=total_complete,
        total_coverage_rate=total_rate,
        is_sealed=is_sealed,
        missing_sensitivity=sensitivity,
    )


def calculate_missing_sensitivity_v1(records: Sequence[CensusRecordV1]) -> dict[str, float]:
    """Calculate sensitivity impact per missing field."""
    field_counts: dict[str, int] = {}
    total = len(records)
    if total == 0:
        return {}

    for r in records:
        for field in r.missing_fields:
            field_counts[field] = field_counts.get(field, 0) + 1

    return {field: count / total for field, count in sorted(field_counts.items())}


def save_census_report_v1(report: CensusSealReportV1, output_path: Path) -> None:
    """Persist sealed census report atomically."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    temp_path.replace(output_path)
