"""契約先行の研究専用V5 SetContext policy adapter tests。"""

from __future__ import annotations

import hashlib

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.neural_model_v5 import (
    SpecialistModelV5,
    transfer_specialist_checkpoint_v4_to_v5,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import SemanticRuntimeCompleteActionV2


def _observation() -> dict[str, object]:
    hand = [{"id": 101, "serial": 1001, "playerIndex": 0}, {"id": 102, "serial": 1002, "playerIndex": 0}]
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": hand, "handCount": 2,
        "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }
    opponent = {**player, "hand": None, "handCount": 0}
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player, opponent], "result": -1, "retreated": False,
            "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
            "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 2, "minCount": 0,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _model_input_and_steps():
    state = build_actor_visible_decision_state_v2(_observation())
    model_input = extract_specialist_model_input_v1(
        state, make_test_card_vocabulary_v1(range(1, 1_000)),
    )
    first = build_specialist_step_input_v1(model_input, ())
    prefix = (next(iter(model_input.local_action_id_to_candidate_row_index)),)
    second = build_specialist_step_input_v1(model_input, prefix)
    return model_input.model_input, first, second


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v5_model(tmp_path):
    base = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=101).eval()
    base_path = tmp_path / "base-v4.pt"
    base_descriptor = save_specialist_checkpoint_v4(base_path, base)
    v5_path = tmp_path / "sidecar-v5.pt"
    v5_descriptor = transfer_specialist_checkpoint_v4_to_v5(
        base_path,
        v5_path,
        expected_base_file_sha256=_sha256(base_path),
        expected_base_tensor_state_sha256=base_descriptor["tensor_state_sha256"],
        head_seed=103,
    )
    model = SpecialistModelV5(
        card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=107,
    ).eval()
    from mage_ptcg.meta_specialist.neural_model_v5 import load_specialist_checkpoint_v5

    load_specialist_checkpoint_v5(
        v5_path,
        model,
        expected_file_sha256=_sha256(v5_path),
        expected_tensor_state_sha256=v5_descriptor["tensor_state_sha256"],
    )
    return model, v5_path


def _policy(model: SpecialistModelV5):
    from mage_ptcg.meta_specialist.neural_policy_v5 import SpecialistNeuralPolicyV5

    return SpecialistNeuralPolicyV5(
        model,
        policy_identity="f" * 64,
        checkpoint_lineage_id="a" * 64,
    )


def test_v5_policy_satisfies_runtime_protocol_and_uses_fresh_factory(tmp_path) -> None:
    from mage_ptcg.meta_specialist.neural_policy_v5 import SpecialistNeuralPolicyV5Factory

    model, _path = _v5_model(tmp_path)
    policy = _policy(model)
    assert isinstance(policy, SpecialistDecisionPolicyV2)
    assert isinstance(policy.begin_decision(), SpecialistDecisionSessionV2)
    factory = SpecialistNeuralPolicyV5Factory(policy)
    first, second = factory.new_policy(), factory.new_policy()
    assert first is not second
    assert isinstance(first, SpecialistDecisionPolicyV2)
    assert first.policy_telemetry().policy_identity == "f" * 64


def test_v5_session_scores_prefixes_with_one_gru_and_reuses_state_cache(tmp_path) -> None:
    model, _path = _v5_model(tmp_path)
    model_input, first, second = _model_input_and_steps()
    memory_forward = model.memory.forward
    memory_calls = 0
    encode_calls = 0

    def recording_memory(input, hidden=None):
        nonlocal memory_calls
        memory_calls += 1
        return memory_forward(input, hidden)

    encode_state = model.encode_state_v4

    def recording_encode(state):
        nonlocal encode_calls
        encode_calls += 1
        return encode_state(state)

    model.memory.forward = recording_memory  # type: ignore[method-assign]
    model.encode_state_v4 = recording_encode  # type: ignore[method-assign]
    session = _policy(model).begin_decision()
    first_logits = session.logits(model_input, first)
    second_logits = session.logits(model_input, second)
    assert len(first_logits.semantic_logits) == len(first.allowed_semantic_classes)
    assert len(second_logits.semantic_logits) == len(second.allowed_semantic_classes)
    assert memory_calls == 1
    assert encode_calls == 1
    assert isinstance(session.next_recurrent_state_token, torch.Tensor)


def test_v5_zero_init_matches_v4_semantic_and_stop_logits(tmp_path) -> None:
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4

    v5, _path = _v5_model(tmp_path)
    v4 = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=101).eval()
    model_input, first, _second = _model_input_and_steps()
    from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1

    state = representation_v4_from_step_input_v1(model_input, first, allow_unbound_selected=True)
    v4_logits, v4_stop = v4.step_logits_v4(state, stop_available=first.stop_available)
    v5_policy = _policy(v5)
    result = v5_policy.begin_decision().logits(model_input, first)
    assert torch.allclose(torch.tensor(result.semantic_logits), v4_logits, atol=1e-6)
    assert result.stop_logit is None if v4_stop is None else result.stop_logit == pytest.approx(float(v4_stop.item()), abs=1e-6)
    assert isinstance(SpecialistNeuralPolicyV4(v4, policy_identity="f" * 64, checkpoint_lineage_id="a" * 64), SpecialistDecisionPolicyV2)


def test_v5_nonzero_context_head_never_changes_stop_and_commit_is_transactional(tmp_path) -> None:
    from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1

    model, _path = _v5_model(tmp_path)
    model_input, first, _second = _model_input_and_steps()
    with torch.no_grad():
        for parameter in model.candidate_context_projection.parameters():
            parameter.fill_(0.125)
        for parameter in model.candidate_residual_head.parameters():
            parameter.fill_(0.125)
    policy = _policy(model)
    session = policy.begin_decision()
    result = session.logits(model_input, first)
    assert result.stop_logit is not None
    base = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=101).eval()
    state = representation_v4_from_step_input_v1(model_input, first, allow_unbound_selected=True)
    _base_logits, base_stop = base.step_logits_v4(state, stop_available=True)
    assert base_stop is not None
    assert result.stop_logit == pytest.approx(float(base_stop.item()), abs=1e-6)
    incoming = session.next_recurrent_state_token
    assert isinstance(incoming, torch.Tensor)
    session.commit(CommittedSemanticDecisionV2(
        semantic_action=SemanticRuntimeCompleteActionV2(
            order_semantics=first.order_semantics,
            semantic_selection=(),
        ),
        semantic_log_probability=0.0,
        next_recurrent_state_token=incoming,
    ))
    next_session = policy.begin_decision()
    assert torch.equal(next_session._incoming_hidden, incoming)  # type: ignore[attr-defined]
    session.abort()


def test_v5_reset_discards_hidden_state_and_abort_discards_cached_logits(tmp_path) -> None:
    model, _path = _v5_model(tmp_path)
    model_input, first, _second = _model_input_and_steps()
    policy = _policy(model)
    session = policy.begin_decision()
    session.logits(model_input, first)
    assert session.next_recurrent_state_token is not None
    session.abort()
    policy.reset()
    fresh = policy.begin_decision()
    assert fresh._incoming_hidden is None  # type: ignore[attr-defined]
    assert fresh.next_recurrent_state_token is None


def test_v5_rejects_malformed_public_inputs_and_identity(tmp_path) -> None:
    model, _path = _v5_model(tmp_path)
    policy = _policy(model)
    model_input, first, _second = _model_input_and_steps()
    with pytest.raises(ValueError, match="model input"):
        policy.begin_decision().logits(object(), first)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="step input"):
        policy.begin_decision().logits(model_input, object())  # type: ignore[arg-type]
    from mage_ptcg.meta_specialist.neural_policy_v5 import NeuralPolicyV5Error, SpecialistNeuralPolicyV5

    with pytest.raises(NeuralPolicyV5Error, match="policy_identity"):
        SpecialistNeuralPolicyV5(model, policy_identity="0" * 63, checkpoint_lineage_id="a" * 64)


def test_v5_loader_policy_identity_is_artifact_file_sha_and_lineage(tmp_path) -> None:
    model, v5_path = _v5_model(tmp_path)
    del model
    from mage_ptcg.meta_specialist.neural_policy_v5 import load_specialist_neural_policy_from_checkpoint_v5
    import torch as _torch

    payload = _torch.load(v5_path, map_location="cpu", weights_only=True)
    expected_tensor = payload["descriptor"]["tensor_state_sha256"]
    identity = _sha256(v5_path)
    policy = load_specialist_neural_policy_from_checkpoint_v5(
        v5_path,
        expected_file_sha256=identity,
        expected_tensor_state_sha256=expected_tensor,
        checkpoint_lineage_id="b" * 64,
    )
    telemetry = policy.policy_telemetry()
    assert isinstance(telemetry, PolicyTelemetrySnapshot)
    assert telemetry.policy_identity == identity
    assert telemetry.checkpoint_lineage_id == "b" * 64
