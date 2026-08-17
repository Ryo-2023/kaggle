"""Ragged semantic-step batching and the weighted loss the L2 oracle authorizes.

The batcher pads only to batch-local maxima and carries exact masks, so padding
never contributes mass, logits, or gradient.  The loss reproduces
:func:`evaluate_reference_losses_v1` exactly:

    example_loss  = sum over rows of  reach_mass * row_cross_entropy
    weighted_loss = example_quality_weight * example_loss
    batch_mean    = sum(weighted_loss) / sum(quality_weight over trainable)

Because the normalizer is a sum of target weights rather than an example count,
accumulating microbatches with :class:`WeightedLossAccumulatorV1` is
mathematically identical to evaluating the whole batch at once.  An OOM shrink
and retry therefore cannot change the optimizer step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2


NEURAL_BATCH_SCHEMA_V1 = "specialist-neural-batch-v1"
MAX_BATCH_ROWS_V1 = 262_144
MAX_ROW_TOKENS_V1 = 4_096


class NeuralBatchV1Error(ValueError):
    """Raised when a ragged batch or its loss cannot be built safely."""


@dataclass(frozen=True, slots=True)
class RaggedStepBatchV1:
    """One padded batch of conditional semantic-step rows.

    ``semantic_mask`` marks the real ``(row, token)`` slots.  The final column of
    a row is STOP when ``stop_available`` is set for that row.
    """

    schema_version: str
    token_mask: torch.Tensor      # (rows, max_tokens) bool
    target_masses: torch.Tensor   # (rows, max_tokens) float64
    reach_mass: torch.Tensor      # (rows,) float64
    example_index: torch.Tensor   # (rows,) int64
    quality_weight: torch.Tensor  # (examples,) float64
    trainable: torch.Tensor       # (examples,) bool
    row_token_keys: tuple[tuple[bytes, ...], ...]

    @property
    def rows(self) -> int:
        return int(self.token_mask.shape[0])

    @property
    def max_tokens(self) -> int:
        return int(self.token_mask.shape[1])

    @property
    def examples(self) -> int:
        return int(self.quality_weight.shape[0])

    def weight_sum(self) -> torch.Tensor:
        """Return the batch normalizer: the total quality weight of trainable examples."""
        return torch.where(
            self.trainable, self.quality_weight, torch.zeros_like(self.quality_weight)
        ).sum()


def _row_tokens(row: Mapping[str, Any]) -> tuple[list[bytes], list[float], bool]:
    tokens = row["token_masses"]
    if type(tokens) is not list or not tokens or len(tokens) > MAX_ROW_TOKENS_V1:
        raise NeuralBatchV1Error("loss row token_masses must be a bounded nonempty list")
    keys: list[bytes] = []
    masses: list[float] = []
    stop_available = False
    for position, token in enumerate(tokens):
        kind = token.get("kind")
        if kind == "semantic":
            if stop_available:
                raise NeuralBatchV1Error("STOP must follow every semantic token")
            keys.append(canonical_json_bytes_v2(token["semantic_action"]))
        elif kind == "stop":
            if stop_available or position != len(tokens) - 1:
                raise NeuralBatchV1Error("STOP may appear at most once, last")
            stop_available = True
            keys.append(b"\x00STOP")
        else:
            raise NeuralBatchV1Error("loss row token has an unknown kind")
        mass = token["mass"]
        if type(mass) is not float:
            raise NeuralBatchV1Error("loss row mass must be a float")
        masses.append(mass)
    return keys, masses, stop_available


def build_ragged_step_batch_v1(
    examples: Sequence[Mapping[str, Any]], *, device: str | torch.device = "cpu",
) -> RaggedStepBatchV1:
    """Build one padded batch from training-snapshot examples, padding to batch maxima."""
    if not examples:
        raise NeuralBatchV1Error("a batch needs at least one example")
    row_keys: list[tuple[bytes, ...]] = []
    row_masses: list[list[float]] = []
    reaches: list[float] = []
    example_of_row: list[int] = []
    qualities: list[float] = []
    trainable: list[bool] = []

    for example_index, example in enumerate(examples):
        rows = example["loss_rows"]
        if type(rows) is not list:
            raise NeuralBatchV1Error("example loss_rows must be a list")
        quality = example["example_quality_weight"]
        if type(quality) is not float or not (0.0 <= quality < float("inf")):
            raise NeuralBatchV1Error("example_quality_weight must be a finite nonnegative float")
        qualities.append(quality)
        trainable.append(bool(rows))
        for row in rows:
            keys, masses, _stop = _row_tokens(row)
            reach = row["reach_mass"]
            if type(reach) is not float:
                raise NeuralBatchV1Error("loss row reach_mass must be a float")
            row_keys.append(tuple(keys))
            row_masses.append(masses)
            reaches.append(reach)
            example_of_row.append(example_index)
            if len(row_keys) > MAX_BATCH_ROWS_V1:
                raise NeuralBatchV1Error("batch exceeds the row bound")

    if not row_keys:
        raise NeuralBatchV1Error("a batch needs at least one trainable row")
    # Pad only to this batch's widest row.
    width = max(len(keys) for keys in row_keys)
    mask = torch.zeros((len(row_keys), width), dtype=torch.bool, device=device)
    targets = torch.zeros((len(row_keys), width), dtype=torch.float64, device=device)
    for index, masses in enumerate(row_masses):
        mask[index, : len(masses)] = True
        targets[index, : len(masses)] = torch.tensor(
            masses, dtype=torch.float64, device=device
        )

    batch = RaggedStepBatchV1(
        schema_version=NEURAL_BATCH_SCHEMA_V1,
        token_mask=mask,
        target_masses=targets,
        reach_mass=torch.tensor(reaches, dtype=torch.float64, device=device),
        example_index=torch.tensor(example_of_row, dtype=torch.int64, device=device),
        quality_weight=torch.tensor(qualities, dtype=torch.float64, device=device),
        trainable=torch.tensor(trainable, dtype=torch.bool, device=device),
        row_token_keys=tuple(row_keys),
    )
    if not torch.isfinite(batch.target_masses).all() or not torch.isfinite(batch.reach_mass).all():
        raise NeuralBatchV1Error("batch targets must be finite")
    return batch


def masked_log_softmax_v1(logits: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Return a numerically stable log-softmax over masked slots only.

    Masked slots are excluded before the maximum and the sum, and are returned as
    zero rather than ``-inf``, so no padding position can produce a NaN gradient.
    """
    if logits.shape != mask.shape:
        raise NeuralBatchV1Error("logits and mask must have the same shape")
    if not torch.isfinite(logits[mask]).all():
        raise NeuralBatchV1Error("logits must be finite on every valid slot")
    neutral = torch.zeros((), dtype=logits.dtype, device=logits.device)
    valid = torch.where(mask, logits, torch.full_like(logits, float("-inf")))
    maximum = valid.max(dim=-1, keepdim=True).values
    shifted = torch.where(mask, logits - maximum, neutral)
    total = torch.where(mask, shifted.exp(), neutral).sum(dim=-1, keepdim=True)
    return torch.where(mask, shifted - total.log(), neutral)


def weighted_value_loss_v1(
    values: torch.Tensor, batch: RaggedStepBatchV1, targets: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return ``(weighted_squared_error_sum, weight_sum)`` for the value head.

    Unnormalized like :func:`weighted_semantic_loss_v1` so several microbatches
    accumulate to exactly one whole-batch value.  Untrainable examples get zero
    weight rather than being dropped, so the value and policy terms are averaged
    over the same population.
    """
    if values.shape != targets.shape:
        raise NeuralBatchV1Error("value predictions and targets must have the same shape")
    live = torch.where(
        batch.trainable, batch.quality_weight, torch.zeros_like(batch.quality_weight)
    )
    squared = (values.to(torch.float64) - targets.to(torch.float64)) ** 2
    return (live * squared).sum(), live.sum()


def weighted_semantic_loss_v1(
    logits: torch.Tensor, batch: RaggedStepBatchV1,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return ``(weighted_loss_sum, weight_sum)`` for one (micro)batch.

    Both parts are returned unnormalized so a caller can accumulate several
    microbatches and divide once, matching a single whole-batch evaluation.
    """
    log_probabilities = masked_log_softmax_v1(logits.to(torch.float64), batch.token_mask)
    row_loss = -(batch.target_masses * log_probabilities).sum(dim=-1)
    row_loss = row_loss * batch.reach_mass
    example_loss = torch.zeros(
        batch.examples, dtype=torch.float64, device=row_loss.device
    ).index_add(0, batch.example_index, row_loss)
    live = torch.where(
        batch.trainable, batch.quality_weight, torch.zeros_like(batch.quality_weight)
    )
    return (live * example_loss).sum(), live.sum()


@dataclass
class WeightedLossAccumulatorV1:
    """Accumulate target-weighted microbatch sums and normalize exactly once."""

    loss_sum: torch.Tensor | None = None
    weight_sum: torch.Tensor | None = None

    def add(self, weighted_loss: torch.Tensor, weight: torch.Tensor) -> None:
        self.loss_sum = weighted_loss if self.loss_sum is None else self.loss_sum + weighted_loss
        self.weight_sum = weight if self.weight_sum is None else self.weight_sum + weight

    def mean(self) -> torch.Tensor:
        if self.loss_sum is None or self.weight_sum is None:
            raise NeuralBatchV1Error("accumulator has no microbatch")
        if float(self.weight_sum) <= 0.0:
            return torch.zeros((), dtype=torch.float64, device=self.loss_sum.device)
        return self.loss_sum / self.weight_sum


def require_finite_update_v1(value: torch.Tensor, *, field: str) -> torch.Tensor:
    """Fail closed on a non-finite loss or gradient before any optimizer step."""
    if not torch.isfinite(value).all():
        raise NeuralBatchV1Error(f"{field} is not finite; refusing the optimizer step")
    return value


__all__ = [
    "MAX_BATCH_ROWS_V1", "MAX_ROW_TOKENS_V1", "NEURAL_BATCH_SCHEMA_V1",
    "NeuralBatchV1Error", "RaggedStepBatchV1", "WeightedLossAccumulatorV1",
    "build_ragged_step_batch_v1", "masked_log_softmax_v1",
    "require_finite_update_v1", "weighted_semantic_loss_v1",
]
