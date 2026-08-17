"""Tests for Slice L4 deployable neural policy adapter and exporter."""

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    SpecialistModelInputV1,
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.neural_export_v1 import (
    NeuralExportV1Error,
    export_specialist_neural_policy_v1,
)
from mage_ptcg.meta_specialist.neural_policy_v1 import (
    NeuralPolicyV1Error,
    SpecialistNeuralPolicyV1,
    load_specialist_neural_policy_v1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import SemanticRuntimeCompleteActionV2

from tests.meta_specialist.test_training_example_envelope_v2 import _observation


CARD_VOCABULARY_SIZE = 1_400


@pytest.fixture
def test_vocabulary() -> CardVocabularyV1:
    return make_test_card_vocabulary_v1(())


@pytest.fixture
def trained_model(test_vocabulary: CardVocabularyV1) -> SpecialistPolicyModelV1:
    config = SpecialistModelConfigV1(card_vocabulary_size=CARD_VOCABULARY_SIZE)
    return build_specialist_policy_model_v1(config, seed=42)


@pytest.fixture
def sample_fixture(test_vocabulary: CardVocabularyV1):
    state = build_actor_visible_decision_state_v2(_observation())
    extracted = extract_specialist_model_input_v1(state, test_vocabulary)
    step = build_specialist_step_input_v1(extracted, ())
    return extracted.model_input, step


def test_export_and_load_neural_policy(
    trained_model: SpecialistPolicyModelV1,
    test_vocabulary: CardVocabularyV1,
    tmp_path,
) -> None:
    lineage_id = "0" * 64
    exported_bytes = export_specialist_neural_policy_v1(
        model=trained_model,
        lineage_id=lineage_id,
    )
    assert isinstance(exported_bytes, bytes)
    assert len(exported_bytes) > 0

    export_file = tmp_path / "policy.pt"
    export_file.write_bytes(exported_bytes)

    policy = load_specialist_neural_policy_v1(
        exported_bytes=exported_bytes,
        lineage_id=lineage_id,
        card_vocabulary=test_vocabulary,
    )
    assert isinstance(policy, SpecialistDecisionPolicyV2)

    telemetry = policy.policy_telemetry()
    assert isinstance(telemetry, PolicyTelemetrySnapshot)
    assert telemetry.candidate_class == "checkpointed_specialist"
    assert telemetry.model_loaded is True
    assert telemetry.checkpoint_lineage_id == lineage_id
    assert telemetry.checkpoint_lineage_reason is None


def test_load_fails_on_lineage_mismatch(
    trained_model: SpecialistPolicyModelV1,
    test_vocabulary: CardVocabularyV1,
) -> None:
    lineage_id = "a" * 64
    wrong_lineage_id = "b" * 64
    exported_bytes = export_specialist_neural_policy_v1(
        model=trained_model,
        lineage_id=lineage_id,
    )
    with pytest.raises(NeuralPolicyV1Error, match="lineage"):
        load_specialist_neural_policy_v1(
            exported_bytes=exported_bytes,
            lineage_id=wrong_lineage_id,
            card_vocabulary=test_vocabulary,
        )


def test_neural_policy_session_lifecycle(
    trained_model: SpecialistPolicyModelV1,
    test_vocabulary: CardVocabularyV1,
    sample_fixture,
) -> None:
    model_input, step_input = sample_fixture
    lineage_id = "c" * 64
    exported_bytes = export_specialist_neural_policy_v1(
        model=trained_model,
        lineage_id=lineage_id,
    )
    policy = load_specialist_neural_policy_v1(
        exported_bytes=exported_bytes,
        lineage_id=lineage_id,
        card_vocabulary=test_vocabulary,
    )

    session = policy.begin_decision()
    assert isinstance(session, SpecialistDecisionSessionV2)

    semantic_logits, stop_logit = session.step_logits(model_input, step_input)
    assert len(semantic_logits) == len(step_input.allowed_semantic_classes)

    action = SemanticRuntimeCompleteActionV2(
        order_semantics="unordered_set",
        semantic_selection=(step_input.allowed_semantic_classes[0].semantic_row,),
    )

    outcome = CommittedSemanticDecisionV2(
        semantic_action=action,
        semantic_log_probability=-0.5,
        next_recurrent_state_token=None,
    )
    session.commit(outcome)

    policy.reset()


def test_both_loaders_clamp_inference_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unclamped loader oversubscribes the host once several actors run.

    Measured before this clamp reached the checkpoint loader: 12 concurrent
    workers on a 28-core host faulted 79% of games (AGENT_ERROR from blowing the
    engine's per-decision budget), against 4% at 2 workers.
    """
    from mage_ptcg.meta_specialist import neural_policy_v1

    torch.set_num_threads(8)
    neural_policy_v1._clamp_inference_threads_v1()
    assert torch.get_num_threads() <= neural_policy_v1._MAX_INFERENCE_THREADS_V1

    # And it never raises the count on a host already below the cap.
    torch.set_num_threads(1)
    neural_policy_v1._clamp_inference_threads_v1()
    assert torch.get_num_threads() == 1


def test_the_checkpoint_loader_clamps_before_running_a_forward() -> None:
    """The actor pool reaches inference through the checkpoint loader."""
    import inspect

    from mage_ptcg.meta_specialist import neural_policy_v1

    source = inspect.getsource(
        neural_policy_v1.load_specialist_neural_policy_from_checkpoint_v1
    )
    assert "_clamp_inference_threads_v1()" in source
