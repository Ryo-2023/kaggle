"""Near-duplicate-safe DAgger/ExIt record store."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class DAggerRecordV1:
    state_hash: str
    policy_version: str
    teacher_distribution: dict[str, float]
    teacher_confidence: float
    query_reason: str
    opponent_provenance: str

    def __post_init__(self) -> None:
        if len(self.state_hash) != 64 or not self.policy_version or not self.query_reason:
            raise ValueError("DAgger identity fields are invalid")
        if not 0 <= self.teacher_confidence <= 1 or not self.teacher_distribution or any(value < 0 for value in self.teacher_distribution.values()):
            raise ValueError("DAgger distribution/confidence is invalid")
        total = sum(self.teacher_distribution.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError("teacher distribution must sum to one")


class DAggerDatasetV1:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DAggerRecordV1] = {}

    def add(self, record: DAggerRecordV1) -> bool:
        key = (record.state_hash, record.policy_version)
        if key in self._records:
            return False
        self._records[key] = record
        return True

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[DAggerRecordV1, ...]:
        return tuple(self._records[key] for key in sorted(self._records))


__all__ = ["DAggerDatasetV1", "DAggerRecordV1"]
