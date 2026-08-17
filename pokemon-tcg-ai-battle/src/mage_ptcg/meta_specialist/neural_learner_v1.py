"""Training step driver: exact microbatch accumulation, OOM shrink, finite guards.

The learner owns only the update contract, not the feature reconstruction: a
caller supplies ``row_logits`` that maps one batch of snapshot examples to the
padded logit tensor for that batch.  This keeps the numerically load-bearing
part -- how partial batches are combined into one optimizer step -- separable
and directly testable.

An out-of-memory shrink is safe by construction: the loss is accumulated as
``(weighted_loss_sum, weight_sum)`` and divided once, so splitting a batch into
microbatches yields the same gradient as evaluating it whole.  A shrink and
retry therefore cannot change the step that is taken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.neural_batch_v1 import (
    WeightedLossAccumulatorV1,
    build_ragged_step_batch_v1,
    require_finite_update_v1,
    weighted_semantic_loss_v1,
    weighted_value_loss_v1,
)


NEURAL_LEARNER_SCHEMA_V1 = "specialist-neural-learner-v1"

RowLogitsFn = Callable[[Sequence[Mapping[str, Any]]], torch.Tensor]


class NeuralLearnerV1Error(ValueError):
    """Raised when a training step cannot be taken safely."""


@dataclass(frozen=True, slots=True)
class TrainingStepResultV1:
    """What one optimizer step actually consumed and produced."""

    loss: float
    weight_sum: float
    examples: int
    rows: int
    microbatches: int
    gradient_norm: float
    skipped: bool
    # Reported separately from `loss` (which already contains it, scaled by
    # value_coefficient) so a run can tell whether the critic or the policy term
    # moved.  Defaults to 0.0 so callers that do not train the critic are
    # unaffected.
    value_loss: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": NEURAL_LEARNER_SCHEMA_V1,
            "loss": self.loss,
            "value_loss": self.value_loss,
            "weight_sum": self.weight_sum,
            "examples": self.examples,
            "rows": self.rows,
            "microbatches": self.microbatches,
            "gradient_norm": self.gradient_norm,
            "skipped": self.skipped,
        }


def _split(examples: Sequence[Mapping[str, Any]], size: int):
    return [examples[start : start + size] for start in range(0, len(examples), size)]


def accumulate_value_loss_v1(
    examples, *, state_values, microbatch_examples: int | None = None,
):
    """Accumulate ``(weighted_squared_error_sum, weight_sum)`` over microbatches.

    Examples whose ``value_target`` is ``None`` carry no value signal and are left
    out of the critic's loss.  ``None`` means the episode never reached a terminal
    state the engine reported (``STEP_LIMIT``, ``AGENT_*``, ``INCOMPLETE``), which
    ``outcome_from_match_result_v1`` deliberately keeps distinct from a draw so an
    unfinished game is not taught as an even one.  Those examples still reach the
    policy loss, because 正典 §9.3 keeps every valid teacher decision a policy
    target.

    Regression: this called ``float(item["value_target"])`` on every example and
    died with ``TypeError: float() argument must be ... not 'NoneType'`` two steps
    into a 4,000-step run, after the corpus had already been sealed and loaded.
    Only the lane whose corpus contained unfinished games hit it.
    """
    chunks = list(_split(examples, len(examples) if microbatch_examples is None else microbatch_examples))
    loss_sum = None
    weight_sum = None
    for chunk in chunks:
        scored = [item for item in chunk if item.get("value_target") is not None]
        if not scored:
            continue
        batch = build_ragged_step_batch_v1(scored)
        targets = torch.tensor(
            [float(item["value_target"]) for item in scored], dtype=torch.float64
        )
        part, weight = weighted_value_loss_v1(state_values(scored), batch, targets)
        loss_sum = part if loss_sum is None else loss_sum + part
        weight_sum = weight if weight_sum is None else weight_sum + weight
    return loss_sum, weight_sum


def accumulate_batch_loss_v1(
    examples: Sequence[Mapping[str, Any]],
    *,
    row_logits: RowLogitsFn,
    microbatch_examples: int,
) -> tuple[torch.Tensor, torch.Tensor, int, int]:
    """Accumulate target-weighted sums over microbatches without normalizing yet."""
    if not examples:
        raise NeuralLearnerV1Error("a training step needs at least one example")
    if type(microbatch_examples) is not int or microbatch_examples < 1:
        raise NeuralLearnerV1Error("microbatch_examples must be a positive int")
    accumulator = WeightedLossAccumulatorV1()
    rows = 0
    chunks = _split(list(examples), microbatch_examples)
    for chunk in chunks:
        batch = build_ragged_step_batch_v1(chunk)
        logits = row_logits(chunk)
        if type(logits) is not torch.Tensor or logits.shape != batch.token_mask.shape:
            raise NeuralLearnerV1Error("row_logits must return one padded logit row per batch row")
        accumulator.add(*weighted_semantic_loss_v1(logits, batch))
        rows += batch.rows
    assert accumulator.loss_sum is not None and accumulator.weight_sum is not None
    return accumulator.loss_sum, accumulator.weight_sum, rows, len(chunks)


def training_step_v1(
    examples: Sequence[Mapping[str, Any]],
    *,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    row_logits: RowLogitsFn,
    microbatch_examples: int | None = None,
    max_gradient_norm: float | None = 1.0,
    oom_shrink: bool = True,
    state_values=None,
    value_coefficient: float = 0.0,
) -> TrainingStepResultV1:
    """Take exactly one optimizer step, or none at all if the update is not finite.

    ``state_values`` and ``value_coefficient`` add the design's value term to the
    BC objective.  Both default to "off" so existing callers keep their exact
    behaviour; ``run_bc_distillation.py`` turns them on.
    """
    if value_coefficient < 0.0:
        raise NeuralLearnerV1Error("value_coefficient must be nonnegative")
    if value_coefficient > 0.0 and state_values is None:
        raise NeuralLearnerV1Error(
            "a positive value_coefficient needs state_values; refusing to report a "
            "value term that contributes no gradient"
        )
    size = len(examples) if microbatch_examples is None else microbatch_examples
    attempts = []
    while size >= 1:
        attempts.append(size)
        size //= 2
        if not oom_shrink:
            break

    optimizer.zero_grad(set_to_none=True)
    last_error: RuntimeError | None = None
    for attempt in attempts:
        try:
            loss_sum, weight_sum, rows, chunks = accumulate_batch_loss_v1(
                examples, row_logits=row_logits, microbatch_examples=attempt
            )
        except torch.cuda.OutOfMemoryError as exc:  # pragma: no cover - CPU tests
            last_error = exc
            optimizer.zero_grad(set_to_none=True)
            continue
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            last_error = exc
            optimizer.zero_grad(set_to_none=True)
            continue
        break
    else:
        raise NeuralLearnerV1Error(
            "every microbatch size ran out of memory"
        ) from last_error

    if float(weight_sum) <= 0.0:
        # No trainable weight: there is nothing to learn from, and dividing would
        # invent a gradient.
        optimizer.zero_grad(set_to_none=True)
        return TrainingStepResultV1(
            loss=0.0, weight_sum=0.0, examples=len(examples), rows=rows,
            microbatches=chunks, gradient_norm=0.0, skipped=True,
        )

    loss = loss_sum / weight_sum
    value_loss_value = 0.0
    if value_coefficient > 0.0:
        value_sum, value_weight = accumulate_value_loss_v1(
            examples, state_values=state_values, microbatch_examples=attempt
        )
        # `None` when every example in the batch lacked a value target; the batch
        # then trains the policy only, rather than failing the whole run.
        if value_weight is not None and float(value_weight) > 0.0:
            value_term = value_sum / value_weight
            value_loss_value = float(value_term.detach())
            loss = loss + value_coefficient * value_term
    if not torch.isfinite(loss).all():
        optimizer.zero_grad(set_to_none=True)
        return TrainingStepResultV1(
            loss=float(loss.detach()), value_loss=value_loss_value,
        weight_sum=float(weight_sum), examples=len(examples),
            rows=rows, microbatches=chunks, gradient_norm=0.0, skipped=True,
        )
    loss.backward()

    parameters = [item for item in model.parameters() if item.grad is not None]
    if not parameters:
        raise NeuralLearnerV1Error("no parameter received a gradient")
    if any(not torch.isfinite(item.grad).all() for item in parameters):
        # Never step on a non-finite gradient; drop the batch instead.
        optimizer.zero_grad(set_to_none=True)
        return TrainingStepResultV1(
            loss=float(loss.detach()), value_loss=value_loss_value,
        weight_sum=float(weight_sum), examples=len(examples),
            rows=rows, microbatches=chunks, gradient_norm=float("nan"), skipped=True,
        )

    if max_gradient_norm is None:
        norm = torch.sqrt(
            sum((item.grad.detach() ** 2).sum() for item in parameters)
        )
    else:
        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_gradient_norm)
    require_finite_update_v1(norm, field="gradient norm")
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    return TrainingStepResultV1(
        loss=float(loss.detach()), value_loss=value_loss_value,
        weight_sum=float(weight_sum), examples=len(examples),
        rows=rows, microbatches=chunks, gradient_norm=float(norm), skipped=False,
    )


__all__ = [
    "NEURAL_LEARNER_SCHEMA_V1", "NeuralLearnerV1Error", "RowLogitsFn",
    "TrainingStepResultV1", "accumulate_batch_loss_v1", "accumulate_value_loss_v1",
    "training_step_v1",
]
