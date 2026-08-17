"""Equivalence-class representation contracts for duplicate public cards."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
    RepresentationV4Error,
    SemanticPrefixTokenV4,
    representation_v4_from_model_input_v1,
    representation_v4_from_step_input_v1,
)


def _ref(card_id: int, *, zone: str = "hand") -> PublicEntityClassRefV4:
    return PublicEntityClassRefV4.actor_visible(1, zone, card_id)


def _entity(entity_id: int, ref: PublicEntityClassRefV4) -> EntityTokenV4:
    return EntityTokenV4(entity_id, 6, 1, 9, ref.card_id, None, (), (), (), ref)


def _action(card_id: int) -> SimpleNamespace:
    endpoint = SimpleNamespace(
        visibility="actor-visible", owner_role=1, semantic_zone="hand", card_id=card_id,
        host_card_id=0, pokemon=None,
    )
    null = SimpleNamespace(
        visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0,
        host_card_id=0, pokemon=None,
    )
    return SimpleNamespace(
        selection_type=1, selection_context=1, option_type=3, source=endpoint, target=null, host=null,
        attack_id=None, special_condition=None, skill_card_id=None, number=None, energy_count=None,
        to_dict=lambda: {"option_type": 3, "source_card": card_id},
    )


def _attach_action(card_id: int, pokemon: SimpleNamespace) -> SimpleNamespace:
    source = SimpleNamespace(
        visibility="actor-visible", owner_role=1, semantic_zone="hand", card_id=card_id,
        host_card_id=0, pokemon=None,
    )
    target = SimpleNamespace(
        visibility="public-visible", owner_role=1, semantic_zone="active", card_id=pokemon.card_id,
        host_card_id=0, pokemon=pokemon,
    )
    null = SimpleNamespace(
        visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0,
        host_card_id=0, pokemon=None,
    )
    return SimpleNamespace(
        selection_type=1, selection_context=1, option_type=8, source=source, target=target, host=null,
        attack_id=None, special_condition=None, skill_card_id=None, number=None, energy_count=None,
        to_dict=lambda: {"option_type": 8, "source_card": card_id, "target_card": pokemon.card_id},
    )


def test_duplicate_visible_source_projects_one_class_with_two_members() -> None:
    """Breaks if an A/A source is forced back to one physical locator."""
    action = _action(9)
    bag = SimpleNamespace(tokens=(9, 9), mask=(1, 1))
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={}, card_bags={"own_hand": bag}, pokemon_entities=(), candidate_rows=(),
    )
    step = SimpleNamespace(
        order_semantics="unordered_set", semantic_prefix=(),
        allowed_semantic_classes=(SimpleNamespace(semantic_row=action, allowed_alias_count=2),),
    )

    state = representation_v4_from_step_input_v1(model_input, step)

    candidate = state.candidates[0]
    assert candidate.source_class_ref == _ref(9)
    assert state.member_count(candidate.source_class_ref) == 2
    assert candidate.allowed_alias_count == 2


def test_duplicate_hand_attach_keeps_a_class_separate_from_b() -> None:
    """Breaks if ATTACH A/A is rejected or A's duplicate class is conflated with B."""
    pokemon = SimpleNamespace(
        owner_role=1, zone="active", card_id=101, hp=100, max_hp=100, appear_this_turn=0,
        energy_type_counts=(), energy_cards=(), tools=(), pre_evolution=(),
    )
    a, b = _attach_action(9, pokemon), _attach_action(10, pokemon)
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={}, card_bags={"own_hand": SimpleNamespace(tokens=(9, 9, 10), mask=(1, 1, 1))},
        pokemon_entities=(pokemon,), candidate_rows=(),
    )
    step = SimpleNamespace(
        order_semantics="ordered_sequence", semantic_prefix=(),
        allowed_semantic_classes=(
            SimpleNamespace(semantic_row=a, allowed_alias_count=2),
            SimpleNamespace(semantic_row=b, allowed_alias_count=1),
        ),
    )

    candidates = representation_v4_from_step_input_v1(model_input, step).candidates

    assert candidates[0].source_class_ref != candidates[1].source_class_ref
    assert candidates[0].allowed_alias_count == 2
    assert candidates[1].allowed_alias_count == 1


def test_active_energy_source_inherits_its_visible_host_pokemon_snapshot() -> None:
    """Regression: a selected attached energy has no pokemon field of its own."""
    pokemon = SimpleNamespace(
        owner_role=1, zone="active", card_id=743, hp=80, max_hp=80, appear_this_turn=1,
        energy_type_counts=(0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0),
        energy_cards=(20,), tools=(), pre_evolution=(742,),
    )
    source = SimpleNamespace(
        visibility="actor-visible", owner_role=1, semantic_zone="active-energy", card_id=20,
        host_card_id=743, pokemon=None,
    )
    host = SimpleNamespace(
        visibility="public-visible", owner_role=1, semantic_zone="active", card_id=743,
        host_card_id=743, pokemon=pokemon,
    )
    null = SimpleNamespace(
        visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0,
        host_card_id=0, pokemon=None,
    )
    action = SimpleNamespace(
        selection_type=1, selection_context=1, option_type=6, source=source, target=host, host=host,
        attack_id=None, special_condition=None, skill_card_id=None, number=None, energy_count=None,
        to_dict=lambda: {"option_type": 6, "source_card": 20, "host_card": 743},
    )
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={}, card_bags={}, pokemon_entities=(pokemon,), candidate_rows=(),
    )
    step = SimpleNamespace(
        order_semantics="unordered_set", semantic_prefix=(),
        allowed_semantic_classes=(SimpleNamespace(semantic_row=action, allowed_alias_count=1),),
    )

    candidate = representation_v4_from_step_input_v1(model_input, step).candidates[0]

    assert candidate.source_class_ref is not None
    assert candidate.source_class_ref.host_card_id == 743
    assert candidate.source_class_ref.pokemon_snapshot


def test_public_stadium_owner_is_normalized_to_the_singleton_state_class() -> None:
    source = SimpleNamespace(
        visibility="public-visible", owner_role=2, semantic_zone="stadium", card_id=1260,
        host_card_id=0, pokemon=None,
    )
    null = SimpleNamespace(
        visibility="not-applicable", owner_role=0, semantic_zone="not-applicable", card_id=0,
        host_card_id=0, pokemon=None,
    )
    action = SimpleNamespace(
        selection_type=1, selection_context=1, option_type=6, source=source, target=null, host=null,
        attack_id=None, special_condition=None, skill_card_id=None, number=None, energy_count=None,
        to_dict=lambda: {"option_type": 6, "source_card": 1260},
    )
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={"stadium": 1260}, card_bags={}, pokemon_entities=(), candidate_rows=(),
    )
    step = SimpleNamespace(
        order_semantics="unordered_set", semantic_prefix=(),
        allowed_semantic_classes=(SimpleNamespace(semantic_row=action, allowed_alias_count=1),),
    )

    candidate = representation_v4_from_step_input_v1(model_input, step).candidates[0]

    assert candidate.source_class_ref == _ref(1260, zone="stadium")


def test_visible_zero_member_fails_but_hidden_endpoint_is_unbound() -> None:
    """Breaks if absent visible data silently becomes the hidden zero vector."""
    visible = _action(99)
    hidden = _action(9)
    hidden.source = SimpleNamespace(
        visibility="hidden-unresolved", owner_role=1, semantic_zone="hand", card_id=0,
        host_card_id=0, pokemon=None,
    )
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={}, card_bags={}, pokemon_entities=(), candidate_rows=(),
    )
    missing = SimpleNamespace(
        order_semantics="unordered_set", semantic_prefix=(),
        allowed_semantic_classes=(SimpleNamespace(semantic_row=visible, allowed_alias_count=1),),
    )
    unresolved = SimpleNamespace(
        order_semantics="unordered_set", semantic_prefix=(),
        allowed_semantic_classes=(SimpleNamespace(semantic_row=hidden, allowed_alias_count=1),),
    )

    with pytest.raises(RepresentationV4Error, match="visible endpoint"):
        representation_v4_from_step_input_v1(model_input, missing)
    assert representation_v4_from_step_input_v1(model_input, unresolved).candidates[0].source_class_ref is None


def test_unordered_prefix_is_count_aware_and_order_invariant() -> None:
    """Breaks if A and A/A collapse to one set, or caller order leaks into a set."""
    a, b = _ref(9), _ref(10)
    first = SemanticPrefixTokenV4(3, (1,), (), a, None, None, a)
    second = SemanticPrefixTokenV4(3, (1,), (), b, None, None, b)
    one_a = ActionCandidateV4("a", 3, a, None, None, (1,), (), 1, ((a, 1),), False, 0, a)
    two_a = ActionCandidateV4("aa", 3, a, None, None, (1,), (), 1, ((a, 2),), False, 0, a)
    ab = ActionCandidateV4("ab", 3, a, None, None, (1,), (), 1, ((a, 1), (b, 1)), False, 0, a)
    ba = ActionCandidateV4("ba", 3, a, None, None, (1,), (), 1, ((b, 1), (a, 1)), False, 0, a)

    assert one_a.selected_class_counts != two_a.selected_class_counts
    assert ab.selected_class_counts == ba.selected_class_counts
    assert RelationalStateV4((0.0,), (_entity(1, a), _entity(2, b)), (ab,), (first, second), False).semantic_prefix == \
        RelationalStateV4((0.0,), (_entity(1, a), _entity(2, b)), (ba,), (second, first), False).semantic_prefix


def test_ordered_prefix_reuses_class_but_retains_positions() -> None:
    """Breaks if ordered A,A is represented as an unordered duplicate set."""
    a, b = _ref(9), _ref(10)
    first = SemanticPrefixTokenV4(3, (1,), (), a, None, None, a)
    second = SemanticPrefixTokenV4(3, (1,), (), b, None, None, b)
    aa = RelationalStateV4((0.0,), (_entity(1, a), _entity(2, b)), (), (first, first), True)
    ab = RelationalStateV4((0.0,), (_entity(1, a), _entity(2, b)), (), (first, second), True)
    ba = RelationalStateV4((0.0,), (_entity(1, a), _entity(2, b)), (), (second, first), True)

    assert aa.semantic_prefix != ab.semantic_prefix
    assert ab.semantic_prefix != ba.semantic_prefix


def test_class_reference_has_no_local_entity_or_serial_identity() -> None:
    """Breaks if local IDs/ordinals become persistent class features."""
    ref = _ref(9)
    payload = ref.canonical_bytes().decode("utf-8")
    assert "entity" not in payload
    assert "serial" not in payload
    assert "index" not in payload


def test_model_input_converter_requires_explicit_alias_counts() -> None:
    """Breaks if a non-step converter silently invents ``allowed_alias_count=1``."""
    action = _action(9)
    bag = SimpleNamespace(tokens=(9, 9), mask=(1, 1))
    model_input = SimpleNamespace(
        state_scalars=(0,), single_card_ids={}, card_bags={"own_hand": bag}, pokemon_entities=(),
        candidate_rows=(action,),
    )

    with pytest.raises(RepresentationV4Error, match="allowed alias counts"):
        representation_v4_from_model_input_v1(model_input, include_candidates=True)
