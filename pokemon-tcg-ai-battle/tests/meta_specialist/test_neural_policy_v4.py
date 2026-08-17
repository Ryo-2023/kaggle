"""Runtime contracts for the research-only recurrent V4 policy adapter."""

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
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2, RuntimeConstraintManifest, make_agent
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


def _hidden_prize_selection_model_input_and_steps():
    """Build a legal multi-select whose selected source has no visible entity."""
    observation = _observation()
    observation["select"] = {
        "context": 7, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 3, "minCount": 3,
        "option": [
            {"type": 3, "area": 6, "index": index, "playerIndex": 0}
            for index in range(3)
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    state = build_actor_visible_decision_state_v2(observation)
    model_input = extract_specialist_model_input_v1(
        state, make_test_card_vocabulary_v1(range(1, 1_000)),
    )
    first = build_specialist_step_input_v1(model_input, ())
    prefix = (next(iter(model_input.local_action_id_to_candidate_row_index)),)
    second = build_specialist_step_input_v1(model_input, prefix)
    return model_input.model_input, first, second


def test_v4_session_scores_legal_prefix_with_hidden_selected_endpoint() -> None:
    """Breaks if runtime rejects a legal hidden selected prefix that V4 training projects unbound."""
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4

    model_input, _first, second = _hidden_prize_selection_model_input_and_steps()
    policy = SpecialistNeuralPolicyV4(
        SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=19).eval(),
        policy_identity="f" * 64,
        checkpoint_lineage_id="a" * 64,
    )

    logits = policy.begin_decision().logits(model_input, second)

    assert len(logits.semantic_logits) == len(second.allowed_semantic_classes)
    assert logits.stop_logit is None


def test_v4_session_reuses_candidate_independent_state_encoding_across_prefixes() -> None:
    """Breaks if a long legal multi-select recomputes its unchanged entity encoding per prefix."""
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4

    model_input, first, second = _model_input_and_steps()
    model = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=23).eval()
    encode_state = model.encode_state_v4
    calls = 0

    def recording_encode_state(state):
        nonlocal calls
        calls += 1
        return encode_state(state)

    model.encode_state_v4 = recording_encode_state  # type: ignore[method-assign]
    session = SpecialistNeuralPolicyV4(
        model, policy_identity="9" * 64, checkpoint_lineage_id="8" * 64,
    ).begin_decision()

    session.logits(model_input, first)
    session.logits(model_input, second)

    assert calls == 1


def test_v4_session_batches_candidate_scoring_within_a_prefix() -> None:
    """Breaks if a wide legal decision invokes the closed candidate MLP once per candidate."""
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4

    model_input, first, _second = _model_input_and_steps()
    model = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=29).eval()
    candidate_mix = model.candidate_mix.forward
    input_shapes: list[tuple[int, ...]] = []

    def recording_candidate_mix(value):
        input_shapes.append(tuple(value.shape))
        return candidate_mix(value)

    model.candidate_mix.forward = recording_candidate_mix  # type: ignore[method-assign]
    session = SpecialistNeuralPolicyV4(
        model, policy_identity="7" * 64, checkpoint_lineage_id="6" * 64,
    ).begin_decision()

    session.logits(model_input, first)

    assert input_shapes == [(len(first.allowed_semantic_classes), model.hidden_dim * 8)]


def test_v4_session_keeps_incoming_hidden_fixed_across_decode_prefixes_then_commits() -> None:
    """Breaks if a GRU advances once per prefix instead of once per complete action."""
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4

    model_input, first, second = _model_input_and_steps()
    model = SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=5).eval()
    seen: list[object] = []
    memory_forward = model.memory.forward

    def recording_memory(input, hidden=None):
        seen.append(hidden)
        return memory_forward(input, hidden)

    model.memory.forward = recording_memory  # type: ignore[method-assign]
    policy = SpecialistNeuralPolicyV4(model, policy_identity="a" * 64, checkpoint_lineage_id="b" * 64)
    session = policy.begin_decision()
    first_logits = session.logits(model_input, first)
    second_logits = session.logits(model_input, second)

    assert len(first_logits.semantic_logits) == len(first.allowed_semantic_classes)
    assert len(second_logits.semantic_logits) == len(second.allowed_semantic_classes)
    assert seen == [None]
    next_hidden = session.next_recurrent_state_token
    assert isinstance(next_hidden, torch.Tensor)

    session.commit(CommittedSemanticDecisionV2(
        semantic_action=SemanticRuntimeCompleteActionV2(
            order_semantics=first.order_semantics,
            semantic_selection=(),
        ),
        semantic_log_probability=0.0,
        next_recurrent_state_token=next_hidden,
    ))
    policy.begin_decision().logits(model_input, first)

    assert seen[-1] is next_hidden


def test_v4_loader_requires_independent_file_and_tensor_hashes(tmp_path) -> None:
    """Breaks if an external V4 checkpoint can be loaded without both digest bindings."""
    from mage_ptcg.meta_specialist.neural_model_v4 import save_specialist_checkpoint_v4
    from mage_ptcg.meta_specialist.neural_policy_v4 import load_specialist_neural_policy_from_checkpoint_v4

    path = tmp_path / "specialist-v4.pt"
    source = SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=7).eval()
    descriptor = save_specialist_checkpoint_v4(path, source)

    loaded = load_specialist_neural_policy_from_checkpoint_v4(
        path,
        expected_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_tensor_state_sha256=descriptor["tensor_state_sha256"],
        checkpoint_lineage_id="c" * 64,
    )

    assert loaded.policy_telemetry().policy_identity == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="strict artifact validation"):
        load_specialist_neural_policy_from_checkpoint_v4(
            path,
            expected_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_tensor_state_sha256="0" * 64,
            checkpoint_lineage_id="c" * 64,
        )


def test_v4_factory_drives_existing_runtime_semantic_decode(tmp_path) -> None:
    """Breaks if V4 cannot be plugged into make_agent's existing alias dispatcher."""
    from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4, SpecialistNeuralPolicyV4Factory

    cards = tuple(range(1, 61))
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = qualify_deck_asset(
        DeckAssetInput.from_path(
            asset_id="v4-runtime", archetype_id="test", path=deck_path,
            source_ref="fixture/deck.csv", source_commit="d" * 40,
            asset_class="deck_only", usage_boundary="bundle_allowed",
            policy_compatibility="specialist-v2", card_database_version="test-db-v1",
        ),
        ArchetypeSpec("test", (), (1,), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _cards: (True, "fixture-cabt-pass"),
    )
    deck_id = deck_identity_from_card_ids(cards)
    lock = create_deck_lock(
        archetype_id="test", selected_deck_identity=deck_id, compared_deck_identities=(deck_id,),
        foundation_init_id="a" * 64, joint_race_schedule_id="b" * 64, equal_transition_budget=1,
    )
    identity = "e" * 64
    policy = SpecialistNeuralPolicyV4(
        SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=13).eval(),
        policy_identity=identity, checkpoint_lineage_id=lock.policy_lineage_id,
    )
    binding = make_agent(
        deck_asset=asset, deck_lock=lock, vocabulary=make_test_card_vocabulary_v1(range(1, 1_000)),
        policy_factory=SpecialistNeuralPolicyV4Factory(policy), expected_policy_identity=identity,
        constraints=RuntimeConstraintManifest.frozen_v1(),
    )

    assert binding.agent({"select": None}) == list(cards)
    action = binding.agent(_observation())
    assert len(action) <= 2 and len(action) == len(set(action))
    assert all(index in (0, 1) for index in action)
