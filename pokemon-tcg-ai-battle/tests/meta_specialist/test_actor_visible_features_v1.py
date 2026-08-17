"""TDD coverage for the serial-free actor-visible feature contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    ACTOR_VISIBLE_FEATURE_DOMAIN_V1,
    CARD_VOCABULARY_SCHEMA_V1,
    FEATURE_SCHEMA_DESCRIPTOR_V1,
    FEATURE_SCHEMA_HASH_V1,
    MODEL_INPUT_SCHEMA_V1,
    SemanticEndpointV1,
    SpecialistFeatureError,
    SpecialistStepLogitsV1,
    build_specialist_step_input_v1,
    canonical_model_input_bytes_v1,
    canonical_step_input_bytes_v1,
    collate_candidate_rows_v1,
    collate_state_scalars_v1,
    evaluate_specialist_step_v1,
    enumerate_semantic_complete_action_distribution_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
    require_production_card_vocabulary_v1,
    validate_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    build_actor_visible_decision_state_v2,
)


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(
    card_id: int,
    serial: int,
    *,
    hp: int = 100,
    tools: list[object] | None = None,
    energy_cards: list[object] | None = None,
) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": hp, "maxHp": 120,
        "appearThisTurn": False, "energies": [1, 1, 3],
        "energyCards": [] if energy_cards is None else energy_cards,
        "tools": [] if tools is None else tools,
        "preEvolution": [],
    }


def _player(hand: object, *, active: list[object] | None = None) -> dict[str, object]:
    return {
        "active": [] if active is None else active, "asleep": False, "bench": [],
        "benchMax": 5, "burned": False, "confused": False, "deckCount": 53,
        "discard": [], "hand": hand, "handCount": len(hand) if isinstance(hand, list) else 0,
        "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }


def _observation(*, serial_offset: int = 0, option_order: tuple[int, ...] = (0, 1)) -> dict[str, object]:
    hand = [_card(101, 1001 + serial_offset, 0), _card(102, 1002 + serial_offset, 0)]
    options = [
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
        {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
    ]
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [_player(hand, active=[_pokemon(201, 2001 + serial_offset)]), _player(None, active=[_pokemon(301, 3001 + serial_offset)])],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 2, "minCount": 0, "option": [options[index] for index in option_order],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _extract(observation: dict[str, object]):
    state = build_actor_visible_decision_state_v2(observation)
    return extract_specialist_model_input_v1(
        state, make_test_card_vocabulary_v1(range(1, 1000))
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_model_input_has_closed_41_scalar_schema_and_recomputed_hash() -> None:
    """Fails if a scalar is added, reordered, or the model schema hash drifts."""
    extracted = _extract(_observation())
    payload = extracted.model_input.to_dict()

    assert tuple(payload) == (
        "schema_version", "feature_domain", "feature_schema_hash", "state_scalars",
        "single_card_ids", "card_bags", "pokemon_entities", "candidate_rows",
    )
    assert payload["schema_version"] == MODEL_INPUT_SCHEMA_V1
    assert payload["feature_domain"] == ACTOR_VISIBLE_FEATURE_DOMAIN_V1
    assert len(payload["state_scalars"]) == 41
    assert tuple(payload["state_scalars"]) == (
        1, 7, 2, 3, 1, 1, 0, 2, 2, 0, 0, 0, 0, 0, 0,
        2, 53, 6, 0, 0, 53, 6, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 5,
    )
    assert FEATURE_SCHEMA_DESCRIPTOR_V1["state_scalar_names"][0] == "first_player_role"
    assert FEATURE_SCHEMA_DESCRIPTOR_V1["state_scalar_names"][-1] == "opponent_bench_max"
    assert FEATURE_SCHEMA_HASH_V1 == hashlib.sha256(
        b"mage_ptcg:specialist-feature-schema:v1\0" + _canonical(dict(FEATURE_SCHEMA_DESCRIPTOR_V1))
    ).hexdigest()
    assert FEATURE_SCHEMA_HASH_V1 == "757d71d89f53e3edb579e064ad571919c0be121cea97a863a5b34cecd03628c3"
    assert extracted.model_input_id == hashlib.sha256(
        b"mage_ptcg:specialist-model-input:v1\0" + _canonical({
            "feature_domain": ACTOR_VISIBLE_FEATURE_DOMAIN_V1,
            "feature_schema_hash": FEATURE_SCHEMA_HASH_V1,
            "model_input": payload,
        })
    ).hexdigest()


def test_serial_and_option_permutations_do_not_change_learned_rows() -> None:
    """Fails if private serials or source option ordering reach the model rows."""
    first = _extract(_observation())
    serial_changed = _extract(_observation(serial_offset=100))
    permuted = _extract(_observation(option_order=(1, 0)))

    assert first.model_input.to_dict() == serial_changed.model_input.to_dict()
    assert first.model_input.to_dict() == permuted.model_input.to_dict()
    assert first.model_input_id == serial_changed.model_input_id == permuted.model_input_id


def test_semantic_card_and_counter_changes_change_their_features() -> None:
    """Fails if semantic source identity or selection counters are discarded."""
    baseline = _extract(_observation())
    changed_card = _observation()
    changed_card["current"]["players"][0]["hand"][0]["id"] = 777  # type: ignore[index]
    changed_counter = _observation()
    changed_counter["select"]["remainDamageCounter"] = 4  # type: ignore[index]

    assert baseline.model_input.to_dict() != _extract(changed_card).model_input.to_dict()
    assert baseline.model_input.state_scalars != _extract(changed_counter).model_input.state_scalars


def test_collation_freezes_categorical_indices_and_finite_saturating_count_transform() -> None:
    """Fails if count normalization leaks categorical values into float features or stops saturating."""
    state_scalars = list(_extract(_observation()).model_input.state_scalars)
    state_scalars[1] = 999_999  # step caps at 4095
    collated = collate_state_scalars_v1(tuple(state_scalars))

    assert collated.categorical_indices == (1, 1, 1)
    assert len(collated.continuous_values) == 38
    assert all(value == value and abs(value) != float("inf") for value in collated.continuous_values)
    assert collated.continuous_values[0] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "flag_index",
    (11, 12, 13, 14, *range(23, 35), 36, 37, 38),
)
def test_scalar_collator_rejects_every_nonbinary_flag(flag_index: int) -> None:
    """Fails if standalone collation bypasses the exact boolean scalar domain."""
    scalars = list(_extract(_observation()).model_input.state_scalars)
    scalars[flag_index] = 2
    with pytest.raises(SpecialistFeatureError, match="flag"):
        collate_state_scalars_v1(tuple(scalars))
    scalars[flag_index] = True
    with pytest.raises(SpecialistFeatureError, match="nonnegative non-bool"):
        collate_state_scalars_v1(tuple(scalars))


def test_card_and_pokemon_multisets_sort_and_retain_duplicate_semantics() -> None:
    """Fails if collection order or physical serials affect bags/entities, or duplicates are collapsed."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [_card(102, 99, 0), _card(101, 2, 0), _card(101, 3, 0)]  # type: ignore[index]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["current"]["players"][0]["bench"] = [_pokemon(401, 1), _pokemon(401, 2)]  # type: ignore[index]
    extracted = _extract(observation)
    bag = extracted.model_input.card_bags["own_hand"]
    duplicate_entities = [entity.to_dict() for entity in extracted.model_input.pokemon_entities if entity.card_id == 402]

    assert bag.tokens[:3] == (102, 102, 103)
    assert bag.mask[:4] == (1, 1, 1, 0)
    assert len(duplicate_entities) == 2
    assert duplicate_entities[0] == duplicate_entities[1]


def test_model_payload_has_no_private_identity_or_ordinal_tokens() -> None:
    """Fails if a serial, locator, local ID, or raw actor payload becomes a feature."""
    payload = _extract(_observation()).model_input.to_dict()
    encoded = _canonical(payload).decode("utf-8")
    forbidden = ("serial", "index", "local_action", "action_key", "digest", "actor_identity", "private")
    assert not any(token in encoded for token in forbidden)
    row = payload["candidate_rows"][0]
    assert tuple(row) == (
        "selection_type", "selection_context", "option_type", "operation", "source",
        "target", "host", "number", "attack_id", "special_condition", "energy_count",
        "skill_card_id",
    )
    assert tuple(row["source"]) == (
        "visibility", "owner_role", "semantic_zone", "card_id", "host_card_id", "pokemon",
    )
    assert tuple(payload["card_bags"]["own_hand"]) == ("tokens", "mask")
    assert tuple(payload["pokemon_entities"][0]) == (
        "owner_role", "zone", "card_id", "hp", "max_hp", "appear_this_turn",
        "energy_type_counts", "energy_cards", "tools", "pre_evolution",
    )


def test_feature_values_reject_mutable_nested_replacements_and_keep_mapping_views_immutable() -> None:
    """Fails if a direct constructor/replace can inject mutable feature containers."""
    extracted = _extract(_observation())
    bag = extracted.model_input.card_bags["own_hand"]

    with pytest.raises(SpecialistFeatureError, match="padded"):
        replace(bag, tokens=list(bag.tokens))
    with pytest.raises(SpecialistFeatureError, match="41"):
        replace(extracted.model_input, state_scalars=list(extracted.model_input.state_scalars))
    with pytest.raises(TypeError):
        extracted.model_input.single_card_ids["stadium"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        extracted.local_action_id_to_candidate_row_index["0" * 64] = 0  # type: ignore[index]


def test_replayed_deep_validation_rejects_bool_and_object_setattr_mutations() -> None:
    """Fails if deep model/step validation trusts a previously frozen nested object."""
    extracted = _extract(_observation())
    entity = extracted.model_input.pokemon_entities[0]
    with pytest.raises(SpecialistFeatureError, match="owner_role"):
        replace(entity, owner_role=True)

    endpoint = extracted.model_input.candidate_rows[0].source
    object.__setattr__(endpoint, "owner_role", True)
    with pytest.raises(SpecialistFeatureError, match="owner_role"):
        validate_specialist_model_input_v1(extracted.model_input)
    with pytest.raises(SpecialistFeatureError, match="owner_role"):
        collate_candidate_rows_v1((extracted.model_input,))

    clean = _extract(_observation())
    step = build_specialist_step_input_v1(clean, ())
    object.__setattr__(step.allowed_semantic_classes[0], "allowed_alias_count", True)
    with pytest.raises(SpecialistFeatureError, match="non-bool int"):
        canonical_step_input_bytes_v1(step)


def test_whole_input_coherence_binds_candidate_schema_board_snapshots_and_attachments() -> None:
    """Fails if valid-looking rows can contradict the serial-free static decision state."""
    attach = _observation()
    attach["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 8, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    attach_model = _extract(attach).model_input
    attach_row = attach_model.candidate_rows[0]
    assert attach_row.target.pokemon is not None
    forged_target = replace(
        attach_row.target,
        pokemon=replace(attach_row.target.pokemon, hp=99),
    )
    with pytest.raises(SpecialistFeatureError, match="static board"):
        replace(attach_model, candidate_rows=(replace(attach_row, target=forged_target),))

    evolve = _observation()
    evolve["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 9, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    evolve_model = _extract(evolve).model_input
    evolve_row = evolve_model.candidate_rows[0]
    assert evolve_row.target.pokemon is not None
    with pytest.raises(SpecialistFeatureError, match="static board"):
        replace(evolve_model, candidate_rows=(replace(
            evolve_row,
            target=replace(evolve_row.target, pokemon=replace(evolve_row.target.pokemon, hp=99)),
        ),))

    board_card = _observation()
    board_card["select"] = {  # type: ignore[index]
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 3, "area": 4, "index": 0, "playerIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    board_model = _extract(board_card).model_input
    board_row = board_model.candidate_rows[0]
    assert board_row.source.pokemon is not None
    with pytest.raises(SpecialistFeatureError, match="static board"):
        replace(board_model, candidate_rows=(replace(
            board_row,
            source=replace(board_row.source, pokemon=replace(board_row.source.pokemon, hp=99)),
        ),))

    # Every row belongs to exactly the model input's one decision schema.
    scalars = list(attach_model.state_scalars)
    scalars[4:6] = [1, 1]
    with pytest.raises(SpecialistFeatureError, match="selection schema"):
        replace(attach_model, state_scalars=tuple(scalars))

    tool = _observation()
    tool["current"]["players"][0]["active"][0]["tools"] = [_card(501, 5001, 0)]  # type: ignore[index]
    tool["select"] = {  # type: ignore[index]
        "context": 26, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 2,
    }
    tool_model = _extract(tool).model_input
    tool_row = tool_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="attachment"):
        replace(tool_model, candidate_rows=(replace(tool_row, source=replace(tool_row.source, card_id=1999)),))
    assert tool_row.target.pokemon is not None
    forged_host = replace(tool_row.target, pokemon=replace(tool_row.target.pokemon, hp=99))
    with pytest.raises(SpecialistFeatureError, match="static board"):
        replace(tool_model, candidate_rows=(replace(tool_row, target=forged_host, host=forged_host),))

    energy = _observation()
    energy["current"]["players"][0]["active"][0]["energyCards"] = [_card(401, 4001, 0)]  # type: ignore[index]
    energy["select"] = {  # type: ignore[index]
        "context": 26, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 5, "area": 4, "index": 0, "playerIndex": 0, "energyIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 2,
    }
    energy_model = _extract(energy).model_input
    energy_row = energy_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="attachment"):
        replace(energy_model, candidate_rows=(replace(energy_row, source=replace(energy_row.source, card_id=1999)),))

    energy_count = _observation()
    energy_count["current"]["players"][0]["active"][0]["energyCards"] = [_card(401, 4001, 0)]  # type: ignore[index]
    energy_count["select"] = {  # type: ignore[index]
        "context": 30, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 6, "area": 4, "index": 0, "playerIndex": 0, "energyIndex": 0, "count": 1}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 4,
    }
    energy_count_model = _extract(energy_count).model_input
    energy_count_row = energy_count_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="attachment"):
        replace(energy_count_model, candidate_rows=(replace(
            energy_count_row, source=replace(energy_count_row.source, card_id=1999)
        ),))

    attached_skill = _observation()
    attached_skill["current"]["players"][0]["active"][0]["tools"] = [_card(501, 5001, 0)]  # type: ignore[index]
    attached_skill["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 15, "cardId": 501, "serial": 5001}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    skill_model = _extract(attached_skill).model_input
    skill_row = skill_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="attachment"):
        replace(skill_model, candidate_rows=(replace(skill_row, source=replace(skill_row.source, card_id=1999)),))


def test_whole_input_coherence_binds_visible_source_tokens_to_static_projections() -> None:
    """Fails if card zones, singleton cards, or pre-evolutions can be forged independently."""
    hand_model = _extract(_observation()).model_input
    hand_row = hand_model.candidate_rows[0]
    forged_hand = replace(hand_row, source=replace(hand_row.source, card_id=1999))
    with pytest.raises(SpecialistFeatureError, match="own_hand"):
        replace(hand_model, candidate_rows=tuple(sorted(
            (forged_hand, hand_model.candidate_rows[1]), key=lambda row: row.canonical_bytes
        )))

    discard = _observation()
    discard["current"]["players"][0]["discard"] = [_card(401, 4001, 0)]  # type: ignore[index]
    discard["select"]["minCount"] = 1  # type: ignore[index]
    discard["select"]["maxCount"] = 1  # type: ignore[index]
    discard["select"]["option"] = [{"type": 3, "area": 3, "index": 0, "playerIndex": 0}]  # type: ignore[index]
    discard_model = _extract(discard).model_input
    discard_row = discard_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="self_discard"):
        replace(discard_model, candidate_rows=(replace(discard_row, source=replace(discard_row.source, card_id=1999)),))

    stadium = _observation()
    stadium["current"]["stadium"] = [_card(601, 6001, 0)]  # type: ignore[index]
    stadium["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 10, "area": 7, "index": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    stadium_model = _extract(stadium).model_input
    stadium_row = stadium_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="stadium"):
        replace(stadium_model, candidate_rows=(replace(stadium_row, source=replace(stadium_row.source, card_id=1999)),))

    context = _observation()
    context["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": _card(701, 7001, 0), "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 15, "cardId": 701, "serial": 7001}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    context_model = _extract(context).model_input
    context_row = context_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="context"):
        replace(context_model, candidate_rows=(replace(context_row, source=replace(context_row.source, card_id=1999)),))

    effect = _observation()
    effect["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": _card(702, 7002, 0),
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 15, "cardId": 702, "serial": 7002}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    effect_model = _extract(effect).model_input
    effect_row = effect_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="effect"):
        replace(effect_model, candidate_rows=(replace(effect_row, source=replace(effect_row.source, card_id=1999)),))

    deck_reveal = _observation()
    deck_reveal["current"]["players"][0]["deckCount"] = 1  # type: ignore[index]
    deck_reveal["select"]["deck"] = [_card(751, 7501, 0)]  # type: ignore[index]
    deck_reveal["select"]["minCount"] = 1  # type: ignore[index]
    deck_reveal["select"]["maxCount"] = 1  # type: ignore[index]
    deck_reveal["select"]["option"] = [{"type": 3, "area": 1, "index": 0, "playerIndex": 0}]  # type: ignore[index]
    deck_model = _extract(deck_reveal).model_input
    deck_row = deck_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="deck_reveal"):
        replace(deck_model, candidate_rows=(replace(deck_row, source=replace(deck_row.source, card_id=1999)),))

    looking_source = _observation()
    looking_source["current"]["looking"] = [_card(752, 7502, 0)]  # type: ignore[index]
    looking_source["select"]["minCount"] = 1  # type: ignore[index]
    looking_source["select"]["maxCount"] = 1  # type: ignore[index]
    looking_source["select"]["option"] = [{"type": 3, "area": 12, "index": 0, "playerIndex": 0}]  # type: ignore[index]
    looking_model = _extract(looking_source).model_input
    looking_row = looking_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="looking_visible"):
        replace(looking_model, candidate_rows=(replace(looking_row, source=replace(looking_row.source, card_id=1999)),))

    pre_evolution = _observation()
    pre_evolution["current"]["players"][0]["active"][0]["preEvolution"] = [_card(801, 8001, 0)]  # type: ignore[index]
    pre_evolution["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 15, "cardId": 801, "serial": 8001}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    pre_model = _extract(pre_evolution).model_input
    pre_row = pre_model.candidate_rows[0]
    with pytest.raises(SpecialistFeatureError, match="pre_evolution"):
        replace(pre_model, candidate_rows=(replace(pre_row, source=replace(pre_row.source, card_id=1999)),))


def test_whole_input_coherence_enforces_exact_static_counts_presence_and_board_capacity() -> None:
    """Fails if deterministic counts/presence flags or bounded public board multiplicities drift."""
    model = _extract(_observation()).model_input

    scalars = list(model.state_scalars)
    scalars[15] -= 1
    with pytest.raises(SpecialistFeatureError, match="own_hand count"):
        replace(model, state_scalars=tuple(scalars))

    invalid_context = dict(model.single_card_ids)
    invalid_context["context"] = 1
    with pytest.raises(SpecialistFeatureError, match="context"):
        replace(model, single_card_ids=invalid_context)

    deck = _observation()
    deck["current"]["players"][0]["deckCount"] = 1  # type: ignore[index]
    deck["select"]["deck"] = [_card(901, 9001, 0)]  # type: ignore[index]
    deck_model = _extract(deck).model_input
    deck_scalars = list(deck_model.state_scalars)
    deck_scalars[33] = 0
    with pytest.raises(SpecialistFeatureError, match="deck_reveal"):
        replace(deck_model, state_scalars=tuple(deck_scalars))

    looking = _observation()
    looking["current"]["looking"] = [_card(901, 9001, 0), None]  # type: ignore[index]
    looking_model = _extract(looking).model_input
    looking_scalars = list(looking_model.state_scalars)
    looking_scalars[34] = 0
    with pytest.raises(SpecialistFeatureError, match="looking"):
        replace(looking_model, state_scalars=tuple(looking_scalars))

    self_active = next(
        entity for entity in model.pokemon_entities
        if entity.owner_role == 1 and entity.zone == "active"
    )
    duplicate_active = tuple(sorted(
        (*model.pokemon_entities, self_active), key=lambda entity: _canonical(entity.to_dict())
    ))
    with pytest.raises(SpecialistFeatureError, match="active"):
        replace(model, pokemon_entities=duplicate_active)

    self_bench = replace(self_active, zone="bench")
    too_many_bench = tuple(sorted(
        (*model.pokemon_entities, *(self_bench for _ in range(model.state_scalars[39] + 1))),
        key=lambda entity: _canonical(entity.to_dict()),
    ))
    with pytest.raises(SpecialistFeatureError, match="bench"):
        replace(model, pokemon_entities=too_many_bench)


def test_whole_input_coherence_preserves_serial_free_duplicate_and_unknown_aliases() -> None:
    """Fails if validation accidentally invents serial uniqueness or known-card identity."""
    aliases = _observation()
    aliases["current"]["players"][0]["hand"] = [_card(1501, 1, 0), _card(1501, 2, 0)]  # type: ignore[index]
    aliases["current"]["players"][0]["handCount"] = 2  # type: ignore[index]
    aliases["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
        {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
    ]
    extracted = _extract(aliases)

    # Both cards map to UNK=1 but remain legal serial-free aliases, and the
    # duplicate equal static feature rows remain valid.
    assert extracted.model_input.card_bags["own_hand"].tokens[:2] == (1, 1)
    validate_specialist_model_input_v1(extracted.model_input)


def test_card_vocabulary_is_explicitly_test_only_until_production_qualification() -> None:
    """Fails if a test vocabulary can silently become a package vocabulary."""
    vocabulary = make_test_card_vocabulary_v1({7, 42})

    assert vocabulary.schema_version == CARD_VOCABULARY_SCHEMA_V1
    assert vocabulary.token_for(7) == 8
    assert vocabulary.token_for(42) == 43
    assert vocabulary.token_for(9) == 1
    assert vocabulary.token_for(None) == 0
    with pytest.raises(SpecialistFeatureError, match="test-only"):
        require_production_card_vocabulary_v1(vocabulary)


def test_step_builder_groups_duplicate_aliases_and_forces_stop_without_policy_call() -> None:
    """Fails if semantic aliases leak local IDs or forced STOP evaluates a policy."""
    duplicated = _observation()
    duplicated["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
    ]
    # Distinct action keys require distinct official options; duplicate semantic aliases are
    # constructed through equal card IDs with distinct serials in two visible hand entries.
    duplicated["current"]["players"][0]["hand"] = [_card(101, 1, 0), _card(101, 2, 0)]  # type: ignore[index]
    duplicated["current"]["players"][0]["handCount"] = 2  # type: ignore[index]
    duplicated["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
        {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
    ]
    extracted = _extract(duplicated)
    step = build_specialist_step_input_v1(extracted, ())
    assert len(step.allowed_semantic_classes) == 1
    assert step.allowed_semantic_classes[0].allowed_alias_count == 2

    selected = tuple(extracted.local_action_id_to_candidate_row_index)
    forced = build_specialist_step_input_v1(extracted, selected)

    class NeverCalled:
        def logits(self, model_input, step_input):  # pragma: no cover - failure path
            raise AssertionError("forced STOP must not invoke policy")

    result = evaluate_specialist_step_v1(NeverCalled(), extracted, forced)
    assert result.forced_stop is True
    assert result.stop_logit is None


def test_unordered_legality_has_one_nondecreasing_feasible_path_and_retains_equal_aliases() -> None:
    """Fails if an unordered complete action has two token paths or a legal equal-row repeat is masked."""
    distinct = _observation()
    distinct["select"]["minCount"] = 2  # type: ignore[index]
    distinct["select"]["maxCount"] = 2  # type: ignore[index]
    extracted = _extract(distinct)
    ordered_ids = tuple(
        local_id
        for local_id, _index in sorted(
            extracted.local_action_id_to_candidate_row_index.items(),
            key=lambda item: extracted.model_input.candidate_rows[item[1]].canonical_bytes,
        )
    )

    initial = build_specialist_step_input_v1(extracted, ())
    after_low = build_specialist_step_input_v1(extracted, (ordered_ids[0],))

    assert len(initial.allowed_semantic_classes) == 1
    assert initial.allowed_semantic_classes[0].semantic_row == extracted.model_input.candidate_rows[
        extracted.local_action_id_to_candidate_row_index[ordered_ids[0]]
    ]
    assert len(after_low.allowed_semantic_classes) == 1
    with pytest.raises(SpecialistFeatureError, match="unreachable"):
        build_specialist_step_input_v1(extracted, (ordered_ids[1],))

    class OnlyLegalClassPolicy:
        def logits(self, model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 1
            return SpecialistStepLogitsV1(semantic_logits=(0.0,), stop_logit=None)

    distribution = enumerate_semantic_complete_action_distribution_v1(
        extracted, OnlyLegalClassPolicy()
    )
    assert len(distribution) == 1
    assert distribution[0].semantic_selection == extracted.model_input.candidate_rows
    assert distribution[0].probability == pytest.approx(1.0)

    aliases_observation = _observation()
    aliases_observation["current"]["players"][0]["hand"] = [_card(101, 1, 0), _card(101, 2, 0)]  # type: ignore[index]
    aliases_observation["current"]["players"][0]["handCount"] = 2  # type: ignore[index]
    aliases_observation["select"]["minCount"] = 2  # type: ignore[index]
    aliases_observation["select"]["maxCount"] = 2  # type: ignore[index]
    aliases = _extract(aliases_observation)
    alias_ids = tuple(aliases.local_action_id_to_candidate_row_index)
    first_alias = min(alias_ids)
    alias_step = build_specialist_step_input_v1(aliases, (first_alias,))

    assert len(alias_step.allowed_semantic_classes) == 1
    assert alias_step.allowed_semantic_classes[0].allowed_alias_count == 1


def test_two_alias_class_vs_singleton_distribution_is_class_normalized_and_alias_reselectable() -> None:
    """Fails if alias multiplicity duplicates class mass or physical identity blocks semantic reselection."""
    observation = _observation()
    hand = [_card(101, 1, 0), _card(101, 2, 0), _card(102, 3, 0)]
    observation["current"]["players"][0]["hand"] = hand  # type: ignore[index]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": index, "playerIndex": 0}
        for index in range(3)
    ]
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    extracted = _extract(observation)

    class FixedClassPolicy:
        def logits(self, model_input, step_input):
            assert [item.allowed_alias_count for item in step_input.allowed_semantic_classes] == [2, 1]
            return SpecialistStepLogitsV1(
                semantic_logits=(math.log(0.6), math.log(0.4)), stop_logit=None
            )

    distribution = enumerate_semantic_complete_action_distribution_v1(
        extracted, FixedClassPolicy()
    )
    probabilities = {
        item.semantic_selection[0].source.card_id: item.probability for item in distribution
    }

    assert probabilities == pytest.approx({102: 0.6, 103: 0.4})
    assert math.fsum(item.probability for item in distribution) == pytest.approx(1.0)
    assert {
        item.semantic_selection[0].source.card_id: item.log_probability
        for item in distribution
    } == pytest.approx({102: math.log(0.6), 103: math.log(0.4)})


@pytest.mark.parametrize("candidate_count", (61, 64, 67))
def test_large_private_candidate_domains_remain_ragged_and_serial_free(candidate_count: int) -> None:
    """Fails if the feature boundary regresses to the public 60-candidate cap."""
    observation = _observation()
    hand = [_card(500 + index, 1500 + index, 0) for index in range(60)]
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["hand"] = hand
    own["handCount"] = len(hand)
    own["bench"] = [_pokemon(801, 8001)]
    options = [
        {"type": 8, "area": 2, "index": index, "inPlayArea": 4, "inPlayIndex": 0}
        for index in range(60)
    ]
    options.extend(
        {"type": 8, "area": 2, "index": index, "inPlayArea": 5, "inPlayIndex": 0}
        for index in range(candidate_count - 60)
    )
    observation["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 0, "option": options,
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }

    extracted = _extract(observation)

    assert len(extracted.model_input.candidate_rows) == candidate_count
    assert len(extracted.local_action_id_to_candidate_row_index) == candidate_count
    assert extracted.model_input.state_scalars[8] == candidate_count


def test_exact_512_candidate_domain_extracts_and_collates_at_batch_width_512() -> None:
    """Fails if the private feature path shares any lower public/card-list cap."""
    observation = _observation()
    hand = [_card(500 + index, 1500 + index, 0) for index in range(60)]
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["hand"] = hand
    own["handCount"] = 60
    own["benchMax"] = 8
    own["bench"] = [_pokemon(800 + index, 8000 + index) for index in range(8)]
    targets = [(4, 0), *((5, index) for index in range(8))]
    options = [
        {"type": 8, "area": 2, "index": source, "inPlayArea": area, "inPlayIndex": target}
        for source in range(60)
        for area, target in targets
    ][:512]
    observation["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 0, "option": options,
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }

    extracted = _extract(observation)
    collated = collate_candidate_rows_v1((extracted.model_input,))

    assert len(extracted.model_input.candidate_rows) == 512
    assert len(collated.rows[0]) == 512
    assert sum(collated.mask[0]) == 512


def test_candidate_collation_pads_to_batch_maximum_not_global_limit() -> None:
    """Fails if a small batch is padded to 512 instead of its ragged logical maximum."""
    two = _extract(_observation())
    zero_observation = _observation()
    zero_observation["select"]["option"] = []  # type: ignore[index]
    zero_observation["select"]["minCount"] = 0  # type: ignore[index]
    zero_observation["select"]["maxCount"] = 0  # type: ignore[index]
    zero = _extract(zero_observation)

    collated = collate_candidate_rows_v1((two.model_input, zero.model_input))

    assert len(collated.rows[0]) == 2
    assert collated.mask == ((1, 1), (0, 0))


def test_stop_legality_respects_minimum_and_maximum_bounds() -> None:
    """Fails if STOP is offered before min_count or candidates remain after max_count."""
    minimum_one = _observation()
    minimum_one["select"]["minCount"] = 1  # type: ignore[index]
    extracted = _extract(minimum_one)
    initial = build_specialist_step_input_v1(extracted, ())
    selected = (next(iter(extracted.local_action_id_to_candidate_row_index)),)
    after_one = build_specialist_step_input_v1(extracted, selected)
    after_maximum = build_specialist_step_input_v1(
        extracted, tuple(extracted.local_action_id_to_candidate_row_index)
    )

    assert initial.stop_available is False
    assert after_one.stop_available is True
    assert after_maximum.stop_available is True
    assert after_maximum.allowed_semantic_classes == ()


def test_skill_order_prefix_keeps_order_while_unordered_prefix_canonicalizes() -> None:
    """Fails if the sole ordered CABT schema is treated as an unordered set."""
    unordered = _extract(_observation())
    ids = tuple(unordered.local_action_id_to_candidate_row_index)
    assert build_specialist_step_input_v1(unordered, ids) == build_specialist_step_input_v1(unordered, ids[::-1])

    ordered = _observation()
    ordered["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 0,
        "option": [{"type": 15, "cardId": 101, "serial": 1001}, {"type": 15, "cardId": 102, "serial": 1002}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    ordered_extracted = _extract(ordered)
    ordered_ids = tuple(ordered_extracted.local_action_id_to_candidate_row_index)
    assert build_specialist_step_input_v1(ordered_extracted, ordered_ids) != build_specialist_step_input_v1(ordered_extracted, ordered_ids[::-1])


def test_zero_skill_card_id_is_a_nonpadding_unknown_semantic_token() -> None:
    """Fails if the official Skill special/non-card ID zero is mistaken for PAD."""
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 0, "option": [{"type": 15, "cardId": 0, "serial": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }

    extracted = _extract(observation)

    assert extracted.model_input.candidate_rows[0].skill_card_id == 1


def test_attached_skill_semantics_include_the_exact_serial_free_public_host() -> None:
    """Fails if identical attachments on different same-zone Pokemon collapse into one class."""
    observation = _observation()
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["bench"] = [
        _pokemon(401, 41, hp=90, tools=[_card(501, 51, 0)]),
        _pokemon(401, 42, hp=10, tools=[_card(501, 52, 0)]),
    ]
    observation["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 0,
        "option": [
            {"type": 15, "cardId": 501, "serial": 51},
            {"type": 15, "cardId": 501, "serial": 52},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }

    extracted = _extract(observation)
    rows = extracted.model_input.candidate_rows

    assert rows[0] != rows[1]
    assert {row.source.host_card_id for row in rows} == {402}
    assert {row.source.pokemon.hp for row in rows if row.source.pokemon is not None} == {10, 90}
    assert b"serial" not in canonical_model_input_bytes_v1(extracted.model_input)

    same_host = _observation()
    same_own = same_host["current"]["players"][0]  # type: ignore[index]
    same_own["bench"] = [
        _pokemon(
            401, 41, hp=90,
            tools=[_card(501, 51, 0), _card(501, 52, 0)],
        )
    ]
    same_host["select"] = observation["select"]  # type: ignore[index]
    aliases = _extract(same_host)
    alias_step = build_specialist_step_input_v1(aliases, ())

    assert aliases.model_input.candidate_rows[0] == aliases.model_input.candidate_rows[1]
    assert len(alias_step.allowed_semantic_classes) == 1
    assert alias_step.allowed_semantic_classes[0].allowed_alias_count == 2


def test_semantic_action_rejects_forged_operation_parameters_and_endpoint_applicability() -> None:
    """Fails if direct semantic dataclasses admit bytes outside the frozen Option union."""
    row = _extract(_observation()).model_input.candidate_rows[0]

    with pytest.raises(SpecialistFeatureError, match="operation"):
        replace(row, operation="FORGED_OPERATION")
    with pytest.raises(SpecialistFeatureError, match="parameter"):
        replace(row, attack_id=123)
    with pytest.raises(SpecialistFeatureError, match="target"):
        replace(row, target=row.source)

    object.__setattr__(row, "operation", "FORGED_OPERATION")
    with pytest.raises(SpecialistFeatureError, match="operation"):
        validate_specialist_model_input_v1(
            replace(_extract(_observation()).model_input, candidate_rows=(row, row))
        )


def test_semantic_action_rejects_endpoints_outside_the_c1_resolver_projection() -> None:
    """Fails if semantic rows admit endpoint/owner pairs no C1 resolver can emit."""
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 8, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    row = _extract(observation).model_input.candidate_rows[0]
    assert row.option_type == 8

    # ATTACH's source is an actor-owned area-index card.  Area 11/player is
    # excluded from its frozen C1 resolver row, so an owner-resolved source can
    # never be emitted for this option.
    with pytest.raises(SpecialistFeatureError, match="resolver projection"):
        replace(
            row,
            source=SemanticEndpointV1(
                visibility="owner-resolved", owner_role=1, semantic_zone="player",
                card_id=0, host_card_id=0, pokemon=None,
            ),
        )

    assert row.target.pokemon is not None
    opponent_target = replace(
        row.target,
        owner_role=2,
        pokemon=replace(row.target.pokemon, owner_role=2),
    )
    # Both ATTACH source and in-play target are fixed to the actor by C1; a
    # structurally valid opposing target is still outside that projection.
    with pytest.raises(SpecialistFeatureError, match="resolver projection"):
        replace(row, target=opponent_target)


@pytest.mark.parametrize(
    ("option", "selection_type", "selection_context"),
    (
        ({"type": 0, "number": 1}, 8, 38),
        ({"type": 1}, 9, 41),
        ({"type": 2}, 9, 41),
        ({"type": 3, "area": 2, "index": 0, "playerIndex": 0}, 1, 1),
        ({"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}, 2, 26),
        ({"type": 5, "area": 4, "index": 0, "playerIndex": 0, "energyIndex": 0}, 2, 26),
        ({"type": 6, "area": 4, "index": 0, "playerIndex": 0, "energyIndex": 0, "count": 1}, 4, 30),
        ({"type": 7, "index": 0}, 0, 0),
        ({"type": 8, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}, 0, 0),
        ({"type": 9, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}, 0, 0),
        ({"type": 10, "area": 4, "index": 0}, 0, 0),
        ({"type": 11, "area": 4, "index": 0}, 0, 0),
        ({"type": 12}, 0, 0),
        ({"type": 13, "attackId": 0}, 6, 35),
        ({"type": 14}, 0, 0),
        ({"type": 15, "cardId": 501, "serial": 5001}, 5, 34),
        ({"type": 16, "specialConditionType": 0}, 10, 47),
    ),
)
def test_semantic_projection_accepts_one_valid_representative_of_every_c1_option_variant(
    option: dict[str, object], selection_type: int, selection_context: int,
) -> None:
    """Fails if the feature projection drifts from any frozen C1 resolver row."""
    observation = _observation()
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["active"][0]["tools"] = [_card(501, 5001, 0)]
    own["active"][0]["energyCards"] = [_card(401, 4001, 0)]
    observation["select"] = {  # type: ignore[index]
        "context": selection_context, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1, "option": [option],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": selection_type,
    }

    extracted = _extract(observation)

    assert extracted.model_input.candidate_rows[0].option_type == option["type"]
    validate_specialist_model_input_v1(extracted.model_input)


def test_complete_action_enumerator_fails_closed_when_finite_mass_underflows() -> None:
    """Fails if a legal finite-logit semantic class is silently assigned zero mass."""
    observation = _observation()
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    extracted = _extract(observation)

    class ExtremeFinitePolicy:
        def logits(self, model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            return SpecialistStepLogitsV1(
                semantic_logits=(-10_000.0, 0.0), stop_logit=None
            )

    with pytest.raises(SpecialistFeatureError, match="underflow"):
        enumerate_semantic_complete_action_distribution_v1(
            extracted, ExtremeFinitePolicy()
        )


def test_complete_action_enumerator_retains_representable_subnormal_finite_mass() -> None:
    """Fails if the fail-closed guard rejects a finite path that float can represent."""
    observation = _observation()
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    extracted = _extract(observation)

    class NearSubnormalPolicy:
        def logits(self, model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            return SpecialistStepLogitsV1(
                semantic_logits=(-744.4, 0.0), stop_logit=None
            )

    distribution = enumerate_semantic_complete_action_distribution_v1(
        extracted, NearSubnormalPolicy()
    )

    assert min(item.probability for item in distribution) > 0.0
    assert math.fsum(item.probability for item in distribution) == pytest.approx(1.0)
    assert min(item.log_probability for item in distribution) < -744.0


def test_runtime_and_training_share_exact_input_and_step_bytes() -> None:
    """Fails if independently constructed runtime/training primitives serialize differently."""
    runtime = _extract(_observation())
    training = _extract(_observation())
    runtime_step = build_specialist_step_input_v1(runtime, ())
    training_step = build_specialist_step_input_v1(training, ())

    assert canonical_model_input_bytes_v1(runtime.model_input) == canonical_model_input_bytes_v1(training.model_input)
    assert canonical_step_input_bytes_v1(runtime_step) == canonical_step_input_bytes_v1(training_step)


def test_policy_rejects_nonfinite_or_wrong_arity_logits() -> None:
    """Fails if policy logits can violate the shared semantic-class domain."""
    extracted = _extract(_observation())
    step = build_specialist_step_input_v1(extracted, ())

    class BadPolicy:
        def logits(self, model_input, step_input):
            return SpecialistStepLogitsV1(semantic_logits=(float("nan"), float("nan")), stop_logit=0.0)

    with pytest.raises(SpecialistFeatureError, match="finite"):
        evaluate_specialist_step_v1(BadPolicy(), extracted, step)

    class WrongArityPolicy:
        def logits(self, model_input, step_input):
            return SpecialistStepLogitsV1(semantic_logits=(), stop_logit=0.0)

    with pytest.raises(SpecialistFeatureError, match="arity"):
        evaluate_specialist_step_v1(WrongArityPolicy(), extracted, step)
