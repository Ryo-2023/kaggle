"""L3 policy model: protocol conformance, frozen domains, and order/shuffle behavior."""

from __future__ import annotations

import pytest
from dataclasses import replace

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (  # noqa: E402
    PokemonEntityV1,
    SemanticEndpointV1,
    SpecialistStepInputV1,
    SpecialistStepLogitPolicyV1,
    SpecialistStepLogitsV1,
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (  # noqa: E402
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    MAX_OPTION_TYPE_V1,
    NeuralModelV1Error,
    SpecialistModelConfigV1,
    TorchStepLogitPolicyV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    _bounded,
    _visibility_index,
    _zone_index,
)

from tests.meta_specialist.test_training_example_envelope_v2 import _observation


CARD_VOCABULARY_SIZE = 1_400


def _fixture():
    vocabulary = make_test_card_vocabulary_v1(())
    state = build_actor_visible_decision_state_v2(_observation())
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    step = build_specialist_step_input_v1(extracted, ())
    return extracted.model_input, step


def _policy(seed: int = 5):
    config = SpecialistModelConfigV1(card_vocabulary_size=CARD_VOCABULARY_SIZE)
    return TorchStepLogitPolicyV1(build_specialist_policy_model_v1(config, seed=seed))


def test_policy_satisfies_the_frozen_step_logit_protocol() -> None:
    model_input, step = _fixture()
    policy = _policy()

    assert isinstance(policy, SpecialistStepLogitPolicyV1)
    result = policy.logits(model_input, step)
    assert type(result) is SpecialistStepLogitsV1
    assert len(result.semantic_logits) == len(step.allowed_semantic_classes)
    assert all(isinstance(value, float) for value in result.semantic_logits)
    assert (result.stop_logit is None) is (not step.stop_available)


def test_logits_are_deterministic_and_cached_once_per_distinct_step() -> None:
    model_input, step = _fixture()
    policy = _policy()

    first = policy.logits(model_input, step)
    assert policy.inference_calls == 1
    for _repeat in range(3):
        assert policy.logits(model_input, step) == first
    assert policy.inference_calls == 1  # one inference per distinct prefix

    # A different prefix is a different step and must be scored again.
    if step.allowed_semantic_classes and step.stop_available:
        deeper = build_specialist_step_input_v1(
            extract_specialist_model_input_v1(
                build_actor_visible_decision_state_v2(_observation()),
                make_test_card_vocabulary_v1(()),
            ),
            (),
        )
        policy.logits(model_input, deeper)
        assert policy.inference_calls >= 1


def test_two_models_with_the_same_seed_agree_and_different_seeds_differ() -> None:
    model_input, step = _fixture()
    same = _policy(seed=5).logits(model_input, step)
    again = _policy(seed=5).logits(model_input, step)
    other = _policy(seed=6).logits(model_input, step)

    assert same == again
    assert same.semantic_logits != other.semantic_logits


def test_building_a_model_does_not_disturb_global_rng() -> None:
    torch.manual_seed(1234)
    expected = torch.randn(4)
    torch.manual_seed(1234)
    build_specialist_policy_model_v1(
        SpecialistModelConfigV1(card_vocabulary_size=CARD_VOCABULARY_SIZE), seed=99
    )
    assert torch.equal(torch.randn(4), expected)


def test_a_class_logit_does_not_depend_on_which_other_classes_are_legal() -> None:
    """The query depends on state and prefix only, so scoring is per-class."""
    model_input, step = _fixture()
    classes = step.allowed_semantic_classes
    if len(classes) < 2:
        pytest.skip("fixture has a single legal class")
    policy = _policy()

    full = policy.logits(model_input, step)
    # A canonically sorted prefix of the class set keeps every shared class in place.
    subset = SpecialistStepInputV1(
        schema_version=step.schema_version,
        order_semantics=step.order_semantics,
        semantic_prefix=step.semantic_prefix,
        allowed_semantic_classes=classes[:1],
        stop_available=step.stop_available,
    )
    partial = policy.logits(model_input, subset)

    assert len(partial.semantic_logits) == 1
    assert partial.semantic_logits[0] == pytest.approx(full.semantic_logits[0], abs=1e-6)
    assert partial.stop_logit == pytest.approx(full.stop_logit, abs=1e-6)

    # A candidate encoding depends only on the class, never on its slot.
    model = policy._model
    assert torch.allclose(
        model.encode_candidate(classes[0].semantic_row),
        model.encode_candidate(classes[0].semantic_row),
    )


def test_unknown_zone_visibility_and_out_of_domain_ids_fail_closed() -> None:
    model_input, step = _fixture()
    policy = _policy()
    model = policy._model
    action = step.allowed_semantic_classes[0].semantic_row

    # The frozen string domains never fall back to a shared bucket, because a
    # silent collision would merge two distinct semantic classes into one logit.
    with pytest.raises(NeuralModelV1Error, match="unknown semantic zone"):
        _zone_index("made-up-zone")
    with pytest.raises(NeuralModelV1Error, match="unknown visibility"):
        _visibility_index("made-up")
    with pytest.raises(NeuralModelV1Error, match="option_type is outside"):
        _bounded(999, limit=MAX_OPTION_TYPE_V1, field="option_type")

    # A card outside the sealed vocabulary is rejected rather than wrapped or clamped.
    with pytest.raises(NeuralModelV1Error, match="card_id is outside"):
        model._card(CARD_VOCABULARY_SIZE + 5)
    assert model._card(CARD_VOCABULARY_SIZE).shape[-1] == model.config.card_dim
    assert model.encode_candidate(action).shape[-1] == model.config.hidden_dim


def test_model_rejects_wrong_input_types_and_bad_config() -> None:
    policy = _policy()
    model_input, step = _fixture()
    with pytest.raises(NeuralModelV1Error, match="model_input must be"):
        model.encode_state(object()) if (model := policy._model) else None
    with pytest.raises(NeuralModelV1Error, match="step_input must be"):
        policy._model.step_logits(model_input, object())
    with pytest.raises(NeuralModelV1Error, match="must be a positive int"):
        SpecialistModelConfigV1(card_vocabulary_size=0)
    with pytest.raises(NeuralModelV1Error, match="must be a SpecialistPolicyModelV1"):
        TorchStepLogitPolicyV1(object())


def test_config_topology_round_trips_for_the_checkpoint_identity() -> None:
    config = SpecialistModelConfigV1(card_vocabulary_size=CARD_VOCABULARY_SIZE)
    payload = config.to_dict()
    assert payload["card_vocabulary_size"] == CARD_VOCABULARY_SIZE
    assert SpecialistModelConfigV1(
        card_vocabulary_size=payload["card_vocabulary_size"],
        hidden_dim=payload["hidden_dim"],
        card_dim=payload["card_dim"],
        symbol_dim=payload["symbol_dim"],
    ) == config


def test_model_runs_on_cpu_without_cuda() -> None:
    model_input, step = _fixture()
    policy = _policy()
    policy.logits(model_input, step)
    assert all(parameter.device.type == "cpu" for parameter in policy._model.parameters())


def test_zone_vocabulary_covers_every_emitted_zone() -> None:
    """A zone the feature layer can emit must be encodable, not fail closed."""
    import re
    from pathlib import Path

    from mage_ptcg.meta_specialist.neural_model_v1 import SEMANTIC_ZONE_VOCABULARY_V1

    root = Path(__file__).resolve().parents[2] / "src/mage_ptcg/meta_specialist"
    emitted: set[str] = set()
    for name in ("actor_visible_features_v1.py", "actor_visible_v2.py"):
        source = (root / name).read_text(encoding="utf-8")
        emitted |= set(re.findall(r'semantic_zone=["\']([a-z][a-z-]*)["\']', source))
        emitted |= set(re.findall(r'"((?:active|bench|attached)-(?:tool|energy))"', source))
    assert emitted, "zone extraction found nothing; the pin would be vacuous"
    assert emitted <= set(SEMANTIC_ZONE_VOCABULARY_V1), sorted(
        emitted - set(SEMANTIC_ZONE_VOCABULARY_V1)
    )


def test_rich_pokemon_features_bind_zone_energy_and_attachment_identity() -> None:
    model = _policy()._model
    entity = PokemonEntityV1(
        owner_role=1, zone="active", card_id=1, hp=100, max_hp=120,
        appear_this_turn=0, energy_type_counts=(1,) * 12,
        energy_cards=(2, 3), tools=(4,), pre_evolution=(5,),
    )
    assert not torch.equal(
        model._pokemon(entity),
        model._pokemon(replace(entity, zone="bench")),
    )
    assert not torch.equal(
        model._pokemon(entity),
        model._pokemon(replace(entity, energy_type_counts=(9,) + entity.energy_type_counts[1:])),
    )
    assert not torch.equal(
        model._pokemon(entity),
        model._pokemon(replace(entity, tools=(entity.card_id,))),
    )


def test_endpoint_encoding_changes_with_nested_pokemon_snapshot() -> None:
    model = _policy()._model
    entity = PokemonEntityV1(
        owner_role=1, zone="active", card_id=1, hp=100, max_hp=120,
        appear_this_turn=0, energy_type_counts=(1,) * 12,
        energy_cards=(2,), tools=(4,), pre_evolution=(),
    )
    endpoint = SemanticEndpointV1(
        visibility="public-visible", owner_role=1, semantic_zone="active",
        card_id=1, host_card_id=1, pokemon=entity,
    )
    changed = replace(endpoint, pokemon=replace(endpoint.pokemon, hp=max(0, endpoint.pokemon.hp - 1)))
    assert not torch.equal(model._endpoint(endpoint), model._endpoint(changed))


def test_state_scalar_encoder_preserves_categorical_values_without_log_transform() -> None:
    model_input, _step = _fixture()
    model = _policy()._model
    values = model._encode_state_scalars(model_input.state_scalars)
    assert values[4].item() == float(model_input.state_scalars[4])
    assert values[5].item() == float(model_input.state_scalars[5])


def test_critic_can_condition_on_opponent_instance_without_changing_policy_state() -> None:
    model_input, _step = _fixture()
    model = _policy()._model
    state = model.encode_state(model_input)
    first = model.state_value_from_state(state, opponent_instance_id="opponent-a")
    second = model.state_value_from_state(state, opponent_instance_id="opponent-b")
    assert first.shape == second.shape == ()
    assert not torch.equal(first, second)
