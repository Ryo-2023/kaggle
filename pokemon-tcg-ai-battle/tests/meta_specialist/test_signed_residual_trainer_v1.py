"""TDD contracts for the signed cross-fitted residual trainer."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest
import torch

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import (
    materialize_signed_outcome_targets_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    build_seed_known_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualSidecarV1,
    STOP_ACTION_KEY_V1,
    build_residual_context_v1,
)
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from tests.meta_specialist.test_build_cross_fitted_outcome_residual_manifest import _write_screen
from tests.meta_specialist.test_cross_fitted_outcome_materializer_v1 import _domain, _manifest

from mage_ptcg.meta_specialist.signed_residual_trainer_v1 import (
    SignedResidualTrainerError,
    train_signed_outcome_materialization_v1,
)


def _tensor_sha(model: SpecialistModelV4) -> str:
    digest = hashlib.sha256(b"mage_ptcg:specialist-neural-state:v4\0")
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous()
        raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(value.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(value.shape)).encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _fixture(tmp_path: Path):
    source = tmp_path / "screen.transitions.jsonl"
    manifest_path = tmp_path / "targets.json"
    _write_screen(source)
    manifest_sha = _manifest(source, manifest_path)
    initial_domain = _domain(source)
    base = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=101)
    provenance = replace(
        initial_domain.provenance,
        checkpoint_tensor_state_sha256=_tensor_sha(base),
    )
    materialization = materialize_signed_outcome_targets_v1(
        manifest_path, expected_manifest_sha256=manifest_sha,
        known_domain=build_seed_known_manifest_v1(
            provenance,
            context_ids=initial_domain.context_ids,
            action_keys=initial_domain.action_keys,
            transition_count=initial_domain.transition_count,
            prefix_count=initial_domain.prefix_count,
        ),
    )
    contexts = []
    action_keys = [STOP_ACTION_KEY_V1]
    for sequence in materialization.sequences:
        for step in sequence.steps:
            context = build_residual_context_v1(step.model_input, step.step_input)
            contexts.append(context.context_id)
            action_keys.extend(context.action_keys)
    domain = build_seed_known_manifest_v1(
        provenance,
        context_ids=contexts,
        action_keys=action_keys,
        transition_count=initial_domain.transition_count,
        prefix_count=sum(len(sequence.steps) for sequence in materialization.sequences),
    )
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16,
        action_feature_dim=8,
        hidden_dim=8,
        known_context_ids=domain.context_ids,
        known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=provenance.checkpoint_tensor_state_sha256,
    )
    return base, provenance, domain, sidecar, materialization


def test_signed_trainer_uses_context_forwards_and_sidecar_only_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base, provenance, domain, sidecar, materialization = _fixture(tmp_path)
    before = {name: tensor.detach().clone() for name, tensor in base.state_dict().items()}
    call_count = 0
    original = base.forward_record_group_v4

    def traced(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(base, "forward_record_group_v4", traced)
    result = train_signed_outcome_materialization_v1(
        base,
        provenance,
        sidecar,
        materialization,
        known_domain=domain,
        max_updates=len(materialization.sequences),
        learning_rate=0.05,
    )

    expected_groups = sum(len({step.record_id for step in sequence.steps}) for sequence in materialization.sequences)
    assert call_count == expected_groups
    assert result.optimizer_updates == len(materialization.sequences)
    assert result.context_only_rows == sum(len(sequence.steps) for sequence in materialization.sequences)
    assert result.signed_loss_rows + result.zero_weight_rows == len(materialization.prefix_targets)
    assert result.positive_effective_mass > 0.0
    assert result.negative_effective_mass > 0.0
    assert result.loss_normalizer == pytest.approx(result.positive_effective_mass + result.negative_effective_mass)
    assert result.target_kind == "signed_behavior_log_probability"
    assert result.target_manifest_file_sha256 == materialization.target_manifest_file_sha256
    assert result.source_episode_sha256 == materialization.source_episode_sha256
    assert all(not parameter.requires_grad and parameter.grad is None for parameter in base.parameters())
    assert all(torch.equal(before[name], tensor) for name, tensor in base.state_dict().items())
    assert result.base_tensor_state_sha256_before == result.base_tensor_state_sha256_after == provenance.checkpoint_tensor_state_sha256
    assert result.training_permitted is False
    assert result.promotion_authority is False
    assert result.longrun_allowed is False


def test_signed_trainer_rejects_unbound_base_or_non_signed_target(tmp_path: Path) -> None:
    base, provenance, domain, sidecar, materialization = _fixture(tmp_path)
    bad_provenance = replace(provenance, checkpoint_tensor_state_sha256="f" * 64)
    with pytest.raises(SignedResidualTrainerError, match="tensor|base|provenance"):
        train_signed_outcome_materialization_v1(
            base, bad_provenance, sidecar, materialization, known_domain=domain,
        )

    wrong_target = materialization.prefix_targets[0]
    object.__setattr__(wrong_target, "target_kind", "teacher_hard_selection")
    bad_materialization = replace(
        materialization,
        prefix_targets=(wrong_target, *materialization.prefix_targets[1:]),
    )
    with pytest.raises(SignedResidualTrainerError, match="target kind|signed"):
        train_signed_outcome_materialization_v1(
            base, provenance, sidecar, bad_materialization, known_domain=domain,
        )
