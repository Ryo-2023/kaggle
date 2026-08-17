"""研究専用V5 SetContext recurrent BC の最小契約テスト。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist import neural_model_v4 as neural_v4
from mage_ptcg.meta_specialist.neural_model_v5 import SpecialistModelV5
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (
    RESEARCH_ONLY_UNIFORM_WEIGHT,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import RecurrentBCStepV4
from mage_ptcg.meta_specialist.recurrent_bc_v4 import RecurrentBCSequenceV4
from mage_ptcg.meta_specialist.recurrent_bc_v5 import (
    _complete_action_nll_v5,
    train_recurrent_bc_v5,
)
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
)


def _state(index: int) -> RelationalStateV4:
    ref = PublicEntityClassRefV4.actor_visible(1, "hand", 9 + index)
    entity = EntityTokenV4(index + 1, 6, 1, 9, 9 + index, None, (), (), (), ref)
    candidate = ActionCandidateV4(
        f"semantic-{index}", 3, ref, None, None, (1,), (), 1, (), False, 0, ref,
    )
    return RelationalStateV4((float(index),), (entity,), (candidate,))


def _sequence(*, group: str, component: str, partition: str) -> RecurrentBCSequenceV4:
    steps = tuple(
        RecurrentBCStepV4(
            state=_state(index), target_index=0, episode_group=group,
            quality_weight=1.0, model_input=object(),
            step_input=SimpleNamespace(stop_available=True),
            target_masses=(0.8, 0.2), reach_mass=1.0, episode_start=index == 0,
            component_id=component, partition=partition,
            record_id=f"{index + (0 if partition == 'train' else 8):064x}",
            content_hash=f"{index + 20:064x}", research_only=True,
        )
        for index in range(2)
    )
    return RecurrentBCSequenceV4(
        "fixture", group, component, partition, steps, burn_in=0, research_only=True,
    )


def _base_provenance() -> dict[str, object]:
    return {
        "path": "/tmp/base-v4.pt",
        "file_sha256": "0" * 64,
        "tensor_state_sha256": "1" * 64,
        "checkpoint_schema": neural_v4.CHECKPOINT_SCHEMA_V4,
    }


def test_v5_tiny_training_updates_set_head_and_records_v4_transfer(tmp_path) -> None:
    model = SpecialistModelV5(
        card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=41,
    )
    before = model.candidate_residual_head[-1].weight.detach().clone()
    result = train_recurrent_bc_v5(
        model,
        (_sequence(group="train", component="train-component", partition="train"),),
        (_sequence(group="validation", component="validation-component", partition="validation"),),
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=tmp_path,
        sequence_order_seed=41,
        epochs=1,
        patience=0,
        learning_rate=1.0e-2,
        tbptt_steps=1,
        base_provenance=_base_provenance(),
    )
    after = model.candidate_residual_head[-1].weight.detach()

    assert result.optimizer_updates_completed == 1
    assert not torch.allclose(before, after)
    assert result.best_checkpoint_path.name == "best-recurrent-bc-v5.pt"

    resume_payload = torch.load(result.last_checkpoint_path, map_location="cpu", weights_only=False)
    assert resume_payload["run_config"]["v4_base_provenance"] == _base_provenance()
    checkpoint_payload = torch.load(result.best_checkpoint_path, map_location="cpu", weights_only=False)
    assert checkpoint_payload["descriptor"]["base_provenance"] == _base_provenance()


def test_v5_complete_action_nll_keeps_stop_on_base_global_token() -> None:
    model = SpecialistModelV5(
        card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=43,
    ).eval()
    state = _state(0)
    step = _sequence(group="one", component="one-component", partition="validation").steps[0]
    output = model.forward_v5(state)
    stop = model.stop_vector @ output.global_token + model.stop_bias
    expected = torch.cat((output.logits, stop.reshape(1)))
    nll = _complete_action_nll_v5(model, step, output)
    manual = -(torch.tensor(step.target_masses) * torch.nn.functional.log_softmax(expected, dim=0)).sum()
    assert torch.allclose(nll, manual)


def test_v5_trainer_rejects_missing_transfer_provenance(tmp_path) -> None:
    model = SpecialistModelV5(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=47)
    with pytest.raises(ValueError, match="base_provenance"):
        train_recurrent_bc_v5(
            model,
            (_sequence(group="train", component="train-component", partition="train"),),
            (_sequence(group="validation", component="validation-component", partition="validation"),),
            mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
            output_dir=tmp_path,
            sequence_order_seed=47,
            epochs=1,
            base_provenance=None,
        )
