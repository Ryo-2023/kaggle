"""Research-only residual trainer over frozen Wave6 recurrent checkpoints.

This is intentionally a sidecar trainer, not a replacement for the V4
trainer.  The base :class:`SpecialistModelV4` is loaded from a hash-bound
provenance record, switched to eval mode, and kept under ``torch.no_grad``.
Only :class:`FrozenResidualSidecarV1` parameters are passed to the optimizer.
No production actor, trainer, evaluator, or CABT module is imported here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

import torch
from torch import Tensor

from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightError,
    SeedKnownDomainManifestV1,
    Wave6ProvenanceV1,
    _canonical,
    _sha,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualError,
    FrozenResidualSidecarV1,
    build_residual_context_v1,
    frozen_residual_loss_v1,
)
from mage_ptcg.meta_specialist.neural_model_v4 import (
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import (
    RecurrentBCSequenceV4,
    RecurrentBCStepV4,
)


class FrozenResidualTrainerError(ValueError):
    """Raised when a sidecar-only training contract is not closed."""


TARGET_KIND_SELF_IMITATION_V1 = "self_imitation_rule_relabel_v1"
TARGET_KIND_SIGNED_BEHAVIOR_V1 = "signed_behavior_log_probability"
_TARGET_KINDS_V1 = frozenset({TARGET_KIND_SELF_IMITATION_V1, TARGET_KIND_SIGNED_BEHAVIOR_V1})


def _finite_positive(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or float(value) <= 0.0:
        raise FrozenResidualTrainerError(f"{field} must be a finite positive number")
    return float(value)


def load_wave6_base_from_provenance_v1(
    provenance: Wave6ProvenanceV1,
    *,
    device: str | torch.device = "cpu",
) -> SpecialistModelV4:
    """Strict-load one closed Wave6 checkpoint and freeze every base parameter."""
    if type(provenance) is not Wave6ProvenanceV1:
        raise FrozenResidualTrainerError("base provenance must be exact Wave6ProvenanceV1")
    path = Path(provenance.checkpoint_path)
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
        descriptor = payload["descriptor"]
        config = descriptor["model_config"]
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, EOFError) as exc:
        raise FrozenResidualTrainerError("Wave6 checkpoint has no closed model descriptor") from exc
    required = {"card_vocabulary_size", "hidden_dim", "embedding_dim", "state_scalar_dim"}
    if type(config) is not dict or set(config) != required or any(type(value) is not int or value < 1 for value in config.values()):
        raise FrozenResidualTrainerError("Wave6 checkpoint model_config is invalid")
    model = SpecialistModelV4(**config, seed=0)
    try:
        load_specialist_checkpoint_v4(
            path,
            model,
            expected_file_sha256=provenance.checkpoint_file_sha256,
            expected_tensor_state_sha256=provenance.checkpoint_tensor_state_sha256,
        )
    except Exception as exc:
        raise FrozenResidualTrainerError("Wave6 checkpoint failed strict closed-artifact validation") from exc
    model = model.to(device).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise FrozenResidualTrainerError("Wave6 base parameter remains trainable")
    return model


def residual_sidecar_tensor_state_sha256_v1(sidecar: FrozenResidualSidecarV1) -> str:
    """Hash the exact dense sidecar tensor state, independent of torch serialization."""
    if type(sidecar) is not FrozenResidualSidecarV1:
        raise FrozenResidualTrainerError("sidecar state hash requires exact sidecar type")
    digest = hashlib.sha256(b"meta-specialist:frozen-residual-sidecar-state:v1\0")
    for name, tensor in sorted(sidecar.state_dict().items()):
        if type(name) is not str or type(tensor) is not torch.Tensor or tensor.layout != torch.strided:
            raise FrozenResidualTrainerError("sidecar state contains a non-dense tensor")
        value = tensor.detach().cpu().contiguous()
        if value.is_floating_point() and not torch.isfinite(value).all():
            raise FrozenResidualTrainerError("sidecar state contains nonfinite values")
        raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(value.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(value.shape)).encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenResidualCheckpointDescriptorV1:
    schema_version: str
    seed: int
    preflight_manifest_sha256: str
    target_kind: str
    target_manifest_sha256: str
    base_checkpoint_file_sha256: str
    base_checkpoint_tensor_state_sha256: str
    sidecar_tensor_state_sha256: str
    optimizer_updates: int
    effective_loss_mass: float
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != "specialist-frozen-wave6-residual-checkpoint-v1":
            raise FrozenResidualTrainerError("residual checkpoint descriptor schema is invalid")
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise FrozenResidualTrainerError("residual checkpoint seed is invalid")
        for field in (
            "preflight_manifest_sha256", "base_checkpoint_file_sha256",
            "base_checkpoint_tensor_state_sha256", "sidecar_tensor_state_sha256",
            "target_manifest_sha256",
        ):
            _sha(getattr(self, field), field=field)
        if type(self.target_kind) is not str or self.target_kind not in _TARGET_KINDS_V1:
            raise FrozenResidualTrainerError("residual checkpoint target_kind is invalid")
        if type(self.optimizer_updates) is not int or self.optimizer_updates < 0:
            raise FrozenResidualTrainerError("residual checkpoint optimizer update count is invalid")
        if type(self.effective_loss_mass) is not float or not math.isfinite(self.effective_loss_mass) or self.effective_loss_mass < 0.0:
            raise FrozenResidualTrainerError("residual checkpoint effective mass is invalid")
        if any(getattr(self, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
            raise FrozenResidualTrainerError("residual checkpoint descriptor grants authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "seed": self.seed,
            "preflight_manifest_sha256": self.preflight_manifest_sha256,
            "target_kind": self.target_kind,
            "target_manifest_sha256": self.target_manifest_sha256,
            "base_checkpoint": {
                "file_sha256": self.base_checkpoint_file_sha256,
                "tensor_state_sha256": self.base_checkpoint_tensor_state_sha256,
            },
            "sidecar_tensor_state_sha256": self.sidecar_tensor_state_sha256,
            "optimizer_updates": self.optimizer_updates,
            "effective_loss_mass": self.effective_loss_mass,
            "training_permitted": self.training_permitted,
            "promotion_authority": self.promotion_authority,
            "longrun_allowed": self.longrun_allowed,
        }


def build_residual_checkpoint_descriptor_v1(
    provenance: Wave6ProvenanceV1,
    sidecar: FrozenResidualSidecarV1,
    *,
    seed: int,
    preflight_manifest_sha256: str,
    target_kind: str,
    target_manifest_sha256: str,
    optimizer_updates: int,
    effective_loss_mass: float,
) -> FrozenResidualCheckpointDescriptorV1:
    if type(provenance) is not Wave6ProvenanceV1 or type(sidecar) is not FrozenResidualSidecarV1:
        raise FrozenResidualTrainerError("checkpoint descriptor provenance/sidecar types are invalid")
    if seed != provenance.seed:
        raise FrozenResidualTrainerError("checkpoint descriptor seed differs from base provenance")
    return FrozenResidualCheckpointDescriptorV1(
        schema_version="specialist-frozen-wave6-residual-checkpoint-v1",
        seed=seed,
        preflight_manifest_sha256=_sha(preflight_manifest_sha256, field="preflight_manifest_sha256"),
        target_kind=target_kind,
        target_manifest_sha256=_sha(target_manifest_sha256, field="target_manifest_sha256"),
        base_checkpoint_file_sha256=provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_state_sha256=provenance.checkpoint_tensor_state_sha256,
        sidecar_tensor_state_sha256=residual_sidecar_tensor_state_sha256_v1(sidecar),
        optimizer_updates=optimizer_updates,
        effective_loss_mass=float(effective_loss_mass),
    )


@dataclass(frozen=True, slots=True)
class FrozenResidualTrainingResultV1:
    optimizer_updates: int
    total_rows: int
    context_only_rows: int
    loss_bearing_rows: int
    denominator_rows: int
    effective_loss_mass: float
    imitation_loss: float
    anchor_kl: float
    residual_l2: float
    sidecar_parameter_count: int
    sidecar_tensor_state_sha256: str


def _record_groups(sequence: RecurrentBCSequenceV4) -> tuple[tuple[RecurrentBCStepV4, ...], ...]:
    groups: list[tuple[RecurrentBCStepV4, ...]] = []
    current: list[RecurrentBCStepV4] = []
    current_id: str | None = None
    for step in sequence.steps:
        if current_id is None or step.record_id == current_id:
            current.append(step)
            current_id = step.record_id
        else:
            groups.append(tuple(current))
            current = [step]
            current_id = step.record_id
    if current:
        groups.append(tuple(current))
    if not groups or not groups[0][0].episode_start or any(group[0].episode_start for group in groups[1:]):
        raise FrozenResidualTrainerError("sequence record groups have invalid episode boundaries")
    if any(any(step.episode_start for step in group[1:]) for group in groups):
        raise FrozenResidualTrainerError("one record group cannot reset inside an action")
    return tuple(groups)


def _validate_domain(sequence: RecurrentBCSequenceV4, domain: SeedKnownDomainManifestV1, sidecar: FrozenResidualSidecarV1) -> None:
    if type(sequence) is not RecurrentBCSequenceV4 or not sequence.research_only or any(not step.research_only for step in sequence.steps):
        raise FrozenResidualTrainerError("residual trainer accepts research-only RecurrentBCSequenceV4 only")
    if type(domain) is not SeedKnownDomainManifestV1 or type(sidecar) is not FrozenResidualSidecarV1:
        raise FrozenResidualTrainerError("residual trainer domain/sidecar types are invalid")
    if sidecar.known_context_ids != frozenset(domain.context_ids) or sidecar.known_action_keys != frozenset(domain.action_keys):
        raise FrozenResidualTrainerError("sidecar known domain differs from preflight seed domain")
    for step in sequence.steps:
        context = build_residual_context_v1(step.model_input, step.step_input)
        if context.context_id not in domain.context_ids:
            raise FrozenResidualTrainerError("sequence context is outside the preflight known domain")
        if any(key not in domain.action_keys for key in (*context.action_keys,)):
            raise FrozenResidualTrainerError("sequence action key is outside the preflight known domain")


def train_residual_sequences_v1(
    base_model: SpecialistModelV4,
    sidecar: FrozenResidualSidecarV1,
    sequences: Sequence[RecurrentBCSequenceV4],
    *,
    known_domain: SeedKnownDomainManifestV1,
    max_updates: int = 1,
    learning_rate: float = 1.0e-3,
    anchor_kl_weight: float = 1.0,
    residual_l2_weight: float = 1.0e-4,
) -> FrozenResidualTrainingResultV1:
    """Run a fixed tiny number of sidecar-only updates over recurrent sequences."""
    if type(base_model) is not SpecialistModelV4 or type(sidecar) is not FrozenResidualSidecarV1:
        raise FrozenResidualTrainerError("base_model/sidecar types are invalid")
    if type(sequences) not in (tuple, list) or not sequences:
        raise FrozenResidualTrainerError("residual trainer sequences are empty")
    if type(max_updates) is not int or max_updates < 1:
        raise FrozenResidualTrainerError("max_updates must be positive")
    learning_rate = _finite_positive(learning_rate, field="learning_rate")
    for parameter in base_model.parameters():
        parameter.requires_grad_(False)
    base_model.eval()
    _validate_domain(sequences[0], known_domain, sidecar)
    for sequence in sequences[1:]:
        _validate_domain(sequence, known_domain, sidecar)
    optimizer = torch.optim.SGD(sidecar.parameters(), lr=learning_rate)
    total_rows = context_rows = loss_rows = denominator_rows = 0
    effective_mass = imitation_total = anchor_total = l2_total = 0.0
    updates = 0
    for sequence in sequences:
        if updates >= max_updates:
            break
        groups = _record_groups(sequence)
        optimizer.zero_grad(set_to_none=True)
        sequence_loss: Tensor | None = None
        sequence_mass = 0.0
        hidden: Tensor | None = None
        for group in groups:
            with torch.no_grad():
                outputs = base_model.forward_record_group_v4(
                    tuple(step.state for step in group),
                    hidden_state=hidden,
                    episode_start=group[0].episode_start,
                )
                hidden = outputs[0].hidden_state
            if hidden is not None:
                hidden = hidden.detach()
            for step, output in zip(group, outputs, strict=True):
                total_rows += 1
                if step.supervision_weight <= 0.0:
                    context_rows += 1
                    continue
                context = build_residual_context_v1(step.model_input, step.step_input)
                residual = sidecar.residuals(context)
                base_logits = output.logits.detach()
                residual_logits = residual.semantic.to(device=base_logits.device, dtype=base_logits.dtype)
                if step.step_input.stop_available:
                    base_stop = (base_model.stop_vector @ output.global_token + base_model.stop_bias).detach().reshape(1)
                    if residual.stop is None:
                        raise FrozenResidualTrainerError("known STOP domain returned no STOP residual")
                    residual_logits = torch.cat((residual_logits, residual.stop.to(device=base_logits.device, dtype=base_logits.dtype).reshape(1)))
                    base_logits = torch.cat((base_logits, base_stop))
                if base_logits.numel() != len(step.target_masses) or not 0 <= step.target_index < base_logits.numel():
                    raise FrozenResidualTrainerError("sequence target/domain arity mismatch")
                breakdown = frozen_residual_loss_v1(
                    base_logits.reshape(1, -1), residual_logits.reshape(1, -1),
                    torch.tensor([step.target_index], dtype=torch.long, device=base_logits.device),
                    anchor_kl_weight=anchor_kl_weight,
                    residual_l2_weight=residual_l2_weight,
                )
                weight = float(step.supervision_weight) * float(step.reach_mass) * float(step.quality_weight)
                sequence_loss = breakdown.total * weight if sequence_loss is None else sequence_loss + breakdown.total * weight
                sequence_mass += weight
                loss_rows += 1
                effective_mass += weight
                imitation_total += float(breakdown.imitation.detach().item()) * weight
                anchor_total += float(breakdown.anchor_kl.detach().item()) * weight
                l2_total += float(breakdown.residual_l2.detach().item()) * weight
        if sequence_loss is None or sequence_mass <= 0.0:
            raise FrozenResidualTrainerError("sequence contains no loss-bearing rows")
        sequence_loss.backward()
        optimizer.step()
        updates += 1
        denominator_rows += sum(1 for group in groups for step in group if step.supervision_weight > 0.0)
    if updates < 1 or effective_mass <= 0.0:
        raise FrozenResidualTrainerError("residual trainer produced no optimizer update")
    parameter_count = sum(parameter.numel() for parameter in sidecar.parameters())
    return FrozenResidualTrainingResultV1(
        optimizer_updates=updates,
        total_rows=total_rows,
        context_only_rows=context_rows,
        loss_bearing_rows=loss_rows,
        denominator_rows=denominator_rows,
        effective_loss_mass=float(effective_mass),
        imitation_loss=imitation_total / effective_mass,
        anchor_kl=anchor_total / effective_mass,
        residual_l2=l2_total / effective_mass,
        sidecar_parameter_count=parameter_count,
        sidecar_tensor_state_sha256=residual_sidecar_tensor_state_sha256_v1(sidecar),
    )


__all__ = [
    "FrozenResidualTrainerError",
    "TARGET_KIND_SELF_IMITATION_V1",
    "TARGET_KIND_SIGNED_BEHAVIOR_V1",
    "FrozenResidualCheckpointDescriptorV1",
    "FrozenResidualTrainingResultV1",
    "load_wave6_base_from_provenance_v1",
    "residual_sidecar_tensor_state_sha256_v1",
    "build_residual_checkpoint_descriptor_v1",
    "train_residual_sequences_v1",
]
