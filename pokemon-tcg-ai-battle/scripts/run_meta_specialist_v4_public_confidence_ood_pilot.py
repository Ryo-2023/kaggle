#!/usr/bin/env python3
"""Plan-only two-seed public confidence/OOD V4 pilot orchestration.

This is deliberately a contract/orchestration layer, not a training or
evaluation command.  It binds the frozen common reference bundle to the
corresponding Wave6 seed screen/checkpoint identities, materializes the
public confidence mask through the existing typed contract, and emits two
fixed-budget trainer invocation descriptors (control and candidate).  No
optimizer, CABT evaluator, model loader, or filesystem output is started.

The candidate and control sequence identities are explicit.  A future runner
may consume the descriptors only after a separate approval step; this module
does not expose a ``best_epoch`` and never selects a checkpoint by validation
performance.  ``final`` and ``last`` checkpoint paths are retained as distinct
fixed roles for honest resume/finalization bookkeeping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType

from scripts.run_meta_specialist_v4_public_confidence_ood_bc import (
    PublicOodMaskContractV1,
    SealedPublicTransitionV1,
    build_public_ood_mask_contract_v1,
    load_public_ood_reference_bundle_v1,
    sealed_public_transition_from_mapping_v1,
    validate_public_ood_policy_manifest_v1,
)


PILOT_PLAN_SCHEMA_V1 = "meta-specialist-v4-public-confidence-ood-pilot-plan-v1"
TRAINER_INVOCATION_SCHEMA_V1 = "meta-specialist-v4-public-confidence-ood-trainer-invocation-v1"
MASK_SUMMARY_SCHEMA_V1 = "meta-specialist-v4-public-confidence-ood-mask-summary-v1"
V4_TRAINER_ENTRYPOINT_V1 = "mage_ptcg.meta_specialist.recurrent_bc_v4.train_recurrent_bc_v4"
_HEX64 = frozenset("0123456789abcdef")


class PublicOodPilotContractError(ValueError):
    """Raised when a two-seed pilot plan cannot be proven closed."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise PublicOodPilotContractError(f"{field} must be a lowercase SHA-256 hex string")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PublicOodPilotContractError("pilot plan identity is not canonical JSON") from exc


def _object_sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class Wave6SeedBindingV1:
    """Hash-bound screen/checkpoint provenance for one Wave6 training seed."""

    seed: int
    screen_path: str
    screen_file_sha256: str
    transitions_path: str
    transitions_file_sha256: str
    init_checkpoint_path: str
    init_checkpoint_file_sha256: str
    init_checkpoint_tensor_state_sha256: str

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise PublicOodPilotContractError("Wave6 pilot seed must be exactly 0 or 1")
        for field in ("screen_path", "transitions_path", "init_checkpoint_path"):
            value = getattr(self, field)
            if not isinstance(value, (str, Path)) or not str(value):
                raise PublicOodPilotContractError(f"Wave6 {field} is invalid")
            object.__setattr__(self, field, str(value))
        for field in (
            "screen_file_sha256", "transitions_file_sha256",
            "init_checkpoint_file_sha256", "init_checkpoint_tensor_state_sha256",
        ):
            _sha(getattr(self, field), field=f"Wave6 {field}")

    def to_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "screen_path": self.screen_path,
            "screen_file_sha256": self.screen_file_sha256,
            "transitions_path": self.transitions_path,
            "transitions_file_sha256": self.transitions_file_sha256,
            "init_checkpoint_path": self.init_checkpoint_path,
            "init_checkpoint_file_sha256": self.init_checkpoint_file_sha256,
            "init_checkpoint_tensor_state_sha256": self.init_checkpoint_tensor_state_sha256,
        }


@dataclass(frozen=True, slots=True)
class PublicOodPilotMaskSummaryV1:
    """Auditable effective loss mass for one seed's public mask."""

    transition_row_count: int
    eligible_row_count: int
    context_only_row_count: int
    effective_loss_mass: float
    record_row_counts: Mapping[str, int]
    group_row_counts: Mapping[str, int]

    @classmethod
    def from_contract(cls, contract: PublicOodMaskContractV1) -> "PublicOodPilotMaskSummaryV1":
        if type(contract) is not PublicOodMaskContractV1:
            raise PublicOodPilotContractError("mask summary requires the typed public mask contract")
        return cls(
            transition_row_count=len(contract.rows),
            eligible_row_count=contract.loss_bearing_row_count,
            context_only_row_count=contract.context_only_row_count,
            effective_loss_mass=float(contract.effective_loss_mass),
            record_row_counts=MappingProxyType(dict(contract.record_row_counts)),
            group_row_counts=MappingProxyType(dict(contract.group_row_counts)),
        )

    def __post_init__(self) -> None:
        if type(self.transition_row_count) is not int or self.transition_row_count < 1:
            raise PublicOodPilotContractError("mask transition row count is invalid")
        if type(self.eligible_row_count) is not int or self.eligible_row_count < 0:
            raise PublicOodPilotContractError("mask eligible row count is invalid")
        if type(self.context_only_row_count) is not int or self.context_only_row_count < 0:
            raise PublicOodPilotContractError("mask context-only row count is invalid")
        if self.eligible_row_count + self.context_only_row_count != self.transition_row_count:
            raise PublicOodPilotContractError("mask row counts do not close")
        if type(self.effective_loss_mass) is not float or not math.isfinite(self.effective_loss_mass):
            raise PublicOodPilotContractError("mask effective loss mass is invalid")
        if not math.isclose(self.effective_loss_mass, float(self.eligible_row_count), rel_tol=0.0, abs_tol=1e-12):
            raise PublicOodPilotContractError("mask effective loss mass differs from eligible row count")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": MASK_SUMMARY_SCHEMA_V1,
            "transition_row_count": self.transition_row_count,
            "eligible_row_count": self.eligible_row_count,
            "context_only_row_count": self.context_only_row_count,
            "effective_loss_mass": self.effective_loss_mass,
            "record_row_counts": dict(self.record_row_counts),
            "group_row_counts": dict(self.group_row_counts),
        }


@dataclass(frozen=True, slots=True)
class FixedTrainerInvocationV1:
    """A fixed final/last checkpoint contract with no best-epoch selection."""

    seed: int
    arm: str
    output_dir: str
    sequence_order_seed: int
    epochs: int
    patience: int
    learning_rate: float
    tbptt_steps: int
    gradient_clip_norm: float
    execution: str = "NOT_STARTED"
    best_checkpoint_selection: bool = False
    trainer_entrypoint: str = V4_TRAINER_ENTRYPOINT_V1
    connected: bool = False

    @property
    def final_checkpoint_path(self) -> str:
        return str(Path(self.output_dir) / "final-recurrent-bc-v4.pt")

    @property
    def last_checkpoint_path(self) -> str:
        return str(Path(self.output_dir) / "last-recurrent-bc-v4.pt")

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise PublicOodPilotContractError("trainer invocation seed is invalid")
        if self.arm not in {"control", "candidate"}:
            raise PublicOodPilotContractError("trainer invocation arm is invalid")
        if type(self.output_dir) is not str or not self.output_dir:
            raise PublicOodPilotContractError("trainer invocation output_dir is invalid")
        if self.sequence_order_seed != self.seed or type(self.sequence_order_seed) is not int:
            raise PublicOodPilotContractError("trainer sequence_order_seed must match the Wave6 seed")
        if self.epochs != 1 or self.patience != 0:
            raise PublicOodPilotContractError("pilot trainer budget must be exactly one fixed epoch")
        if type(self.learning_rate) not in (int, float) or type(self.learning_rate) is bool or not math.isfinite(float(self.learning_rate)) or self.learning_rate <= 0.0:
            raise PublicOodPilotContractError("trainer learning_rate is invalid")
        if type(self.tbptt_steps) is not int or self.tbptt_steps < 1:
            raise PublicOodPilotContractError("trainer tbptt_steps is invalid")
        if type(self.gradient_clip_norm) not in (int, float) or type(self.gradient_clip_norm) is bool or not math.isfinite(float(self.gradient_clip_norm)) or self.gradient_clip_norm <= 0.0:
            raise PublicOodPilotContractError("trainer gradient_clip_norm is invalid")
        if self.execution != "NOT_STARTED":
            raise PublicOodPilotContractError("trainer invocation execution must remain NOT_STARTED")
        if self.best_checkpoint_selection is not False:
            raise PublicOodPilotContractError("best checkpoint selection is forbidden in the pilot plan")
        if self.trainer_entrypoint != V4_TRAINER_ENTRYPOINT_V1 or self.connected is not False:
            raise PublicOodPilotContractError("V4 trainer connection must remain a non-executing descriptor")

    def to_dict(self) -> dict[str, object]:
        # Intentionally omit ``best_epoch``.  A future executor must treat
        # final and last as fixed roles, not infer a validation-selected best.
        return {
            "schema_version": TRAINER_INVOCATION_SCHEMA_V1,
            "seed": self.seed,
            "arm": self.arm,
            "output_dir": self.output_dir,
            "sequence_order_seed": self.sequence_order_seed,
            "epochs": self.epochs,
            "patience": self.patience,
            "learning_rate": self.learning_rate,
            "tbptt_steps": self.tbptt_steps,
            "gradient_clip_norm": self.gradient_clip_norm,
            "execution": self.execution,
            "best_checkpoint_selection": self.best_checkpoint_selection,
            "trainer_entrypoint": self.trainer_entrypoint,
            "connected": self.connected,
            "checkpoint_roles": {
                "final": self.final_checkpoint_path,
                "last": self.last_checkpoint_path,
                "selection": "fixed_final_and_last_no_best_epoch",
            },
        }


@dataclass(frozen=True, slots=True)
class PublicOodPilotSeedPlanV1:
    seed: int
    wave6: Wave6SeedBindingV1
    mask: PublicOodPilotMaskSummaryV1
    base_sequence_sha256: str
    control_sequence_sha256: str
    candidate_sequence_sha256: str
    control_trainer: FixedTrainerInvocationV1
    trainer: FixedTrainerInvocationV1

    def __post_init__(self) -> None:
        if self.seed != self.wave6.seed:
            raise PublicOodPilotContractError("seed plan and Wave6 binding seed differ")
        for field in ("base_sequence_sha256", "control_sequence_sha256", "candidate_sequence_sha256"):
            _sha(getattr(self, field), field=field)
        if self.control_sequence_sha256 == self.candidate_sequence_sha256:
            raise PublicOodPilotContractError("candidate sequence identity must differ from control mask")
        if self.control_trainer.arm != "control" or self.trainer.arm != "candidate":
            raise PublicOodPilotContractError("seed plan trainer arms are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "wave6": self.wave6.to_dict(),
            "mask": self.mask.to_dict(),
            "base_sequence_sha256": self.base_sequence_sha256,
            "control_sequence_sha256": self.control_sequence_sha256,
            "candidate_sequence_sha256": self.candidate_sequence_sha256,
            "control_trainer": self.control_trainer.to_dict(),
            "trainer": self.trainer.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PublicOodPilotPlanV1:
    schema_version: str
    status: str
    seeds: tuple[PublicOodPilotSeedPlanV1, ...]
    base_sequence_sha256: str
    control_sequence_sha256: str
    common_reference_artifact: str
    common_reference_artifact_sha256: str | None
    common_reference_source_list_sha256: str
    common_reference_source_sha256s: tuple[str, ...]
    promotion_authority: bool
    longrun_allowed: bool
    training_permitted: bool

    def __post_init__(self) -> None:
        if self.schema_version != PILOT_PLAN_SCHEMA_V1 or self.status != "PLAN_ONLY_NOT_EXECUTED":
            raise PublicOodPilotContractError("pilot plan schema/status is invalid")
        if tuple(seed.seed for seed in self.seeds) != (0, 1):
            raise PublicOodPilotContractError("pilot plan must contain seed0 then seed1")
        _sha(self.base_sequence_sha256, field="pilot base sequence SHA-256")
        _sha(self.control_sequence_sha256, field="pilot control sequence SHA-256")
        if any(
            seed.base_sequence_sha256 != self.base_sequence_sha256
            or seed.control_sequence_sha256 != self.control_sequence_sha256
            for seed in self.seeds
        ):
            raise PublicOodPilotContractError("seed plans do not share the fixed base/control identities")
        _sha(self.common_reference_source_list_sha256, field="pilot source_list SHA-256")
        if len(self.common_reference_source_sha256s) != 2:
            raise PublicOodPilotContractError("pilot plan must bind two common-reference source hashes")
        for item in self.common_reference_source_sha256s:
            _sha(item, field="pilot common-reference source SHA-256")
        if self.promotion_authority is not False or self.longrun_allowed is not False or self.training_permitted is not False:
            raise PublicOodPilotContractError("pilot plan unexpectedly grants authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "promotion_authority": self.promotion_authority,
            "longrun_allowed": self.longrun_allowed,
            "training_permitted": self.training_permitted,
            "base_sequence_sha256": self.base_sequence_sha256,
            "control_sequence_sha256": self.control_sequence_sha256,
            "common_reference": {
                "artifact": self.common_reference_artifact,
                "artifact_sha256": self.common_reference_artifact_sha256,
                "source_list_sha256": self.common_reference_source_list_sha256,
                "source_sha256s": list(self.common_reference_source_sha256s),
            },
            "seeds": [seed.to_dict() for seed in self.seeds],
        }


def _candidate_sequence_identity_v1(
    *,
    base_sequence_sha256: str,
    control_sequence_sha256: str,
    seed: int,
    mask: PublicOodMaskContractV1,
) -> str:
    """Hash public mask topology only; hidden/private metadata never enters."""

    rows = [
        {
            "record_id": item.source.record_id,
            "group_id": item.source.group_id,
            "row_index": item.source.row_index,
            "episode_start": item.source.episode_start,
            "bucket_id": item.score.bucket_id,
            "supervision_weight": item.supervision_weight,
        }
        for item in mask.rows
    ]
    return _object_sha({
        "schema": "meta-specialist-v4-public-confidence-ood-candidate-sequence-v1",
        "base_sequence_sha256": base_sequence_sha256,
        "control_sequence_sha256": control_sequence_sha256,
        "seed": seed,
        "rows": rows,
    })


def _validate_binding_set_v1(bindings: tuple[Wave6SeedBindingV1, ...]) -> None:
    if len(bindings) != 2 or tuple(binding.seed for binding in bindings) != (0, 1):
        raise PublicOodPilotContractError("pilot requires exactly two ordered bindings: seed0, seed1")
    for left, right in (
        ("screen_file_sha256", "screen_file_sha256"),
        ("transitions_file_sha256", "transitions_file_sha256"),
        ("init_checkpoint_file_sha256", "init_checkpoint_file_sha256"),
        ("init_checkpoint_tensor_state_sha256", "init_checkpoint_tensor_state_sha256"),
    ):
        if getattr(bindings[0], left) == getattr(bindings[1], right):
            raise PublicOodPilotContractError(f"Wave6 seed bindings must have distinct {left}")


def build_public_ood_pilot_plan_v1(
    *,
    seed_bindings: Sequence[Wave6SeedBindingV1],
    common_reference: Mapping[str, object] | Path | str,
    policy_manifest: Mapping[str, object] | Path | str,
    records_by_seed: Mapping[int, Sequence[SealedPublicTransitionV1]],
    base_sequence_sha256: str,
    control_sequence_sha256: str,
    output_root: Path | str,
    learning_rate: float = 1e-4,
    tbptt_steps: int = 8,
    gradient_clip_norm: float = 1.0,
) -> PublicOodPilotPlanV1:
    """Build a sealed two-seed plan; do not train or evaluate anything."""

    bindings = tuple(seed_bindings)
    if any(type(binding) is not Wave6SeedBindingV1 for binding in bindings):
        raise PublicOodPilotContractError("seed_bindings must contain typed Wave6SeedBindingV1 values")
    _validate_binding_set_v1(bindings)
    base_sha = _sha(base_sequence_sha256, field="base_sequence_sha256")
    control_sha = _sha(control_sequence_sha256, field="control_sequence_sha256")
    if not isinstance(output_root, (str, Path)) or not str(output_root):
        raise PublicOodPilotContractError("output_root is invalid")

    manifest = validate_public_ood_policy_manifest_v1(policy_manifest)
    reference = load_public_ood_reference_bundle_v1(
        common_reference,
        expected_artifact_sha256=(
            manifest.reference_artifact_sha256 if not isinstance(common_reference, Mapping) else None
        ),
        expected_source_list_sha256=manifest.reference_source_list_sha256,
        expected_source_sha256s=manifest.reference_source_sha256s,
    )
    if reference.source_sha256 != manifest.reference_source_list_sha256:
        raise PublicOodPilotContractError("common reference source_list SHA differs from policy manifest")
    if reference.rare_count_threshold != manifest.rare_count_threshold:
        raise PublicOodPilotContractError("common reference rare threshold differs from policy manifest")
    if not isinstance(records_by_seed, Mapping) or set(records_by_seed) != {0, 1}:
        raise PublicOodPilotContractError("records_by_seed must contain exactly seed0 and seed1")
    transition_hashes = tuple(binding.transitions_file_sha256 for binding in bindings)
    if transition_hashes != manifest.reference_source_sha256s:
        raise PublicOodPilotContractError("Wave6 transition SHA order differs from common reference source order")

    root = Path(output_root)
    seed_plans: list[PublicOodPilotSeedPlanV1] = []
    for binding in bindings:
        raw_rows = records_by_seed[binding.seed]
        if type(raw_rows) not in (tuple, list) or not raw_rows:
            raise PublicOodPilotContractError(f"seed{binding.seed} public transition fixture is empty")
        normalized_rows = tuple(
            sealed_public_transition_from_mapping_v1(row) if isinstance(row, Mapping) else row
            for row in raw_rows
        )
        if any(type(row) is not SealedPublicTransitionV1 for row in normalized_rows):
            raise PublicOodPilotContractError("records_by_seed must contain typed sealed public rows")
        mask_contract = build_public_ood_mask_contract_v1(
            normalized_rows, reference=reference, policy_manifest=policy_manifest,
        )
        mask_summary = PublicOodPilotMaskSummaryV1.from_contract(mask_contract)
        candidate_sha = _candidate_sequence_identity_v1(
            base_sequence_sha256=base_sha,
            control_sequence_sha256=control_sha,
            seed=binding.seed,
            mask=mask_contract,
        )
        control_invocation = FixedTrainerInvocationV1(
            seed=binding.seed,
            arm="control",
            output_dir=str(root / f"seed-{binding.seed}" / "control"),
            sequence_order_seed=binding.seed,
            epochs=1,
            patience=0,
            learning_rate=learning_rate,
            tbptt_steps=tbptt_steps,
            gradient_clip_norm=gradient_clip_norm,
        )
        candidate_invocation = FixedTrainerInvocationV1(
            seed=binding.seed,
            arm="candidate",
            output_dir=str(root / f"seed-{binding.seed}" / "candidate"),
            sequence_order_seed=binding.seed,
            epochs=1,
            patience=0,
            learning_rate=learning_rate,
            tbptt_steps=tbptt_steps,
            gradient_clip_norm=gradient_clip_norm,
        )
        seed_plans.append(PublicOodPilotSeedPlanV1(
            seed=binding.seed,
            wave6=binding,
            mask=mask_summary,
            base_sequence_sha256=base_sha,
            control_sequence_sha256=control_sha,
            candidate_sequence_sha256=candidate_sha,
            control_trainer=control_invocation,
            trainer=candidate_invocation,
        ))

    return PublicOodPilotPlanV1(
        schema_version=PILOT_PLAN_SCHEMA_V1,
        status="PLAN_ONLY_NOT_EXECUTED",
        seeds=tuple(seed_plans),
        base_sequence_sha256=base_sha,
        control_sequence_sha256=control_sha,
        common_reference_artifact=manifest.reference_artifact,
        common_reference_artifact_sha256=manifest.reference_artifact_sha256,
        common_reference_source_list_sha256=manifest.reference_source_list_sha256,
        common_reference_source_sha256s=manifest.reference_source_sha256s,
        promotion_authority=False,
        longrun_allowed=False,
        training_permitted=False,
    )


def run_public_ood_pilot_v1(*, execute: bool = False, **kwargs: object) -> PublicOodPilotPlanV1:
    """Expose the future execution seam while refusing actual work today."""

    if execute:
        raise PublicOodPilotContractError(
            "public OOD pilot is plan-only; training/eval execution is not started"
        )
    return build_public_ood_pilot_plan_v1(**kwargs)  # type: ignore[arg-type]


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    raise PublicOodPilotContractError(
        "public OOD pilot has no CLI execution path; use the typed plan API"
    )


__all__ = [
    "FixedTrainerInvocationV1",
    "MASK_SUMMARY_SCHEMA_V1",
    "PILOT_PLAN_SCHEMA_V1",
    "PublicOodPilotContractError",
    "PublicOodPilotMaskSummaryV1",
    "PublicOodPilotPlanV1",
    "PublicOodPilotSeedPlanV1",
    "TRAINER_INVOCATION_SCHEMA_V1",
    "V4_TRAINER_ENTRYPOINT_V1",
    "Wave6SeedBindingV1",
    "build_public_ood_pilot_plan_v1",
    "main",
    "run_public_ood_pilot_v1",
]
