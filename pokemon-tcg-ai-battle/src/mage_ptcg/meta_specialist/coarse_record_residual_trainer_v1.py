"""Research-only complete-action trainer for a coarse residual table.

This module is deliberately separate from the V4 trainer and runtime.  It
aggregates all prefixes belonging to one physical record into one selected
complete-action log-probability, then applies record- or episode-normalized
signed weights.  Only the bounded residual table is optimized; base logits are
provided as detached sealed inputs.  The module grants no training,
performance, promotion, or long-run authority.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Iterable, Mapping

import torch
from torch import Tensor, nn

SCHEMA_V1 = "specialist-coarse-complete-action-residual-trainer-v1"
_HEX64 = frozenset("0123456789abcdef")


class CoarseRecordResidualTrainerError(ValueError):
    """Raised when a closed coarse complete-action training contract fails."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise CoarseRecordResidualTrainerError(f"{field} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class CoarsePrefixLogitRowV1:
    """One sealed prefix row with base logits and a legal action domain."""

    episode_id: str
    record_id: str
    prefix_index: int
    bucket_id: str
    action_keys: tuple[str, ...]
    base_logits: tuple[float, ...]
    target_index: int
    signed_weight: float

    def __post_init__(self) -> None:
        if type(self.episode_id) is not str or not self.episode_id:
            raise CoarseRecordResidualTrainerError("episode_id must be nonempty")
        if type(self.record_id) is not str or not self.record_id:
            raise CoarseRecordResidualTrainerError("record_id must be nonempty")
        if type(self.prefix_index) is not int or self.prefix_index < 0:
            raise CoarseRecordResidualTrainerError("prefix_index must be nonnegative")
        if type(self.signed_weight) not in (int, float) or type(self.signed_weight) is bool or not math.isfinite(float(self.signed_weight)) or not -1.0 <= float(self.signed_weight) <= 1.0:
            raise CoarseRecordResidualTrainerError("signed_weight must be finite in [-1, 1]")
        _sha(self.bucket_id, field="bucket_id")
        if type(self.action_keys) is not tuple or not self.action_keys:
            raise CoarseRecordResidualTrainerError("action_keys must be a nonempty tuple")
        if tuple(sorted(self.action_keys)) != self.action_keys or len(set(self.action_keys)) != len(self.action_keys):
            raise CoarseRecordResidualTrainerError("action_keys must be sorted and duplicate-free")
        for key in self.action_keys:
            _sha(key, field="action_keys[]")
        if type(self.base_logits) is not tuple or len(self.base_logits) != len(self.action_keys):
            raise CoarseRecordResidualTrainerError("base_logits/action_keys arity mismatch")
        if any(type(value) not in (int, float) or type(value) is bool or math.isnan(float(value)) for value in self.base_logits):
            raise CoarseRecordResidualTrainerError("base logits must be finite or legal -inf values")
        if not any(math.isfinite(float(value)) for value in self.base_logits):
            raise CoarseRecordResidualTrainerError("base logits contain no finite legal action")
        if type(self.target_index) is not int or not 0 <= self.target_index < len(self.action_keys):
            raise CoarseRecordResidualTrainerError("target_index is outside the legal domain")
        if not math.isfinite(float(self.base_logits[self.target_index])):
            raise CoarseRecordResidualTrainerError("target action has a nonfinite base logit")


@dataclass(frozen=True, slots=True)
class CoarseNormalizedRowsV1:
    schema_version: str
    mode: str
    rows: tuple[CoarsePrefixLogitRowV1, ...]
    weights: tuple[float, ...]
    record_total_abs: float
    episode_total_abs: float
    by_record_abs: Mapping[str, float]
    by_episode_abs: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1:
            raise CoarseRecordResidualTrainerError("normalization schema is invalid")
        if len(self.rows) != len(self.weights) or not self.rows:
            raise CoarseRecordResidualTrainerError("normalized rows/weights are invalid")
        if any(not math.isfinite(float(value)) for value in self.weights):
            raise CoarseRecordResidualTrainerError("normalized weights must be finite")
        for name in ("record_total_abs", "episode_total_abs"):
            value = getattr(self, name)
            if not math.isfinite(float(value)) or value < 0.0:
                raise CoarseRecordResidualTrainerError(f"{name} is invalid")
        object.__setattr__(self, "by_record_abs", MappingProxyType(dict(self.by_record_abs)))
        object.__setattr__(self, "by_episode_abs", MappingProxyType(dict(self.by_episode_abs)))


def normalize_complete_action_rows_v1(
    rows: Iterable[CoarsePrefixLogitRowV1], *, mode: str,
) -> CoarseNormalizedRowsV1:
    """Aggregate one signed record target and distribute it across prefixes."""

    materialized = tuple(rows)
    if not materialized or any(type(row) is not CoarsePrefixLogitRowV1 for row in materialized):
        raise CoarseRecordResidualTrainerError("rows must contain exact coarse prefix rows")
    if mode not in {"record_normalized", "episode_normalized"}:
        raise CoarseRecordResidualTrainerError("normalization mode is invalid")
    groups: dict[tuple[str, str], list[CoarsePrefixLogitRowV1]] = defaultdict(list)
    for row in materialized:
        groups[(row.episode_id, row.record_id)].append(row)
    for (_episode_id, _record_id), group in groups.items():
        indices = sorted(row.prefix_index for row in group)
        if indices != list(range(len(indices))):
            raise CoarseRecordResidualTrainerError(f"prefix indices must be contiguous for {_record_id}")
    record_weight = {
        record_key: sum(float(row.signed_weight) for row in group) / len(group)
        for record_key, group in groups.items()
    }
    episode_denominator: dict[str, float] = defaultdict(float)
    for (episode_id, _record_id), weight in record_weight.items():
        episode_denominator[episode_id] += abs(weight)
    normalized_record_weight: dict[tuple[str, str], float] = {}
    for (episode_id, record_id), weight in record_weight.items():
        if mode == "record_normalized":
            normalized_record_weight[(episode_id, record_id)] = weight
        else:
            denominator = episode_denominator[episode_id]
            normalized_record_weight[(episode_id, record_id)] = weight / denominator if denominator else 0.0
    record_id_counts: dict[str, int] = defaultdict(int)
    for (_episode_id, record_id) in record_weight:
        record_id_counts[record_id] += 1
    duplicate_record_ids = {record_id for record_id, count in record_id_counts.items() if count > 1}
    output: list[float] = []
    by_record: dict[str, float] = defaultdict(float)
    by_episode: dict[str, float] = defaultdict(float)
    for row in materialized:
        group_key = (row.episode_id, row.record_id)
        coefficient = normalized_record_weight[group_key] / len(groups[group_key])
        output.append(coefficient)
        record_key = f"{row.episode_id}:{row.record_id}" if row.record_id in duplicate_record_ids else row.record_id
        by_record[record_key] += abs(coefficient)
        by_episode[row.episode_id] += abs(coefficient)
    return CoarseNormalizedRowsV1(
        schema_version=SCHEMA_V1,
        mode=mode,
        rows=materialized,
        weights=tuple(output),
        record_total_abs=sum(by_record.values()),
        episode_total_abs=sum(by_episode.values()),
        by_record_abs=dict(by_record),
        by_episode_abs=dict(by_episode),
    )


@dataclass(frozen=True, slots=True)
class CoarseRecordResidualTrainingResultV1:
    schema_version: str
    mode: str
    optimizer_updates: int
    records: int
    prefix_rows: int
    loss_normalizer: float
    signed_complete_action_loss: float
    anchor_kl: float
    residual_l2: float
    max_abs_residual: float
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False
    performance_evidence: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1 or self.mode not in {"record_normalized", "episode_normalized"}:
            raise CoarseRecordResidualTrainerError("training result schema/mode is invalid")
        if self.optimizer_updates < 1 or self.records < 1 or self.prefix_rows < 1:
            raise CoarseRecordResidualTrainerError("training result counts are invalid")
        for name in ("loss_normalizer", "signed_complete_action_loss", "anchor_kl", "residual_l2", "max_abs_residual"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise CoarseRecordResidualTrainerError(f"training result {name} is invalid")
            if name in {"loss_normalizer", "anchor_kl", "residual_l2", "max_abs_residual"} and value < 0.0:
                raise CoarseRecordResidualTrainerError(f"training result {name} is invalid")
        if any(getattr(self, name) is not False for name in ("training_permitted", "promotion_authority", "longrun_allowed", "performance_evidence")):
            raise CoarseRecordResidualTrainerError("training result grants authority")


class CoarseResidualTableV1(nn.Module):
    """Zero-initialized bounded residual table keyed by coarse bucket/action."""

    def __init__(self, keys: tuple[tuple[str, str], ...], *, max_abs_residual: float) -> None:
        super().__init__()
        if not keys or tuple(sorted(set(keys))) != keys:
            raise CoarseRecordResidualTrainerError("table keys must be sorted and duplicate-free")
        if not math.isfinite(float(max_abs_residual)) or not 0.0 < float(max_abs_residual) <= 1.0:
            raise CoarseRecordResidualTrainerError("max_abs_residual is invalid")
        self.max_abs_residual = float(max_abs_residual)
        self._keys = keys
        self._key_to_index = {key: index for index, key in enumerate(keys)}
        self.raw_residual = nn.Parameter(torch.zeros(len(keys), dtype=torch.float32))

    @classmethod
    def from_rows(cls, rows: Iterable[CoarsePrefixLogitRowV1], *, max_abs_residual: float) -> "CoarseResidualTableV1":
        materialized = tuple(rows)
        if not materialized:
            raise CoarseRecordResidualTrainerError("cannot build an empty residual table")
        keys = tuple(sorted({(row.bucket_id, action) for row in materialized for action in row.action_keys}))
        return cls(keys, max_abs_residual=max_abs_residual)

    @property
    def keys(self) -> tuple[tuple[str, str], ...]:
        return self._keys

    def bounded_residuals(self) -> Tensor:
        return float(self.max_abs_residual) * torch.tanh(self.raw_residual)

    def validate_rows(self, rows: Iterable[CoarsePrefixLogitRowV1]) -> tuple[CoarsePrefixLogitRowV1, ...]:
        materialized = tuple(rows)
        if not materialized:
            raise CoarseRecordResidualTrainerError("rows must be nonempty")
        for row in materialized:
            if type(row) is not CoarsePrefixLogitRowV1:
                raise CoarseRecordResidualTrainerError("rows must contain exact coarse prefix rows")
            for action in row.action_keys:
                if (row.bucket_id, action) not in self._key_to_index:
                    raise CoarseRecordResidualTrainerError("row contains an unknown bucket/action key")
        return materialized

    def residual_vector(self, row: CoarsePrefixLogitRowV1) -> Tensor:
        values = self.raw_residual.new_zeros(len(row.action_keys))
        bounded = self.bounded_residuals()
        for index, action in enumerate(row.action_keys):
            values[index] = bounded[self._key_to_index[(row.bucket_id, action)]]
        return values

    def to_residual_table(self) -> dict[str, dict[str, float]]:
        bounded = self.bounded_residuals().detach().cpu().tolist()
        output: dict[str, dict[str, float]] = defaultdict(dict)
        for (bucket, action), value in zip(self._keys, bounded, strict=True):
            output[bucket][action] = float(value)
        return {bucket: dict(sorted(actions.items())) for bucket, actions in sorted(output.items())}


def _record_groups(rows: tuple[CoarsePrefixLogitRowV1, ...]) -> tuple[tuple[CoarsePrefixLogitRowV1, ...], ...]:
    grouped: dict[tuple[str, str], list[CoarsePrefixLogitRowV1]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (row.episode_id, row.record_id)
        if key not in grouped:
            order.append(key)
        grouped[key].append(row)
    return tuple(tuple(sorted(grouped[key], key=lambda row: row.prefix_index)) for key in order)


def train_coarse_record_residual_v1(
    table: CoarseResidualTableV1,
    rows: Iterable[CoarsePrefixLogitRowV1],
    *,
    mode: str,
    max_updates: int = 1,
    learning_rate: float = 1.0e-2,
    anchor_kl_weight: float = 1.0,
    residual_l2_weight: float = 1.0e-4,
) -> CoarseRecordResidualTrainingResultV1:
    """Run a bounded table-only update over complete-action groups."""

    if type(table) is not CoarseResidualTableV1:
        raise CoarseRecordResidualTrainerError("table must be exact CoarseResidualTableV1")
    if type(max_updates) is not int or max_updates < 1:
        raise CoarseRecordResidualTrainerError("max_updates must be positive")
    for value, field in ((learning_rate, "learning_rate"), (anchor_kl_weight, "anchor_kl_weight"), (residual_l2_weight, "residual_l2_weight")):
        if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or float(value) < 0.0 or (field == "learning_rate" and float(value) == 0.0):
            raise CoarseRecordResidualTrainerError(f"{field} is invalid")
    materialized = table.validate_rows(rows)
    normalized = normalize_complete_action_rows_v1(materialized, mode=mode)
    optimizer = torch.optim.SGD(table.parameters(), lr=float(learning_rate))
    records = _record_groups(materialized)
    row_positions = {id(row): index for index, row in enumerate(materialized)}
    total_loss_value = anchor_value = l2_value = 0.0
    denominator = sum(abs(value) for value in normalized.weights)
    if denominator <= 0.0:
        raise CoarseRecordResidualTrainerError("rows have no nonzero normalized signed mass")
    for _update in range(max_updates):
        optimizer.zero_grad(set_to_none=True)
        total: Tensor | None = None
        for group in records:
            group_indices = [row_positions[id(row)] for row in group]
            record_loss: Tensor | None = None
            record_anchor: Tensor | None = None
            record_l2: Tensor | None = None
            for row, row_index in zip(group, group_indices, strict=True):
                base = torch.tensor(row.base_logits, dtype=table.raw_residual.dtype)
                residual = table.residual_vector(row)
                valid = torch.isfinite(base)
                adjusted = torch.where(valid, base + residual, base)
                logp = torch.log_softmax(adjusted, dim=-1)[row.target_index]
                coefficient = float(normalized.weights[row_index])
                term = -coefficient * logp
                record_loss = term if record_loss is None else record_loss + term
                base_valid = base[valid]
                adjusted_valid = adjusted[valid]
                p = torch.softmax(base_valid.detach(), dim=-1)
                q_log = torch.log_softmax(adjusted_valid, dim=-1)
                # Floating-point cancellation can make the KL expression a
                # tiny negative number even though the mathematical value is
                # nonnegative.  Clamp the diagnostic/regularizer at zero so
                # closed result validation does not turn harmless roundoff
                # into a training-arm failure.
                kl = torch.sum(p * (torch.log(p.clamp_min(1e-12)) - q_log)).clamp_min(0.0)
                l2 = residual[valid].square().mean()
                record_anchor = kl if record_anchor is None else record_anchor + kl
                record_l2 = l2 if record_l2 is None else record_l2 + l2
                total_loss_value += float(term.detach().item())
                anchor_value += float(kl.detach().item()) / len(group)
                l2_value += float(l2.detach().item()) / len(group)
            assert record_loss is not None and record_anchor is not None and record_l2 is not None
            auxiliary = float(anchor_kl_weight) * record_anchor / len(group) + float(residual_l2_weight) * record_l2 / len(group)
            total = record_loss + auxiliary if total is None else total + record_loss + auxiliary
        assert total is not None
        (total / denominator).backward()
        optimizer.step()
    max_seen = float(table.bounded_residuals().detach().abs().max().item())
    return CoarseRecordResidualTrainingResultV1(
        schema_version=SCHEMA_V1,
        mode=mode,
        optimizer_updates=max_updates,
        records=len(records),
        prefix_rows=len(materialized),
        loss_normalizer=float(denominator),
        signed_complete_action_loss=float(total_loss_value / denominator),
        anchor_kl=float(anchor_value / len(records)),
        residual_l2=float(l2_value / len(records)),
        max_abs_residual=max_seen,
    )


__all__ = [
    "SCHEMA_V1",
    "CoarseRecordResidualTrainerError",
    "CoarsePrefixLogitRowV1",
    "CoarseNormalizedRowsV1",
    "normalize_complete_action_rows_v1",
    "CoarseRecordResidualTrainingResultV1",
    "CoarseResidualTableV1",
    "train_coarse_record_residual_v1",
]
