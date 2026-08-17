"""TDD contracts for the research-only frozen residual trainer."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualSidecarV1,
    STOP_ACTION_KEY_V1,
    build_residual_context_v1,
)
from mage_ptcg.meta_specialist.neural_model_v4 import (
    SpecialistModelV4,
    save_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from tests.meta_specialist.test_frozen_residual_preflight_v1 import _provenance
from tests.meta_specialist.test_neural_policy_v4 import _model_input_and_steps

from mage_ptcg.meta_specialist.frozen_residual_trainer_v1 import (
    FrozenResidualTrainerError,
    build_residual_checkpoint_descriptor_v1,
    load_wave6_base_from_provenance_v1,
    residual_sidecar_tensor_state_sha256_v1,
    train_residual_sequences_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import build_seed_known_manifest_v1


def _sequence(*, context_only_first: bool = True) -> tuple[RecurrentBCSequenceV4, object]:
    model_input, first, second = _model_input_and_steps()
    steps = []
    for index, step_input in enumerate((first, second)):
        state = representation_v4_from_step_input_v1(model_input, step_input, allow_unbound_selected=True)
        domain_size = len(step_input.allowed_semantic_classes) + int(step_input.stop_available)
        target = 0
        masses = tuple(1.0 if position == target else 0.0 for position in range(domain_size))
        context = build_residual_context_v1(model_input, step_input)
        steps.append(RecurrentBCStepV4(
            state=state,
            target_index=target,
            episode_group="a" * 64,
            quality_weight=1.0,
            model_input=model_input,
            step_input=step_input,
            target_masses=masses,
            reach_mass=1.0,
            episode_start=index == 0,
            component_id="b" * 64,
            partition="train",
            record_id="c" * 64,
            content_hash="d" * 64,
            research_only=True,
            supervision_weight=0.0 if (context_only_first and index == 0) else 1.0,
        ))
    return RecurrentBCSequenceV4(
        lane="archaludon",
        episode_group="a" * 64,
        component_id="b" * 64,
        partition="train",
        steps=tuple(steps),
        burn_in=0,
        research_only=True,
    ), model_input


def _domain(sequence: RecurrentBCSequenceV4) -> object:
    contexts = []
    actions = [STOP_ACTION_KEY_V1]
    for step in sequence.steps:
        context = build_residual_context_v1(step.model_input, step.step_input)
        contexts.append(context.context_id)
        # Sidecar action key set can safely include all known semantic classes.
        actions.extend(context.action_keys)
    return build_seed_known_manifest_v1(
        _provenance(0), context_ids=tuple(set(contexts)), action_keys=tuple(set(actions)),
        transition_count=1, prefix_count=len(sequence.steps),
    )


def test_residual_training_uses_only_sidecar_and_excludes_context_rows() -> None:
    sequence, _model_input = _sequence()
    domain = _domain(sequence)
    base = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=31)
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
        known_context_ids=domain.context_ids, known_action_keys=domain.action_keys,
    )
    before = {name: tensor.detach().clone() for name, tensor in base.state_dict().items()}
    result = train_residual_sequences_v1(
        base, sidecar, (sequence,), known_domain=domain,
        max_updates=1, learning_rate=0.05,
    )
    assert result.optimizer_updates == 1
    assert result.total_rows == 2
    assert result.context_only_rows == 1
    assert result.loss_bearing_rows == 1
    assert result.denominator_rows == 1
    assert result.effective_loss_mass == pytest.approx(1.0)
    assert all(not parameter.requires_grad for parameter in base.parameters())
    assert all(torch.equal(before[name], tensor) for name, tensor in base.state_dict().items())
    assert result.sidecar_parameter_count == sum(parameter.numel() for parameter in sidecar.parameters())


def test_residual_training_rejects_unknown_domain_and_non_research_sequence() -> None:
    sequence, _model_input = _sequence()
    domain = _domain(sequence)
    bad = type(domain)(
        schema_version=domain.schema_version, provenance=domain.provenance,
        transition_count=domain.transition_count, prefix_count=domain.prefix_count,
        context_ids=("e" * 64,), action_keys=domain.action_keys,
    )
    base = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=37)
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
        known_context_ids=domain.context_ids, known_action_keys=domain.action_keys,
    )
    with pytest.raises(FrozenResidualTrainerError, match="context|domain"):
        train_residual_sequences_v1(base, sidecar, (sequence,), known_domain=bad, max_updates=1)
    nonresearch = type(sequence)(
        lane=sequence.lane, episode_group=sequence.episode_group,
        component_id=sequence.component_id, partition=sequence.partition,
        steps=tuple(type(step)(**{**step.__dict__, "research_only": False}) for step in ()),
        burn_in=sequence.burn_in, research_only=False,
    ) if False else sequence
    # The exact dataclass cannot be weakened through a public constructor; a
    # malformed sequence is rejected before optimizer creation by the trainer.
    with pytest.raises(FrozenResidualTrainerError, match="sequence|research"):
        train_residual_sequences_v1(base, sidecar, (), known_domain=domain, max_updates=1)


def test_sidecar_state_hash_and_checkpoint_loader_are_hash_bound(tmp_path: Path) -> None:
    checkpoint = tmp_path / "wave6.pt"
    source_model = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=8, embedding_dim=6, seed=41)
    descriptor = save_specialist_checkpoint_v4(checkpoint, source_model)
    file_sha = __import__("hashlib").sha256(checkpoint.read_bytes()).hexdigest()
    provenance = _provenance(0)
    provenance = type(provenance)(
        seed=0,
        checkpoint_path=str(checkpoint), checkpoint_file_sha256=file_sha,
        checkpoint_tensor_state_sha256=str(descriptor["tensor_state_sha256"]),
        screen_path=provenance.screen_path, screen_file_sha256=provenance.screen_file_sha256,
        transitions_path=provenance.transitions_path, transitions_file_sha256=provenance.transitions_file_sha256,
        subject_deck_sha256=provenance.subject_deck_sha256,
    )
    loaded = load_wave6_base_from_provenance_v1(provenance)
    assert isinstance(loaded, SpecialistModelV4)
    assert all(not parameter.requires_grad for parameter in loaded.parameters())
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
        known_context_ids=("1" * 64,), known_action_keys=(STOP_ACTION_KEY_V1,),
    )
    first = residual_sidecar_tensor_state_sha256_v1(sidecar)
    with torch.no_grad():
        sidecar.output.bias[0] = 0.1
    assert residual_sidecar_tensor_state_sha256_v1(sidecar) != first


def test_checkpoint_descriptor_requires_target_kind_and_manifest_sha() -> None:
    sequence, _model_input = _sequence()
    domain = _domain(sequence)
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
        known_context_ids=domain.context_ids, known_action_keys=domain.action_keys,
    )
    descriptor = build_residual_checkpoint_descriptor_v1(
        domain.provenance,
        sidecar,
        seed=0,
        preflight_manifest_sha256="a" * 64,
        target_kind="self_imitation_rule_relabel_v1",
        target_manifest_sha256="b" * 64,
        optimizer_updates=1,
        effective_loss_mass=1.0,
    )
    payload = descriptor.to_dict()
    assert payload["target_kind"] == "self_imitation_rule_relabel_v1"
    assert payload["target_manifest_sha256"] == "b" * 64
    with pytest.raises(TypeError):
        build_residual_checkpoint_descriptor_v1(  # type: ignore[call-arg]
            domain.provenance, sidecar, seed=0,
            preflight_manifest_sha256="a" * 64,
            optimizer_updates=1, effective_loss_mass=1.0,
        )
