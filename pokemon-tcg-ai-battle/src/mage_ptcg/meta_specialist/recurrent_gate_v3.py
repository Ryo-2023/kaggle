"""Bounded recurrent R3 training and carry-versus-reset evaluation.

This module deliberately accepts a *factory* for production sequences.  The
factory is invoked for every pass so a caller can revalidate its sealed
selection manifest before streaming a single physical episode at a time.  A
corpus-wide tuple is only accepted as a bounded fixture convenience.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re

import torch
from torch import nn
from torch.nn import functional as F

from mage_ptcg.meta_specialist.bc_trainer_v3 import BCExampleV3, RecurrentBCSequenceV3
from mage_ptcg.meta_specialist.neural_model_v3 import PolicyOutputV3, SpecialistModelV3
from mage_ptcg.meta_specialist.recurrent_dataset_v3 import (
    PreparedRecurrentLaneV3,
    prepare_sealed_recurrent_lane_v3,
    read_recurrent_selection_manifest_v3,
    stream_prepared_recurrent_selection_v3,
    stream_recurrent_selection_v3,
    validate_prepared_recurrent_pair_v3,
)


_CANDIDATES = frozenset({"current-R2", "R3-A", "R3-B"})
_CANDIDATE_ORDER = ("current-R2", "R3-A", "R3-B")
_R3_CANDIDATE_ORDER = ("R3-A", "R3-B")
_LANES = ("alakazam", "archaludon")
_SEEDS = (7, 17, 29)
_LEARNING_RATE = 1e-4
_GRADIENT_CLIP_NORM = 1.0
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_RESULT_SCHEMA = "meta-specialist-recurrent-gate-result-v2"
_SELECTION_SCHEMA = "meta-specialist-recurrent-gate-selection-v2"
_LEGACY_RESULT_SCHEMA = "meta-specialist-recurrent-gate-result-v1"
_LEGACY_SELECTION_SCHEMA = "meta-specialist-recurrent-gate-selection-v1"


@dataclass(frozen=True, slots=True)
class RecurrentSequenceSourceV3:
    """Re-openable source of physically ordered sealed episode sequences.

    ``stream_factory`` is intentionally a factory rather than an iterator:
    every train/validation pass must reopen the sealed authority.  The
    caller-supplied ``production`` flag is descriptive only; it cannot prove
    manifest/index revalidation.  Task 4 must introduce a dedicated sealed
    adapter before production execution is enabled.  The Gate never
    accumulates yielded sequences or decoded states.
    """

    stream_factory: Callable[[], Iterator[RecurrentBCSequenceV3]]
    authority_label: str
    production: bool

    def __post_init__(self) -> None:
        if not callable(self.stream_factory) or type(self.authority_label) is not str or not self.authority_label:
            raise ValueError("recurrent sequence source is invalid")

    def iter_sequences(self) -> Iterator[RecurrentBCSequenceV3]:
        stream = self.stream_factory()
        if not isinstance(stream, Iterator):
            raise ValueError("recurrent sequence source factory must return an iterator")
        for sequence in stream:
            if type(sequence) is not RecurrentBCSequenceV3:
                raise ValueError("recurrent sequence source yielded an untrusted sequence")
            yield sequence


def bounded_fixture_sequence_source_v3(
    sequences: Sequence[RecurrentBCSequenceV3], *, authority_label: str = "bounded-fixture",
) -> RecurrentSequenceSourceV3:
    """Wrap an in-memory tuple for unit/integration fixtures only."""
    frozen = tuple(sequences)
    if not frozen or any(type(item) is not RecurrentBCSequenceV3 for item in frozen):
        raise ValueError("bounded fixture sequences are invalid")
    return RecurrentSequenceSourceV3(lambda: iter(frozen), authority_label, False)


@dataclass(frozen=True, slots=True)
class SealedRecurrentSequenceSourceV3:
    """Task 3.5's non-generic production source.

    This type owns only a pinned raw manifest path/SHA and a single sealed
    partition.  It intentionally has no caller-provided sequence factory;
    every pass calls the authority-revalidating Task 2 stream anew.
    """

    manifest_path: Path
    expected_manifest_file_sha256: str
    burn_in: int
    partition: str

    def __post_init__(self) -> None:
        if (not self.manifest_path.name or _HEX64.fullmatch(self.expected_manifest_file_sha256) is None
                or type(self.burn_in) is not int or self.burn_in < 0
                or self.partition not in {"train", "validation"}):
            raise ValueError("sealed recurrent sequence source is invalid")

    def iter_sequences(self) -> Iterator[RecurrentBCSequenceV3]:
        stream = stream_recurrent_selection_v3(
            self.manifest_path,
            expected_manifest_file_sha256=self.expected_manifest_file_sha256,
            burn_in=self.burn_in,
            partition=self.partition,
        )
        for sequence in stream:
            if type(sequence) is not RecurrentBCSequenceV3 or sequence.partition != self.partition:
                raise ValueError("sealed recurrent sequence stream crossed its pinned partition")
            yield sequence


def sealed_recurrent_sequence_source_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str, burn_in: int,
    partition: str,
) -> SealedRecurrentSequenceSourceV3:
    """Create the only source type accepted for a production recurrent run."""
    return SealedRecurrentSequenceSourceV3(
        Path(manifest_path), expected_manifest_file_sha256, burn_in, partition,
    )


@dataclass(frozen=True, slots=True)
class PreparedRecurrentSequenceSourceV3:
    """Production-only adapter pinned to an externally anchored preflight receipt."""

    receipt_path: Path
    expected_receipt_file_sha256: str
    burn_in: int
    partition: str

    def __post_init__(self) -> None:
        if (not self.receipt_path.name or _HEX64.fullmatch(self.expected_receipt_file_sha256) is None
                or type(self.burn_in) is not int or self.burn_in < 0
                or self.partition not in {"train", "validation"}):
            raise ValueError("prepared recurrent sequence source is invalid")

    def iter_sequences(self) -> Iterator[RecurrentBCSequenceV3]:
        stream = stream_prepared_recurrent_selection_v3(
            self.receipt_path, expected_receipt_file_sha256=self.expected_receipt_file_sha256,
            burn_in=self.burn_in, partition=self.partition,
        )
        for sequence in stream:
            if type(sequence) is not RecurrentBCSequenceV3 or sequence.partition != self.partition:
                raise ValueError("prepared recurrent sequence stream crossed its pinned partition")
            yield sequence


def prepared_recurrent_sequence_source_v3(
    receipt_path: str | Path, *, expected_receipt_file_sha256: str, burn_in: int,
    partition: str,
) -> PreparedRecurrentSequenceSourceV3:
    return PreparedRecurrentSequenceSourceV3(
        Path(receipt_path), expected_receipt_file_sha256, burn_in, partition,
    )


@dataclass(frozen=True, slots=True)
class RecurrentGateMetricsV3:
    carry_complete_nll: float
    reset_complete_nll: float
    carry_stop_nll: float | None
    reset_stop_nll: float | None
    complete_rows: int
    stop_target_rows: int
    forced_sole_stop_rows: int
    non_reset_hidden_steps: int


@dataclass(frozen=True, slots=True)
class RecurrentTrainingResultV3:
    candidate: str
    best_epoch: int
    best_validation_nll: float
    history: tuple[Mapping[str, float], ...]
    checkpoint_state: Mapping[str, torch.Tensor]
    parameter_delta_l1: float
    epochs: int
    stop_reason: str
    optimizer_updates: int


@dataclass(frozen=True, slots=True)
class RecurrentGateLaneInputV3:
    """One full-corpus manifest, pinned by raw-file SHA before any parsing."""

    manifest_path: Path
    expected_manifest_file_sha256: str

    def __post_init__(self) -> None:
        if (not self.manifest_path.name
                or _HEX64.fullmatch(self.expected_manifest_file_sha256) is None):
            raise ValueError("recurrent Gate lane input is invalid")


@dataclass(frozen=True, slots=True)
class RecurrentGateRunV3:
    status: str
    output_path: Path
    decision_path: Path
    result_sha256: str


@dataclass(slots=True)
class _PassStatsV3:
    complete_loss_sum: float = 0.0
    complete_weight: float = 0.0
    stop_loss_sum: float = 0.0
    stop_weight: float = 0.0
    complete_rows: int = 0
    stop_target_rows: int = 0
    forced_sole_stop_rows: int = 0
    non_reset_hidden_steps: int = 0

    def complete_nll(self) -> float:
        if self.complete_weight <= 0:
            raise ValueError("recurrent pass has no non-forced loss rows")
        return self.complete_loss_sum / self.complete_weight

    def stop_nll(self) -> float | None:
        return None if self.stop_weight == 0 else self.stop_loss_sum / self.stop_weight


def _as_source_v3(
    source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3],
) -> RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3:
    if type(source) in {RecurrentSequenceSourceV3, SealedRecurrentSequenceSourceV3, PreparedRecurrentSequenceSourceV3}:
        normalized = source
    elif isinstance(source, Sequence):
        normalized = bounded_fixture_sequence_source_v3(source)
    else:
        raise ValueError("recurrent sequences must be a re-openable source or bounded fixture sequence")
    return normalized


def _require_production_adapter_v3(
    source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3, production_run: bool,
) -> None:
    """Accept production only through the manifest-bound Task 3.5 adapter."""
    if type(production_run) is not bool:
        raise ValueError("production_run must be a bool")
    if production_run and type(source) is not PreparedRecurrentSequenceSourceV3:
        raise ValueError("production recurrent run requires an externally anchored preflight receipt adapter")


def _audit_training_split_v3(
    train_source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3,
    validation_source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3,
) -> None:
    """Read only identity metadata before any optimizer state can change."""
    if type(train_source) is PreparedRecurrentSequenceSourceV3 and type(validation_source) is PreparedRecurrentSequenceSourceV3:
        if train_source.partition != "train" or validation_source.partition != "validation":
            raise ValueError("prepared recurrent sources crossed their sealed partitions")
        validate_prepared_recurrent_pair_v3(
            train_source.receipt_path,
            train_expected_receipt_file_sha256=train_source.expected_receipt_file_sha256,
            validation_receipt_path=validation_source.receipt_path,
            validation_expected_receipt_file_sha256=validation_source.expected_receipt_file_sha256,
        )
        return
    train_components: set[str] = set()
    train_episodes: set[str] = set()
    for sequence in train_source.iter_sequences():
        if sequence.partition != "train":
            raise ValueError("recurrent training sequence source crossed its sealed partition")
        train_components.add(sequence.component_id)
        train_episodes.add(sequence.episode_id)
    if not train_components or not train_episodes:
        raise ValueError("recurrent training source is empty")
    validation_components: set[str] = set()
    validation_episodes: set[str] = set()
    for sequence in validation_source.iter_sequences():
        if sequence.partition != "validation":
            raise ValueError("recurrent validation sequence source crossed its sealed partition")
        validation_components.add(sequence.component_id)
        validation_episodes.add(sequence.episode_id)
    if not validation_components or not validation_episodes:
        raise ValueError("recurrent validation source is empty")
    if train_components & validation_components or train_episodes & validation_episodes:
        raise ValueError("recurrent training/validation component or episode overlap")


def _is_forced_sole_stop_v3(example: BCExampleV3) -> bool:
    return (
        not example.state.candidates
        and bool(getattr(example.step_input, "stop_available", False))
        and len(example.target_masses) == 1
        and math.isclose(example.target_masses[0], 1.0, rel_tol=0.0, abs_tol=1e-12)
    )


def _soft_loss_v3(
    model: nn.Module, example: BCExampleV3, *, hidden_state: torch.Tensor | None,
    episode_start: bool,
) -> tuple[torch.Tensor, torch.Tensor | None, float | None]:
    """Return full semantic+STOP NLL, next hidden, and STOP contribution."""
    forward = getattr(model, "forward_v3", None)
    if not callable(forward):
        raise ValueError("recurrent model must implement forward_v3")
    output = forward(example.state, hidden_state=hidden_state, episode_start=episode_start)
    if type(output) is not PolicyOutputV3:
        raise ValueError("forward_v3 must return PolicyOutputV3")
    stop_available = bool(getattr(example.step_input, "stop_available", False))
    logits = output.logits
    if logits.ndim != 1 or logits.numel() != len(example.state.candidates):
        raise ValueError("recurrent model candidate logits disagree with the sealed action domain")
    if stop_available:
        stop_vector = getattr(model, "stop_vector", None)
        stop_bias = getattr(model, "stop_bias", None)
        if not isinstance(stop_vector, torch.Tensor) or not isinstance(stop_bias, torch.Tensor):
            raise ValueError("recurrent model lacks the required STOP head")
        stop_logit = stop_vector @ output.global_token + stop_bias
        logits = torch.cat((logits, stop_logit.reshape(1)))
    if logits.numel() != len(example.target_masses) or not torch.isfinite(logits).all():
        raise ValueError("recurrent legal action or STOP logits are non-finite/misaligned")
    target = torch.tensor(example.target_masses, dtype=logits.dtype, device=logits.device)
    log_probs = F.log_softmax(logits, dim=0)
    full_loss = -(target * log_probs).sum() * float(example.quality_weight)
    stop_loss = None
    if stop_available and target[-1].item() > 0:
        stop_loss = float((-target[-1] * log_probs[-1] * float(example.quality_weight)).detach().item())
    return full_loss, output.hidden_state, stop_loss


def _iterate_pass_v3(
    model: nn.Module, source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3, *, device: torch.device,
    carry_hidden: bool, burn_in: int | None, expected_partition: str | None = None,
    truncated_bptt_steps: int | None = None,
) -> _PassStatsV3:
    if truncated_bptt_steps is not None and (type(truncated_bptt_steps) is not int or truncated_bptt_steps < 1):
        raise ValueError("truncated_bptt_steps must be a positive integer or None")
    stats = _PassStatsV3()
    for sequence in source.iter_sequences():
        if expected_partition is not None and sequence.partition != expected_partition:
            raise ValueError("recurrent sequence source crossed its sealed partition")
        effective_burn_in = sequence.burn_in if burn_in is None else burn_in
        if type(effective_burn_in) is not int or effective_burn_in < 0 or effective_burn_in != sequence.burn_in:
            raise ValueError("recurrent source and requested burn-in disagree")
        hidden: torch.Tensor | None = None
        for index, example in enumerate(sequence.steps):
            if example.episode_start != (index == 0):
                raise ValueError("recurrent sequence has an invalid episode boundary")
            forced = _is_forced_sole_stop_v3(example)
            input_hidden = hidden if carry_hidden else None
            input_start = example.episode_start if carry_hidden else True
            loss, next_hidden, stop_loss = _soft_loss_v3(
                model, example, hidden_state=input_hidden, episode_start=input_start,
            )
            if carry_hidden and input_hidden is not None:
                stats.non_reset_hidden_steps += 1
            hidden = next_hidden if carry_hidden else None
            if truncated_bptt_steps and hidden is not None and (index + 1) % truncated_bptt_steps == 0:
                hidden = hidden.detach()
            if forced:
                stats.forced_sole_stop_rows += 1
                continue
            if index < effective_burn_in:
                continue
            stats.complete_rows += 1
            stats.complete_loss_sum += float(loss.detach().item())
            stats.complete_weight += float(example.quality_weight)
            if stop_loss is not None:
                stats.stop_target_rows += 1
                stats.stop_loss_sum += stop_loss
                stats.stop_weight += float(example.quality_weight)
    return stats


def _train_epoch_v3(
    model: SpecialistModelV3, source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3, *, optimizer: torch.optim.Optimizer,
    device: torch.device, gradient_clip_norm: float, truncated_bptt_steps: int | None,
) -> tuple[_PassStatsV3, int]:
    """Consume exactly one episode graph at a time, then release it after update."""
    stats = _PassStatsV3()
    updates = 0
    for sequence in source.iter_sequences():
        if sequence.partition != "train":
            raise ValueError("recurrent training sequence source crossed its sealed partition")
        hidden: torch.Tensor | None = None
        sequence_losses: list[torch.Tensor] = []
        for index, example in enumerate(sequence.steps):
            if example.episode_start != (index == 0):
                raise ValueError("recurrent sequence has an invalid episode boundary")
            forced = _is_forced_sole_stop_v3(example)
            loss, next_hidden, stop_loss = _soft_loss_v3(
                model, example, hidden_state=hidden, episode_start=example.episode_start,
            )
            if hidden is not None:
                stats.non_reset_hidden_steps += 1
            hidden = next_hidden
            if truncated_bptt_steps and hidden is not None and (index + 1) % truncated_bptt_steps == 0:
                hidden = hidden.detach()
            if forced:
                stats.forced_sole_stop_rows += 1
                continue
            if index < sequence.burn_in:
                continue
            stats.complete_rows += 1
            stats.complete_loss_sum += float(loss.detach().item())
            stats.complete_weight += float(example.quality_weight)
            if stop_loss is not None:
                stats.stop_target_rows += 1
                stats.stop_loss_sum += stop_loss
                stats.stop_weight += float(example.quality_weight)
            sequence_losses.append(loss)
        if not sequence_losses:
            continue
        optimizer.zero_grad(set_to_none=True)
        torch.stack(sequence_losses).mean().backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
        optimizer.step()
        updates += 1
    return stats, updates


def evaluate_carry_vs_reset_v3(
    model: nn.Module, sequences: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3], *,
    device: torch.device, burn_in: int | None = None, production_run: bool = False,
) -> RecurrentGateMetricsV3:
    """Evaluate a checkpoint twice over the same re-opened sequence source."""
    if not isinstance(device, torch.device):
        raise ValueError("device must be a torch.device")
    source = _as_source_v3(sequences)
    _require_production_adapter_v3(source, production_run)
    model.to(device)
    model.eval()
    with torch.no_grad():
        carry = _iterate_pass_v3(model, source, device=device, carry_hidden=True, burn_in=burn_in)
        reset = _iterate_pass_v3(model, source, device=device, carry_hidden=False, burn_in=burn_in)
    if (carry.complete_rows != reset.complete_rows or carry.stop_target_rows != reset.stop_target_rows
            or carry.forced_sole_stop_rows != reset.forced_sole_stop_rows):
        raise RuntimeError("carry/reset passes consumed different sealed supervision rows")
    return RecurrentGateMetricsV3(
        carry_complete_nll=carry.complete_nll(), reset_complete_nll=reset.complete_nll(),
        carry_stop_nll=carry.stop_nll(), reset_stop_nll=reset.stop_nll(),
        complete_rows=carry.complete_rows, stop_target_rows=carry.stop_target_rows,
        forced_sole_stop_rows=carry.forced_sole_stop_rows,
        non_reset_hidden_steps=carry.non_reset_hidden_steps,
    )


def _state_delta_l1_v3(before: Mapping[str, torch.Tensor], after: Mapping[str, torch.Tensor]) -> float:
    if set(before) != set(after):
        raise ValueError("recurrent model parameters changed their state keys")
    return float(sum((before[key] - after[key].detach().cpu()).abs().sum().item() for key in before))


def train_recurrent_r3_v3(
    model: SpecialistModelV3,
    train_sequences: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3],
    validation_sequences: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3], *,
    candidate: str, device: torch.device, max_epochs: int = 10, patience: int = 2,
    min_delta: float = 0.0, learning_rate: float = 1e-4,
    gradient_clip_norm: float = _GRADIENT_CLIP_NORM,
    truncated_bptt_steps: int | None = None, production_run: bool = False,
) -> RecurrentTrainingResultV3:
    """Train R3 with real updates and retain the best carry-validation state."""
    if type(model) is not SpecialistModelV3:
        raise ValueError("recurrent R3 trainer requires SpecialistModelV3")
    if (candidate not in _R3_CANDIDATE_ORDER or not isinstance(device, torch.device) or type(max_epochs) is not int
            or max_epochs < 1 or type(patience) is not int or patience < 1
            or not math.isfinite(min_delta) or min_delta < 0 or learning_rate <= 0
            or gradient_clip_norm <= 0):
        raise ValueError("recurrent R3 training arguments are invalid")
    train_source = _as_source_v3(train_sequences)
    validation_source = _as_source_v3(validation_sequences)
    _require_production_adapter_v3(train_source, production_run)
    _require_production_adapter_v3(validation_source, production_run)
    _audit_training_split_v3(train_source, validation_source)
    model.to(device)
    before = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    best_nll = float("inf")
    best_epoch = -1
    best_state: dict[str, torch.Tensor] = {}
    history: list[Mapping[str, float]] = []
    stale_epochs = 0
    updates = 0
    stop_reason = "max_epochs"
    for epoch in range(max_epochs):
        model.train()
        train_stats, epoch_updates = _train_epoch_v3(
            model, train_source, optimizer=optimizer, device=device,
            gradient_clip_norm=gradient_clip_norm, truncated_bptt_steps=truncated_bptt_steps,
        )
        if epoch_updates == 0:
            raise ValueError("recurrent training source has no non-forced post-burn-in loss rows")
        updates += epoch_updates
        model.eval()
        with torch.no_grad():
            validation_stats = _iterate_pass_v3(
                model, validation_source, device=device, carry_hidden=True, burn_in=None,
                expected_partition="validation",
            )
        validation_nll = validation_stats.complete_nll()
        history.append({
            "epoch": float(epoch), "train_complete_nll": train_stats.complete_nll(),
            "validation_complete_nll": validation_nll,
            "train_stop_nll": train_stats.stop_nll() if train_stats.stop_nll() is not None else float("nan"),
            "validation_stop_nll": validation_stats.stop_nll() if validation_stats.stop_nll() is not None else float("nan"),
            "optimizer_updates": float(updates),
        })
        if validation_nll < best_nll - min_delta:
            best_nll, best_epoch, stale_epochs = validation_nll, epoch, 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                stop_reason = "patience"
                break
    if best_epoch < 0 or not best_state:
        raise RuntimeError("recurrent R3 training produced no best validation checkpoint")
    model.load_state_dict(best_state)
    after = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    parameter_delta_l1 = _state_delta_l1_v3(before, after)
    if not math.isfinite(parameter_delta_l1) or parameter_delta_l1 <= 0.0:
        raise RuntimeError("recurrent R3 best checkpoint has no positive parameter delta")
    return RecurrentTrainingResultV3(
        candidate=candidate, best_epoch=best_epoch, best_validation_nll=best_nll,
        history=tuple(history), checkpoint_state=best_state,
        parameter_delta_l1=parameter_delta_l1, epochs=len(history),
        stop_reason=stop_reason, optimizer_updates=updates,
    )


def _current_r2_loss_v3(model: nn.Module, example: BCExampleV3) -> tuple[torch.Tensor, float | None]:
    step_logits = getattr(model, "step_logits", None)
    if not callable(step_logits):
        raise ValueError("current-R2 model must implement step_logits")
    semantic, stop = step_logits(example.model_input, example.step_input)
    if not isinstance(semantic, torch.Tensor) or semantic.ndim != 1 or semantic.numel() != len(example.state.candidates):
        raise ValueError("current-R2 semantic logits disagree with sealed action domain")
    logits = semantic
    stop_available = bool(getattr(example.step_input, "stop_available", False))
    if stop_available:
        if not isinstance(stop, torch.Tensor) or stop.numel() != 1:
            raise ValueError("current-R2 STOP logit is invalid")
        logits = torch.cat((semantic, stop.reshape(1)))
    if logits.numel() != len(example.target_masses) or not torch.isfinite(logits).all():
        raise ValueError("current-R2 complete-action logits are non-finite or misaligned")
    target = torch.tensor(example.target_masses, dtype=logits.dtype, device=logits.device)
    log_probs = F.log_softmax(logits, dim=0)
    loss = -(target * log_probs).sum() * float(example.quality_weight)
    stop_loss = None if not stop_available or target[-1].item() <= 0 else float(
        (-target[-1] * log_probs[-1] * float(example.quality_weight)).detach().item()
    )
    return loss, stop_loss


def _current_r2_pass_v3(
    model: nn.Module, source: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3,
    *, expected_partition: str, optimizer: torch.optim.Optimizer | None,
    gradient_clip_norm: float = _GRADIENT_CLIP_NORM,
) -> tuple[_PassStatsV3, int]:
    if not math.isfinite(gradient_clip_norm) or gradient_clip_norm <= 0:
        raise ValueError("current-R2 gradient clip norm is invalid")
    stats = _PassStatsV3(); updates = 0
    for sequence in source.iter_sequences():
        if sequence.partition != expected_partition:
            raise ValueError("current-R2 source crossed its sealed partition")
        losses: list[torch.Tensor] = []
        for index, example in enumerate(sequence.steps):
            if _is_forced_sole_stop_v3(example):
                stats.forced_sole_stop_rows += 1
                continue
            if index < sequence.burn_in:
                continue
            loss, stop_loss = _current_r2_loss_v3(model, example)
            stats.complete_rows += 1; stats.complete_loss_sum += float(loss.detach().item())
            stats.complete_weight += float(example.quality_weight); losses.append(loss)
            if stop_loss is not None:
                stats.stop_target_rows += 1; stats.stop_loss_sum += stop_loss
                stats.stop_weight += float(example.quality_weight)
        if optimizer is not None and losses:
            optimizer.zero_grad(set_to_none=True); torch.stack(losses).mean().backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
            optimizer.step(); updates += 1
    return stats, updates


def _train_current_r2_v3(
    model: nn.Module,
    train_sequences: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3],
    validation_sequences: RecurrentSequenceSourceV3 | SealedRecurrentSequenceSourceV3 | PreparedRecurrentSequenceSourceV3 | Sequence[RecurrentBCSequenceV3],
    *, device: torch.device, max_epochs: int, patience: int, min_delta: float,
    learning_rate: float = 1e-4, gradient_clip_norm: float = _GRADIENT_CLIP_NORM,
    production_run: bool = False,
) -> RecurrentTrainingResultV3:
    """Train current-R2 with the same sequence-update and epoch budget as R3."""
    if (not isinstance(model, nn.Module) or not isinstance(device, torch.device)
            or type(max_epochs) is not int or max_epochs < 1 or type(patience) is not int or patience < 1
            or not math.isfinite(min_delta) or min_delta < 0 or not math.isfinite(learning_rate) or learning_rate <= 0
            or not math.isfinite(gradient_clip_norm) or gradient_clip_norm <= 0):
        raise ValueError("current-R2 training arguments are invalid")
    train_source = _as_source_v3(train_sequences); validation_source = _as_source_v3(validation_sequences)
    _require_production_adapter_v3(train_source, production_run); _require_production_adapter_v3(validation_source, production_run)
    _audit_training_split_v3(train_source, validation_source)
    model.to(device); before = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    best_nll = float("inf"); best_epoch = -1; best_state: dict[str, torch.Tensor] = {}
    history: list[Mapping[str, float]] = []; stale_epochs = 0; updates = 0; stop_reason = "max_epochs"
    for epoch in range(max_epochs):
        model.train(); train_stats, epoch_updates = _current_r2_pass_v3(
            model, train_source, expected_partition="train", optimizer=optimizer,
            gradient_clip_norm=gradient_clip_norm,
        )
        if epoch_updates < 1:
            raise ValueError("current-R2 training source has no non-forced post-burn-in loss rows")
        updates += epoch_updates
        model.eval()
        with torch.no_grad():
            validation_stats, _ = _current_r2_pass_v3(
                model, validation_source, expected_partition="validation", optimizer=None,
                gradient_clip_norm=gradient_clip_norm,
            )
        validation_nll = validation_stats.complete_nll()
        history.append({"epoch": float(epoch), "train_complete_nll": train_stats.complete_nll(), "validation_complete_nll": validation_nll, "optimizer_updates": float(updates)})
        if validation_nll < best_nll - min_delta:
            best_nll = validation_nll; best_epoch = epoch; stale_epochs = 0
            best_state = {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                stop_reason = "patience"; break
    if best_epoch < 0 or not best_state:
        raise RuntimeError("current-R2 training produced no best validation checkpoint")
    model.load_state_dict(best_state)
    parameter_delta = _state_delta_l1_v3(before, best_state)
    if not math.isfinite(parameter_delta) or parameter_delta <= 0:
        raise RuntimeError("current-R2 best checkpoint has no positive parameter delta")
    return RecurrentTrainingResultV3(
        candidate="current-R2", best_epoch=best_epoch, best_validation_nll=best_nll,
        history=tuple(history), checkpoint_state=best_state, parameter_delta_l1=parameter_delta,
        epochs=len(history), stop_reason=stop_reason, optimizer_updates=updates,
    )


def _supervision_metrics_v3(
    source: PreparedRecurrentSequenceSourceV3, *, train_source: PreparedRecurrentSequenceSourceV3, device: torch.device,
    logits_for_example: Callable[[BCExampleV3, torch.Tensor | None, bool], tuple[torch.Tensor, torch.Tensor | None]],
    carry_hidden: bool,
) -> dict[str, object]:
    """Score the common complete-action authority without retaining the corpus."""
    train_action_frequency: dict[int, int] = {}
    for sequence in train_source.iter_sequences():
        if sequence.partition != "train":
            raise ValueError("recurrent training source crossed its pinned partition")
        for example in sequence.steps:
            if example.target_index < len(example.state.candidates):
                action_type = example.state.candidates[example.target_index].action_type
                train_action_frequency[action_type] = train_action_frequency.get(action_type, 0) + 1
    losses: list[float] = []; confidences: list[tuple[float, float]] = []
    top1 = top3 = eligible = rare_hits = 0
    for sequence in source.iter_sequences():
        if sequence.partition != "validation":
            raise ValueError("recurrent validation source crossed its pinned partition")
        hidden: torch.Tensor | None = None
        for index, example in enumerate(sequence.steps):
            logits, next_hidden = logits_for_example(example, hidden if carry_hidden else None, example.episode_start if carry_hidden else True)
            hidden = next_hidden if carry_hidden else None
            if _is_forced_sole_stop_v3(example) or index < sequence.burn_in:
                continue
            target = torch.tensor(example.target_masses, dtype=logits.dtype, device=logits.device)
            log_probs = F.log_softmax(logits, dim=0); probs = log_probs.exp()
            losses.append(float((-(target * log_probs).sum()).detach().item()))
            target_index = min((index for index, mass in enumerate(example.target_masses) if mass == max(example.target_masses)), default=0)
            prediction = int(logits.argmax().item())
            top1 += int(prediction == target_index); top3 += int(target_index in logits.topk(min(3, logits.numel())).indices.tolist())
            confidences.append((float(probs[prediction].item()), float(prediction == target_index)))
            if target_index < len(example.state.candidates):
                action_type = example.state.candidates[target_index].action_type
                if train_action_frequency.get(action_type, 0) <= 1:
                    eligible += 1; rare_hits += int(target_index in logits.topk(min(3, logits.numel())).indices.tolist())
    if not losses or not confidences or eligible < 1:
        raise ValueError("recurrent Gate common validation metrics are not measured")
    ece = 0.0
    for bucket in range(10):
        members = [(confidence, correct) for confidence, correct in confidences if min(9, int(confidence * 10)) == bucket]
        if members:
            ece += abs(sum(confidence for confidence, _ in members) / len(members) - sum(correct for _, correct in members) / len(members)) * len(members) / len(confidences)
    return {
        "validation_complete_nll": math.fsum(losses) / len(losses), "top1": top1 / len(losses), "top3": top3 / len(losses),
        "rare_action_recall": {"rule_version": "train-action-type-frequency-lte-1-v1", "eligible": eligible, "value": rare_hits / eligible, "status": "measured"},
        "calibration": {"bin_count": 10, "expected_calibration_error": ece, "sample_count": len(confidences)},
    }


def _canonical_json_v3(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _object_sha256_v3(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json_v3(value)).hexdigest()


def _file_sha256_v3(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha256_v3(value: object, *, field: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _finite_float_v3(value: object, *, field: str) -> float:
    if type(value) not in {int, float} or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return float(value)


def _atomic_write_json_v3(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical_json_v3(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_save_checkpoint_v3(path: Path, state: Mapping[str, torch.Tensor]) -> tuple[str, str]:
    """Persist a CPU tensor state atomically and prove its raw/state digests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    frozen = {name: value.detach().cpu().contiguous() for name, value in state.items()}
    try:
        with temporary.open("xb") as handle:
            torch.save(frozen, handle)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        reloaded = torch.load(path, map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("recurrent Gate checkpoint cannot be reloaded") from exc
    if type(reloaded) is not dict or _state_sha256_v3(reloaded) != _state_sha256_v3(frozen):
        raise RuntimeError("recurrent Gate checkpoint state hash does not verify")
    return _file_sha256_v3(path), _state_sha256_v3(frozen)


def _read_canonical_object_v3(path: Path, *, name: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid JSON") from exc
    if type(value) is not dict or _canonical_json_v3(value) != raw:
        raise ValueError(f"{name} is not canonical JSON")
    return value


def _state_sha256_v3(state: Mapping[str, torch.Tensor]) -> str:
    """Hash checkpoint tensors without serializing pickle or device metadata."""
    digest = hashlib.sha256()
    for name in sorted(state):
        value = state[name]
        if not isinstance(value, torch.Tensor):
            raise ValueError("recurrent checkpoint state contains a non-tensor")
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(json.dumps(list(tensor.shape), separators=(",", ":")).encode("ascii"))
        digest.update(b"\0")
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


_COVERAGE_COUNTERS_V3 = frozenset({
    "sequence_count", "step_count", "stop_available_count", "positive_stop_target_count",
    "nonempty_prefix_count", "ordered_nonempty_prefix_count", "burn_in_step_count",
})


def _validate_cell_coverage_v3(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {"schema", "train", "validation"} or value.get("schema") != "meta-specialist-recurrent-cell-coverage-v1":
        raise ValueError("recurrent Gate cell coverage has an invalid closed schema")
    for partition in ("train", "validation"):
        row = value.get(partition)
        if type(row) is not dict or set(row) != _COVERAGE_COUNTERS_V3:
            raise ValueError("recurrent Gate partition coverage has an invalid closed schema")
        if any(type(row[key]) is not int or isinstance(row[key], bool) or row[key] < 0 for key in _COVERAGE_COUNTERS_V3):
            raise ValueError("recurrent Gate partition coverage counter is invalid")
        if row["sequence_count"] < 1 or row["step_count"] < row["sequence_count"]:
            raise ValueError("recurrent Gate partition sequence coverage is empty")
        if not (row["ordered_nonempty_prefix_count"] <= row["nonempty_prefix_count"] <= row["step_count"]
                and row["positive_stop_target_count"] <= row["stop_available_count"] <= row["step_count"]
                and row["burn_in_step_count"] <= row["step_count"]):
            raise ValueError("recurrent Gate partition coverage counters are inconsistent")
    return value


def _measure_source_coverage_v3(source: PreparedRecurrentSequenceSourceV3, *, partition: str) -> dict[str, int]:
    counters = {key: 0 for key in _COVERAGE_COUNTERS_V3}
    for sequence in source.iter_sequences():
        if sequence.partition != partition:
            raise ValueError("recurrent Gate coverage source crossed its pinned partition")
        counters["sequence_count"] += 1
        for index, example in enumerate(sequence.steps):
            counters["step_count"] += 1
            stop_available = bool(getattr(example.step_input, "stop_available", False))
            counters["stop_available_count"] += int(stop_available)
            counters["positive_stop_target_count"] += int(stop_available and example.target_masses[-1] > 0.0)
            nonempty_prefix = bool(example.state.semantic_prefix)
            counters["nonempty_prefix_count"] += int(nonempty_prefix)
            counters["ordered_nonempty_prefix_count"] += int(nonempty_prefix and example.state.prefix_order_sensitive)
            counters["burn_in_step_count"] += int(index < sequence.burn_in)
    return counters


def _cell_coverage_v3(
    train_source: PreparedRecurrentSequenceSourceV3, validation_source: PreparedRecurrentSequenceSourceV3,
) -> dict[str, object]:
    value = {"schema": "meta-specialist-recurrent-cell-coverage-v1",
             "train": _measure_source_coverage_v3(train_source, partition="train"),
             "validation": _measure_source_coverage_v3(validation_source, partition="validation")}
    return _validate_cell_coverage_v3(value)


def _selection_cell_v3(cell: Mapping[str, object]) -> tuple[str, str, int, dict[str, float]]:
    if type(cell) is not dict:
        raise ValueError("recurrent Gate cell must be an object")
    candidate, lane, seed = cell.get("candidate"), cell.get("lane"), cell.get("seed")
    if candidate not in _CANDIDATES or lane not in _LANES or type(seed) is not int or isinstance(seed, bool) or seed not in _SEEDS:
        raise ValueError("recurrent Gate cell identity is invalid")
    for field in ("manifest_file_sha256", "manifest_sha256"):
        _require_sha256_v3(cell.get(field), field=f"recurrent Gate {field}")
    _validate_cell_coverage_v3(cell.get("coverage"))
    budget = cell.get("budget")
    if (type(budget) is not dict or set(budget) != {
                "max_epochs", "patience", "min_delta", "burn_in", "learning_rate",
                "gradient_clip_norm",
            }
            or type(budget["max_epochs"]) is not int or budget["max_epochs"] < 1
            or type(budget["patience"]) is not int or budget["patience"] < 1
            or type(budget["burn_in"]) is not int or budget["burn_in"] < 0):
        raise ValueError("recurrent Gate cell budget is invalid")
    _finite_float_v3(budget["min_delta"], field="recurrent Gate min_delta")
    learning_rate = _finite_float_v3(budget["learning_rate"], field="recurrent Gate learning_rate")
    if learning_rate != _LEARNING_RATE:
        raise ValueError("recurrent Gate learning_rate changed from the prefixed common budget")
    gradient_clip_norm = _finite_float_v3(
        budget["gradient_clip_norm"], field="recurrent Gate gradient_clip_norm",
    )
    if gradient_clip_norm != _GRADIENT_CLIP_NORM:
        raise ValueError("recurrent Gate gradient_clip_norm changed from the prefixed common budget")
    metrics = {field: _finite_float_v3(cell.get(field), field=f"recurrent Gate {field}") for field in (
        "validation_complete_nll", "top1", "top3",
    )}
    if not 0.0 <= metrics["top1"] <= metrics["top3"] <= 1.0:
        raise ValueError("recurrent Gate top-k metrics are invalid")
    rare = cell.get("rare_action_recall")
    if (type(rare) is not dict or set(rare) != {"rule_version", "eligible", "value", "status"}
            or rare.get("rule_version") != "train-action-type-frequency-lte-1-v1"
            or type(rare.get("eligible")) is not int or rare["eligible"] < 1
            or rare.get("status") != "measured"):
        raise ValueError("recurrent Gate rare-action metric is not measured")
    _finite_float_v3(rare.get("value"), field="recurrent Gate rare-action recall")
    calibration = cell.get("calibration")
    if (type(calibration) is not dict or set(calibration) != {"bin_count", "expected_calibration_error", "sample_count"}
            or type(calibration.get("bin_count")) is not int or calibration["bin_count"] < 1
            or type(calibration.get("sample_count")) is not int or calibration["sample_count"] < 1):
        raise ValueError("recurrent Gate calibration is not measured")
    _finite_float_v3(calibration.get("expected_calibration_error"), field="recurrent Gate calibration ECE")
    for field in ("checkpoint_sha256",):
        _require_sha256_v3(cell.get(field), field=f"recurrent Gate {field}")
    checkpoint = cell.get("checkpoint")
    if (type(checkpoint) is not dict or set(checkpoint) != {
                "basename", "path", "file_sha256", "state_sha256", "candidate", "lane", "seed",
                "gradient_clip_norm",
            }
            or checkpoint.get("candidate") != candidate or checkpoint.get("lane") != lane or checkpoint.get("seed") != seed
            or checkpoint.get("gradient_clip_norm") != gradient_clip_norm
            or type(checkpoint.get("basename")) is not str or Path(checkpoint["basename"]).name != checkpoint["basename"]
            or type(checkpoint.get("path")) is not str or Path(checkpoint["path"]).is_absolute()
            or ".." in Path(checkpoint["path"]).parts or Path(checkpoint["path"]).name != checkpoint["basename"]):
        raise ValueError("recurrent Gate checkpoint descriptor or gradient_clip_norm is invalid")
    for field in ("file_sha256", "state_sha256"):
        _require_sha256_v3(checkpoint.get(field), field=f"recurrent Gate checkpoint {field}")
    training = cell.get("training")
    if (type(training) is not dict or set(training) != {"epochs", "best_epoch", "stop_reason", "update_unit", "validation_authority"}
            or type(training.get("epochs")) is not int or not 1 <= training["epochs"] <= budget["max_epochs"]
            or type(training.get("best_epoch")) is not int or not 0 <= training["best_epoch"] < training["epochs"]
            or training.get("stop_reason") not in {"patience", "max_epochs"}
            or training.get("update_unit") != "physical-sequence"
            or training.get("validation_authority") != "independent-sealed-validation"):
        raise ValueError("recurrent Gate training evidence is invalid")
    updates = cell.get("optimizer_updates"); non_reset = cell.get("non_reset_hidden_steps")
    complete_rows = cell.get("complete_rows"); stop_rows = cell.get("stop_target_rows"); forced_rows = cell.get("forced_sole_stop_rows")
    evidence = [("optimizer_updates", updates, 1), ("complete_rows", complete_rows, 1), ("stop_target_rows", stop_rows, 1), ("forced_sole_stop_rows", forced_rows, 0)]
    if candidate in _R3_CANDIDATE_ORDER:
        evidence.append(("non_reset_hidden_steps", non_reset, 1))
    for field, value, minimum in evidence:
        if type(value) is not int or isinstance(value, bool) or value < minimum:
            raise ValueError(f"recurrent Gate {field} is invalid")
    parameter_delta = _finite_float_v3(cell.get("parameter_delta_l1"), field="recurrent Gate parameter_delta_l1")
    if parameter_delta <= 0.0:
        raise ValueError("recurrent Gate parameter_delta_l1 must be positive")
    if candidate == "current-R2":
        if cell.get("reference_kind") != "CurrentR2GateAdapterV3":
            raise ValueError("recurrent Gate current-R2 reference adapter is invalid")
        return candidate, lane, seed, metrics
    metrics.update({field: _finite_float_v3(cell.get(field), field=f"recurrent Gate {field}") for field in (
        "carry_complete_nll", "reset_complete_nll", "carry_stop_nll", "reset_stop_nll",
    )})
    return candidate, lane, seed, metrics


def select_recurrent_r3_v3(cells: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Select each lane independently from current-R2/R3-A/R3-B evidence.

    This is supervised selection only.  Runtime rollout/fault evidence is a
    separate authority, so no result from this function can authorize a
    promotion.
    """
    blockers: list[str] = []
    by_identity: dict[tuple[str, str, int], Mapping[str, object]] = {}
    for cell in cells:
        try:
            candidate, lane, seed, _metrics = _selection_cell_v3(cell)
        except ValueError as exc:
            blockers.append(str(exc))
            continue
        identity = (candidate, lane, seed)
        if identity in by_identity:
            blockers.append(f"duplicate recurrent Gate cell {candidate}/{lane}/{seed}")
        else:
            by_identity[identity] = cell
    required = {(candidate, lane, seed) for candidate in _CANDIDATE_ORDER for lane in _LANES for seed in _SEEDS}
    for candidate, lane, seed in sorted(required - set(by_identity)):
        blockers.append(f"missing recurrent Gate cell {candidate}/{lane}/{seed}")
    if len(by_identity) != len(required):
        blockers.append("recurrent Gate matrix is not exactly 18 cells")
    lanes: dict[str, dict[str, object]] = {}
    summaries: dict[str, dict[str, dict[str, float]]] = {}
    for lane in _LANES:
        lane_blockers = [item for item in blockers if f"/{lane}/" in item or f" {lane} " in item or "matrix" in item]
        rows: dict[str, list[Mapping[str, object]]] = {
            candidate: [by_identity[(candidate, lane, seed)] for seed in _SEEDS if (candidate, lane, seed) in by_identity]
            for candidate in _CANDIDATE_ORDER
        }
        summaries[lane] = {}
        for candidate, candidate_rows in rows.items():
            if len(candidate_rows) == len(_SEEDS):
                parsed = [_selection_cell_v3(row)[3] for row in candidate_rows]
                summaries[lane][candidate] = {metric: math.fsum(item[metric] for item in parsed) / len(parsed) for metric in parsed[0]}
        if any(len(rows[candidate]) != len(_SEEDS) for candidate in _CANDIDATE_ORDER):
            lanes[lane] = {"status": "BLOCKED", "preferred": None, "blockers": sorted(set(lane_blockers)), "summaries": summaries[lane]}
            continue
        anchors = {(str(row["manifest_file_sha256"]), str(row["manifest_sha256"]), _canonical_json_v3(row["budget"])) for candidate in _CANDIDATE_ORDER for row in rows[candidate]}
        if len(anchors) != 1:
            lane_blockers.append(f"recurrent Gate {lane} has cross-candidate source, teacher overlay, or budget drift")
        coverage_values = {_canonical_json_v3(row["coverage"]) for candidate in _CANDIDATE_ORDER for row in rows[candidate]}
        if len(coverage_values) != 1:
            lane_blockers.append(f"recurrent Gate {lane} has cross-candidate coverage drift")
            lanes[lane] = {"status": "UNMEASURED_ORDERED_PREFIX", "preferred": "current-R2", "blockers": sorted(set(lane_blockers)), "summaries": summaries[lane]}
            continue
        coverage = rows["current-R2"][0]["coverage"]
        assert type(coverage) is dict and type(coverage["train"]) is dict and type(coverage["validation"]) is dict
        ordered_coverage = int(coverage["train"]["ordered_nonempty_prefix_count"]) + int(coverage["validation"]["ordered_nonempty_prefix_count"])
        if ordered_coverage == 0:
            lane_blockers.append(f"recurrent Gate {lane} ordered nonempty-prefix coverage is unmeasured")
            lanes[lane] = {"status": "UNMEASURED_ORDERED_PREFIX", "preferred": "current-R2", "blockers": sorted(set(lane_blockers)), "summaries": summaries[lane]}
            continue
        r2 = summaries[lane]["current-R2"]
        eligible: dict[str, bool] = {}
        for candidate in _R3_CANDIDATE_ORDER:
            candidate_rows = rows[candidate]; summary = summaries[lane][candidate]
            temporal_complete = summary["carry_complete_nll"] <= summary["reset_complete_nll"] + 0.02
            temporal_stop = summary["carry_stop_nll"] <= summary["reset_stop_nll"] + 0.02
            complete_improved = (summary["carry_complete_nll"] <= summary["reset_complete_nll"] - 0.01 and sum(_selection_cell_v3(row)[3]["carry_complete_nll"] <= _selection_cell_v3(row)[3]["reset_complete_nll"] - 0.01 for row in candidate_rows) >= 2)
            stop_improved = (summary["carry_stop_nll"] <= summary["reset_stop_nll"] - 0.01 and sum(_selection_cell_v3(row)[3]["carry_stop_nll"] <= _selection_cell_v3(row)[3]["reset_stop_nll"] - 0.01 for row in candidate_rows) >= 2)
            absolute = summary["carry_complete_nll"] <= r2["validation_complete_nll"] + 0.02 and summary["top1"] >= r2["top1"] - 0.02
            eligible[candidate] = not lane_blockers and temporal_complete and temporal_stop and (complete_improved or stop_improved) and absolute
            if not eligible[candidate]:
                lane_blockers.append(f"recurrent Gate {candidate}/{lane} does not satisfy temporal or current-R2 criteria")
        if eligible.get("R3-A"):
            preferred = "R3-A"
            if eligible.get("R3-B") and (summaries[lane]["R3-B"]["carry_complete_nll"] <= summaries[lane]["R3-A"]["carry_complete_nll"] - 0.01 or summaries[lane]["R3-B"]["carry_stop_nll"] <= summaries[lane]["R3-A"]["carry_stop_nll"] - 0.02):
                preferred = "R3-B"
            lanes[lane] = {"status": "MODEL_SELECTED_PENDING_RUNTIME", "preferred": preferred, "blockers": sorted(set(lane_blockers)), "summaries": summaries[lane]}
        else:
            lanes[lane] = {"status": "CURRENT_R2_FALLBACK", "preferred": "current-R2", "blockers": sorted(set(lane_blockers)), "summaries": summaries[lane]}
    statuses = {value["status"] for value in lanes.values()}
    status = ("BLOCKED_COVERAGE_UNMEASURED" if "UNMEASURED_ORDERED_PREFIX" in statuses else "BLOCKED" if "BLOCKED" in statuses else "MODEL_SELECTED_PENDING_RUNTIME"
              if "MODEL_SELECTED_PENDING_RUNTIME" in statuses else "CURRENT_R2_FALLBACK")
    return {"status": status, "lanes": lanes, "blockers": sorted(set(blockers)), "summaries": summaries, "promotion_authority": False}


def _load_lane_input_v3(lane: str, value: RecurrentGateLaneInputV3) -> dict[str, object]:
    raw_path = value.manifest_path.resolve()
    if _file_sha256_v3(raw_path) != value.expected_manifest_file_sha256:
        raise ValueError(f"recurrent Gate {lane} manifest external file SHA-256 does not match")
    manifest = read_recurrent_selection_manifest_v3(raw_path)
    if manifest.get("lane") != lane:
        raise ValueError(f"recurrent Gate manifest lane mismatches {lane}")
    return manifest


def _run_recurrent_cell_v3(
    *, candidate: str, lane: str, seed: int, lane_input: RecurrentGateLaneInputV3,
    manifest: Mapping[str, object], prepared_lane: PreparedRecurrentLaneV3,
    max_epochs: int, patience: int, min_delta: float,
    burn_in: int, device: torch.device, checkpoint_dir: Path,
) -> dict[str, object]:
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import _load_production_vocabulary_v3

    torch.manual_seed(seed)
    vocabulary = _load_production_vocabulary_v3()
    model = SpecialistModelV3(
        card_vocabulary_size=max(vocabulary.recognized_card_ids), seed=seed,
        encoder_kind="zone-deepsets" if candidate == "R3-A" else "relation-attention",
    )
    train_source = prepared_recurrent_sequence_source_v3(
        prepared_lane.receipt_path, expected_receipt_file_sha256=prepared_lane.expected_receipt_file_sha256,
        burn_in=burn_in, partition="train",
    )
    validation_source = prepared_recurrent_sequence_source_v3(
        prepared_lane.receipt_path, expected_receipt_file_sha256=prepared_lane.expected_receipt_file_sha256,
        burn_in=burn_in, partition="validation",
    )
    peak_memory = None
    device_name = None
    if device.type == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA recurrent Gate requested but CUDA is unavailable")
        with torch.cuda.device(device):
            torch.cuda.synchronize(device)
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(device)
            device_name = torch.cuda.get_device_name(device)
    trained = train_recurrent_r3_v3(
        model, train_source, validation_source, candidate=candidate, device=device,
        max_epochs=max_epochs, patience=patience, min_delta=min_delta, learning_rate=_LEARNING_RATE,
        gradient_clip_norm=_GRADIENT_CLIP_NORM,
        production_run=True,
    )
    model.load_state_dict(trained.checkpoint_state)
    metrics = evaluate_carry_vs_reset_v3(
        model, validation_source, device=device, burn_in=burn_in, production_run=True,
    )
    if device.type == "cuda":
        with torch.cuda.device(device):
            torch.cuda.synchronize(device)
            peak_memory = int(torch.cuda.max_memory_allocated(device))
            if peak_memory <= 0:
                raise RuntimeError("CUDA recurrent Gate has no measurable allocation")
    if (trained.optimizer_updates < 1 or trained.parameter_delta_l1 <= 0.0
            or metrics.non_reset_hidden_steps < 1 or metrics.stop_target_rows < 1):
        raise RuntimeError("recurrent Gate lacks required update, carry, or STOP evidence")
    def logits_for_example(example: BCExampleV3, hidden: torch.Tensor | None, episode_start: bool) -> tuple[torch.Tensor, torch.Tensor | None]:
        output = model.forward_v3(example.state, hidden_state=hidden, episode_start=episode_start)
        logits = output.logits
        if bool(getattr(example.step_input, "stop_available", False)):
            logits = torch.cat((logits, (model.stop_vector @ output.global_token + model.stop_bias).reshape(1)))
        return logits, output.hidden_state
    common_metrics = _supervision_metrics_v3(validation_source, train_source=train_source, device=device, logits_for_example=logits_for_example, carry_hidden=True)
    coverage = _cell_coverage_v3(train_source, validation_source)
    checkpoint_name = f"recurrent-checkpoint-{candidate}-{lane}-{seed}.pt"
    checkpoint_path = checkpoint_dir / checkpoint_name
    checkpoint_file_sha256, checkpoint_state_sha256 = _atomic_save_checkpoint_v3(checkpoint_path, trained.checkpoint_state)
    return {
        "candidate": candidate, "lane": lane, "seed": seed,
        "manifest_file_sha256": lane_input.expected_manifest_file_sha256,
        "manifest_sha256": manifest["manifest_sha256"],
        "budget": {
            "max_epochs": max_epochs, "patience": patience, "min_delta": min_delta,
            "burn_in": burn_in, "learning_rate": _LEARNING_RATE,
            "gradient_clip_norm": _GRADIENT_CLIP_NORM,
        },
        "checkpoint_sha256": checkpoint_state_sha256,
        "checkpoint": {
            "basename": checkpoint_name,
            "path": str(checkpoint_path.relative_to(checkpoint_dir.parent)),
            "file_sha256": checkpoint_file_sha256, "state_sha256": checkpoint_state_sha256,
            "candidate": candidate, "lane": lane, "seed": seed,
            "gradient_clip_norm": _GRADIENT_CLIP_NORM,
        },
        "optimizer_updates": trained.optimizer_updates, "parameter_delta_l1": trained.parameter_delta_l1,
        "training": {"epochs": trained.epochs, "best_epoch": trained.best_epoch, "stop_reason": trained.stop_reason,
                     "update_unit": "physical-sequence", "validation_authority": "independent-sealed-validation"},
        "carry_complete_nll": metrics.carry_complete_nll, "reset_complete_nll": metrics.reset_complete_nll,
        "carry_stop_nll": metrics.carry_stop_nll, "reset_stop_nll": metrics.reset_stop_nll,
        "complete_rows": metrics.complete_rows, "stop_target_rows": metrics.stop_target_rows,
        "forced_sole_stop_rows": metrics.forced_sole_stop_rows,
        "non_reset_hidden_steps": metrics.non_reset_hidden_steps,
        "cuda_peak_memory_bytes": peak_memory, "cuda_device_name": device_name,
        "cuda_peak_memory_measured": device.type == "cuda",
        "coverage": coverage,
        **common_metrics,
    }


def _run_current_r2_reference_cell_v3(
    *, lane: str, seed: int, lane_input: RecurrentGateLaneInputV3, manifest: Mapping[str, object],
    prepared_lane: PreparedRecurrentLaneV3, max_epochs: int, patience: int, min_delta: float,
    burn_in: int, device: torch.device, checkpoint_dir: Path,
) -> dict[str, object]:
    """Train and evaluate current-R2 on the exact sealed R3 authority."""
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import CurrentR2GateAdapterV3, _load_production_vocabulary_v3
    vocabulary = _load_production_vocabulary_v3()
    adapter = CurrentR2GateAdapterV3(card_vocabulary_size=max(vocabulary.recognized_card_ids), seed=seed)
    peak_memory: int | None = None; device_name: str | None = None
    if device.type == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA recurrent Gate requested but CUDA is unavailable")
        with torch.cuda.device(device):
            torch.cuda.synchronize(device); torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats(device)
            device_name = torch.cuda.get_device_name(device)
    adapter.model.to(device)
    validation_source = prepared_recurrent_sequence_source_v3(
        prepared_lane.receipt_path, expected_receipt_file_sha256=prepared_lane.expected_receipt_file_sha256,
        burn_in=burn_in, partition="validation",
    )
    train_source = prepared_recurrent_sequence_source_v3(
        prepared_lane.receipt_path, expected_receipt_file_sha256=prepared_lane.expected_receipt_file_sha256,
        burn_in=burn_in, partition="train",
    )
    trained = _train_current_r2_v3(
        adapter.model, train_source, validation_source, device=device, max_epochs=max_epochs,
        patience=patience, min_delta=min_delta, learning_rate=_LEARNING_RATE,
        gradient_clip_norm=_GRADIENT_CLIP_NORM, production_run=True,
    )
    adapter.model.load_state_dict(trained.checkpoint_state); adapter.model.eval()
    def logits_for_example(example: BCExampleV3, _hidden: torch.Tensor | None, _episode_start: bool) -> tuple[torch.Tensor, torch.Tensor | None]:
        # Use the adapter's published current-R2 object; the STOP logit remains
        # part of the real v1 model API and is appended only when legal.
        semantic, stop = adapter.model.step_logits(example.model_input, example.step_input)
        logits = semantic
        if bool(getattr(example.step_input, "stop_available", False)):
            logits = torch.cat((logits, stop.reshape(1)))
        return logits, None
    common_metrics = _supervision_metrics_v3(validation_source, train_source=train_source, device=device, logits_for_example=logits_for_example, carry_hidden=False)
    coverage = _cell_coverage_v3(train_source, validation_source)
    with torch.no_grad():
        validation_stats, _ = _current_r2_pass_v3(
            adapter.model, validation_source, expected_partition="validation", optimizer=None,
            gradient_clip_norm=_GRADIENT_CLIP_NORM,
        )
    checkpoint_name = f"recurrent-checkpoint-current-R2-{lane}-{seed}.pt"
    checkpoint_path = checkpoint_dir / checkpoint_name
    checkpoint_file_sha256, checkpoint_state_sha256 = _atomic_save_checkpoint_v3(checkpoint_path, trained.checkpoint_state)
    if device.type == "cuda":
        with torch.cuda.device(device):
            torch.cuda.synchronize(device); peak_memory = int(torch.cuda.max_memory_allocated(device))
            if peak_memory <= 0:
                raise RuntimeError("CUDA recurrent Gate has no measurable allocation")
    return {
        "candidate": "current-R2", "lane": lane, "seed": seed,
        "manifest_file_sha256": lane_input.expected_manifest_file_sha256, "manifest_sha256": manifest["manifest_sha256"],
        "budget": {
            "max_epochs": max_epochs, "patience": patience, "min_delta": min_delta,
            "burn_in": burn_in, "learning_rate": _LEARNING_RATE,
            "gradient_clip_norm": _GRADIENT_CLIP_NORM,
        },
        "reference_kind": "CurrentR2GateAdapterV3", "checkpoint_sha256": checkpoint_state_sha256,
        "checkpoint": {"basename": checkpoint_name, "path": str(checkpoint_path.relative_to(checkpoint_dir.parent)),
                       "file_sha256": checkpoint_file_sha256, "state_sha256": checkpoint_state_sha256,
                       "candidate": "current-R2", "lane": lane, "seed": seed,
                       "gradient_clip_norm": _GRADIENT_CLIP_NORM},
        "optimizer_updates": trained.optimizer_updates, "parameter_delta_l1": trained.parameter_delta_l1,
        "training": {"epochs": trained.epochs, "best_epoch": trained.best_epoch, "stop_reason": trained.stop_reason,
                     "update_unit": "physical-sequence", "validation_authority": "independent-sealed-validation"},
        "carry_complete_nll": validation_stats.complete_nll(), "reset_complete_nll": validation_stats.complete_nll(),
        "carry_stop_nll": validation_stats.stop_nll(), "reset_stop_nll": validation_stats.stop_nll(),
        "complete_rows": validation_stats.complete_rows, "stop_target_rows": validation_stats.stop_target_rows,
        "forced_sole_stop_rows": validation_stats.forced_sole_stop_rows, "non_reset_hidden_steps": 0,
        "cuda_peak_memory_bytes": peak_memory,
        "cuda_device_name": device_name, "cuda_peak_memory_measured": device.type == "cuda", **common_metrics,
        "coverage": coverage,
    }


def _cuda_evidence_blockers_v3(cells: Sequence[Mapping[str, object]]) -> list[str]:
    """Require independently measured CUDA evidence from every physical cell."""
    blockers: list[str] = []
    for index, cell in enumerate(cells):
        label = f"recurrent Gate CUDA cell {index}"
        if type(cell) is not dict:
            blockers.append(f"{label} is not an object")
            continue
        name = cell.get("cuda_device_name")
        peak = cell.get("cuda_peak_memory_bytes")
        measured = cell.get("cuda_peak_memory_measured")
        if type(name) is not str or not name.strip():
            blockers.append(f"{label} lacks a nonempty cuda_device_name")
        if type(peak) is not int or isinstance(peak, bool) or peak <= 0:
            blockers.append(f"{label} lacks a positive cuda_peak_memory_bytes")
        if measured is not True:
            blockers.append(f"{label} lacks a measured CUDA peak flag")
    return blockers


def _selection_for_device_v3(cells: Sequence[Mapping[str, object]], *, device: str) -> dict[str, object]:
    """Bind candidate selection to device evidence and promotion authority.

    CPU can exercise the implementation but cannot produce a θ0-authorizing
    decision.  CUDA failure remains a CUDA failure and is never relabelled as
    a CPU result.
    """
    base = select_recurrent_r3_v3(cells)
    if device == "cpu":
        return {
            "status": "RESEARCH_ONLY", "preferred": None,
            "blockers": base["blockers"], "summaries": base["summaries"], "lanes": base["lanes"],
            "promotion_authority": False, "research_decision_status": base["status"],
        }
    if device != "cuda:0":
        raise ValueError("recurrent Gate device is invalid")
    cuda_blockers = _cuda_evidence_blockers_v3(cells)
    if cuda_blockers:
        return {
            "status": "BLOCKED", "preferred": None,
            "blockers": sorted(set([*base["blockers"], *cuda_blockers])), "summaries": base["summaries"],
            "lanes": base["lanes"], "promotion_authority": False,
        }
    return base


def _read_recurrent_gate_result_v3(path: Path) -> dict[str, object]:
    payload = _read_canonical_object_v3(path, name="recurrent Gate result")
    required = {"schema", "device", "seeds", "cells", "selection", "result_sha256"}
    if payload.get("schema") == _LEGACY_RESULT_SCHEMA:
        if set(payload) != required:
            raise ValueError("legacy recurrent Gate result has an invalid closed schema")
        result_sha = _require_sha256_v3(payload.get("result_sha256"), field="legacy recurrent Gate result_sha256")
        if _object_sha256_v3({key: value for key, value in payload.items() if key != "result_sha256"}) != result_sha:
            raise ValueError("legacy recurrent Gate result self hash does not verify")
        return payload
    if set(payload) != required or payload.get("schema") != _RESULT_SCHEMA:
        raise ValueError("recurrent Gate result has an invalid closed schema")
    result_sha = _require_sha256_v3(payload.get("result_sha256"), field="recurrent Gate result_sha256")
    if _object_sha256_v3({key: value for key, value in payload.items() if key != "result_sha256"}) != result_sha:
        raise ValueError("recurrent Gate result self hash does not verify")
    if type(payload["device"]) is not str or payload["device"] not in {"cpu", "cuda:0"} or payload["seeds"] != list(_SEEDS):
        raise ValueError("recurrent Gate result device/seeds are invalid")
    if type(payload["cells"]) is not list or type(payload["selection"]) is not dict:
        raise ValueError("recurrent Gate result cells/selection are invalid")
    root = path.parent.resolve()
    for cell in payload["cells"]:
        if type(cell) is dict and set(cell) == {"candidate", "lane", "seed", "failure"}:
            continue
        candidate, _lane, _seed, _metrics = _selection_cell_v3(cell)
        if candidate in _CANDIDATE_ORDER:
            descriptor = cell["checkpoint"]
            assert type(descriptor) is dict
            checkpoint = (root / str(descriptor["path"])).resolve()
            if root not in checkpoint.parents or checkpoint.name != descriptor["basename"]:
                raise ValueError("recurrent Gate checkpoint path escapes result directory")
            if _file_sha256_v3(checkpoint) != descriptor["file_sha256"]:
                raise ValueError("recurrent Gate checkpoint external file SHA-256 does not match")
            try:
                state = torch.load(checkpoint, map_location="cpu", weights_only=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError("recurrent Gate checkpoint cannot be read") from exc
            if type(state) is not dict or _state_sha256_v3(state) != descriptor["state_sha256"] or descriptor["state_sha256"] != cell["checkpoint_sha256"]:
                raise ValueError("recurrent Gate checkpoint state SHA-256 does not match")
    computed = _selection_for_device_v3(payload["cells"], device=payload["device"])
    if payload["selection"] != computed:
        if payload["device"] == "cuda:0" and _cuda_evidence_blockers_v3(payload["cells"]):
            raise ValueError("recurrent Gate CUDA evidence is missing or invalid")
        raise ValueError("recurrent Gate result selection does not match the closed rules")
    return payload


def read_recurrent_gate_selection_v3(path: str | Path) -> dict[str, object]:
    manifest_path = Path(path)
    payload = _read_canonical_object_v3(manifest_path, name="recurrent Gate selection")
    required = {"schema", "result_path", "result_file_sha256", "result_sha256", "selection", "selection_sha256"}
    if payload.get("schema") == _LEGACY_SELECTION_SCHEMA:
        if set(payload) != required:
            raise ValueError("legacy recurrent Gate selection has an invalid closed schema")
    elif set(payload) != required or payload.get("schema") != _SELECTION_SCHEMA:
        raise ValueError("recurrent Gate selection has an invalid closed schema")
    selection_sha = _require_sha256_v3(payload.get("selection_sha256"), field="recurrent Gate selection_sha256")
    if _object_sha256_v3({key: value for key, value in payload.items() if key != "selection_sha256"}) != selection_sha:
        raise ValueError("recurrent Gate selection self hash does not verify")
    result_name = payload.get("result_path")
    if type(result_name) is not str or Path(result_name).name != result_name:
        raise ValueError("recurrent Gate selection result path is invalid")
    _require_sha256_v3(payload.get("result_file_sha256"), field="recurrent Gate result_file_sha256")
    _require_sha256_v3(payload.get("result_sha256"), field="recurrent Gate result_sha256")
    if type(payload.get("selection")) is not dict:
        raise ValueError("recurrent Gate selection decision is invalid")
    return payload


def verify_recurrent_gate_anchor_v3(
    selection_path: str | Path, *, expected_selection_file_sha256: str,
    expected_result_file_sha256: str, expected_result_sha256: str,
) -> dict[str, object]:
    """Verify caller-owned raw file anchors before trusting a Gate decision."""
    selection_file = Path(selection_path)
    _require_sha256_v3(expected_selection_file_sha256, field="expected recurrent selection file SHA-256")
    _require_sha256_v3(expected_result_file_sha256, field="expected recurrent result file SHA-256")
    _require_sha256_v3(expected_result_sha256, field="expected recurrent result SHA-256")
    if _file_sha256_v3(selection_file) != expected_selection_file_sha256:
        raise ValueError("recurrent Gate selection external file SHA-256 does not match")
    selection = read_recurrent_gate_selection_v3(selection_file)
    result_path = selection_file.parent / str(selection["result_path"])
    if _file_sha256_v3(result_path) != expected_result_file_sha256:
        raise ValueError("recurrent Gate result external file SHA-256 does not match")
    if selection["result_file_sha256"] != expected_result_file_sha256 or selection["result_sha256"] != expected_result_sha256:
        raise ValueError("recurrent Gate selection does not bind caller-provided result anchors")
    result = _read_recurrent_gate_result_v3(result_path)
    if result.get("schema") != _RESULT_SCHEMA or selection.get("schema") != _SELECTION_SCHEMA:
        raise ValueError("legacy recurrent Gate artifacts are read-only and cannot authorize promotion")
    if result["result_sha256"] != expected_result_sha256 or result["selection"] != selection["selection"]:
        raise ValueError("recurrent Gate result/selection binding mismatches")
    if result["device"] != "cuda:0":
        raise ValueError("recurrent Gate CPU evidence is research-only and cannot authorize promotion")
    if result["selection"].get("promotion_authority") is not True:
        raise ValueError("recurrent Gate runtime evidence is pending and cannot authorize promotion")
    return result


def run_recurrent_gate_v3(
    *, lane_inputs: Mapping[str, RecurrentGateLaneInputV3], max_epochs: int,
    patience: int, min_delta: float, burn_in: int, device: torch.device,
    output_dir: str | Path, seeds: tuple[int, ...] = _SEEDS,
) -> RecurrentGateRunV3:
    """Run the closed 2 lane × 2 R3 candidate × 3 seed recurrent Gate."""
    if (set(lane_inputs) != set(_LANES) or any(type(value) is not RecurrentGateLaneInputV3 for value in lane_inputs.values())
            or seeds != _SEEDS or type(max_epochs) is not int or max_epochs < 1
            or type(patience) is not int or patience < 1 or not math.isfinite(min_delta) or min_delta < 0
            or type(burn_in) is not int or burn_in < 0 or not isinstance(device, torch.device)
            or str(device) not in {"cpu", "cuda:0"}):
        raise ValueError("recurrent Gate arguments are invalid")
    manifests = {lane: _load_lane_input_v3(lane, lane_inputs[lane]) for lane in _LANES}
    command_identity = _object_sha256_v3({
        "runner": "meta-specialist-recurrent-gate-v3", "device": str(device),
        "max_epochs": max_epochs, "patience": patience, "min_delta": min_delta,
        "burn_in": burn_in, "learning_rate": _LEARNING_RATE,
        "gradient_clip_norm": _GRADIENT_CLIP_NORM, "seeds": list(seeds),
        "lane_manifest_file_sha256": {lane: lane_inputs[lane].expected_manifest_file_sha256 for lane in _LANES},
    })
    prepared_lanes = {
        lane: prepare_sealed_recurrent_lane_v3(
            lane_inputs[lane].manifest_path,
            expected_manifest_file_sha256=lane_inputs[lane].expected_manifest_file_sha256,
            output_dir=Path(output_dir) / "recurrent-preflight" / lane,
            command_identity=command_identity,
        )
        for lane in _LANES
    }
    cells: list[dict[str, object]] = []
    for candidate in _CANDIDATE_ORDER:
        for lane in _LANES:
            for seed in _SEEDS:
                try:
                    if candidate == "current-R2":
                        cells.append(_run_current_r2_reference_cell_v3(
                            lane=lane, seed=seed, lane_input=lane_inputs[lane], manifest=manifests[lane], prepared_lane=prepared_lanes[lane],
                            max_epochs=max_epochs, patience=patience, min_delta=min_delta, burn_in=burn_in, device=device,
                            checkpoint_dir=Path(output_dir) / "checkpoints",
                        ))
                    else:
                        cells.append(_run_recurrent_cell_v3(
                            candidate=candidate, lane=lane, seed=seed, lane_input=lane_inputs[lane],
                            manifest=manifests[lane], prepared_lane=prepared_lanes[lane],
                            max_epochs=max_epochs, patience=patience,
                            min_delta=min_delta, burn_in=burn_in, device=device, checkpoint_dir=Path(output_dir) / "checkpoints",
                        ))
                except (OSError, RuntimeError, ValueError) as exc:
                    cells.append({"candidate": candidate, "lane": lane, "seed": seed, "failure": {
                        "type": type(exc).__name__, "message": str(exc),
                    }})
    decision = _selection_for_device_v3(cells, device=str(device))
    device_name = "cuda-0" if str(device) == "cuda:0" else "cpu"
    output = Path(output_dir) / f"recurrent-gate-result-v3-{device_name}.json"
    selection_path = Path(output_dir) / f"recurrent-gate-selection-v3-{device_name}.json"
    result_without_hash: dict[str, object] = {
        "schema": _RESULT_SCHEMA, "device": str(device), "seeds": list(_SEEDS),
        "cells": cells, "selection": decision,
    }
    result = {**result_without_hash, "result_sha256": _object_sha256_v3(result_without_hash)}
    _atomic_write_json_v3(output, result)
    # Reload the bytes before deriving an adjacent manifest; do not retain a
    # pre-publication dictionary as proof of what reached the filesystem.
    written = _read_recurrent_gate_result_v3(output)
    selection_without_hash: dict[str, object] = {
        "schema": _SELECTION_SCHEMA, "result_path": output.name,
        "result_file_sha256": _file_sha256_v3(output), "result_sha256": written["result_sha256"],
        "selection": written["selection"],
    }
    selection = {**selection_without_hash, "selection_sha256": _object_sha256_v3(selection_without_hash)}
    _atomic_write_json_v3(selection_path, selection)
    reloaded_selection = read_recurrent_gate_selection_v3(selection_path)
    if reloaded_selection != selection:
        raise RuntimeError("recurrent Gate atomic selection reload differs from written bytes")
    return RecurrentGateRunV3(
        status=str(decision["status"]), output_path=output, decision_path=selection_path,
        result_sha256=str(written["result_sha256"]),
    )


__all__ = [
    "PreparedRecurrentSequenceSourceV3", "RecurrentGateLaneInputV3", "RecurrentGateMetricsV3", "RecurrentGateRunV3", "RecurrentSequenceSourceV3", "RecurrentTrainingResultV3",
    "SealedRecurrentSequenceSourceV3", "bounded_fixture_sequence_source_v3",
    "evaluate_carry_vs_reset_v3", "prepared_recurrent_sequence_source_v3", "read_recurrent_gate_selection_v3", "run_recurrent_gate_v3",
    "sealed_recurrent_sequence_source_v3", "select_recurrent_r3_v3", "train_recurrent_r3_v3",
    "verify_recurrent_gate_anchor_v3",
]
