"""Representation v3 relation and invariance contracts.

These tests intentionally run before the v3 implementation.  They describe the
information that v2 lost when a linear per-Pokemon encoder was followed by
mean-pooling: board zone, attachment host, owner relation, and action endpoint
binding must remain observable, while an exchangeable bench permutation must
not matter.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v3 import (  # noqa: E402
    RelationAwareEncoderV3,
    SpecialistModelV3,
    ZoneDeepSetsEncoderV3,
)
from mage_ptcg.meta_specialist.representation_v3 import (  # noqa: E402
    ActionCandidateV3,
    EntityTokenV3,
    PublicEntityLocatorV3,
    RelationalStateV3,
    SemanticPrefixTokenV3,
    RepresentationV3Error,
    representation_v3_from_model_input_v1,
    representation_v3_from_step_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (  # noqa: E402
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2  # noqa: E402


def _entity(
    entity_id: int,
    *,
    entity_type: int = 1,
    owner: int = 1,
    zone: int = 1,
    card_id: int = 10,
    host_entity_id: int | None = None,
    flags: tuple[int, ...] = (0, 0),
) -> EntityTokenV3:
    return EntityTokenV3(
        entity_id=entity_id,
        entity_type=entity_type,
        owner=owner,
        zone=zone,
        card_id=card_id,
        host_entity_id=host_entity_id,
        scalar_features=(0.5, 0.25),
        categorical_features=(entity_type, zone),
        binary_flags=flags,
    )


def _candidate(
    stable_action_id: str,
    *,
    action_type: int = 1,
    source: int | None = 1,
    target: int | None = 2,
    selection_step: int = 0,
) -> ActionCandidateV3:
    return ActionCandidateV3(
        stable_action_id=stable_action_id,
        action_type=action_type,
        source_entity_id=source,
        target_entity_id=target,
        categorical_args=(action_type,),
        numeric_args=(0.25,),
        selection_step=selection_step,
    )


def _state(entities: tuple[EntityTokenV3, ...], candidates: tuple[ActionCandidateV3, ...] = ()) -> RelationalStateV3:
    return RelationalStateV3(
        state_scalars=(0.1, 0.2, 0.3),
        entities=entities,
        candidates=candidates,
    )


def _encoder() -> RelationAwareEncoderV3:
    encoder = RelationAwareEncoderV3(
        card_vocabulary_size=128,
        hidden_dim=32,
        embedding_dim=16,
        attention_heads=4,
        attention_blocks=2,
        seed=7,
    )
    encoder.eval()
    return encoder


def test_active_bench_swap_changes_global_representation() -> None:
    encoder = _encoder()
    base = _state((_entity(1, zone=1, card_id=10), _entity(2, zone=2, card_id=20)))
    swapped = _state((replace(base.entities[0], zone=2), replace(base.entities[1], zone=1)))

    first = encoder.encode_state_v3(base).global_token
    second = encoder.encode_state_v3(swapped).global_token

    assert not torch.allclose(first, second, atol=1e-6)


def test_attachment_host_swap_changes_global_representation() -> None:
    encoder = _encoder()
    hosts = (_entity(1, zone=1, card_id=10), _entity(2, zone=2, card_id=20))
    attachment = _entity(3, entity_type=2, zone=3, card_id=30, host_entity_id=1)
    swapped = replace(attachment, host_entity_id=2)

    first = encoder.encode_state_v3(_state((*hosts, attachment))).global_token
    second = encoder.encode_state_v3(_state((*hosts, swapped))).global_token

    assert not torch.allclose(first, second, atol=1e-6)


def test_owner_swap_changes_global_representation() -> None:
    encoder = _encoder()
    base = _state((_entity(1, owner=1, card_id=10), _entity(2, owner=2, card_id=20)))
    swapped = _state((replace(base.entities[0], owner=2), replace(base.entities[1], owner=1)))

    assert not torch.allclose(
        encoder.encode_state_v3(base).global_token,
        encoder.encode_state_v3(swapped).global_token,
        atol=1e-6,
    )


def test_exchangeable_bench_permutation_is_invariant() -> None:
    encoder = _encoder()
    first = _state((_entity(1, zone=2, card_id=10), _entity(2, zone=2, card_id=20), _entity(3, zone=2, card_id=30)))
    second = _state((first.entities[2], first.entities[0], first.entities[1]))

    assert torch.allclose(
        encoder.encode_state_v3(first).global_token,
        encoder.encode_state_v3(second).global_token,
        atol=1e-5,
    )


def test_hidden_card_identity_is_not_an_observable_feature() -> None:
    encoder = _encoder()
    visible = _entity(1, zone=5, card_id=0)
    altered = replace(visible, entity_id=99, card_id=0)

    assert torch.allclose(
        encoder.encode_state_v3(_state((visible,))).global_token,
        encoder.encode_state_v3(_state((altered,))).global_token,
        atol=1e-6,
    )


def test_candidate_source_target_binding_changes_candidate_encoding() -> None:
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    first_locator = PublicEntityLocatorV3(1, "active", 0)
    second_locator = PublicEntityLocatorV3(1, "bench", 0)
    state = _state((
        replace(_entity(1), public_locator=first_locator),
        replace(_entity(2, zone=2), public_locator=second_locator),
    ))
    encoding = model.encoder.encode_state_v3(state)
    first = model.encode_candidate_v3(replace(
        _candidate("a", source=1, target=2), source_locator=first_locator, target_locator=second_locator,
    ), state_encoding=encoding)
    swapped = model.encode_candidate_v3(replace(
        _candidate("b", source=2, target=1), source_locator=second_locator, target_locator=first_locator,
    ), state_encoding=encoding)

    assert not torch.allclose(first, swapped, atol=1e-6)


def test_action_order_permutation_only_permutates_logits() -> None:
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    state = _state(
        (_entity(1, zone=1, card_id=10), _entity(2, zone=2, card_id=20)),
        (_candidate("a", source=1, target=2), _candidate("b", source=2, target=1)),
    )
    first = model.forward_v3(state)
    permuted = _state(state.entities, (state.candidates[1], state.candidates[0]))
    second = model.forward_v3(permuted)

    assert first.logits.shape == second.logits.shape == (2,)
    assert torch.allclose(second.logits[0], first.logits[1], atol=1e-6)
    assert torch.allclose(second.logits[1], first.logits[0], atol=1e-6)


def test_encoder_initialization_is_seeded_without_touching_global_rng() -> None:
    torch.manual_seed(314)
    before = torch.random.get_rng_state()
    first = RelationAwareEncoderV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=17)
    after = torch.random.get_rng_state()
    second = RelationAwareEncoderV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=17)
    assert torch.equal(before, after)
    assert all(torch.equal(a, b) for a, b in zip(first.state_dict().values(), second.state_dict().values()))


def test_zone_deepsets_encoder_is_a_relation_safe_alternative() -> None:
    first_state = _state((_entity(1, zone=2, card_id=10), _entity(2, zone=2, card_id=20)))
    encoder = ZoneDeepSetsEncoderV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=23)
    encoder.eval()
    first = encoder.encode_state_v3(first_state).global_token
    permuted = replace(first_state, entities=(first_state.entities[1], first_state.entities[0]))
    second = encoder.encode_state_v3(permuted).global_token
    assert torch.allclose(first, second, atol=1e-6)


def test_v1_projection_preserves_public_schema_shape() -> None:
    model_input = SimpleNamespace(
        state_scalars=(0,) * 41,
        single_card_ids={"stadium": 7, "context": 8, "effect": 9},
        card_bags={},
        pokemon_entities=(),
        candidate_rows=(),
    )
    projected = representation_v3_from_model_input_v1(model_input)
    assert projected.state_scalars == (0.0,) * 41
    assert len(projected.entities) == 3
    assert all(entity.card_id in {7, 8, 9} for entity in projected.entities)


def test_semantic_prefix_changes_learned_stop_without_using_stable_ids() -> None:
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=37)
    base = _state((_entity(1, card_id=10),), (_candidate("a", target=None),))
    prefix = SemanticPrefixTokenV3(2, (1, 1, 2), (0.0,), None, None)
    with_prefix = replace(base, semantic_prefix=(prefix,))
    first_logits, first_stop = model.step_logits_v3(base, stop_available=True)
    second_logits, second_stop = model.step_logits_v3(with_prefix, stop_available=True)
    assert first_stop is not None and second_stop is not None
    assert not torch.allclose(first_stop, second_stop, atol=1e-6)
    assert not torch.allclose(first_logits, second_logits, atol=1e-6)


def test_semantic_prefix_respects_order_only_for_ordered_selections() -> None:
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=41)
    model.eval()
    base = _state((_entity(1, card_id=10),), (_candidate("a", target=None),))
    first = SemanticPrefixTokenV3(2, (1,), (0.0,), None, None)
    second = SemanticPrefixTokenV3(3, (2,), (1.0,), None, None)
    unordered = replace(base, semantic_prefix=(first, second), prefix_order_sensitive=False)
    unordered_swapped = replace(base, semantic_prefix=(second, first), prefix_order_sensitive=False)
    ordered = replace(base, semantic_prefix=(first, second), prefix_order_sensitive=True)
    ordered_swapped = replace(base, semantic_prefix=(second, first), prefix_order_sensitive=True)

    unordered_logits, unordered_stop = model.step_logits_v3(unordered, stop_available=True)
    unordered_swapped_logits, unordered_swapped_stop = model.step_logits_v3(unordered_swapped, stop_available=True)
    ordered_logits, ordered_stop = model.step_logits_v3(ordered, stop_available=True)
    ordered_swapped_logits, ordered_swapped_stop = model.step_logits_v3(ordered_swapped, stop_available=True)

    assert unordered_stop is not None and unordered_swapped_stop is not None
    assert ordered_stop is not None and ordered_swapped_stop is not None
    assert torch.allclose(unordered_logits, unordered_swapped_logits, atol=1e-6)
    assert torch.allclose(unordered_stop, unordered_swapped_stop, atol=1e-6)
    assert not torch.allclose(ordered_logits, ordered_swapped_logits, atol=1e-6)
    assert not torch.allclose(ordered_stop, ordered_swapped_stop, atol=1e-6)


def test_episode_start_resets_recurrent_memory() -> None:
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=11)
    model.eval()
    state = _state((_entity(1, zone=1, card_id=10),), (_candidate("a", target=None),))
    first = model.forward_v3(state, episode_start=True)
    carried = model.forward_v3(state, hidden_state=first.hidden_state)
    reset = model.forward_v3(state, hidden_state=first.hidden_state, episode_start=True)
    assert not torch.allclose(carried.global_token, reset.global_token, atol=1e-6)
    assert torch.allclose(first.global_token, reset.global_token, atol=1e-6)


def test_public_locator_resolves_duplicate_card_by_owner_zone_and_snapshot() -> None:
    """Fails if v1 migration restores duplicate endpoints with card-id first-match."""
    active = SimpleNamespace(
        owner_role=1, zone="active", card_id=10, hp=100, max_hp=120,
        appear_this_turn=0, energy_type_counts=(0,) * 12,
        energy_cards=(), tools=(), pre_evolution=(),
    )
    bench = SimpleNamespace(
        owner_role=1, zone="bench", card_id=10, hp=90, max_hp=120,
        appear_this_turn=0, energy_type_counts=(0,) * 12,
        energy_cards=(), tools=(), pre_evolution=(),
    )
    null = SimpleNamespace(visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0, pokemon=None)
    target = SimpleNamespace(visibility="public-visible", owner_role=1, semantic_zone="bench", card_id=10, pokemon=bench)
    action = SimpleNamespace(
        source=null, target=target, host=null, selection_type=1, selection_context=1,
        attack_id=None, special_condition=None, skill_card_id=None, number=None,
        energy_count=None, option_type=8,
        to_dict=lambda: {"kind": "bench-target"},
    )
    model_input = SimpleNamespace(
        state_scalars=(0,) * 41, single_card_ids={"stadium": 0, "context": 0, "effect": 0},
        card_bags={}, pokemon_entities=(active, bench), candidate_rows=(action,),
    )

    projected = representation_v3_from_model_input_v1(model_input)

    assert projected.candidates[0].target_locator == PublicEntityLocatorV3(1, "bench", 0)
    assert projected.candidates[0].target_locator != projected.entities[0].public_locator


def test_public_locator_rejects_indistinguishable_duplicate_snapshot() -> None:
    """Fails if ambiguous v1 public aliases silently bind to an arbitrary entity."""
    duplicate = SimpleNamespace(
        owner_role=1, zone="bench", card_id=10, hp=100, max_hp=120,
        appear_this_turn=0, energy_type_counts=(0,) * 12,
        energy_cards=(), tools=(), pre_evolution=(),
    )
    null = SimpleNamespace(visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0, pokemon=None)
    endpoint = SimpleNamespace(visibility="public-visible", owner_role=1, semantic_zone="bench", card_id=10, pokemon=duplicate)
    action = SimpleNamespace(
        source=endpoint, target=null, host=null, selection_type=1, selection_context=1,
        attack_id=None, special_condition=None, skill_card_id=None, number=None,
        energy_count=None, option_type=3, to_dict=lambda: {"kind": "ambiguous"},
    )
    model_input = SimpleNamespace(
        state_scalars=(0,) * 41, single_card_ids={"stadium": 0, "context": 0, "effect": 0},
        card_bags={}, pokemon_entities=(duplicate, duplicate), candidate_rows=(action,),
    )

    with pytest.raises(RepresentationV3Error, match="ambiguous_public_locator"):
        representation_v3_from_model_input_v1(model_input)


def test_required_topology_uses_five_public_pools_and_r3b_dimensions() -> None:
    """Fails if R3-A collapses owner/board pools or R3-B changes its published capacity."""
    deepsets = ZoneDeepSetsEncoderV3(card_vocabulary_size=64, seed=3)
    relation = RelationAwareEncoderV3(card_vocabulary_size=64, seed=3)

    assert set(deepsets.zone_pool) == {
        "own-active", "own-bench", "opponent-active", "opponent-bench", "other-public",
    }
    assert relation.embedding_dim == 192
    assert len(relation.attention) == 2
    assert relation.attention[0].num_heads == 4
    assert relation.ffn[0][0].out_features == 512
    assert relation.attention[0].dropout == pytest.approx(0.05)


def test_required_topology_keeps_public_evolution_host_relation_observable() -> None:
    """Fails if a public pre-evolution token stops binding to its host Pokemon."""
    encoder = _encoder()
    hosts = (_entity(1, zone=1, card_id=10), _entity(2, zone=2, card_id=20))
    first = _entity(3, entity_type=4, zone=4, card_id=30, host_entity_id=1)
    second = replace(first, host_entity_id=2)

    assert not torch.allclose(
        encoder.encode_state_v3(_state((*hosts, first))).global_token,
        encoder.encode_state_v3(_state((*hosts, second))).global_token,
        atol=1e-6,
    )


def test_stable_id_rename_does_not_change_semantic_candidate_encoding() -> None:
    """Fails if a stable action ID hash is embedded as a semantic candidate feature."""
    locator = PublicEntityLocatorV3(1, "active", 0)
    state = _state((replace(_entity(1), public_locator=locator),))
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    encoding = model.encoder.encode_state_v3(state)
    first = ActionCandidateV3(
        stable_action_id="provenance-a", action_type=1, source_entity_id=1, target_entity_id=None,
        categorical_args=(1,), numeric_args=(), selection_step=0, source_locator=locator,
    )
    renamed = replace(first, stable_action_id="provenance-b")

    assert torch.allclose(
        model.encode_candidate_v3(first, state_encoding=encoding),
        model.encode_candidate_v3(renamed, state_encoding=encoding), atol=1e-6,
    )


def test_multi_selection_canonicalizes_sets_and_excludes_selected_duplicate() -> None:
    """Fails if unordered prefixes retain order or allow a selected public entity twice."""
    first = PublicEntityLocatorV3(1, "hand", 0)
    second = PublicEntityLocatorV3(1, "hand", 1)
    candidate = ActionCandidateV3(
        stable_action_id="selection", action_type=3, source_entity_id=None, target_entity_id=None,
        categorical_args=(1,), numeric_args=(), selection_step=9, source_locator=second,
        selected_locators=(second, first), selection_order_sensitive=False,
    )

    assert candidate.selected_locators == (first, second)
    assert candidate.selection_step == 0
    assert candidate.excludes_selected_duplicate


def test_order_sensitive_multi_selection_preserves_prefix_and_step() -> None:
    """Fails if ordered selection drops prefix order or its selection-step signal."""
    first = PublicEntityLocatorV3(1, "hand", 0)
    second = PublicEntityLocatorV3(1, "hand", 1)
    candidate = ActionCandidateV3(
        stable_action_id="ordered-selection", action_type=3, source_entity_id=None, target_entity_id=None,
        categorical_args=(1,), numeric_args=(), selection_step=2, source_locator=None,
        selected_locators=(second, first), selection_order_sensitive=True,
    )

    assert candidate.selected_locators == (second, first)
    assert candidate.selection_step == 2


def _step_adapter_fixture(*, ordered: bool):
    hand = [
        {"id": 101, "serial": 1001, "playerIndex": 0},
        {"id": 102, "serial": 1002, "playerIndex": 0},
        {"id": 103, "serial": 1003, "playerIndex": 0},
    ]
    player = lambda cards: {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": cards,
        "handCount": 0 if cards is None else len(cards), "paralyzed": False,
        "poisoned": False, "prize": [None] * 6,
    }
    if ordered:
        selection = {
            "context": 34, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 3, "minCount": 0,
            "option": [{"type": 15, "cardId": card["id"], "serial": card["serial"]} for card in hand],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
        }
    else:
        selection = {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 3, "minCount": 0,
            "option": [{"type": 3, "area": 2, "index": index, "playerIndex": 0} for index in range(3)],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        }
    observation = {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player(hand), player(None)], "result": -1, "retreated": False,
            "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
            "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        }, "select": selection, "step": 7,
    }
    extracted = extract_specialist_model_input_v1(
        build_actor_visible_decision_state_v2(observation), make_test_card_vocabulary_v1(range(1, 1000)),
    )
    selected = tuple(extracted.local_action_id_to_candidate_row_index)[:2]
    return extracted.model_input, build_specialist_step_input_v1(extracted, selected)


def test_step_adapter_uses_real_unordered_prefix_and_allowed_domain() -> None:
    """Fails if v3 always emits an empty, unordered zero-step candidate context."""
    model_input, step_input = _step_adapter_fixture(ordered=False)

    projected = representation_v3_from_step_input_v1(model_input, step_input)

    assert len(projected.candidates) == len(step_input.allowed_semantic_classes)
    assert all(candidate.selection_order_sensitive is False for candidate in projected.candidates)
    assert all(candidate.selection_step == 0 for candidate in projected.candidates)
    assert all(candidate.selected_locators for candidate in projected.candidates)
    assert projected.candidates[0].selected_locators == tuple(sorted(
        projected.candidates[0].selected_locators, key=PublicEntityLocatorV3.canonical_key,
    ))


def test_step_adapter_uses_real_ordered_prefix_and_step() -> None:
    """Fails if v3 drops ordered prefix order or its selection-step signal."""
    model_input, step_input = _step_adapter_fixture(ordered=True)

    projected = representation_v3_from_step_input_v1(model_input, step_input)

    assert len(projected.candidates) == len(step_input.allowed_semantic_classes)
    assert all(candidate.selection_order_sensitive for candidate in projected.candidates)
    assert all(candidate.selection_step == len(step_input.semantic_prefix) for candidate in projected.candidates)


def test_skill_card_argument_changes_candidate_encoding_without_stable_id_hash() -> None:
    """Fails if the fifth canonical argument (skill_card_id) is truncated from the semantic input."""
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    first = ActionCandidateV3("provenance-a", 15, None, None, (5, 34, 0, 0, 7), (), 0)
    second = ActionCandidateV3("provenance-b", 15, None, None, (5, 34, 0, 0, 8), (), 0)

    assert not torch.allclose(model.encode_candidate_v3(first), model.encode_candidate_v3(second), atol=1e-6)


def _without_relation(encoder: RelationAwareEncoderV3, name: str) -> RelationAwareEncoderV3:
    """Return an otherwise identical encoder with one real relation contribution removed."""
    altered = _encoder()
    altered.load_state_dict(encoder.state_dict())
    with torch.no_grad():
        for parameter in getattr(altered, name).parameters():
            parameter.zero_()
    return altered


@pytest.mark.parametrize(("name", "state"), (
    ("owner_relation", lambda: _state((_entity(1, owner=1), _entity(2, owner=1, zone=2), _entity(3, owner=2, card_id=20)))),
    ("active_relation", lambda: _state((_entity(1, owner=1), _entity(2, owner=1, zone=2, card_id=20)))),
    ("same_host_relation", lambda: _state((_entity(1), _entity(2, entity_type=2, zone=3, host_entity_id=1), _entity(3, entity_type=3, zone=3, host_entity_id=1)))),
    ("evolution_relation", lambda: _state((_entity(1), _entity(2, entity_type=4, zone=4, host_entity_id=1)))),
))
def test_required_relation_contribution_is_observable(name: str, state) -> None:
    """Fails if the named typed relation is removed from the actual forward path."""
    encoder = _encoder()
    removed = _without_relation(encoder, name)

    assert not torch.allclose(
        encoder.encode_state_v3(state()).global_token,
        removed.encode_state_v3(state()).global_token,
        atol=1e-6,
    )


def test_required_source_target_relation_contribution_is_observable() -> None:
    """Fails if candidate source/target interaction is reduced to independent endpoint vectors."""
    first = PublicEntityLocatorV3(1, "active", 0)
    second = PublicEntityLocatorV3(1, "bench", 0)
    state = _state((replace(_entity(1), public_locator=first), replace(_entity(2, zone=2), public_locator=second)))
    candidate = ActionCandidateV3("relation", 8, 1, 2, (1, 1, 0, 0, 0), (), 0, first, second)
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    altered = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    altered.load_state_dict(model.state_dict())
    with torch.no_grad():
        for parameter in altered.source_target_relation.parameters():
            parameter.zero_()
    model.eval()
    altered.eval()

    assert not torch.allclose(
        model.encode_candidate_v3(candidate, state_encoding=model.encoder.encode_state_v3(state)),
        altered.encode_candidate_v3(candidate, state_encoding=altered.encoder.encode_state_v3(state)),
        atol=1e-6,
    )


def _endpoint_selection_fixture(*, option_type: int):
    hand = [
        {"id": 101, "serial": 1001, "playerIndex": 0},
        {"id": 102, "serial": 1002, "playerIndex": 0},
    ]
    active = {
        "id": 201, "serial": 2001, "hp": 100, "maxHp": 120, "appearThisTurn": False,
        "energies": [], "energyCards": [],
        "tools": [{"id": 501, "serial": 5001, "playerIndex": 0}, {"id": 502, "serial": 5002, "playerIndex": 0}],
        "preEvolution": [],
    }
    bench = {
        "id": 202, "serial": 2002, "hp": 90, "maxHp": 120, "appearThisTurn": False,
        "energies": [], "energyCards": [], "tools": [], "preEvolution": [],
    }
    player = lambda cards, active_cards, bench_cards=(): {
        "active": active_cards, "asleep": False, "bench": list(bench_cards), "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": cards,
        "handCount": 0 if cards is None else len(cards), "paralyzed": False,
        "poisoned": False, "prize": [None] * 6,
    }
    if option_type == 4:
        selection_type, context = 2, 26
        options = [
            {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": index}
            for index in range(2)
        ]
    else:
        selection_type, context = 0, 0
        options = [
            {"type": 8, "area": 2, "index": index, "inPlayArea": 4 + index, "inPlayIndex": 0}
            for index in range(2)
        ]
        # Give the second ATTACH action a distinct public target.
        # The first remains active (area 4); the second is bench (area 5).
    observation = {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player(hand, [active], (bench,)), player(None, [])], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {"context": context, "contextCard": None, "deck": None, "effect": None,
                   "maxCount": 2, "minCount": 0, "option": options,
                   "remainDamageCounter": 0, "remainEnergyCost": 0, "type": selection_type},
        "step": 7,
    }
    extracted = extract_specialist_model_input_v1(
        build_actor_visible_decision_state_v2(observation), make_test_card_vocabulary_v1(range(1, 1000)),
    )
    selected = (next(iter(extracted.local_action_id_to_candidate_row_index)),)
    return extracted.model_input, build_specialist_step_input_v1(extracted, selected)



def test_step_adapter_selects_source_when_source_target_and_host_are_public() -> None:
    """Fails if an attached-tool selection is guessed as its host target instead of its source."""
    model_input, step = _endpoint_selection_fixture(option_type=4)

    projected = representation_v3_from_step_input_v1(model_input, step)

    assert projected.candidates[0].selected_locators == (PublicEntityLocatorV3(1, "active-tool", 0),)


def test_step_adapter_selects_target_when_attach_source_and_target_are_public() -> None:
    """Fails if ATTACH selection follows source-first order instead of its target policy."""
    model_input, step = _endpoint_selection_fixture(option_type=8)

    projected = representation_v3_from_step_input_v1(model_input, step)

    assert projected.candidates[0].selected_locators == (PublicEntityLocatorV3(1, "active", 0),)


def test_step_adapter_rejects_unclassified_or_missing_policy_endpoint() -> None:
    """Fails if unknown endpoint roles fall back to source/target/host probing."""
    model_input, step = _endpoint_selection_fixture(option_type=8)
    row = step.semantic_prefix[0]
    unknown = SimpleNamespace(option_type=999, source=row.source, target=row.target, host=row.host)
    missing_target = SimpleNamespace(option_type=8, source=row.source, target=SimpleNamespace(), host=row.host)

    with pytest.raises(RepresentationV3Error, match="unclassified selectable endpoint"):
        representation_v3_from_step_input_v1(model_input, SimpleNamespace(
            order_semantics=step.order_semantics, semantic_prefix=(unknown,), allowed_semantic_classes=(),
        ))
    with pytest.raises(RepresentationV3Error, match="selectable endpoint is not uniquely public"):
        representation_v3_from_step_input_v1(model_input, SimpleNamespace(
            order_semantics=step.order_semantics, semantic_prefix=(missing_target,), allowed_semantic_classes=(),
        ))


@pytest.mark.parametrize(("option_type", "selected_zone"), ((4, "active-tool"), (8, "active")))
def test_duplicate_mask_uses_same_selectable_endpoint_policy_as_prefix(option_type: int, selected_zone: str) -> None:
    """Fails if logits always compare source_locator after target-select prefix construction."""
    model_input, step = _endpoint_selection_fixture(option_type=option_type)
    base = representation_v3_from_model_input_v1(model_input)
    selected = representation_v3_from_step_input_v1(model_input, step).candidates[0].selected_locators
    candidates = tuple(replace(
        candidate,
        selected_locators=selected,
        selectable_locator=(candidate.source_locator if option_type == 4 else candidate.target_locator),
    ) for candidate in base.candidates)
    state = replace(base, candidates=candidates)
    model = SpecialistModelV3(card_vocabulary_size=1_000, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    logits = model.forward_v3(state).logits
    selected_index = next(
        index for index, candidate in enumerate(candidates)
        if candidate.selectable_locator is not None and candidate.selectable_locator.semantic_zone == selected_zone
        and candidate.selectable_locator in selected
    )
    other_index = next(index for index in range(len(candidates)) if index != selected_index)

    assert torch.isneginf(logits[selected_index])
    assert torch.isfinite(logits[other_index])


def test_ordered_selected_prefix_changes_candidate_encoding_but_unordered_set_does_not() -> None:
    """Fails if ordered selected locators are mean-pooled like an unordered set."""
    first = PublicEntityLocatorV3(1, "active", 0)
    second = PublicEntityLocatorV3(1, "bench", 0)
    entities = (
        replace(_entity(1), public_locator=first),
        replace(_entity(2, zone=2), public_locator=second),
    )
    base = _state(entities)
    ordered_ab = ActionCandidateV3("ordered-a", 3, None, None, (5, 34, 0, 0, 7), (), 2,
                                   selected_locators=(first, second), selection_order_sensitive=True)
    ordered_ba = replace(ordered_ab, stable_action_id="ordered-b", selected_locators=(second, first))
    unordered_ab = replace(ordered_ab, stable_action_id="unordered-a", selection_order_sensitive=False)
    unordered_ba = replace(unordered_ab, stable_action_id="unordered-b", selected_locators=(second, first))
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7)
    model.eval()
    encoding = model.encoder.encode_state_v3(base)

    assert not torch.allclose(
        model.encode_candidate_v3(ordered_ab, state_encoding=encoding),
        model.encode_candidate_v3(ordered_ba, state_encoding=encoding), atol=1e-6,
    )
    assert not torch.allclose(
        model.forward_v3(replace(base, candidates=(ordered_ab,))).logits,
        model.forward_v3(replace(base, candidates=(ordered_ba,))).logits, atol=1e-6,
    )
    assert torch.allclose(
        model.encode_candidate_v3(unordered_ab, state_encoding=encoding),
        model.encode_candidate_v3(unordered_ba, state_encoding=encoding), atol=1e-6,
    )


def test_selected_prefix_rejects_more_than_v3_candidate_limit() -> None:
    """Fails if arbitrary prefix length can exceed the R3 512-candidate contract."""
    locators = tuple(PublicEntityLocatorV3(1, "hand", index) for index in range(513))

    with pytest.raises(RepresentationV3Error, match="maximum of 512"):
        ActionCandidateV3("too-many", 3, None, None, (1,), (), 512,
                          selected_locators=locators, selection_order_sensitive=True)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is unavailable in this test environment")
def test_v3_cuda_forward_with_ordered_prefix_keeps_logits_and_gradients_on_device() -> None:
    """Fails if CUDA factories, duplicate mask, or finite-candidate backward diverge."""
    first = PublicEntityLocatorV3(1, "active", 0)
    second = PublicEntityLocatorV3(1, "bench", 0)
    state = _state(
        (replace(_entity(1), public_locator=first), replace(_entity(2, zone=2), public_locator=second)),
        (
            ActionCandidateV3(
            "cuda-selected", 3, 1, 2, (5, 34, 0, 0, 7), (), 1,
            source_locator=first, target_locator=second,
            selected_locators=(first,), selection_order_sensitive=True,
            selectable_locator=first,
            ),
            ActionCandidateV3(
            "cuda-remaining", 3, 2, 1, (5, 34, 0, 0, 8), (), 1,
            source_locator=second, target_locator=first,
            selected_locators=(first,), selection_order_sensitive=True,
            selectable_locator=second,
            ),
        ),
    )
    model = SpecialistModelV3(card_vocabulary_size=128, hidden_dim=32, embedding_dim=16, seed=7).to("cuda")
    output = model.forward_v3(state)
    output.logits[1].backward()

    assert output.logits.device.type == "cuda"
    assert torch.isneginf(output.logits[0])
    assert torch.isfinite(output.logits[1])
    assert model.candidate_bias.weight.grad is not None
    assert model.candidate_bias.weight.grad.device.type == "cuda"
