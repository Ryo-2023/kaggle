"""Bounded, research-only recurrent behavior cloning for representation V4.

This module deliberately has no promotion authority.  In particular, its
uniform-weight source is an opt-in diagnostic path for a sealed selection when
the formal READY teacher-quality overlay is unavailable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import random
import stat
import tempfile
import time
from typing import Iterator

import torch
from torch.nn import functional as F

from mage_ptcg.meta_specialist.neural_model_v4 import (
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
    save_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v3 import RecurrentRecordAuthorityRowV3
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import (
    RecurrentBCSequenceV4,
    RecurrentBCStepV4,
    _project_record_steps_v4,
)
from mage_ptcg.meta_specialist.outcome_weighted_v4 import (
    RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
)


RESEARCH_ONLY_UNIFORM_WEIGHT = "RESEARCH_ONLY_UNIFORM_WEIGHT"
_RESULT_SCHEMA = "meta-specialist-recurrent-bc-v4-research"
SHORT_PILOT_MAJOR_REGRESSION_NLL = 0.01
SHORT_PILOT_MIN_MEAN_DELTA_NLL = 0.01
SHORT_PILOT_MIN_EPISODES_PER_PARTITION = 4
SHORT_PILOT_MIN_COMPONENTS_PER_PARTITION = 4

# Conservative research-only weights.  They upweight under-represented
# macro-actions without changing the default uniform objective.  The helper
# below renormalizes the supplied mapping to arithmetic mean 1.0.
ACTION_BALANCED_WEIGHTS_V1: Mapping[str, float] = {
    "0": 0.75, "1": 0.75, "2": 0.75, "3": 0.75,
    "7": 1.0, "8": 1.0, "9": 1.5, "10": 1.0,
    "12": 1.25, "13": 1.25, "14": 1.5, "STOP": 0.75,
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def trainer_implementation_sha256_v4() -> str:
    """Identity of the training/projection/model semantic closure used by BC."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256(b"meta-specialist-v4-training-closure-v1\0")
    for name in ("recurrent_bc_v4.py", "recurrent_dataset_v4.py", "representation_v4.py", "neural_model_v4.py"):
        raw = (root / name).read_bytes()
        digest.update(name.encode("ascii") + b"\0" + len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def selected_objective_sha256_v4(sequences: Sequence[RecurrentBCSequenceV4]) -> str:
    """Bind exact projected state/action targets and reach weights in physical order."""
    digest = hashlib.sha256(b"meta-specialist-v4-selected-objective-v1\0")
    for sequence in sequences:
        identity = (sequence.partition, sequence.episode_group, sequence.component_id, sequence.burn_in)
        digest.update(repr(identity).encode("utf-8") + b"\0")
        for step in sequence.steps:
            objective = (
                step.record_id, step.content_hash, step.episode_start,
                repr(step.state), tuple(step.target_masses), step.reach_mass,
                step.quality_weight, step.supervision_weight,
            )
            digest.update(repr(objective).encode("utf-8") + b"\0")
    return digest.hexdigest()


def _shuffled_train_sequences_v4(
    sequences: Sequence[RecurrentBCSequenceV4], *, sequence_order_seed: int, epoch: int,
) -> tuple[RecurrentBCSequenceV4, ...]:
    """Return a reproducible per-epoch training order without touching validation order."""
    if type(sequence_order_seed) is not int or type(epoch) is not int or epoch < 0:
        raise ValueError("recurrent BC sequence-order identity is invalid")
    domain_seed = hashlib.sha256(
        f"meta-specialist-recurrent-bc-v4:train-sequence-order:{sequence_order_seed}:{epoch}".encode("ascii")
    ).digest()
    shuffled = list(sequences)
    random.Random(int.from_bytes(domain_seed, byteorder="big")).shuffle(shuffled)
    return tuple(shuffled)


def _require_research_mode(mode: object) -> str:
    if mode not in {RESEARCH_ONLY_UNIFORM_WEIGHT, RESEARCH_ONLY_OUTCOME_WEIGHTED_V4}:
        raise ValueError(
            "research-only BC mode is not recognized; explicit "
            "RESEARCH_ONLY_UNIFORM_WEIGHT or RESEARCH_ONLY_OUTCOME_WEIGHTED_V4 is required"
        )
    return str(mode)


def _normalized_action_type_weights_v4(
    weights: Mapping[str, float] | None,
) -> dict[str, float] | None:
    """Validate and mean-normalize optional action-type loss weights."""
    if weights is None:
        return None
    if not isinstance(weights, Mapping) or not weights:
        raise ValueError("action_type_weights must be a non-empty mapping")
    normalized: dict[str, float] = {}
    for key, value in weights.items():
        if type(key) is not str or not key or type(value) is bool or type(value) not in {int, float}:
            raise ValueError("action_type_weights keys/values are invalid")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise ValueError("action_type_weights must contain positive finite values")
        normalized[key] = numeric
    mean = math.fsum(normalized.values()) / len(normalized)
    if not math.isfinite(mean) or mean <= 0.0:
        raise ValueError("action_type_weights mean is invalid")
    return {key: value / mean for key, value in normalized.items()}


def _step_action_type_v4(step: RecurrentBCStepV4) -> str:
    """Read the sealed target macro-action without consulting runtime IDs."""
    if step.target_index < len(step.state.candidates):
        return str(step.state.candidates[step.target_index].action_type)
    if bool(getattr(step.step_input, "stop_available", False)) and step.target_index == len(step.state.candidates):
        return "STOP"
    raise ValueError("recurrent BC target action type is outside its sealed domain")


@dataclass(frozen=True, slots=True)
class ResearchSubsetV4:
    """An intentionally non-promotable bounded slice of a sealed selection."""

    lane: str
    selection_manifest_path: Path
    selection_manifest_file_sha256: str
    sequences: tuple[RecurrentBCSequenceV4, ...]
    records_by_partition: Mapping[str, int]
    target_records_by_partition: Mapping[str, int]
    card_vocabulary_size: int
    card_vocabulary_card_id_count: int
    mode: str = RESEARCH_ONLY_UNIFORM_WEIGHT
    promotion_authority: bool = False
    episodes_per_partition: int = SHORT_PILOT_MIN_EPISODES_PER_PARTITION
    components_per_partition: int = SHORT_PILOT_MIN_COMPONENTS_PER_PARTITION
    require_positive_stop: bool = False
    train_episodes_per_partition: int | None = None
    validation_episodes_per_partition: int | None = None
    train_components_per_partition: int | None = None
    validation_components_per_partition: int | None = None


@dataclass(frozen=True, slots=True)
class RecurrentBCTrainingResultV4:
    schema: str
    mode: str
    promotion_authority: bool
    best_epoch: int
    epochs_completed: int
    initial_validation_complete_action_nll: float
    best_validation_complete_action_nll: float
    validation_delta_nll: float
    improved: bool
    validation_by_component: Mapping[str, float]
    history: tuple[Mapping[str, float], ...]
    best_checkpoint_path: Path
    best_checkpoint_file_sha256: str
    best_checkpoint_tensor_state_sha256: str
    last_checkpoint_path: Path | None = None
    optimizer_updates_completed: int = 0
    elapsed_seconds: float = 0.0
    invocation_elapsed_seconds: float = 0.0
    cumulative_train_elapsed_seconds: float = 0.0


def target_records_by_partition_v4(
    counts: Mapping[str, object], *, max_records: int, subset_fraction: float,
) -> dict[str, int]:
    """Pick a bounded target while retaining a meaningful validation slice."""
    if (
        set(counts) != {"train", "validation"}
        or any(type(counts[partition]) is not int or counts[partition] < 1 for partition in counts)
        or type(max_records) is not int or max_records < 4
        or not 0.0 < subset_fraction <= 0.1
    ):
        raise ValueError("research split counts or bounded subset arguments are invalid")
    requested = {
        "train": max(1, int(math.ceil(int(counts["train"]) * subset_fraction))),
        "validation": max(2, int(math.ceil(int(counts["validation"]) * subset_fraction))),
    }
    if sum(requested.values()) <= max_records:
        return requested
    total = int(counts["train"]) + int(counts["validation"])
    validation = max(2, int(round(max_records * int(counts["validation"]) / total)))
    validation = min(validation, max_records - 1)
    return {"train": max_records - validation, "validation": validation}


def short_pilot_selection_status_v4(deltas: Sequence[float], *, epochs: int = 1) -> str:
    """Classify a single-epoch two-seed signal without promotion authority.

    Any best-checkpoint comparison over more than one epoch is selection-biased,
    so it is deliberately never labelled positive here.
    """
    if len(deltas) != 2 or any(type(value) is not float or not math.isfinite(value) for value in deltas):
        raise ValueError("short pilot requires exactly two finite seed deltas")
    if type(epochs) is not int or epochs < 1:
        raise ValueError("short pilot epochs must be a positive integer")
    if epochs != 1:
        return "SHORT_PILOT_SELECTION_BIASED"
    if (
        math.fsum(deltas) / 2.0 <= -SHORT_PILOT_MIN_MEAN_DELTA_NLL
        and all(value <= SHORT_PILOT_MAJOR_REGRESSION_NLL for value in deltas)
    ):
        return "SHORT_PILOT_POSITIVE"
    return "SHORT_PILOT_NEGATIVE"


def _should_select_fast_episode_v4(
    partition: str, component_id: str, *, episodes: Mapping[str, int], components: Mapping[str, set[str]],
    positive_stop_rows: Mapping[str, int] | None = None, require_positive_stop: bool = False,
    episodes_per_partition: int = SHORT_PILOT_MIN_EPISODES_PER_PARTITION,
    components_per_partition: int = SHORT_PILOT_MIN_COMPONENTS_PER_PARTITION,
    episode_targets: Mapping[str, int] | None = None,
    component_targets: Mapping[str, int] | None = None,
) -> bool:
    """Take only the first four distinct split components per partition.

    The selection index is physical-order sorted, so accepting surplus train
    episodes can otherwise exhaust a bounded pilot before later validation
    components are even inspected.
    """
    if partition not in {"train", "validation"} or not component_id:
        raise ValueError("fast research episode split identity is invalid")
    if episode_targets is None:
        episode_targets = {"train": episodes_per_partition, "validation": episodes_per_partition}
    if component_targets is None:
        component_targets = {"train": components_per_partition, "validation": components_per_partition}
    if (
        set(episode_targets) != {"train", "validation"}
        or set(component_targets) != {"train", "validation"}
        or any(type(episode_targets[key]) is not int or not 4 <= episode_targets[key] <= 512 for key in episode_targets)
        or any(type(component_targets[key]) is not int or not 4 <= component_targets[key] <= episode_targets[key] for key in component_targets)
    ):
        raise ValueError("fast research episode/component targets are invalid")
    if type(require_positive_stop) is not bool:
        raise ValueError("fast research positive STOP requirement is invalid")
    if positive_stop_rows is None:
        positive_stop_rows = {"train": 0, "validation": 0}
    if set(positive_stop_rows) != {"train", "validation"} or any(
        type(value) is not int or value < 0 for value in positive_stop_rows.values()
    ):
        raise ValueError("fast research positive STOP counters are invalid")
    if episodes[partition] >= episode_targets[partition]:
        return require_positive_stop and positive_stop_rows[partition] == 0
    if component_id in components[partition] and len(components[partition]) < component_targets[partition]:
        return False
    return True


def _sequence_records(sequence: RecurrentBCSequenceV4) -> int:
    return len({step.record_id for step in sequence.steps})


def _validate_sequences(
    sequences: Sequence[RecurrentBCSequenceV4], *, partition: str, mode: str,
) -> None:
    mode = _require_research_mode(mode)
    if not sequences:
        raise ValueError(f"{partition} sequences are empty")
    for sequence in sequences:
        if type(sequence) is not RecurrentBCSequenceV4 or sequence.partition != partition:
            raise ValueError("recurrent sequence partition is invalid")
        if not sequence.research_only or any(not step.research_only for step in sequence.steps):
            raise ValueError("research trainer only accepts explicitly research-only sequences")
        if mode == RESEARCH_ONLY_UNIFORM_WEIGHT and any(step.quality_weight != 1.0 for step in sequence.steps):
            raise ValueError("RESEARCH_ONLY_UNIFORM_WEIGHT requires explicit uniform 1.0 step weights")
        if not sequence.steps[0].episode_start or any(step.episode_start for step in sequence.steps[1:]):
            raise ValueError("recurrent sequence must reset exactly at its episode boundary")
        for group in _record_groups(sequence):
            if any(step.quality_weight != group[0].quality_weight for step in group[1:]):
                raise ValueError("record decoder rows must share one quality_weight")
            if any(
                type(step.supervision_weight) is bool
                or not math.isfinite(float(step.supervision_weight))
                or not 0.0 <= float(step.supervision_weight) <= 1.0
                for step in group
            ):
                raise ValueError("record decoder rows contain an invalid supervision_weight")


def _complete_action_nll_from_output(
    model: SpecialistModelV4, step: RecurrentBCStepV4, output: object,
) -> torch.Tensor:
    logits = output.logits  # type: ignore[union-attr]
    stop_available = bool(getattr(step.step_input, "stop_available", False))
    if stop_available:
        stop = model.stop_vector @ output.global_token + model.stop_bias  # type: ignore[union-attr]
        logits = torch.cat((logits, stop.reshape(1)))
    if logits.numel() != len(step.target_masses):
        raise ValueError("semantic/STOP target does not match the complete legal action domain")
    masses = torch.tensor(step.target_masses, dtype=logits.dtype, device=logits.device)
    return -(masses * F.log_softmax(logits, dim=0)).sum()


def _record_groups(sequence: RecurrentBCSequenceV4) -> tuple[tuple[RecurrentBCStepV4, ...], ...]:
    """Keep all decoder prefixes for one physical record on one GRU transition."""
    groups: list[tuple[RecurrentBCStepV4, ...]] = []
    current: list[RecurrentBCStepV4] = []
    current_id: str | None = None
    for step in sequence.steps:
        if current_id is None or step.record_id == current_id:
            current.append(step)
            current_id = step.record_id
            continue
        groups.append(tuple(current))
        current = [step]
        current_id = step.record_id
    if current:
        groups.append(tuple(current))
    if any(not group or group[0].episode_start != (index == 0) for index, group in enumerate(groups)):
        raise ValueError("record-group episode boundary is invalid")
    if any(any(step.episode_start for step in group[1:]) for group in groups):
        raise ValueError("decoder prefix rows must not reset recurrent state")
    return tuple(groups)


def _evaluate(
    model: SpecialistModelV4, sequences: Sequence[RecurrentBCSequenceV4], *, mode: str,
) -> tuple[float, Mapping[str, float]]:
    _validate_sequences(sequences, partition="validation", mode=mode)
    previous_training = model.training
    model.eval()
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    total_nll = total_weight = 0.0
    with torch.no_grad():
        for sequence in sequences:
            hidden: torch.Tensor | None = None
            for record_index, group in enumerate(_record_groups(sequence)):
                outputs = model.forward_record_group_v4(
                    tuple(step.state for step in group),
                    hidden_state=hidden, episode_start=group[0].episode_start,
                )
                next_hidden = outputs[0].hidden_state
                if record_index >= sequence.burn_in:
                    record_nll = 0.0
                    has_supervised_row = False
                    for step, output in zip(group, outputs, strict=True):
                        if step.supervision_weight <= 0.0:
                            continue
                        nll = _complete_action_nll_from_output(model, step, output)
                        step_weight = step.supervision_weight * step.reach_mass
                        record_nll += step_weight * float(nll.item())
                        has_supervised_row = True
                    if has_supervised_row:
                        weight = group[0].quality_weight
                        totals[sequence.component_id][0] += weight * record_nll
                        totals[sequence.component_id][1] += weight
                        total_nll += weight * record_nll
                        total_weight += weight
                hidden = next_hidden
                if hidden is not None:
                    hidden = hidden.detach()
    model.train(previous_training)
    if total_weight <= 0.0:
        raise ValueError("validation contains no post-burn-in complete actions")
    by_component = {
        component: numerator / denominator
        for component, (numerator, denominator) in sorted(totals.items()) if denominator > 0.0
    }
    return total_nll / total_weight, by_component


def positive_stop_target_metrics_v4(
    model: SpecialistModelV4, sequences: Sequence[RecurrentBCSequenceV4], *, mode: str,
) -> Mapping[str, float | int | None]:
    """Measure reachable positive STOP targets after burn-in on the reloaded model."""
    _validate_sequences(sequences, partition="validation", mode=mode)
    previous_training = model.training
    model.eval()
    rows = 0
    numerator = denominator = 0.0
    with torch.no_grad():
        for sequence in sequences:
            hidden: torch.Tensor | None = None
            for record_index, group in enumerate(_record_groups(sequence)):
                outputs = model.forward_record_group_v4(
                    tuple(step.state for step in group),
                    hidden_state=hidden, episode_start=group[0].episode_start,
                )
                if record_index >= sequence.burn_in:
                    for step, output in zip(group, outputs, strict=True):
                        stop_mass = step.target_masses[-1] if bool(getattr(step.step_input, "stop_available", False)) else 0.0
                        if stop_mass <= 0.0 or step.supervision_weight <= 0.0:
                            continue
                        stop = model.stop_vector @ output.global_token + model.stop_bias
                        logits = torch.cat((output.logits, stop.reshape(1)))
                        weight = group[0].quality_weight * step.supervision_weight * step.reach_mass * stop_mass
                        numerator += weight * float((-F.log_softmax(logits, dim=0)[-1]).item())
                        denominator += weight
                        rows += 1
                hidden = outputs[0].hidden_state
                if hidden is not None:
                    hidden = hidden.detach()
    model.train(previous_training)
    return {
        "positive_stop_target_rows": rows,
        "positive_stop_target_conditional_nll": numerator / denominator if denominator > 0.0 else None,
    }


def _train_epoch(
    model: SpecialistModelV4, sequences: Sequence[RecurrentBCSequenceV4], *,
    optimizer: torch.optim.Optimizer, tbptt_steps: int, gradient_clip_norm: float,
    mode: str, telemetry: dict[str, float] | None = None,
    action_type_weights: Mapping[str, float] | None = None,
    progress_callback: object | None = None,
) -> float:
    _validate_sequences(sequences, partition="train", mode=mode)
    normalized_action_weights = _normalized_action_type_weights_v4(action_type_weights)
    model.train()
    total_nll = total_weight = 0.0
    updates = 0
    gradient_norm_total = 0.0
    started = time.monotonic()
    for sequence_index, sequence in enumerate(sequences):
        groups = _record_groups(sequence)
        if normalized_action_weights is None:
            sequence_weight = math.fsum(
                group[0].quality_weight
                for group in groups[sequence.burn_in:]
                if any(step.supervision_weight > 0.0 for step in group)
            )
            if mode == RESEARCH_ONLY_OUTCOME_WEIGHTED_V4:
                # Keep episode-level quality in the gradient.  The old code
                # used the same quality-weighted mass as both numerator and
                # denominator, so one constant weight per episode cancelled
                # exactly and the outcome arm reduced to uniform BC.
                sequence_weight = math.fsum(
                    1.0
                    for group in groups[sequence.burn_in:]
                    if any(step.supervision_weight > 0.0 for step in group)
                )
        else:
            sequence_weight = math.fsum(
                step.quality_weight * step.supervision_weight * step.reach_mass
                * normalized_action_weights.get(_step_action_type_v4(step), 1.0)
                for group in groups[sequence.burn_in:]
                for step in group
                if len(step.target_masses) > 1 and step.supervision_weight > 0.0
            )
            if mode == RESEARCH_ONLY_OUTCOME_WEIGHTED_V4:
                sequence_weight = math.fsum(
                    step.supervision_weight * step.reach_mass
                    * normalized_action_weights.get(_step_action_type_v4(step), 1.0)
                    for group in groups[sequence.burn_in:]
                    for step in group
                    if len(step.target_masses) > 1 and step.supervision_weight > 0.0
                )
        if sequence_weight <= 0.0:
            raise ValueError("training sequence contains no post-burn-in decoder rows")
        optimizer.zero_grad(set_to_none=True)
        hidden: torch.Tensor | None = None
        chunk_loss: torch.Tensor | None = None
        unrolled = 0
        for record_index, group in enumerate(groups):
            if record_index < sequence.burn_in:
                with torch.no_grad():
                    outputs = model.forward_record_group_v4(
                        tuple(step.state for step in group),
                        hidden_state=hidden, episode_start=group[0].episode_start,
                    )
                    hidden = outputs[0].hidden_state
                if hidden is not None:
                    hidden = hidden.detach()
                continue
            outputs = model.forward_record_group_v4(
                tuple(step.state for step in group),
                hidden_state=hidden, episode_start=group[0].episode_start,
            )
            hidden = outputs[0].hidden_state
            for step, output in zip(group, outputs, strict=True):
                # A one-choice domain updates the hidden state but contributes
                # no policy gradient.  Balanced research runs also exclude it
                # from the normalization denominator.
                if normalized_action_weights is not None and len(step.target_masses) <= 1:
                    continue
                if step.supervision_weight <= 0.0:
                    continue
                action_weight = (
                    normalized_action_weights.get(_step_action_type_v4(step), 1.0)
                    if normalized_action_weights is not None else 1.0
                )
                weight = step.quality_weight * step.supervision_weight * step.reach_mass * action_weight
                weighted = _complete_action_nll_from_output(model, step, output) * weight
                normalized = weighted / sequence_weight
                chunk_loss = normalized if chunk_loss is None else chunk_loss + normalized
                total_nll += float(weighted.detach().item())
                if normalized_action_weights is not None:
                    total_weight += weight
            if normalized_action_weights is None:
                if any(step.supervision_weight > 0.0 for step in group):
                    total_weight += group[0].quality_weight
            unrolled += 1
            if unrolled == tbptt_steps:
                if chunk_loss is not None:
                    chunk_loss.backward()
                    chunk_loss = None
                unrolled = 0
                if hidden is not None:
                    hidden = hidden.detach()
        if chunk_loss is not None:
            chunk_loss.backward()
        gradient_norm_total += float(torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm).item())
        optimizer.step()
        updates += 1
        if callable(progress_callback):
            progress_callback({
                "sequences_completed": sequence_index + 1,
                "sequences_total": len(sequences),
                "optimizer_updates_in_epoch": updates,
                "partial_train_complete_action_nll": (
                    total_nll / total_weight if total_weight > 0.0 else None
                ),
                "epoch_elapsed_seconds": time.monotonic() - started,
            })
    if total_weight <= 0.0:
        raise ValueError("training contains no post-burn-in complete actions")
    if telemetry is not None:
        telemetry.update({
            "optimizer_updates": float(updates),
            "mean_preclip_gradient_norm": gradient_norm_total / updates if updates else 0.0,
            "train_elapsed_seconds": time.monotonic() - started,
        })
    return total_nll / total_weight


_RESUME_SCHEMA = "meta-specialist-recurrent-bc-v4-epoch-resume-v1"


def _canonical_run_config_v4(value: Mapping[str, object] | None) -> dict[str, object]:
    """Pin the complete optimizer/data identity used by an internal resume file."""
    raw = {} if value is None else dict(value)
    try:
        encoded = __import__("json").dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
        decoded = __import__("json").loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("recurrent BC run_config must be JSON-stable") from exc
    if not isinstance(decoded, dict):
        raise ValueError("recurrent BC run_config must be a JSON object")
    return decoded


def _atomic_torch_save_v4(path: Path, payload: Mapping[str, object]) -> None:
    """Publish a checkpoint by rename so an interruption never leaves a partial resume state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    os.close(descriptor)
    try:
        torch.save(dict(payload), temporary)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_epoch_resume_v4(
    path: Path, *, model: SpecialistModelV4, optimizer: torch.optim.Optimizer,
    run_config: Mapping[str, object], sequence_order_seed: int, epochs: int,
) -> dict[str, object]:
    try:
        payload = torch.load(path, map_location=next(model.parameters()).device, weights_only=False)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("recurrent BC resume checkpoint is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _RESUME_SCHEMA:
        raise ValueError("recurrent BC resume checkpoint schema is invalid")
    if (
        payload.get("run_config") != dict(run_config)
        or payload.get("sequence_order_seed") != sequence_order_seed
        or payload.get("epochs") != epochs
    ):
        raise ValueError("recurrent BC resume checkpoint configuration differs")
    next_epoch = payload.get("next_epoch")
    history = payload.get("history")
    if type(next_epoch) is not int or not 0 <= next_epoch <= epochs or not isinstance(history, list) or len(history) != next_epoch:
        raise ValueError("recurrent BC resume checkpoint epoch history is invalid")
    try:
        model.load_state_dict(payload["model_state"])
        optimizer.load_state_dict(payload["optimizer_state"])
    except (KeyError, RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("recurrent BC resume checkpoint state is invalid") from exc
    return payload


def train_recurrent_bc_v4(
    model: SpecialistModelV4, train_sequences: Sequence[RecurrentBCSequenceV4],
    validation_sequences: Sequence[RecurrentBCSequenceV4], *, mode: str,
    output_dir: str | Path, sequence_order_seed: int, epochs: int = 3, patience: int = 1,
    learning_rate: float = 1e-3, tbptt_steps: int = 8,
    gradient_clip_norm: float = 1.0,
    action_type_weights: Mapping[str, float] | None = None,
    run_config: Mapping[str, object] | None = None,
    resume: bool = False,
    epoch_callback: object | None = None,
    train_progress_callback: object | None = None,
) -> RecurrentBCTrainingResultV4:
    """Train/reload a sealed research run, with honest epoch-boundary resume only."""
    _require_research_mode(mode)
    normalized_action_weights = _normalized_action_type_weights_v4(action_type_weights)
    if (
        type(model) is not SpecialistModelV4 or type(epochs) is not int or epochs < 1
        or type(patience) is not int or patience < 0 or learning_rate <= 0.0
        or type(tbptt_steps) is not int or tbptt_steps < 1 or gradient_clip_norm <= 0.0
        or type(sequence_order_seed) is not int or type(resume) is not bool
    ):
        raise ValueError("recurrent BC configuration is invalid")
    _validate_sequences(train_sequences, partition="train", mode=mode)
    _validate_sequences(validation_sequences, partition="validation", mode=mode)
    train_components = {sequence.component_id for sequence in train_sequences}
    valid_components = {sequence.component_id for sequence in validation_sequences}
    if train_components & valid_components:
        raise ValueError("component split leaks between train and validation")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    checkpoint = destination / "best-recurrent-bc-v4.pt"
    last_checkpoint = destination / "last-recurrent-bc-v4.pt"
    model_device = next(model.parameters()).device
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    explicit_selected_sequence_sha = None
    if isinstance(run_config, Mapping):
        candidate_selected_sha = run_config.get("selected_sequence_sha256")
        if candidate_selected_sha is not None:
            if (
                type(candidate_selected_sha) is not str
                or len(candidate_selected_sha) != 64
                or any(char not in "0123456789abcdef" for char in candidate_selected_sha)
            ):
                raise ValueError("selected_sequence_sha256 must be a lowercase 64-character SHA-256")
            explicit_selected_sequence_sha = candidate_selected_sha
    stable_run_config = _canonical_run_config_v4({
        "mode": mode, "learning_rate": learning_rate, "tbptt_steps": tbptt_steps,
        "gradient_clip_norm": gradient_clip_norm, "user": _canonical_run_config_v4(run_config),
        "action_type_weights": normalized_action_weights,
        # The materializer owns the canonical full-sequence order.  Rebuilding
        # it as train-then-validation here changes the digest when the source
        # sequence order interleaves partitions, making a valid checkpoint
        # impossible to resume through the long-run wrapper.
        "selected_objective_sha256": (
            explicit_selected_sequence_sha
            if explicit_selected_sequence_sha is not None
            else selected_objective_sha256_v4(tuple(train_sequences) + tuple(validation_sequences))
        ),
        "trainer_implementation_sha256": trainer_implementation_sha256_v4(),
    })
    started = time.monotonic()
    next_epoch = 0
    optimizer_updates_completed = 0
    cumulative_train_elapsed_seconds = 0.0
    if resume and last_checkpoint.is_file():
        resume_payload = _load_epoch_resume_v4(
            last_checkpoint, model=model, optimizer=optimizer, run_config=stable_run_config,
            sequence_order_seed=sequence_order_seed, epochs=epochs,
        )
        initial_nll = float(resume_payload["initial_validation_complete_action_nll"])
        best_nll = float(resume_payload["best_validation_complete_action_nll"])
        best_epoch = int(resume_payload["best_epoch"])
        stale_epochs = int(resume_payload["stale_epochs"])
        best_components = dict(resume_payload["best_validation_by_component"])
        history = [dict(item) for item in resume_payload["history"]]
        next_epoch = int(resume_payload["next_epoch"])
        optimizer_updates_completed = int(resume_payload.get("optimizer_updates_completed", 0))
        cumulative_train_elapsed_seconds = float(resume_payload.get("cumulative_train_elapsed_seconds", 0.0))
        best_descriptor = {"tensor_state_sha256": str(resume_payload["best_checkpoint_tensor_state_sha256"])}
        if not checkpoint.is_file():
            raise ValueError("recurrent BC resume has no best checkpoint artifact")
    else:
        initial_nll, _initial_components = _evaluate(model, validation_sequences, mode=mode)
        best_nll = math.inf
        best_epoch = -1
        stale_epochs = 0
        best_descriptor: Mapping[str, object] | None = None
        best_components: Mapping[str, float] = {}
        history: list[Mapping[str, float]] = []
    for epoch in range(next_epoch, epochs):
        telemetry: dict[str, float] = {}
        updates_before_epoch = optimizer_updates_completed

        def train_progress(payload: Mapping[str, object]) -> None:
            if not callable(train_progress_callback):
                return
            event = dict(payload)
            event["epoch"] = epoch
            event["optimizer_updates_completed"] = (
                updates_before_epoch + int(payload.get("optimizer_updates_in_epoch", 0))
            )
            train_progress_callback(event)

        train_nll = _train_epoch(
            model, _shuffled_train_sequences_v4(
                train_sequences, sequence_order_seed=sequence_order_seed, epoch=epoch,
            ), optimizer=optimizer, tbptt_steps=tbptt_steps,
            gradient_clip_norm=gradient_clip_norm, mode=mode, telemetry=telemetry,
            action_type_weights=normalized_action_weights,
            progress_callback=train_progress if callable(train_progress_callback) else None,
        )
        validation_nll, by_component = _evaluate(model, validation_sequences, mode=mode)
        epoch_updates = float(telemetry.get("optimizer_updates", len(train_sequences)))
        optimizer_updates_completed += int(epoch_updates)
        cumulative_train_elapsed_seconds += float(telemetry.get("train_elapsed_seconds", 0.0))
        history.append({
            "epoch": float(epoch), "train_complete_action_nll": train_nll,
            "validation_complete_action_nll": validation_nll,
            "optimizer_updates": epoch_updates,
            "optimizer_updates_completed": float(optimizer_updates_completed),
            "mean_preclip_gradient_norm": telemetry.get("mean_preclip_gradient_norm", 0.0),
            "train_elapsed_seconds": telemetry.get("train_elapsed_seconds", 0.0),
        })
        if validation_nll < best_nll:
            best_nll, best_epoch, stale_epochs = validation_nll, epoch, 0
            best_descriptor = save_specialist_checkpoint_v4(checkpoint, model)
            best_components = by_component
        else:
            stale_epochs += 1
        completed = epoch + 1
        if best_descriptor is None:
            raise RuntimeError("recurrent BC did not produce a validation checkpoint")
        _atomic_torch_save_v4(last_checkpoint, {
            "schema": _RESUME_SCHEMA, "run_config": stable_run_config,
            "sequence_order_seed": sequence_order_seed, "epochs": epochs,
            "next_epoch": completed, "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(), "history": [dict(row) for row in history],
            "initial_validation_complete_action_nll": initial_nll,
            "best_validation_complete_action_nll": best_nll, "best_epoch": best_epoch,
            "stale_epochs": stale_epochs, "best_validation_by_component": dict(best_components),
            "best_checkpoint_tensor_state_sha256": str(best_descriptor["tensor_state_sha256"]),
            "optimizer_updates_completed": optimizer_updates_completed,
            "cumulative_train_elapsed_seconds": cumulative_train_elapsed_seconds,
        })
        if callable(epoch_callback):
            epoch_callback({
                "epoch": epoch, "epochs_completed": completed, "epochs_requested": epochs,
                "optimizer_updates_completed": optimizer_updates_completed,
                "last_checkpoint_path": str(last_checkpoint), "history_row": dict(history[-1]),
            })
        if stale_epochs >= patience:
            break
    if best_descriptor is None or best_epoch < 0:
        raise RuntimeError("recurrent BC did not produce a validation checkpoint")
    file_sha = _file_sha256(checkpoint)
    tensor_sha = str(best_descriptor["tensor_state_sha256"])
    load_specialist_checkpoint_v4(
        checkpoint, model, expected_file_sha256=file_sha, expected_tensor_state_sha256=tensor_sha,
    )
    if any(parameter.device != model_device for parameter in model.parameters()):
        raise RuntimeError("recurrent BC checkpoint reload changed the model device")
    return RecurrentBCTrainingResultV4(
        schema=_RESULT_SCHEMA, mode=mode, promotion_authority=False, best_epoch=best_epoch,
        epochs_completed=len(history), initial_validation_complete_action_nll=initial_nll,
        best_validation_complete_action_nll=best_nll,
        validation_delta_nll=best_nll - initial_nll, improved=best_nll - initial_nll < 0.0,
        validation_by_component=dict(best_components), history=tuple(history),
        best_checkpoint_path=checkpoint, best_checkpoint_file_sha256=file_sha,
        best_checkpoint_tensor_state_sha256=tensor_sha,
        last_checkpoint_path=last_checkpoint, optimizer_updates_completed=optimizer_updates_completed,
        elapsed_seconds=cumulative_train_elapsed_seconds,
        invocation_elapsed_seconds=time.monotonic() - started,
        cumulative_train_elapsed_seconds=cumulative_train_elapsed_seconds,
    )


def _research_authority_rows(
    selection_manifest_path: Path, *, expected_selection_manifest_file_sha256: str,
) -> tuple[str, Mapping[str, object], Iterator[RecurrentRecordAuthorityRowV3], object]:
    """Open a sealed selection for bounded research, without READY quality authority.

    This intentionally uses the same source requalification functions as the
    formal streamer, but does not claim its full-corpus completion guarantee.
    """
    from mage_ptcg.meta_specialist import recurrent_dataset_v3 as source

    if _file_sha256(selection_manifest_path) != expected_selection_manifest_file_sha256:
        raise ValueError("research selection manifest file SHA-256 does not match")
    manifest, root, snapshot, permission, trusted, vocabulary = source._generic_record_authorities_v3(
        selection_manifest_path, expected_manifest_file_sha256=expected_selection_manifest_file_sha256,
    )
    index = selection_manifest_path.parent.resolve() / manifest["selection_index_path"]
    entries = source._frozen_index_entries_v3(index, expected_sha=manifest["selection_index_sha256"])

    def rows() -> Iterator[RecurrentRecordAuthorityRowV3]:
        for record, model_payload, physical in source._iter_requalified_records(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=manifest["qualification_time_utc"], vocabulary=vocabulary,
        ):
            try:
                entry = next(entries)
            except StopIteration as exc:
                raise ValueError("research selection index is missing a qualified row") from exc
            if any(entry[field] != physical[field] for field in physical):
                raise ValueError("research selection index raw line changed")
            yield RecurrentRecordAuthorityRowV3(
                record=record, model_payload=model_payload, shard=str(physical["shard"]),
                line=int(physical["line"]), record_id=str(physical["record_id"]),
                content_hash=str(physical["content_hash"]), raw_line_sha256=str(physical["raw_line_sha256"]),
                component_id=str(entry["component_id"]), partition=str(entry["partition"]),
            )
    return str(manifest["lane"]), manifest["split"], rows(), vocabulary


def materialize_research_uniform_subset_v4(
    selection_manifest_path: str | Path, *, expected_selection_manifest_file_sha256: str,
    max_records: int = 32, subset_fraction: float = 0.05, burn_in: int = 1,
    mode: str = RESEARCH_ONLY_UNIFORM_WEIGHT,
) -> ResearchSubsetV4:
    """Materialize complete episodes only, capped for CPU research smoke runs."""
    _require_research_mode(mode)
    if type(max_records) is not int or max_records < 4 or not 0.0 < subset_fraction <= 0.1 or type(burn_in) is not int or burn_in < 0:
        raise ValueError("research subset must be bounded to 1--10% and at least four records")
    path = Path(selection_manifest_path)
    lane, split, rows, vocabulary = _research_authority_rows(
        path, expected_selection_manifest_file_sha256=expected_selection_manifest_file_sha256,
    )
    counts = split.get("counts") if isinstance(split, Mapping) else None
    if not isinstance(counts, Mapping) or set(counts) != {"train", "validation"}:
        raise ValueError("research selection split is invalid")
    requested = target_records_by_partition_v4(
        counts, max_records=max_records, subset_fraction=subset_fraction,
    )
    recognized = getattr(vocabulary, "recognized_card_ids", None)
    if (
        type(recognized) is not frozenset or not recognized
        or any(type(card_id) is not int or card_id < 1 for card_id in recognized)
    ):
        raise ValueError("research source vocabulary is invalid")
    vocabulary_size = max(recognized)
    selected: list[RecurrentBCSequenceV4] = []
    actual = {"train": 0, "validation": 0}
    current_rows: list[RecurrentRecordAuthorityRowV3] = []
    current_episode: str | None = None

    def close_episode() -> None:
        nonlocal current_rows
        if not current_rows:
            return
        partition = current_rows[0].partition
        if (
            actual[partition] >= requested[partition]
            or sum(actual.values()) + len(current_rows) > max_records
        ):
            current_rows = []
            return
        draft_steps = []
        for record_index, row in enumerate(current_rows):
            draft_steps.extend(_project_record_steps_v4(
                row, vocabulary=vocabulary, episode_start=record_index == 0,
            ))
        steps = tuple(RecurrentBCStepV4(
            state=step.state, target_index=step.target_index, episode_group=step.episode_group,
            quality_weight=1.0, model_input=step.model_input, step_input=step.step_input,
            target_masses=step.target_masses, reach_mass=step.reach_mass,
            episode_start=step.episode_start,
            component_id=step.component_id, partition=step.partition,
            record_id=step.record_id, content_hash=step.content_hash, research_only=True,
            supervision_weight=float(getattr(step, "supervision_weight", 1.0)),
        ) for step in draft_steps)
        selected.append(RecurrentBCSequenceV4(
            lane, str(current_rows[0].record["episode_id_hash"]), current_rows[0].component_id,
            partition, steps, burn_in=burn_in, research_only=True,
        ))
        actual[partition] += len(current_rows)
        current_rows = []

    for row in rows:
        episode = row.record.get("episode_id_hash")
        if type(episode) is not str or not episode:
            raise ValueError("research record episode is invalid")
        if current_episode is None:
            current_episode = episode
        elif episode != current_episode:
            close_episode()
            if actual["train"] >= requested["train"] and actual["validation"] >= requested["validation"]:
                break
            current_episode = episode
        if current_rows and (row.partition != current_rows[0].partition or row.component_id != current_rows[0].component_id):
            raise ValueError("research source crosses a split component inside an episode")
        current_rows.append(row)
    else:
        close_episode()
    if not actual["train"] or not actual["validation"]:
        raise ValueError("research bounded source did not contain both split partitions")
    return ResearchSubsetV4(
        lane=lane, selection_manifest_path=path, selection_manifest_file_sha256=expected_selection_manifest_file_sha256,
        sequences=tuple(selected), records_by_partition=dict(actual), target_records_by_partition=requested,
        card_vocabulary_size=vocabulary_size, card_vocabulary_card_id_count=len(recognized),
    )


def materialize_fast_research_uniform_subset_v4(
    selection_manifest_path: str | Path, *, expected_selection_manifest_file_sha256: str,
    max_records: int = 1024, subset_fraction: float = 0.05, burn_in: int = 1,
    episodes_per_partition: int = SHORT_PILOT_MIN_EPISODES_PER_PARTITION,
    components_per_partition: int | None = None,
    train_episodes_per_partition: int | None = None,
    validation_episodes_per_partition: int | None = None,
    train_components_per_partition: int | None = None,
    validation_components_per_partition: int | None = None,
    require_positive_stop: bool = False,
    mode: str = RESEARCH_ONLY_UNIFORM_WEIGHT,
) -> ResearchSubsetV4:
    """Bounded research reader that verifies only index-selected source shards.

    The formal reader remains the full-snapshot authority.  This explicit
    research reader pins the manifest and complete selection sidecar, then
    verifies each source shard it actually touches through one ``O_NOFOLLOW``
    descriptor copied to a private spool before any indexed row is consumed.
    """
    from mage_ptcg.meta_specialist import recurrent_dataset_v3 as source
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        MAX_LOCAL_RECORD_BYTES_V2,
        parse_canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import (
        _load_production_vocabulary_v3,
        _production_vocabulary_identity_v3,
    )

    _require_research_mode(mode)
    if components_per_partition is None:
        components_per_partition = episodes_per_partition
    episode_targets = {
        "train": episodes_per_partition if train_episodes_per_partition is None else train_episodes_per_partition,
        "validation": episodes_per_partition if validation_episodes_per_partition is None else validation_episodes_per_partition,
    }
    component_targets = {
        "train": components_per_partition if train_components_per_partition is None else train_components_per_partition,
        "validation": components_per_partition if validation_components_per_partition is None else validation_components_per_partition,
    }
    if type(max_records) is not int or max_records < 4 or not 0.0 < subset_fraction <= 0.1 or type(burn_in) is not int or burn_in < 0:
        raise ValueError("fast research subset must be bounded to 1--10% and at least four records")
    if (
        any(type(episode_targets[key]) is not int or not 4 <= episode_targets[key] <= 512 for key in episode_targets)
        or any(type(component_targets[key]) is not int or not 4 <= component_targets[key] <= episode_targets[key] for key in component_targets)
    ):
        raise ValueError("fast research episode/component targets must be in 4..512")
    if type(require_positive_stop) is not bool:
        raise ValueError("fast research require_positive_stop must be bool")
    manifest_path = Path(selection_manifest_path)
    manifest_raw = source._regular_file_bytes_v3(
        manifest_path, expected_sha256=expected_selection_manifest_file_sha256,
        name="fast research selection manifest",
    )
    manifest = source._read_root_manifest(manifest_path, raw=manifest_raw, validate_sidecar=False)
    root = source._strict_root(manifest["root"])
    snapshot, snapshot_path, teacher_path, permission, _permission_bytes, trusted = source._load_authorities(root)
    source._regular_file_sha256_v3(
        snapshot_path, expected_sha256=manifest["snapshot_index_sha256"], name="fast research snapshot index",
    )
    source._regular_file_sha256_v3(
        teacher_path, expected_sha256=manifest["teacher_manifest_sha256"], name="fast research teacher manifest",
    )
    vocabulary = _load_production_vocabulary_v3()
    if manifest["vocabulary"] != _production_vocabulary_identity_v3():
        raise ValueError("fast research production vocabulary identity changed")
    recognized = getattr(vocabulary, "recognized_card_ids", None)
    if type(recognized) is not frozenset or not recognized:
        raise ValueError("fast research source vocabulary is invalid")
    counts = manifest["split"]["counts"]
    requested = target_records_by_partition_v4(
        counts, max_records=max_records, subset_fraction=subset_fraction,
    )
    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise ValueError("fast research snapshot lacks dataset chunks")
    shard_hashes: dict[str, str] = {}
    for chunk in chunks:
        if type(chunk) is not dict:
            raise ValueError("fast research snapshot chunk is invalid")
        shard = source._snapshot_chunk_shard_path(root, chunk.get("path"))
        expected_sha = source._require_digest(chunk.get("dataset_snapshot_sha256"), field="fast research shard SHA-256")
        if shard.name in shard_hashes:
            raise ValueError("fast research snapshot repeats a shard")
        shard_hashes[shard.name] = expected_sha
    index = manifest_path.parent.resolve() / manifest["selection_index_path"]
    index_rows = source._frozen_index_entries_v3(index, expected_sha=manifest["selection_index_sha256"])

    current_shard: str | None = None
    current_spool: tempfile._TemporaryFileWrapper[bytes] | None = None
    current_line = 0
    current_descriptor_identity: tuple[int, int, int, int, int, int] | None = None

    def close_shard() -> None:
        nonlocal current_spool, current_shard, current_line, current_descriptor_identity
        if current_spool is not None:
            current_spool.close()
        current_spool = None
        current_shard = None
        current_line = 0
        current_descriptor_identity = None

    def open_shard(shard: str) -> None:
        nonlocal current_spool, current_shard, current_line, current_descriptor_identity
        if shard == current_shard:
            return
        close_shard()
        expected_sha = shard_hashes.get(shard)
        if expected_sha is None:
            raise ValueError("fast research index references an undeclared shard")
        path = source._strict_shard_path(root, shard)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError("fast research shard cannot be opened without following a symlink") from exc
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("fast research shard is not a regular file")
            spool = tempfile.TemporaryFile(mode="w+b", prefix="fast-recurrent-v4-")
            digest = hashlib.sha256()
            try:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                    spool.write(block)
                after = os.fstat(handle.fileno())
                if source._descriptor_identity_v3(before) != source._descriptor_identity_v3(after):
                    raise ValueError("fast research shard changed during private spool")
                if digest.hexdigest() != expected_sha:
                    raise ValueError("fast research shard SHA-256 does not match snapshot")
                spool.seek(0)
            except BaseException:
                spool.close()
                raise
        current_shard = shard
        current_spool = spool
        current_line = 0
        current_descriptor_identity = source._descriptor_identity_v3(before)

    def raw_at(entry: Mapping[str, object]) -> bytes:
        nonlocal current_line
        shard = entry["shard"]
        line = entry["line"]
        if type(shard) is not str or type(line) is not int or line < 1:
            raise ValueError("fast research index physical locator is invalid")
        open_shard(shard)
        assert current_spool is not None
        if line <= current_line:
            raise ValueError("fast research index physical order is not strictly increasing")
        while current_line < line:
            raw = current_spool.readline(MAX_LOCAL_RECORD_BYTES_V2 + 2)
            if not raw:
                raise ValueError("fast research index line exceeds verified shard EOF")
            current_line += 1
        if not raw.endswith(b"\n") or raw == b"\n" or len(raw) > MAX_LOCAL_RECORD_BYTES_V2 + 1:
            raise ValueError("fast research indexed shard line is invalid")
        return raw

    selected: list[RecurrentBCSequenceV4] = []
    actual = {"train": 0, "validation": 0}
    episodes = {"train": 0, "validation": 0}
    components: dict[str, set[str]] = {"train": set(), "validation": set()}
    positive_stop_rows = {"train": 0, "validation": 0}
    current_rows: list[RecurrentRecordAuthorityRowV3] = []
    current_episode: str | None = None

    def has_short_pilot_coverage() -> bool:
        return (
            episodes["train"] >= episode_targets["train"]
            and episodes["validation"] >= episode_targets["validation"]
            and len(components["train"]) >= component_targets["train"]
            and len(components["validation"]) >= component_targets["validation"]
            and (not require_positive_stop or (
                positive_stop_rows["train"] > 0 and positive_stop_rows["validation"] > 0
            ))
        )

    def close_episode() -> bool:
        nonlocal current_rows
        if not current_rows:
            return False
        partition = current_rows[0].partition
        component_id = current_rows[0].component_id
        if not _should_select_fast_episode_v4(
            partition, component_id, episodes=episodes, components=components,
            positive_stop_rows=positive_stop_rows, require_positive_stop=require_positive_stop,
            episode_targets=episode_targets, component_targets=component_targets,
        ):
            current_rows = []
            return has_short_pilot_coverage()
        if sum(actual.values()) + len(current_rows) > max_records:
            current_rows = []
            raise ValueError(
                f"fast research complete episode cannot fit cap while {partition} remains underfilled"
            )
        draft_steps = []
        for record_index, row in enumerate(current_rows):
            draft_steps.extend(_project_record_steps_v4(row, vocabulary=vocabulary, episode_start=record_index == 0))
        steps = tuple(RecurrentBCStepV4(
            state=step.state, target_index=step.target_index, episode_group=step.episode_group,
            quality_weight=1.0, model_input=step.model_input, step_input=step.step_input,
            target_masses=step.target_masses, reach_mass=step.reach_mass,
            episode_start=step.episode_start,
            component_id=step.component_id, partition=step.partition,
            record_id=step.record_id, content_hash=step.content_hash, research_only=True,
            supervision_weight=float(getattr(step, "supervision_weight", 1.0)),
        ) for step in draft_steps)
        selected.append(RecurrentBCSequenceV4(
            str(manifest["lane"]), str(current_rows[0].record["episode_id_hash"]),
            component_id, partition,
            steps,
            burn_in=burn_in, research_only=True,
        ))
        actual[partition] += len(current_rows)
        episodes[partition] += 1
        components[partition].add(component_id)
        positive_stop_rows[partition] += sum(
            bool(getattr(step.step_input, "stop_available", False)) and step.target_masses[-1] > 0.0
            for step in steps
        )
        current_rows = []
        return has_short_pilot_coverage()

    try:
        for entry in index_rows:
            entry_partition = entry.get("partition")
            if entry_partition not in {"train", "validation"}:
                raise ValueError("fast research index partition is invalid")
            # Once a partition has its exact four stratified episodes, its
            # later physical rows cannot improve the bounded pilot.  Retain
            # sidecar integrity, but avoid re-parsing/re-qualifying the flood.
            if (
                episodes[entry_partition] >= episode_targets[entry_partition]
                and (not require_positive_stop or positive_stop_rows[entry_partition] > 0)
            ):
                if current_rows and current_rows[0].partition != entry_partition:
                    if close_episode():
                        break
                continue
            raw = raw_at(entry)
            try:
                record = parse_canonical_json_bytes_v2(raw[:-1])
                model_payload, _labels = require_qualified_training_record_v2(
                    record, vocabulary=vocabulary, trusted_permissions=trusted,
                    qualification_time_utc=manifest["qualification_time_utc"],
                )
            except Exception as exc:
                raise ValueError("fast research indexed record is no longer qualified") from exc
            if (
                record.get("record_id") != entry["record_id"] or record.get("content_hash") != entry["content_hash"]
                or hashlib.sha256(raw).hexdigest() != entry["raw_line_sha256"]
            ):
                raise ValueError("fast research indexed record identity does not match the sealed selection")
            source_info = record.get("source")
            if (
                type(source_info) is not dict or source_info.get("artifact_sha256") != permission.get("artifact_sha256")
                or source_info.get("permission_manifest_id") != permission.get("permission_manifest_id")
            ):
                raise ValueError("fast research indexed record permission does not match sealed authority")
            episode = record.get("episode_id_hash")
            if type(episode) is not str or not episode:
                raise ValueError("fast research indexed episode is invalid")
            if current_episode is None:
                current_episode = episode
            elif episode != current_episode:
                if close_episode():
                    break
                current_episode = episode
            row = RecurrentRecordAuthorityRowV3(
                record=record, model_payload=model_payload, shard=str(entry["shard"]), line=int(entry["line"]),
                record_id=str(entry["record_id"]), content_hash=str(entry["content_hash"]),
                raw_line_sha256=str(entry["raw_line_sha256"]), component_id=str(entry["component_id"]),
                partition=str(entry["partition"]),
            )
            if current_rows and (row.partition != current_rows[0].partition or row.component_id != current_rows[0].component_id):
                raise ValueError("fast research source crosses a split component inside an episode")
            current_rows.append(row)
        else:
            close_episode()
    finally:
        close_shard()
        close = getattr(index_rows, "close", None)
        if callable(close):
            close()
    if not actual["train"] or not actual["validation"]:
        raise ValueError("fast research bounded source did not contain both split partitions")
    if not has_short_pilot_coverage():
        component_counts = {key: len(value) for key, value in components.items()}
        raise ValueError(
            "fast research complete-episode coverage is below the short-pilot minimum: "
            f"records={actual}, episodes={episodes}, components={component_counts}, "
            f"positive_stop_rows={positive_stop_rows}"
        )
    train_components = {item.component_id for item in selected if item.partition == "train"}
    validation_components = {item.component_id for item in selected if item.partition == "validation"}
    if train_components & validation_components:
        raise ValueError("fast research selected split component overlap is nonzero")
    return ResearchSubsetV4(
        lane=str(manifest["lane"]), selection_manifest_path=manifest_path,
        selection_manifest_file_sha256=expected_selection_manifest_file_sha256,
        sequences=tuple(selected), records_by_partition=dict(actual), target_records_by_partition=requested,
        card_vocabulary_size=max(recognized), card_vocabulary_card_id_count=len(recognized),
        episodes_per_partition=episodes_per_partition, components_per_partition=components_per_partition,
        require_positive_stop=require_positive_stop,
        train_episodes_per_partition=episode_targets["train"],
        validation_episodes_per_partition=episode_targets["validation"],
        train_components_per_partition=component_targets["train"],
        validation_components_per_partition=component_targets["validation"],
    )


__all__ = [
    "RESEARCH_ONLY_UNIFORM_WEIGHT", "RESEARCH_ONLY_OUTCOME_WEIGHTED_V4",
    "SHORT_PILOT_MAJOR_REGRESSION_NLL",
    "SHORT_PILOT_MIN_MEAN_DELTA_NLL", "SHORT_PILOT_MIN_EPISODES_PER_PARTITION",
    "SHORT_PILOT_MIN_COMPONENTS_PER_PARTITION", "ResearchSubsetV4",
    "RecurrentBCTrainingResultV4", "materialize_fast_research_uniform_subset_v4",
    "materialize_research_uniform_subset_v4",
    "positive_stop_target_metrics_v4", "short_pilot_selection_status_v4",
    "target_records_by_partition_v4", "train_recurrent_bc_v4",
    "trainer_implementation_sha256_v4",
    "selected_objective_sha256_v4",
]
