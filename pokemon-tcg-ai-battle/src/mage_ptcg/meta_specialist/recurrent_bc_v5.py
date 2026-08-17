"""研究専用 V5 SetContext recurrent behavior cloning.

V5 deliberately owns a separate trainer entry point.  The sequence schema and
objective are the sealed V4 ones, but every recurrent forward is dispatched to
``SpecialistModelV5.forward_record_group_v5`` and checkpoints are written by
the strict V5 loader.  This keeps the V4 trainer's exact-type contract and
checkpoint format untouched while making the V4 transfer provenance explicit
in both the run resume record and the V5 checkpoint descriptor.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import torch
from torch.nn import functional as F

from mage_ptcg.meta_specialist.neural_model_v5 import (
    SpecialistModelV5,
    load_specialist_checkpoint_v5,
    save_specialist_checkpoint_v5,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (
    RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    RecurrentBCSequenceV4,
    RecurrentBCStepV4,
    _normalized_action_type_weights_v4,
    _record_groups,
    _require_research_mode,
    _shuffled_train_sequences_v4,
    _step_action_type_v4,
    _validate_sequences,
    selected_objective_sha256_v4,
)
from mage_ptcg.meta_specialist.neural_model_v4 import CHECKPOINT_SCHEMA_V4


_RESULT_SCHEMA_V5 = "meta-specialist-recurrent-bc-v5-set-context-research"
_RESUME_SCHEMA_V5 = "meta-specialist-recurrent-bc-v5-set-context-epoch-resume-v1"


def _file_sha256_v5(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_run_config_v5(value: Mapping[str, object] | None) -> dict[str, object]:
    raw = {} if value is None else dict(value)
    try:
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("v5 recurrent BC run_config must be JSON-stable") from exc
    if not isinstance(decoded, dict):
        raise ValueError("v5 recurrent BC run_config must be a JSON object")
    return decoded


def _validate_base_provenance_v5(value: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("base_provenance is required for v5 recurrent BC")
    provenance = dict(value)
    expected = {"path", "file_sha256", "tensor_state_sha256", "checkpoint_schema"}
    if set(provenance) != expected:
        raise ValueError("base_provenance must contain path, file_sha256, tensor_state_sha256, checkpoint_schema")
    if type(provenance["path"]) is not str or not provenance["path"]:
        raise ValueError("base_provenance path is invalid")
    for name in ("file_sha256", "tensor_state_sha256"):
        digest = provenance[name]
        if type(digest) is not str or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"base_provenance {name} must be a lowercase SHA-256")
    if provenance["checkpoint_schema"] != CHECKPOINT_SCHEMA_V4:
        raise ValueError("base_provenance checkpoint_schema must be V4")
    return provenance


@dataclass(frozen=True, slots=True)
class RecurrentBCTrainingResultV5:
    """Closed result of one non-promotable V5 recurrent BC run."""

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
    last_checkpoint_path: Path | None
    optimizer_updates_completed: int
    elapsed_seconds: float
    invocation_elapsed_seconds: float
    cumulative_train_elapsed_seconds: float


def _complete_action_nll_v5(
    model: SpecialistModelV5, step: RecurrentBCStepV4, output: object,
) -> torch.Tensor:
    """Compute the V4 complete-action objective from V5's V4-shaped output.

    ``PolicyOutputV4.global_token`` returned by V5 is intentionally the base
    global token, so this expression preserves the V4 STOP semantics exactly.
    """
    logits = output.logits  # type: ignore[union-attr]
    if bool(getattr(step.step_input, "stop_available", False)):
        stop = model.stop_vector @ output.global_token + model.stop_bias  # type: ignore[union-attr]
        logits = torch.cat((logits, stop.reshape(1)))
    if logits.numel() != len(step.target_masses):
        raise ValueError("semantic/STOP target does not match the complete legal action domain")
    masses = torch.tensor(step.target_masses, dtype=logits.dtype, device=logits.device)
    return -(masses * F.log_softmax(logits, dim=0)).sum()


def _evaluate_v5(
    model: SpecialistModelV5, sequences: Sequence[RecurrentBCSequenceV4], *, mode: str,
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
                outputs = model.forward_record_group_v5(
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
                        nll = _complete_action_nll_v5(model, step, output)
                        step_weight = step.supervision_weight * step.reach_mass
                        record_nll += step_weight * float(nll.item())
                        has_supervised_row = True
                    if has_supervised_row:
                        weight = group[0].quality_weight
                        totals[sequence.component_id][0] += weight * record_nll
                        totals[sequence.component_id][1] += weight
                        total_nll += weight * record_nll
                        total_weight += weight
                hidden = next_hidden.detach() if next_hidden is not None else None
    model.train(previous_training)
    if total_weight <= 0.0:
        raise ValueError("validation contains no post-burn-in complete actions")
    by_component = {
        component: numerator / denominator
        for component, (numerator, denominator) in sorted(totals.items())
        if denominator > 0.0
    }
    return total_nll / total_weight, by_component


def _train_epoch_v5(
    model: SpecialistModelV5, sequences: Sequence[RecurrentBCSequenceV4], *,
    optimizer: torch.optim.Optimizer, tbptt_steps: int, gradient_clip_norm: float,
    mode: str, telemetry: dict[str, float] | None = None,
    action_type_weights: Mapping[str, float] | None = None,
    progress_callback: object | None = None,
) -> float:
    """Run one V5 epoch using the exact V4 sequence/group objective."""
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
                # Keep the corrected V4 outcome semantics: q is in the
                # numerator but not the sequence normalization denominator.
                sequence_weight = math.fsum(
                    1.0 for group in groups[sequence.burn_in:]
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
                    outputs = model.forward_record_group_v5(
                        tuple(step.state for step in group),
                        hidden_state=hidden, episode_start=group[0].episode_start,
                    )
                hidden = outputs[0].hidden_state
                if hidden is not None:
                    hidden = hidden.detach()
                continue
            outputs = model.forward_record_group_v5(
                tuple(step.state for step in group),
                hidden_state=hidden, episode_start=group[0].episode_start,
            )
            hidden = outputs[0].hidden_state
            for step, output in zip(group, outputs, strict=True):
                if normalized_action_weights is not None and len(step.target_masses) <= 1:
                    continue
                if step.supervision_weight <= 0.0:
                    continue
                action_weight = (
                    normalized_action_weights.get(_step_action_type_v4(step), 1.0)
                    if normalized_action_weights is not None else 1.0
                )
                weight = step.quality_weight * step.supervision_weight * step.reach_mass * action_weight
                weighted = _complete_action_nll_v5(model, step, output) * weight
                normalized = weighted / sequence_weight
                chunk_loss = normalized if chunk_loss is None else chunk_loss + normalized
                total_nll += float(weighted.detach().item())
                if normalized_action_weights is not None:
                    total_weight += weight
            if normalized_action_weights is None and any(step.supervision_weight > 0.0 for step in group):
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
                "partial_train_complete_action_nll": total_nll / total_weight if total_weight > 0.0 else None,
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


def _atomic_torch_save_v5(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        import os

        os.close(descriptor)
        torch.save(dict(payload), temporary)
        with open(temporary, "rb") as handle:
            import os

            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_epoch_resume_v5(
    path: Path, *, model: SpecialistModelV5, optimizer: torch.optim.Optimizer,
    run_config: Mapping[str, object], sequence_order_seed: int, epochs: int,
) -> dict[str, object]:
    try:
        payload = torch.load(path, map_location=next(model.parameters()).device, weights_only=False)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("v5 recurrent BC resume checkpoint is unreadable") from exc
    if type(payload) is not dict or payload.get("schema") != _RESUME_SCHEMA_V5:
        raise ValueError("v5 recurrent BC resume checkpoint schema is invalid")
    if (
        payload.get("run_config") != dict(run_config)
        or payload.get("sequence_order_seed") != sequence_order_seed
        or payload.get("epochs") != epochs
    ):
        raise ValueError("v5 recurrent BC resume checkpoint configuration differs")
    next_epoch = payload.get("next_epoch")
    history = payload.get("history")
    if type(next_epoch) is not int or not 0 <= next_epoch <= epochs or not isinstance(history, list) or len(history) != next_epoch:
        raise ValueError("v5 recurrent BC resume checkpoint epoch history is invalid")
    try:
        model.load_state_dict(payload["model_state"], strict=True)
        optimizer.load_state_dict(payload["optimizer_state"])
    except (KeyError, RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("v5 recurrent BC resume checkpoint state is invalid") from exc
    return payload


def train_recurrent_bc_v5(
    model: SpecialistModelV5,
    train_sequences: Sequence[RecurrentBCSequenceV4],
    validation_sequences: Sequence[RecurrentBCSequenceV4], *,
    mode: str,
    output_dir: str | Path,
    sequence_order_seed: int,
    base_provenance: Mapping[str, object] | None,
    epochs: int = 3,
    patience: int = 1,
    learning_rate: float = 1e-3,
    tbptt_steps: int = 8,
    gradient_clip_norm: float = 1.0,
    action_type_weights: Mapping[str, float] | None = None,
    run_config: Mapping[str, object] | None = None,
    resume: bool = False,
    epoch_callback: object | None = None,
    train_progress_callback: object | None = None,
) -> RecurrentBCTrainingResultV5:
    """Train one bounded V5 sidecar run without touching V4 artifacts."""
    _require_research_mode(mode)
    normalized_action_weights = _normalized_action_type_weights_v4(action_type_weights)
    provenance = _validate_base_provenance_v5(base_provenance)
    if (
        type(model) is not SpecialistModelV5 or type(epochs) is not int or epochs < 1
        or type(patience) is not int or patience < 0 or learning_rate <= 0.0
        or type(tbptt_steps) is not int or tbptt_steps < 1 or gradient_clip_norm <= 0.0
        or type(sequence_order_seed) is not int or type(resume) is not bool
    ):
        raise ValueError("v5 recurrent BC configuration is invalid")
    _validate_sequences(train_sequences, partition="train", mode=mode)
    _validate_sequences(validation_sequences, partition="validation", mode=mode)
    train_components = {sequence.component_id for sequence in train_sequences}
    valid_components = {sequence.component_id for sequence in validation_sequences}
    if train_components & valid_components:
        raise ValueError("component split leaks between train and validation")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    checkpoint = destination / "best-recurrent-bc-v5.pt"
    last_checkpoint = destination / "last-recurrent-bc-v5.pt"
    explicit_selected_sequence_sha = None
    if isinstance(run_config, Mapping) and run_config.get("selected_sequence_sha256") is not None:
        candidate_selected_sha = run_config.get("selected_sequence_sha256")
        if (
            type(candidate_selected_sha) is not str
            or len(candidate_selected_sha) != 64
            or any(char not in "0123456789abcdef" for char in candidate_selected_sha)
        ):
            raise ValueError("selected_sequence_sha256 must be a lowercase 64-character SHA-256")
        explicit_selected_sequence_sha = candidate_selected_sha
    stable_run_config = _canonical_run_config_v5({
        "mode": mode,
        "learning_rate": learning_rate,
        "tbptt_steps": tbptt_steps,
        "gradient_clip_norm": gradient_clip_norm,
        "action_type_weights": normalized_action_weights,
        "v4_base_provenance": provenance,
        "user": _canonical_run_config_v5(run_config),
        "selected_objective_sha256": (
            explicit_selected_sequence_sha
            if explicit_selected_sequence_sha is not None
            else selected_objective_sha256_v4(tuple(train_sequences) + tuple(validation_sequences))
        ),
        "trainer": "recurrent_bc_v5",
    })
    started = time.monotonic()
    model_device = next(model.parameters()).device
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    next_epoch = 0
    optimizer_updates_completed = 0
    cumulative_train_elapsed_seconds = 0.0
    if resume and last_checkpoint.is_file():
        resume_payload = _load_epoch_resume_v5(
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
        best_descriptor: Mapping[str, object] | None = {
            "tensor_state_sha256": str(resume_payload["best_checkpoint_tensor_state_sha256"]),
        }
        if not checkpoint.is_file():
            raise ValueError("v5 recurrent BC resume has no best checkpoint artifact")
    else:
        initial_nll, _ = _evaluate_v5(model, validation_sequences, mode=mode)
        best_nll = math.inf
        best_epoch = -1
        stale_epochs = 0
        best_components: Mapping[str, float] = {}
        best_descriptor = None
        history: list[Mapping[str, float]] = []
    for epoch in range(next_epoch, epochs):
        telemetry: dict[str, float] = {}
        updates_before_epoch = optimizer_updates_completed

        def train_progress(payload: Mapping[str, object]) -> None:
            if not callable(train_progress_callback):
                return
            event = dict(payload)
            event["epoch"] = epoch
            event["optimizer_updates_completed"] = updates_before_epoch + int(
                payload.get("optimizer_updates_in_epoch", 0)
            )
            train_progress_callback(event)

        train_nll = _train_epoch_v5(
            model,
            _shuffled_train_sequences_v4(train_sequences, sequence_order_seed=sequence_order_seed, epoch=epoch),
            optimizer=optimizer,
            tbptt_steps=tbptt_steps,
            gradient_clip_norm=gradient_clip_norm,
            mode=mode,
            telemetry=telemetry,
            action_type_weights=normalized_action_weights,
            progress_callback=train_progress if callable(train_progress_callback) else None,
        )
        validation_nll, by_component = _evaluate_v5(model, validation_sequences, mode=mode)
        epoch_updates = float(telemetry.get("optimizer_updates", len(train_sequences)))
        optimizer_updates_completed += int(epoch_updates)
        cumulative_train_elapsed_seconds += float(telemetry.get("train_elapsed_seconds", 0.0))
        history.append({
            "epoch": float(epoch),
            "train_complete_action_nll": train_nll,
            "validation_complete_action_nll": validation_nll,
            "optimizer_updates": epoch_updates,
            "optimizer_updates_completed": float(optimizer_updates_completed),
            "mean_preclip_gradient_norm": telemetry.get("mean_preclip_gradient_norm", 0.0),
            "train_elapsed_seconds": telemetry.get("train_elapsed_seconds", 0.0),
        })
        if validation_nll < best_nll:
            best_nll, best_epoch, stale_epochs = validation_nll, epoch, 0
            best_descriptor = save_specialist_checkpoint_v5(
                checkpoint, model, base_provenance=provenance,
            )
            best_components = by_component
        else:
            stale_epochs += 1
        if best_descriptor is None:
            raise RuntimeError("v5 recurrent BC did not produce a validation checkpoint")
        completed = epoch + 1
        _atomic_torch_save_v5(last_checkpoint, {
            "schema": _RESUME_SCHEMA_V5,
            "run_config": stable_run_config,
            "sequence_order_seed": sequence_order_seed,
            "epochs": epochs,
            "next_epoch": completed,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "history": [dict(row) for row in history],
            "initial_validation_complete_action_nll": initial_nll,
            "best_validation_complete_action_nll": best_nll,
            "best_epoch": best_epoch,
            "stale_epochs": stale_epochs,
            "best_validation_by_component": dict(best_components),
            "best_checkpoint_tensor_state_sha256": str(best_descriptor["tensor_state_sha256"]),
            "optimizer_updates_completed": optimizer_updates_completed,
            "cumulative_train_elapsed_seconds": cumulative_train_elapsed_seconds,
        })
        if callable(epoch_callback):
            epoch_callback({
                "epoch": epoch,
                "epochs_completed": completed,
                "epochs_requested": epochs,
                "optimizer_updates_completed": optimizer_updates_completed,
                "last_checkpoint_path": str(last_checkpoint),
                "history_row": dict(history[-1]),
            })
        if stale_epochs >= patience:
            break
    if best_descriptor is None or best_epoch < 0:
        raise RuntimeError("v5 recurrent BC did not produce a validation checkpoint")
    file_sha = _file_sha256_v5(checkpoint)
    tensor_sha = str(best_descriptor["tensor_state_sha256"])
    load_specialist_checkpoint_v5(
        checkpoint,
        model,
        expected_file_sha256=file_sha,
        expected_tensor_state_sha256=tensor_sha,
    )
    if any(parameter.device != model_device for parameter in model.parameters()):
        raise RuntimeError("v5 recurrent BC checkpoint reload changed the model device")
    return RecurrentBCTrainingResultV5(
        schema=_RESULT_SCHEMA_V5,
        mode=mode,
        promotion_authority=False,
        best_epoch=best_epoch,
        epochs_completed=len(history),
        initial_validation_complete_action_nll=initial_nll,
        best_validation_complete_action_nll=best_nll,
        validation_delta_nll=best_nll - initial_nll,
        improved=best_nll - initial_nll < 0.0,
        validation_by_component=dict(best_components),
        history=tuple(history),
        best_checkpoint_path=checkpoint,
        best_checkpoint_file_sha256=file_sha,
        best_checkpoint_tensor_state_sha256=tensor_sha,
        last_checkpoint_path=last_checkpoint,
        optimizer_updates_completed=optimizer_updates_completed,
        elapsed_seconds=cumulative_train_elapsed_seconds,
        invocation_elapsed_seconds=time.monotonic() - started,
        cumulative_train_elapsed_seconds=cumulative_train_elapsed_seconds,
    )


__all__ = [
    "RecurrentBCTrainingResultV5",
    "_complete_action_nll_v5",
    "_evaluate_v5",
    "_train_epoch_v5",
    "train_recurrent_bc_v5",
]
