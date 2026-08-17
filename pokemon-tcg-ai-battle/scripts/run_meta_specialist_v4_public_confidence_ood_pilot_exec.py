#!/usr/bin/env python3
"""研究専用 public confidence/OOD pilot の最小 executor 契約。

このファイルは、sealed transition を canonical V4 sequence へ変換し、
同じ完全 episode を control/candidate の双方へ渡すための境界を提供する。
実学習・CABT評価はデフォルトでは開始せず、production actor pool、提出経路、
promotion authority も持たない。teacher は明示的に research-only provenance
を渡された場合にだけ relabel に使える。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.dagger_v4 import (
    merge_dagger_episode_sequences_v4,
    parse_transition_payload_v4,
    relabel_transition_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    selected_objective_sha256_v4,
    train_recurrent_bc_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, load_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PublicBucketReferenceV1,
    PublicEligibilityPolicyV1,
    score_public_step_v1,
)
from mage_ptcg.meta_specialist.actor_pool_v1 import build_rule_agent_policy_factory_v1
from scripts.run_meta_specialist_v4_public_confidence_ood_pilot import Wave6SeedBindingV1


TEACHER_PROVENANCE_SCHEMA_V1 = "meta-specialist-v4-public-ood-teacher-provenance-v1"
TRANSITION_SCHEMA_V1 = "meta-specialist-v4-dagger-transition-v1"
_HEX64 = frozenset("0123456789abcdef")


class PublicOodPilotExecutionError(ValueError):
    """Raised when the executor cannot prove a closed research boundary."""


def replay_public_mask_for_rows_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    model: SpecialistModelV4,
    reference: PublicBucketReferenceV1,
    policy: PublicEligibilityPolicyV1,
    device: str = "cpu",
) -> dict[tuple[str, int], tuple[bool, ...]]:
    """Recompute the public eligibility mask in sealed recurrent order.

    Game/transition identifiers are used only to reset and order the GRU;
    they never enter ``score_public_step_v1`` or the returned feature payload.
    """

    if type(model) is not SpecialistModelV4:
        raise PublicOodPilotExecutionError("mask replay requires exact SpecialistModelV4")
    if type(reference) is not PublicBucketReferenceV1 or type(policy) is not PublicEligibilityPolicyV1:
        raise PublicOodPilotExecutionError("mask replay reference/policy types are invalid")
    if not rows:
        raise PublicOodPilotExecutionError("mask replay rows are empty")
    model.eval()
    hidden: torch.Tensor | None = None
    previous_game: object = None
    output: dict[tuple[str, int], tuple[bool, ...]] = {}
    with torch.inference_mode():
        for row in rows:
            game_id = row.get("game_id")
            transition_index = row.get("transition_index")
            transition = row.get("parsed_transition")
            if type(game_id) is not str or type(transition_index) is not int or transition is None:
                raise PublicOodPilotExecutionError("mask replay row identity is invalid")
            states = tuple(
                representation_v4_from_step_input_v1(
                    transition.model_input, prefix.step_input, allow_unbound_selected=True,
                )
                for prefix in transition.prefix_steps
            )
            outputs = model.forward_record_group_v4(
                states,
                hidden_state=hidden,
                episode_start=(previous_game != game_id),
            )
            next_hidden = outputs[-1].hidden_state
            hidden = next_hidden.detach() if next_hidden is not None else None
            previous_game = game_id
            eligible: list[bool] = []
            for prefix, record_output in zip(transition.prefix_steps, outputs, strict=True):
                semantic_logits = tuple(float(value) for value in record_output.logits.detach().cpu().tolist())
                stop_logit = None
                if prefix.step_input.stop_available:
                    stop_logit = float((model.stop_vector @ record_output.global_token + model.stop_bias).detach().cpu().item())
                logits = SpecialistStepLogitsV1(semantic_logits=semantic_logits, stop_logit=stop_logit)
                score = score_public_step_v1(
                    transition.model_input,
                    prefix.step_input,
                    logits,
                    chosen_semantic_action=prefix.chosen_semantic_action,
                    chosen_is_stop=prefix.chosen_is_stop,
                    reference=reference,
                    policy=policy,
                )
                eligible.append(bool(score.eligible))
            output[(game_id, transition_index)] = tuple(eligible)
    return output


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _HEX64 for c in value):
        raise PublicOodPilotExecutionError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sealed_wave6_transition_rows_v1(
    path: Path | str,
    *,
    binding: Wave6SeedBindingV1,
    partition: str,
) -> tuple[dict[str, object], ...]:
    """Read a hash-bound canonical transition sidecar for one seed."""

    if type(binding) is not Wave6SeedBindingV1:
        raise PublicOodPilotExecutionError("binding must be Wave6SeedBindingV1")
    if partition not in {"train", "validation"}:
        raise PublicOodPilotExecutionError("partition is invalid")
    resolved = Path(path)
    if not resolved.is_file() or resolved.is_symlink():
        raise PublicOodPilotExecutionError("transition sidecar must be a regular file")
    expected = _sha(binding.transitions_file_sha256, field="transitions_file_sha256")
    actual = _file_sha(resolved)
    if actual != expected:
        raise PublicOodPilotExecutionError("transition sidecar SHA does not match binding")
    output: list[dict[str, object]] = []
    for line_no, raw in enumerate(resolved.read_bytes().splitlines(), start=1):
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicOodPilotExecutionError(f"transition line {line_no} is invalid JSON") from exc
        if type(value) is not dict:
            raise PublicOodPilotExecutionError("transition row must be an object")
        expected_keys = {
            "schema", "game_id", "episode_group", "component_id", "partition",
            "opponent_id", "seat", "env_seed", "transition_index", "transition",
        }
        if set(value) != expected_keys or value.get("schema") != TRANSITION_SCHEMA_V1:
            raise PublicOodPilotExecutionError("transition row has an open schema")
        for field in ("game_id", "episode_group", "component_id"):
            _sha(value.get(field), field=field)
        if value.get("partition") != partition:
            continue
        if type(value.get("transition_index")) is not int or value["transition_index"] < 0:
            raise PublicOodPilotExecutionError("transition_index is invalid")
        try:
            parsed = parse_transition_payload_v4(value["transition"])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise PublicOodPilotExecutionError(f"transition line {line_no} failed canonical parse") from exc
        if parsed.to_dict() != value["transition"]:
            raise PublicOodPilotExecutionError("transition payload is not canonical")
        output.append({**value, "parsed_transition": parsed})
    if not output:
        raise PublicOodPilotExecutionError("selected partition contains no transitions")
    # The sidecar is expected to be physically ordered by game and transition.
    for game_id, rows in _group_rows(output).items():
        indices = [int(row["transition_index"]) for row in rows]
        if indices != list(range(len(indices))):
            raise PublicOodPilotExecutionError(f"transition indices are not contiguous for game {game_id}")
    return tuple(output)


def _group_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        game_id = row.get("game_id")
        if type(game_id) is not str:
            raise PublicOodPilotExecutionError("game_id is invalid")
        grouped[game_id].append(row)
    return grouped


def _validate_teacher_provenance(value: Mapping[str, object]) -> str:
    expected = {"schema_version", "kind", "scope", "policy_identity", "promotion_authority"}
    if set(value) != expected or value.get("schema_version") != TEACHER_PROVENANCE_SCHEMA_V1:
        raise PublicOodPilotExecutionError("teacher provenance schema is not closed")
    if value.get("kind") != "rule_teacher" or value.get("scope") != "research-only":
        raise PublicOodPilotExecutionError("teacher provenance must be explicit research-only")
    if value.get("promotion_authority") is not False:
        raise PublicOodPilotExecutionError("teacher provenance grants promotion authority")
    return _sha(value.get("policy_identity"), field="teacher policy_identity")


def _fixture_logits_for_step(_model_input: object, step_input: object) -> SpecialistStepLogitsV1:
    """Deterministic fixture logits used by the contract tests only."""

    allowed = tuple(getattr(step_input, "allowed_semantic_classes", ()))
    stop = 0.0 if bool(getattr(step_input, "stop_available", False)) else None
    return SpecialistStepLogitsV1(semantic_logits=tuple(0.0 for _ in allowed), stop_logit=stop)


def _topology_sha(sequences: Sequence[RecurrentBCSequenceV4]) -> str:
    digest = hashlib.sha256(b"meta-specialist-public-ood-topology-v1\0")
    for sequence in sequences:
        digest.update(repr((sequence.partition, sequence.episode_group, sequence.component_id)).encode())
        for step in sequence.steps:
            digest.update(repr((step.record_id, step.content_hash, step.episode_start, repr(step.state))).encode())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class MaskedEpisodeMaterialV1:
    control_sequences: tuple[RecurrentBCSequenceV4, ...]
    candidate_sequences: tuple[RecurrentBCSequenceV4, ...]
    transition_count: int
    prefix_count: int
    eligible_prefix_count: int
    context_only_prefix_count: int
    effective_loss_mass: float
    control_topology_sha256: str
    candidate_topology_sha256: str
    control_sequence_sha256: str
    candidate_sequence_sha256: str


def build_masked_episode_material_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    eligible_by_transition: Mapping[tuple[str, int], Sequence[bool]],
    teacher_factory: object,
    teacher_provenance: Mapping[str, object],
    lane: str,
) -> MaskedEpisodeMaterialV1:
    """Relabel complete games and apply only the supplied public prefix mask."""

    if not rows or not callable(getattr(teacher_factory, "new_policy", None)):
        raise PublicOodPilotExecutionError("rows/teacher_factory are invalid")
    policy_identity = _validate_teacher_provenance(teacher_provenance)
    grouped_all = _group_rows(rows)
    # The trainer requires every sequence to contain at least one supervised
    # post-burn-in row.  Keep complete episodes for games selected by the
    # public mask, but do not materialize all-context-only games: they would
    # be valid GRU context in a mixed episode, yet invalid standalone trainer
    # sequences and fail with a misleading zero-loss error.
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for game_id, game_rows in grouped_all.items():
        has_eligible = False
        for row in game_rows:
            transition = row.get("parsed_transition")
            if transition is None:
                transition = parse_transition_payload_v4(row.get("transition"))
            key = (game_id, int(row["transition_index"]))
            if key not in eligible_by_transition:
                raise PublicOodPilotExecutionError("public prefix mask is missing a transition")
            mask = tuple(bool(value) for value in eligible_by_transition[key])
            if len(mask) != len(transition.prefix_steps):
                raise PublicOodPilotExecutionError("public prefix mask length differs from transition")
            has_eligible = has_eligible or any(mask)
        if has_eligible:
            grouped[game_id] = game_rows
    if not grouped:
        raise PublicOodPilotExecutionError("public mask selected no complete episode")
    control: list[RecurrentBCSequenceV4] = []
    candidate: list[RecurrentBCSequenceV4] = []
    eligible_count = 0
    prefix_count = 0
    for game_id in sorted(grouped):
        game_rows = sorted(grouped[game_id], key=lambda row: int(row["transition_index"]))
        relabelled: list[RecurrentBCSequenceV4] = []
        for row in game_rows:
            transition = row.get("parsed_transition")
            if transition is None:
                transition = parse_transition_payload_v4(row.get("transition"))
            sequence = relabel_transition_v4(
                transition,
                teacher_factory=teacher_factory,
                policy_version=policy_identity,
                lane=lane,
                episode_group=str(row["episode_group"]),
                component_id=str(row["component_id"]),
                partition=str(row["partition"]),
            )
            mask = tuple(eligible_by_transition.get((game_id, int(row["transition_index"])), ()))
            if len(mask) != len(sequence.steps):
                raise PublicOodPilotExecutionError("public prefix mask length differs from transition")
            relabelled.append(sequence)
            prefix_count += len(sequence.steps)
            eligible_count += sum(bool(value) for value in mask)
        merged_control = merge_dagger_episode_sequences_v4(tuple(relabelled))
        merged_candidate = replace(
            merged_control,
            steps=tuple(
                replace(
                    step,
                    supervision_weight=1.0 if bool(eligible_by_transition[(game_id, int(row["transition_index"]))][prefix_index]) else 0.0,
                )
                for row in game_rows
                for prefix_index, step in enumerate(
                    relabelled[game_rows.index(row)].steps
                )
            ),
        )
        # The comprehension above intentionally follows the same physical
        # row/prefix order as the merged sequences; normalize the episode flag.
        merged_candidate = replace(
            merged_candidate,
            steps=tuple(replace(step, episode_start=index == 0) for index, step in enumerate(merged_candidate.steps)),
        )
        control.append(merged_control)
        candidate.append(merged_candidate)
    context_count = prefix_count - eligible_count
    return MaskedEpisodeMaterialV1(
        control_sequences=tuple(control),
        candidate_sequences=tuple(candidate),
        transition_count=sum(len(game_rows) for game_rows in grouped.values()),
        prefix_count=prefix_count,
        eligible_prefix_count=eligible_count,
        context_only_prefix_count=context_count,
        effective_loss_mass=float(eligible_count),
        control_topology_sha256=_topology_sha(control),
        candidate_topology_sha256=_topology_sha(candidate),
        control_sequence_sha256=selected_objective_sha256_v4(tuple(control)),
        candidate_sequence_sha256=selected_objective_sha256_v4(tuple(candidate)),
    )


def build_pilot_execution_report_v1(
    *,
    seed: int,
    binding: Wave6SeedBindingV1,
    rows: Sequence[Mapping[str, object]],
    material: MaskedEpisodeMaterialV1 | None,
    common_reference_artifact: str,
    common_reference_artifact_sha256: str,
    common_reference_source_list_sha256: str,
    policy_manifest: Mapping[str, object],
    execute: bool = False,
) -> dict[str, object]:
    """Build a dry-run report; actual trainer invocation is intentionally absent."""

    if execute:
        raise PublicOodPilotExecutionError("execution is not enabled in the contract report builder")
    _sha(common_reference_artifact_sha256, field="common_reference_artifact_sha256")
    _sha(common_reference_source_list_sha256, field="common_reference_source_list_sha256")
    if type(binding) is not Wave6SeedBindingV1:
        raise PublicOodPilotExecutionError("binding is invalid")
    return {
        "schema_version": "meta-specialist-v4-public-confidence-ood-pilot-execution-report-v1",
        "seed": seed,
        "execution": "DRY_RUN_NOT_EXECUTED",
        "training_started": False,
        "cabt_eval_started": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "wave6": binding.to_dict(),
        "transition_count": len(rows),
        "material": None if material is None else {
            "prefix_count": material.prefix_count,
            "eligible_prefix_count": material.eligible_prefix_count,
            "context_only_prefix_count": material.context_only_prefix_count,
            "effective_loss_mass": material.effective_loss_mass,
            "control_sequence_sha256": material.control_sequence_sha256,
            "candidate_sequence_sha256": material.candidate_sequence_sha256,
        },
        "common_reference": {
            "artifact": common_reference_artifact,
            "artifact_sha256": common_reference_artifact_sha256,
            "source_list_sha256": common_reference_source_list_sha256,
        },
        "policy_manifest_schema": policy_manifest.get("schema_version"),
    }


def execute_public_ood_pilot_seed_v1(
    *,
    seed: int,
    binding: Wave6SeedBindingV1,
    train_rows: Sequence[Mapping[str, object]],
    validation_rows: Sequence[Mapping[str, object]],
    model_config: Mapping[str, int],
    init_checkpoint: Path | str,
    init_file_sha256: str,
    init_tensor_state_sha256: str,
    reference: PublicBucketReferenceV1,
    policy: PublicEligibilityPolicyV1,
    teacher_provenance: Mapping[str, object],
    output_root: Path | str,
    device: str = "cuda:0",
    torch_threads: int = 2,
    execute: bool = False,
) -> dict[str, object]:
    """Run one explicit, fixed one-epoch control/candidate research seed.

    This function is intentionally opt-in.  It does not run CABT and does not
    assign promotion authority.  Control and candidate use identical complete
    episodes/teacher targets; only candidate supervision masks differ.
    """

    if not execute:
        raise PublicOodPilotExecutionError("pilot execution requires explicit execute=True")
    if seed not in {0, 1} or binding.seed != seed:
        raise PublicOodPilotExecutionError("seed/binding mismatch")
    if type(model_config) is not dict or set(model_config) != {"card_vocabulary_size", "hidden_dim", "embedding_dim"}:
        raise PublicOodPilotExecutionError("model_config must be closed")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise PublicOodPilotExecutionError("requested CUDA device is unavailable")
    if type(torch_threads) is not int or torch_threads < 1:
        raise PublicOodPilotExecutionError("torch_threads must be positive")
    torch.set_num_threads(torch_threads)
    checkpoint = Path(init_checkpoint)
    if not checkpoint.is_file() or _file_sha(checkpoint) != _sha(init_file_sha256, field="init_file_sha256"):
        raise PublicOodPilotExecutionError("init checkpoint file SHA mismatch")
    init_tensor_sha = _sha(init_tensor_state_sha256, field="init_tensor_state_sha256")
    model = SpecialistModelV4(**{key: int(value) for key, value in model_config.items()}).to(device)
    load_specialist_checkpoint_v4(
        checkpoint, model,
        expected_file_sha256=init_file_sha256,
        expected_tensor_state_sha256=init_tensor_sha,
    )
    train_mask = replay_public_mask_for_rows_v1(
        train_rows, model=model, reference=reference, policy=policy, device=device,
    )
    validation_mask = replay_public_mask_for_rows_v1(
        validation_rows, model=model, reference=reference, policy=policy, device=device,
    )
    teacher_factory, teacher_identity = build_rule_agent_policy_factory_v1()
    provenance = dict(teacher_provenance)
    if provenance.get("policy_identity") != teacher_identity:
        raise PublicOodPilotExecutionError("teacher provenance identity differs from live teacher")
    train_material = build_masked_episode_material_v1(
        train_rows, eligible_by_transition=train_mask,
        teacher_factory=teacher_factory, teacher_provenance=provenance, lane="archaludon",
    )
    validation_material = build_masked_episode_material_v1(
        validation_rows, eligible_by_transition=validation_mask,
        teacher_factory=teacher_factory, teacher_provenance=provenance, lane="archaludon",
    )
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    arm_results: dict[str, object] = {}
    for arm, train_sequences, validation_sequences in (
        ("control", train_material.control_sequences, validation_material.control_sequences),
        ("candidate", train_material.candidate_sequences, validation_material.candidate_sequences),
    ):
        arm_dir = root / f"seed-{seed}" / arm
        arm_model = SpecialistModelV4(**{key: int(value) for key, value in model_config.items()}).to(device)
        load_specialist_checkpoint_v4(
            checkpoint, arm_model,
            expected_file_sha256=init_file_sha256,
            expected_tensor_state_sha256=init_tensor_sha,
        )
        result = train_recurrent_bc_v4(
            arm_model,
            tuple(train_sequences),
            tuple(validation_sequences),
            mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
            output_dir=arm_dir,
            sequence_order_seed=seed,
            epochs=1,
            patience=0,
            learning_rate=1.0e-4,
            tbptt_steps=8,
            gradient_clip_norm=1.0,
            run_config={
                "research_only": True,
                "promotion_authority": False,
                "longrun_allowed": False,
                "arm": arm,
                "seed": seed,
                "teacher_policy_identity": teacher_identity,
                "mask_semantics": "candidate eligible-only; control all rows; context-only advances GRU",
                "train_sequence_sha256": selected_objective_sha256_v4(tuple(train_sequences)),
                "validation_sequence_sha256": selected_objective_sha256_v4(tuple(validation_sequences)),
            },
        )
        last_path = result.last_checkpoint_path or (arm_dir / "last-recurrent-bc-v4.pt")
        arm_results[arm] = {
            "train_sequence_sha256": selected_objective_sha256_v4(tuple(train_sequences)),
            "validation_sequence_sha256": selected_objective_sha256_v4(tuple(validation_sequences)),
            "best_checkpoint_path": str(result.best_checkpoint_path),
            "last_checkpoint_path": str(last_path),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "last_checkpoint_file_sha256": _file_sha(Path(last_path)) if Path(last_path).is_file() else None,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "last_validation_complete_action_nll": result.history[-1].get("validation_complete_action_nll") if result.history else None,
            "epochs_completed": result.epochs_completed,
            "optimizer_updates_completed": result.optimizer_updates_completed,
            "elapsed_seconds": result.elapsed_seconds,
        }
    report = {
        "schema_version": "meta-specialist-v4-public-confidence-ood-pilot-execution-v1",
        "status": "RESEARCH_ONLY_COMPLETE",
        "seed": seed,
        "promotion_authority": False,
        "longrun_allowed": False,
        "cabt_eval_started": False,
        "wave6": binding.to_dict(),
        "teacher_provenance": provenance,
        "model_config": dict(model_config),
        "mask": {
            "train": {
                "transition_count": train_material.transition_count,
                "prefix_count": train_material.prefix_count,
                "eligible_prefix_count": train_material.eligible_prefix_count,
                "context_only_prefix_count": train_material.context_only_prefix_count,
                "effective_loss_mass": train_material.effective_loss_mass,
            },
            "validation": {
                "transition_count": validation_material.transition_count,
                "prefix_count": validation_material.prefix_count,
                "eligible_prefix_count": validation_material.eligible_prefix_count,
                "context_only_prefix_count": validation_material.context_only_prefix_count,
                "effective_loss_mass": validation_material.effective_loss_mass,
            },
        },
        "arms": arm_results,
    }
    report_path = root / f"seed-{seed}" / "pilot-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    raise PublicOodPilotExecutionError("CLI execution is disabled; use explicit research API after review")


__all__ = [
    "MaskedEpisodeMaterialV1",
    "PublicOodPilotExecutionError",
    "TEACHER_PROVENANCE_SCHEMA_V1",
    "build_masked_episode_material_v1",
    "build_pilot_execution_report_v1",
    "execute_public_ood_pilot_seed_v1",
    "load_sealed_wave6_transition_rows_v1",
    "replay_public_mask_for_rows_v1",
    "main",
    "_fixture_logits_for_step",
]
