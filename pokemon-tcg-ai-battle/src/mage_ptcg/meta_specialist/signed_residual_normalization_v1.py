"""Research-only complete-action normalization for signed residual targets.

The current signed trainer consumes prefix rows independently.  This module
does not change that trainer; it defines and tests the weighting contract for
the next arm.  A physical record receives one signed target (the mean of its
aligned prefix targets), and that target is distributed over its prefixes so
the record's total contribution is independent of prefix count.  The
episode-normalized mode additionally gives every episode unit total absolute
mass.  No runtime, CABT, training, or promotion authority is granted here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Iterable, Literal, Mapping


NormalizationModeV1 = Literal["record_normalized", "episode_normalized"]
SCHEMA_V1 = "specialist-signed-residual-normalization-v1"


class SignedResidualNormalizationError(ValueError):
    """Raised when an aligned signed-prefix normalization contract is open."""


@dataclass(frozen=True, slots=True)
class SignedPrefixWeightV1:
    episode_id: str
    record_id: str
    prefix_index: int
    signed_weight: float

    def __post_init__(self) -> None:
        if type(self.episode_id) is not str or not self.episode_id:
            raise SignedResidualNormalizationError("episode_id must be nonempty")
        if type(self.record_id) is not str or not self.record_id:
            raise SignedResidualNormalizationError("record_id must be nonempty")
        if type(self.prefix_index) is not int or self.prefix_index < 0:
            raise SignedResidualNormalizationError("prefix_index must be nonnegative")
        if type(self.signed_weight) not in (int, float) or type(self.signed_weight) is bool:
            raise SignedResidualNormalizationError("signed_weight must be numeric")
        if not math.isfinite(float(self.signed_weight)) or not -1.0 <= float(self.signed_weight) <= 1.0:
            raise SignedResidualNormalizationError("signed_weight must be finite in [-1, 1]")


@dataclass(frozen=True, slots=True)
class SignedNormalizedWeightsV1:
    schema_version: str
    mode: str
    weights: tuple[float, ...]
    record_total_abs: float
    episode_total_abs: float
    by_record_abs: Mapping[str, float]
    by_episode_abs: Mapping[str, float]
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False
    performance_evidence: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1:
            raise SignedResidualNormalizationError("normalization schema is invalid")
        if self.mode not in {"record_normalized", "episode_normalized"}:
            raise SignedResidualNormalizationError("normalization mode is invalid")
        if any(not math.isfinite(float(value)) for value in self.weights):
            raise SignedResidualNormalizationError("normalized weights must be finite")
        for name in ("record_total_abs", "episode_total_abs"):
            value = getattr(self, name)
            if not math.isfinite(float(value)) or value < 0.0:
                raise SignedResidualNormalizationError(f"{name} must be nonnegative and finite")
        for name in ("by_record_abs", "by_episode_abs"):
            mapping = getattr(self, name)
            if not isinstance(mapping, Mapping) or any(float(value) < 0.0 for value in mapping.values()):
                raise SignedResidualNormalizationError(f"{name} is invalid")
            object.__setattr__(self, name, MappingProxyType(dict(mapping)))
        for name in ("training_permitted", "promotion_authority", "longrun_allowed", "performance_evidence"):
            if getattr(self, name) is not False:
                raise SignedResidualNormalizationError(f"normalization cannot grant {name}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "weights": list(self.weights),
            "record_total_abs": self.record_total_abs,
            "episode_total_abs": self.episode_total_abs,
            "by_record_abs": dict(sorted(self.by_record_abs.items())),
            "by_episode_abs": dict(sorted(self.by_episode_abs.items())),
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
            "performance_evidence": False,
        }


def _validate_rows(rows: Iterable[SignedPrefixWeightV1]) -> tuple[SignedPrefixWeightV1, ...]:
    materialized = tuple(rows)
    if not materialized:
        raise SignedResidualNormalizationError("rows must be nonempty")
    if any(type(row) is not SignedPrefixWeightV1 for row in materialized):
        raise SignedResidualNormalizationError("rows must contain exact SignedPrefixWeightV1")
    grouped: dict[str, list[SignedPrefixWeightV1]] = defaultdict(list)
    episode_for_record: dict[str, str] = {}
    for row in materialized:
        prior_episode = episode_for_record.setdefault(row.record_id, row.episode_id)
        if prior_episode != row.episode_id:
            raise SignedResidualNormalizationError("record belongs to multiple episodes")
        grouped[row.record_id].append(row)
    for record_id, record_rows in grouped.items():
        indices = sorted(row.prefix_index for row in record_rows)
        if indices != list(range(len(indices))):
            raise SignedResidualNormalizationError(f"prefix indices must be contiguous for {record_id}")
    return materialized


def normalize_signed_prefix_weights_v1(
    rows: Iterable[SignedPrefixWeightV1],
    *,
    mode: NormalizationModeV1,
) -> SignedNormalizedWeightsV1:
    """Normalize aligned prefix targets without prefix-count over-weighting."""

    if mode not in {"record_normalized", "episode_normalized"}:
        raise SignedResidualNormalizationError("normalization mode is invalid")
    materialized = _validate_rows(rows)
    records: dict[str, list[SignedPrefixWeightV1]] = defaultdict(list)
    for row in materialized:
        records[row.record_id].append(row)
    record_episode: dict[str, str] = {}
    record_raw: dict[str, float] = {}
    for record_id, record_rows in records.items():
        record_rows.sort(key=lambda row: row.prefix_index)
        record_episode[record_id] = record_rows[0].episode_id
        record_raw[record_id] = sum(float(row.signed_weight) for row in record_rows) / len(record_rows)

    episode_denominator: dict[str, float] = defaultdict(float)
    if mode == "episode_normalized":
        for record_id, raw in record_raw.items():
            episode_denominator[record_episode[record_id]] += abs(raw)

    normalized_by_record: dict[str, float] = {}
    for record_id, raw in record_raw.items():
        if mode == "record_normalized":
            normalized_by_record[record_id] = raw
        else:
            denominator = episode_denominator[record_episode[record_id]]
            normalized_by_record[record_id] = raw / denominator if denominator else 0.0

    output: list[float] = []
    by_record_abs: dict[str, float] = defaultdict(float)
    by_episode_abs: dict[str, float] = defaultdict(float)
    for row in materialized:
        record_rows = records[row.record_id]
        coefficient = normalized_by_record[row.record_id] / len(record_rows)
        output.append(coefficient)
        by_record_abs[row.record_id] += abs(coefficient)
        by_episode_abs[row.episode_id] += abs(coefficient)
    return SignedNormalizedWeightsV1(
        schema_version=SCHEMA_V1,
        mode=mode,
        weights=tuple(output),
        record_total_abs=sum(by_record_abs.values()),
        episode_total_abs=sum(by_episode_abs.values()),
        by_record_abs=dict(by_record_abs),
        by_episode_abs=dict(by_episode_abs),
    )


__all__ = [
    "NormalizationModeV1",
    "SCHEMA_V1",
    "SignedResidualNormalizationError",
    "SignedPrefixWeightV1",
    "SignedNormalizedWeightsV1",
    "normalize_signed_prefix_weights_v1",
]
