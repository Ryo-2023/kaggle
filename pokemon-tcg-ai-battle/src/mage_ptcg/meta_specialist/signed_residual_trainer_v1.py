"""Research-only signed outcome trainer for a frozen Wave6 residual sidecar.

The module deliberately has no production policy, evaluator, or CABT imports.
It consumes only hash-bound :class:`SignedOutcomeMaterializationV1` rows and
uses their signed behavior weights directly; it never turns those weights into
hard teacher labels or ordinary cross entropy targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import math

import torch
from torch import Tensor

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import (
    AlignedSignedResidualPrefixV1,
    SignedOutcomeMaterializationV1,
    TARGET_KIND_V1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    SeedKnownDomainManifestV1,
    Wave6ProvenanceV1,
    _sha,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualSidecarV1,
    build_residual_context_v1,
    frozen_residual_signed_behavior_loss_v1,
)
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4


class SignedResidualTrainerError(ValueError):
    """Raised when the research-only signed residual contract is not closed."""


def _base_tensor_state_sha256_v1(base_model: SpecialistModelV4) -> str:
    """Match the closed V4 tensor-state digest without serializing a checkpoint."""
    if type(base_model) is not SpecialistModelV4:
        raise SignedResidualTrainerError("base model must be exact SpecialistModelV4")
    digest = hashlib.sha256(b"mage_ptcg:specialist-neural-state:v4\0")
    for name, tensor in sorted(base_model.state_dict().items()):
        if type(name) is not str or type(tensor) is not Tensor or tensor.layout != torch.strided:
            raise SignedResidualTrainerError("base model state contains a non-dense tensor")
        value = tensor.detach().cpu().contiguous()
        if (value.is_floating_point() or value.is_complex()) and not torch.isfinite(value).all():
            raise SignedResidualTrainerError("base model state contains nonfinite values")
        raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(value.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(value.shape)).encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _finite_positive(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or float(value) <= 0.0:
        raise SignedResidualTrainerError(f"{field} must be a finite positive number")
    return float(value)


def _finite_nonnegative(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or float(value) < 0.0:
        raise SignedResidualTrainerError(f"{field} must be a finite nonnegative number")
    return float(value)


@dataclass(frozen=True, slots=True)
class SignedResidualTrainingResultV1:
    """Closed diagnostic summary of a bounded sidecar-only optimizer run."""

    seed: int
    target_kind: str
    target_manifest_file_sha256: str
    source_transitions_file_sha256: str
    source_episode_sha256: str
    base_checkpoint_file_sha256: str
    base_tensor_state_sha256_before: str
    base_tensor_state_sha256_after: str
    optimizer_updates: int
    context_only_rows: int
    signed_loss_rows: int
    positive_effective_mass: float
    negative_effective_mass: float
    zero_weight_rows: int
    loss_normalizer: float
    signed_behavior_loss: float
    anchor_kl: float
    residual_l2: float
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise SignedResidualTrainerError("result seed is invalid")
        if self.target_kind != TARGET_KIND_V1:
            raise SignedResidualTrainerError("result target kind must remain signed behavior")
        for field in (
            "target_manifest_file_sha256", "source_transitions_file_sha256", "source_episode_sha256",
            "base_checkpoint_file_sha256", "base_tensor_state_sha256_before", "base_tensor_state_sha256_after",
        ):
            _sha(getattr(self, field), field=field)
        if self.base_tensor_state_sha256_before != self.base_tensor_state_sha256_after:
            raise SignedResidualTrainerError("frozen base tensor state changed during sidecar training")
        for field in ("optimizer_updates", "context_only_rows", "signed_loss_rows", "zero_weight_rows"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise SignedResidualTrainerError(f"result {field} is invalid")
        for field in ("positive_effective_mass", "negative_effective_mass", "loss_normalizer", "signed_behavior_loss", "anchor_kl", "residual_l2"):
            if type(getattr(self, field)) is not float or not math.isfinite(getattr(self, field)):
                raise SignedResidualTrainerError(f"result {field} is invalid")
        if self.positive_effective_mass < 0.0 or self.negative_effective_mass < 0.0 or self.loss_normalizer <= 0.0:
            raise SignedResidualTrainerError("result loss masses are invalid")
        if any(getattr(self, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
            raise SignedResidualTrainerError("signed residual result grants authority")

    def to_summary_dict(self) -> dict[str, object]:
        return {
            "schema_version": "specialist-signed-outcome-residual-training-v1",
            "seed": self.seed,
            "target_kind": self.target_kind,
            "target_manifest_file_sha256": self.target_manifest_file_sha256,
            "source_transitions_file_sha256": self.source_transitions_file_sha256,
            "source_episode_sha256": self.source_episode_sha256,
            "base_checkpoint_file_sha256": self.base_checkpoint_file_sha256,
            "base_tensor_state_sha256_before": self.base_tensor_state_sha256_before,
            "base_tensor_state_sha256_after": self.base_tensor_state_sha256_after,
            "optimizer_updates": self.optimizer_updates,
            "context_only_rows": self.context_only_rows,
            "signed_loss_rows": self.signed_loss_rows,
            "positive_effective_mass": self.positive_effective_mass,
            "negative_effective_mass": self.negative_effective_mass,
            "zero_weight_rows": self.zero_weight_rows,
            "loss_normalizer": self.loss_normalizer,
            "signed_behavior_loss": self.signed_behavior_loss,
            "anchor_kl": self.anchor_kl,
            "residual_l2": self.residual_l2,
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
        }


def _record_groups(sequence: RecurrentBCSequenceV4) -> tuple[tuple[RecurrentBCStepV4, ...], ...]:
    groups: list[tuple[RecurrentBCStepV4, ...]] = []
    active: list[RecurrentBCStepV4] = []
    active_record_id: str | None = None
    for step in sequence.steps:
        if active_record_id is None or step.record_id == active_record_id:
            active.append(step)
            active_record_id = step.record_id
            continue
        groups.append(tuple(active))
        active = [step]
        active_record_id = step.record_id
    if active:
        groups.append(tuple(active))
    if not groups or not groups[0][0].episode_start:
        raise SignedResidualTrainerError("sequence lacks an initial episode boundary")
    if any(group[0].episode_start for group in groups[1:]) or any(step.episode_start for group in groups for step in group[1:]):
        raise SignedResidualTrainerError("sequence record group resets are invalid")
    return tuple(groups)


def _validate_inputs(
    base_model: SpecialistModelV4,
    base_provenance: Wave6ProvenanceV1,
    sidecar: FrozenResidualSidecarV1,
    materialization: SignedOutcomeMaterializationV1,
    known_domain: SeedKnownDomainManifestV1,
) -> None:
    if type(base_model) is not SpecialistModelV4 or type(base_provenance) is not Wave6ProvenanceV1:
        raise SignedResidualTrainerError("base model/provenance types are invalid")
    if type(sidecar) is not FrozenResidualSidecarV1 or type(materialization) is not SignedOutcomeMaterializationV1:
        raise SignedResidualTrainerError("sidecar/materialization types are invalid")
    if type(known_domain) is not SeedKnownDomainManifestV1:
        raise SignedResidualTrainerError("known domain type is invalid")
    if known_domain.provenance != base_provenance:
        raise SignedResidualTrainerError("known-domain provenance differs from frozen base provenance")
    if materialization.seed != base_provenance.seed:
        raise SignedResidualTrainerError("materialization seed differs from frozen base provenance")
    if materialization.source_transitions_file_sha256 != base_provenance.transitions_file_sha256:
        raise SignedResidualTrainerError("materialization source transition SHA differs from base provenance")
    if sidecar.base_checkpoint_file_sha256 != base_provenance.checkpoint_file_sha256 or sidecar.base_checkpoint_tensor_sha256 != base_provenance.checkpoint_tensor_state_sha256:
        raise SignedResidualTrainerError("sidecar is not bound to the frozen base provenance")
    if sidecar.known_context_ids != frozenset(known_domain.context_ids) or sidecar.known_action_keys != frozenset(known_domain.action_keys):
        raise SignedResidualTrainerError("sidecar known domain differs from the materialization domain")
    if _base_tensor_state_sha256_v1(base_model) != base_provenance.checkpoint_tensor_state_sha256:
        raise SignedResidualTrainerError("base tensor state differs from frozen base provenance")
    if any(getattr(materialization, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
        raise SignedResidualTrainerError("materialization grants authority")


def _target_index(materialization: SignedOutcomeMaterializationV1) -> Mapping[tuple[int, int], AlignedSignedResidualPrefixV1]:
    if not materialization.prefix_targets:
        raise SignedResidualTrainerError("materialization has no signed prefix targets")
    targets: dict[tuple[int, int], AlignedSignedResidualPrefixV1] = {}
    for target in materialization.prefix_targets:
        if type(target) is not AlignedSignedResidualPrefixV1 or target.target_kind != TARGET_KIND_V1:
            raise SignedResidualTrainerError("signed residual trainer refuses a non-signed target kind")
        key = (target.sequence_index, target.sequence_step_index)
        if key in targets:
            raise SignedResidualTrainerError("materialization has duplicate signed prefix targets")
        targets[key] = target
    expected = {
        (sequence_index, step_index)
        for sequence_index, sequence in enumerate(materialization.sequences)
        for step_index in range(len(sequence.steps))
    }
    if set(targets) != expected:
        raise SignedResidualTrainerError("materialization signed targets do not cover each context prefix exactly once")
    return targets


def _validate_sequence_step(
    sequence: RecurrentBCSequenceV4,
    step: RecurrentBCStepV4,
    target: AlignedSignedResidualPrefixV1,
    known_domain: SeedKnownDomainManifestV1,
) -> None:
    if type(sequence) is not RecurrentBCSequenceV4 or not sequence.research_only or type(step) is not RecurrentBCStepV4 or not step.research_only:
        raise SignedResidualTrainerError("signed trainer accepts research-only V4 sequences only")
    if step.supervision_weight != 0.0:
        raise SignedResidualTrainerError("signed trainer refuses ordinary BC supervision weights")
    if target.episode_id != sequence.episode_group or target.transition_sha256 != step.record_id:
        raise SignedResidualTrainerError("aligned signed target does not match its V4 sequence row")
    if target.target_index != step.target_index or not 0 <= target.target_index < len(step.target_masses):
        raise SignedResidualTrainerError("aligned signed target is outside its legal V4 domain")
    context = build_residual_context_v1(step.model_input, step.step_input)
    if context.context_id not in known_domain.context_ids or any(key not in known_domain.action_keys for key in context.action_keys):
        raise SignedResidualTrainerError("V4 context is outside the sealed sidecar domain")


def train_signed_outcome_materialization_v1(
    base_model: SpecialistModelV4,
    base_provenance: Wave6ProvenanceV1,
    sidecar: FrozenResidualSidecarV1,
    materialization: SignedOutcomeMaterializationV1,
    *,
    known_domain: SeedKnownDomainManifestV1,
    max_updates: int = 1,
    learning_rate: float = 1.0e-3,
    anchor_kl_weight: float = 1.0,
    residual_l2_weight: float = 1.0e-4,
) -> SignedResidualTrainingResultV1:
    """Run a bounded, sidecar-only signed-behavior update over sealed rows.

    Each V4 record group is forwarded once under ``no_grad`` (including
    zero-weight context prefixes).  The variable legal domain, including a
    STOP action when present, is retained per prefix; no padded batch is made.
    """
    _validate_inputs(base_model, base_provenance, sidecar, materialization, known_domain)
    if type(max_updates) is not int or max_updates < 1:
        raise SignedResidualTrainerError("max_updates must be a positive int")
    learning_rate = _finite_positive(learning_rate, field="learning_rate")
    anchor_kl_weight = _finite_nonnegative(anchor_kl_weight, field="anchor_kl_weight")
    residual_l2_weight = _finite_nonnegative(residual_l2_weight, field="residual_l2_weight")
    targets = _target_index(materialization)
    base_before = _base_tensor_state_sha256_v1(base_model)
    for parameter in base_model.parameters():
        parameter.requires_grad_(False)
        parameter.grad = None
    base_model.eval()
    sidecar.train()
    optimizer = torch.optim.SGD(sidecar.parameters(), lr=learning_rate)
    updates = context_rows = signed_rows = zero_rows = 0
    positive_mass = negative_mass = 0.0
    signed_total = anchor_total = l2_total = 0.0
    for sequence_index, sequence in enumerate(materialization.sequences):
        if updates >= max_updates:
            break
        groups = _record_groups(sequence)
        optimizer.zero_grad(set_to_none=True)
        total_loss: Tensor | None = None
        normalizer = 0.0
        hidden: Tensor | None = None
        sequence_rows = 0
        for group in groups:
            with torch.no_grad():
                outputs = base_model.forward_record_group_v4(
                    tuple(step.state for step in group),
                    hidden_state=hidden,
                    episode_start=group[0].episode_start,
                )
                hidden = outputs[0].hidden_state.detach()
            for step, output in zip(group, outputs, strict=True):
                target = targets[(sequence_index, sequence_rows)]
                _validate_sequence_step(sequence, step, target, known_domain)
                sequence_rows += 1
                context_rows += 1
                signed_weight = float(target.signed_weight)
                if signed_weight == 0.0:
                    zero_rows += 1
                    continue
                context = build_residual_context_v1(step.model_input, step.step_input)
                residual = sidecar.residuals(context)
                base_logits = output.logits.detach()
                residual_logits = residual.semantic.to(device=base_logits.device, dtype=base_logits.dtype)
                if step.step_input.stop_available:
                    base_stop = (base_model.stop_vector @ output.global_token + base_model.stop_bias).detach().reshape(1)
                    if residual.stop is None:
                        raise SignedResidualTrainerError("sealed STOP row returned no residual STOP logit")
                    base_logits = torch.cat((base_logits, base_stop))
                    residual_logits = torch.cat((residual_logits, residual.stop.to(device=base_logits.device, dtype=base_logits.dtype).reshape(1)))
                if base_logits.numel() != len(step.target_masses) or target.target_index >= base_logits.numel():
                    raise SignedResidualTrainerError("variable legal domain/STOP arity changed during forward")
                breakdown = frozen_residual_signed_behavior_loss_v1(
                    base_logits.reshape(1, -1),
                    residual_logits.reshape(1, -1),
                    torch.tensor([target.target_index], dtype=torch.long, device=base_logits.device),
                    torch.tensor([signed_weight], dtype=base_logits.dtype, device=base_logits.device),
                    anchor_kl_weight=anchor_kl_weight,
                    residual_l2_weight=residual_l2_weight,
                )
                total_loss = breakdown.total if total_loss is None else total_loss + breakdown.total
                magnitude = abs(signed_weight)
                normalizer += magnitude
                signed_rows += 1
                if signed_weight > 0.0:
                    positive_mass += magnitude
                else:
                    negative_mass += magnitude
                signed_total += float(breakdown.imitation.detach().item())
                anchor_total += float(breakdown.anchor_kl.detach().item())
                l2_total += float(breakdown.residual_l2.detach().item())
        if total_loss is None or normalizer <= 0.0:
            raise SignedResidualTrainerError("bounded update contains no nonzero signed outcome targets")
        (total_loss / normalizer).backward()
        optimizer.step()
        updates += 1
    if updates < 1:
        raise SignedResidualTrainerError("signed residual trainer produced no optimizer update")
    base_after = _base_tensor_state_sha256_v1(base_model)
    if base_after != base_before or base_after != base_provenance.checkpoint_tensor_state_sha256:
        raise SignedResidualTrainerError("frozen base tensor state changed during sidecar training")
    if any(parameter.requires_grad or parameter.grad is not None for parameter in base_model.parameters()):
        raise SignedResidualTrainerError("frozen base acquired a trainable parameter or gradient")
    total_normalizer = positive_mass + negative_mass
    return SignedResidualTrainingResultV1(
        seed=materialization.seed,
        target_kind=TARGET_KIND_V1,
        target_manifest_file_sha256=materialization.target_manifest_file_sha256,
        source_transitions_file_sha256=materialization.source_transitions_file_sha256,
        source_episode_sha256=materialization.source_episode_sha256,
        base_checkpoint_file_sha256=base_provenance.checkpoint_file_sha256,
        base_tensor_state_sha256_before=base_before,
        base_tensor_state_sha256_after=base_after,
        optimizer_updates=updates,
        context_only_rows=context_rows,
        signed_loss_rows=signed_rows,
        positive_effective_mass=float(positive_mass),
        negative_effective_mass=float(negative_mass),
        zero_weight_rows=zero_rows,
        loss_normalizer=float(total_normalizer),
        signed_behavior_loss=float(signed_total / total_normalizer),
        anchor_kl=float(anchor_total / total_normalizer),
        residual_l2=float(l2_total / total_normalizer),
    )


__all__ = [
    "SignedResidualTrainerError",
    "SignedResidualTrainingResultV1",
    "train_signed_outcome_materialization_v1",
]
