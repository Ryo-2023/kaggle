"""Task 3B C1 v2 actor-visible decision boundary contracts."""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.meta_specialist import actor_visible_v2
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorVisibleV2Error,
    OPTION_RESOLVER_TABLE_V1,
    build_actor_visible_decision_state_v2,
    derive_local_action_id_v1,
    project_c1v2_to_c1v1_own_private_state,
    project_c1v2_to_c1v1_public_state,
    rebuild_actor_visible_action_binding_core_v1,
    serialize_actor_visible_decision_state_v2,
    deserialize_actor_visible_decision_state_v2,
    validate_actor_visible_decision_state_v2,
    validate_actor_visible_legal_action_v2,
)


def _card(card_id: int, serial: int, owner: int) -> dict[str, object]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    """Official Pokemon deliberately has no ``playerIndex`` field."""
    return {
        "id": card_id,
        "serial": serial,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [1],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _player(*, owner: int, hand: object, active: list[object] | None = None) -> dict[str, object]:
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": hand,
        "handCount": len(hand) if isinstance(hand, list) else 0,
        "paralyzed": False,
        "poisoned": False,
        "prize": [None] * 6,
    }


def _observation() -> dict[str, object]:
    own_hand = [_card(101, 1001, 0), _card(102, 1002, 0)]
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "looking": None,
            "players": [
                _player(owner=0, hand=own_hand, active=[_pokemon(201, 2001)]),
                _player(owner=1, hand=None, active=[_pokemon(301, 3001)]),
            ],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": 1,
            "contextCard": None,
            "deck": None,
            "effect": None,
            "maxCount": 1,
            "minCount": 1,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0,
            "remainEnergyCost": 0,
            "type": 1,
        },
        "step": 7,
    }


def test_v2_derives_pokemon_owner_and_projects_exact_v1_state() -> None:
    """Fails if v2 requires a non-official Pokemon.playerIndex or alters C1 v1."""
    observation = _observation()
    legacy = build_decision_state(observation)

    state = build_actor_visible_decision_state_v2(observation)

    assert state.information_view.self_player.active[0].owner == 0
    assert state.information_view.opponent_player.active[0].owner == 1
    assert project_c1v2_to_c1v1_public_state(state) == legacy.actor_view.public_state
    assert project_c1v2_to_c1v1_own_private_state(state) == legacy.actor_view.own_private_state
    assert state.legacy_public_state_digest == legacy.metadata.public_state_digest
    assert "1001" not in repr(state)


def test_v2_records_only_the_presence_of_a_matching_pokemon_owner_extension_for_v1_parity() -> None:
    """Fails if v2 loses the optional legacy playerIndex topology bit."""
    observation = _observation()
    observation["current"]["players"][0]["active"][0]["playerIndex"] = 0  # type: ignore[index]
    legacy = build_decision_state(observation)

    state = build_actor_visible_decision_state_v2(observation)

    pokemon = state.information_view.self_player.active[0]
    assert pokemon is not None
    assert pokemon.owner == 0
    assert pokemon.ref.legacy_player_index_extension_present is True
    assert project_c1v2_to_c1v1_public_state(state) == legacy.actor_view.public_state


def test_v2_rejects_a_pokemon_owner_extension_that_conflicts_with_its_container() -> None:
    """Fails if an optional wire extension can override derived Pokemon ownership."""
    observation = _observation()
    observation["current"]["players"][0]["active"][0]["playerIndex"] = 1  # type: ignore[index]

    with pytest.raises(ActorVisibleV2Error, match="derived owner"):
        build_actor_visible_decision_state_v2(observation)


def test_v1_projection_is_reconstructed_from_typed_v2_state_not_a_retained_legacy_blob() -> None:
    """Fails if a v2 compatibility projection merely deserializes old state bytes."""
    legacy = build_decision_state(_observation())
    state = build_actor_visible_decision_state_v2(_observation())

    assert not hasattr(state, "legacy_public_state_json")
    assert not hasattr(state, "legacy_own_private_state_json")
    assert not hasattr(state, "legacy_trace_payload_json")
    assert project_c1v2_to_c1v1_public_state(state) == legacy.actor_view.public_state
    assert project_c1v2_to_c1v1_own_private_state(state) == legacy.actor_view.own_private_state
    assert state.to_public_trace_payload() == legacy.to_trace_payload()


def test_public_trace_has_no_stored_json_injection_surface() -> None:
    """Direct construction/replace cannot substitute an unrelated private trace blob."""
    state = build_actor_visible_decision_state_v2(_observation())

    with pytest.raises(TypeError, match="legacy_trace_payload_json"):
        replace(
            state,
            legacy_trace_payload_json='{"hand_card_ids":[101],"secret":101}',
        )


def test_actor_one_uses_the_same_owner_derived_public_and_private_boundary() -> None:
    """Fails if C1v2 accidentally treats player zero as the only actor."""
    observation = _observation()
    observation["current"]["yourIndex"] = 1  # type: ignore[index]
    players = observation["current"]["players"]  # type: ignore[index]
    players[0]["hand"] = None
    players[0]["handCount"] = 0
    players[1]["hand"] = [_card(701, 7001, 1), _card(702, 7002, 1)]
    players[1]["handCount"] = 2
    observation["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": 0, "playerIndex": 1},
        {"type": 3, "area": 2, "index": 1, "playerIndex": 1},
    ]
    legacy = build_decision_state(observation)

    state = build_actor_visible_decision_state_v2(observation)

    assert state.information_view.actor == 1
    assert state.information_view.self_player.active[0].owner == 1
    assert state.legal_actions[0].binding.core.source.owner_player_index == 1
    assert project_c1v2_to_c1v1_public_state(state) == legacy.actor_view.public_state
    assert project_c1v2_to_c1v1_own_private_state(state) == legacy.actor_view.own_private_state


def test_v2_fails_closed_on_private_shape_and_scalar_violations() -> None:
    """Covers fields C1 v1 need not consume but C1v2 may safely retain locally."""
    cases: list[dict[str, object]] = []

    hand_count_mismatch = _observation()
    hand_count_mismatch["current"]["players"][0]["handCount"] = 1  # type: ignore[index]
    cases.append(hand_count_mismatch)

    opponent_hand_contents = _observation()
    opponent_hand_contents["current"]["players"][1]["hand"] = [_card(711, 7101, 1)]  # type: ignore[index]
    cases.append(opponent_hand_contents)

    overlong_looking = _observation()
    overlong_looking["current"]["looking"] = [_card(712, 7102, 0)] * 61  # type: ignore[index]
    cases.append(overlong_looking)

    overlong_hand = _observation()
    overlong_hand["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(720 + index, 7200 + index, 0) for index in range(61)
    ]
    overlong_hand["current"]["players"][0]["handCount"] = 61  # type: ignore[index]
    cases.append(overlong_hand)

    overlong_reveal = _observation()
    overlong_reveal["current"]["players"][0]["deckCount"] = 61  # type: ignore[index]
    overlong_reveal["select"]["deck"] = [  # type: ignore[index]
        _card(790 + index, 7900 + index, 0) for index in range(61)
    ]
    cases.append(overlong_reveal)

    bool_counter = _observation()
    bool_counter["select"]["remainDamageCounter"] = True  # type: ignore[index]
    cases.append(bool_counter)

    ownerless_context = _observation()
    ownerless_context["select"]["contextCard"] = {"id": 713, "serial": 7103}  # type: ignore[index]
    cases.append(ownerless_context)

    for observation in cases:
        with pytest.raises(ActorVisibleV2Error):
            build_actor_visible_decision_state_v2(observation)


@pytest.mark.parametrize("candidate_count", (61, 67))
def test_v2_allows_large_legal_option_sets_without_widening_card_collection_limits(
    candidate_count: int,
) -> None:
    """Observed MAIN attach combinations may exceed 60 while every card list stays bounded."""
    observation = _observation()
    own_hand = [_card(900 + index, 1900 + index, 0) for index in range(60)]
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["hand"] = own_hand
    own["handCount"] = len(own_hand)
    own["bench"] = [_pokemon(801, 8001)]
    options = [
        {"type": 8, "area": 2, "index": index, "inPlayArea": 4, "inPlayIndex": 0}
        for index in range(60)
    ]
    options.extend(
        {"type": 8, "area": 2, "index": index, "inPlayArea": 5, "inPlayIndex": 0}
        for index in range(candidate_count - len(options))
    )
    observation["select"] = {
        "context": 0,
        "contextCard": None,
        "deck": None,
        "effect": None,
        "maxCount": 1,
        "minCount": 1,
        "option": options,
        "remainDamageCounter": 0,
        "remainEnergyCost": 0,
        "type": 0,
    }

    state = build_actor_visible_decision_state_v2(observation)

    assert len(state.legal_actions) == candidate_count
    assert len({action.action_key_digest for action in state.legal_actions}) == candidate_count
    assert len({action.local_action_id for action in state.legal_actions}) == candidate_count


def test_v2_rejects_513_options_before_the_frozen_v1_builder_traverses_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw cap preflight is length-only and precedes expensive v1 option parsing."""
    observation = _observation()
    observation["select"]["option"] = [object()] * 513  # type: ignore[index]
    v1_called = False

    def forbidden_v1_call(value: object) -> object:
        del value
        nonlocal v1_called
        v1_called = True
        raise AssertionError("v1 builder must not receive an oversized option list")

    monkeypatch.setattr(actor_visible_v2, "build_decision_state", forbidden_v1_call)

    with pytest.raises(ActorVisibleV2Error, match="select.option exceeds"):
        build_actor_visible_decision_state_v2(observation)
    assert v1_called is False


@pytest.mark.parametrize(
    ("mutate", "expected"),
    (
        (lambda observation: observation["current"].__setitem__("players", [object(), object(), object()]), "players"),
        (lambda observation: observation["current"].__setitem__("stadium", [object(), object()]), "stadium"),
        (lambda observation: observation["current"]["players"][0].__setitem__("active", [object(), object()]), "active"),
        (lambda observation: observation["current"]["players"][0].__setitem__("hand", [object()] * 61), "hand"),
        (lambda observation: observation["current"]["players"][0].__setitem__("prize", [object()] * 61), "prize"),
        (lambda observation: observation["current"]["players"][1].__setitem__("prize", [object()] * 61), "prize"),
        (lambda observation: observation["current"]["players"][0].__setitem__("bench", [object()] * 61), "bench"),
        (lambda observation: observation["current"]["players"][0].__setitem__("discard", [object()] * 61), "discard"),
        (lambda observation: observation["select"].__setitem__("deck", [object()] * 61), "deck"),
        (lambda observation: observation["current"].__setitem__("looking", [object()] * 61), "looking"),
        (lambda observation: observation["current"]["players"][0]["active"][0].__setitem__("tools", [object()] * 61), "tools"),
        (lambda observation: observation["current"]["players"][0]["active"][0].__setitem__("energyCards", [object()] * 61), "energyCards"),
        (lambda observation: observation["current"]["players"][0]["active"][0].__setitem__("preEvolution", [object()] * 61), "preEvolution"),
    ),
)
def test_topology_preflight_rejects_every_bounded_container_before_v1_touches_elements(
    monkeypatch: pytest.MonkeyPatch, mutate: object, expected: str,
) -> None:
    """Fails if an oversized allowlisted container reaches frozen v1 traversal."""
    observation = _observation()
    mutate(observation)  # type: ignore[operator]
    called = False

    def forbidden_v1_call(value: object) -> object:
        del value
        nonlocal called
        called = True
        raise AssertionError("v1 must not inspect an over-cap topology")

    monkeypatch.setattr(actor_visible_v2, "build_decision_state", forbidden_v1_call)
    with pytest.raises(ActorVisibleV2Error, match=expected):
        build_actor_visible_decision_state_v2(observation)
    assert called is False


def test_topology_preflight_checks_opponent_hand_is_null_without_touching_its_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opponent hand content is forbidden even when it is an opaque sequence."""
    observation = _observation()
    observation["current"]["players"][1]["hand"] = [object()] * 61  # type: ignore[index]
    called = False

    def forbidden_v1_call(value: object) -> object:
        del value
        nonlocal called
        called = True
        raise AssertionError("v1 must not inspect opponent hand")

    monkeypatch.setattr(actor_visible_v2, "build_decision_state", forbidden_v1_call)
    with pytest.raises(ActorVisibleV2Error, match="opponent.*hand"):
        build_actor_visible_decision_state_v2(observation)
    assert called is False


def test_duplicate_frozen_actionkeys_fail_before_any_binding_resolver_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate generic ActionKeys are rejected before local binding allocation/resolution."""
    observation = _observation()
    key = build_actor_visible_decision_state_v2(observation).legal_actions[0].action_key
    frozen = SimpleNamespace(legal_actions=(
        SimpleNamespace(option_index=0, action_key=key),
        SimpleNamespace(option_index=1, action_key=key),
    ))
    resolver_called = False

    def forbidden_resolver(*args: object, **kwargs: object) -> object:
        del args, kwargs
        nonlocal resolver_called
        resolver_called = True
        raise AssertionError("duplicate ActionKeys must stop before resolver")

    monkeypatch.setattr(actor_visible_v2, "build_decision_state", lambda value: frozen)
    monkeypatch.setattr(actor_visible_v2, "_source_from_option", forbidden_resolver)
    with pytest.raises(ActorVisibleV2Error, match="globally unique ActionKey"):
        build_actor_visible_decision_state_v2(observation)
    assert resolver_called is False


def test_v2_keeps_private_candidates_unique_when_public_action_ids_collide() -> None:
    """Fails if v2 repeats v1's public-identity uniqueness restriction."""
    state = build_actor_visible_decision_state_v2(_observation())

    assert len(state.legal_actions) == 2
    assert len({action.action_key_digest for action in state.legal_actions}) == 2
    assert len({action.local_action_id for action in state.legal_actions}) == 2
    assert len({action.public_action_id for action in state.legal_actions}) == 1
    assert all(not hasattr(action.binding.core, "local_action_id") for action in state.legal_actions)
    assert all(not hasattr(action.binding.core, "public_action_id") for action in state.legal_actions)
    assert state.public_collision_groups == ((state.legal_actions[0].public_action_id, 2),)
    assert "1001" not in json.dumps(state.to_public_trace_payload(), sort_keys=True)


def _resolver_observation(option: dict[str, object], *, select_type: int, context: int) -> dict[str, object]:
    observation = _observation()
    observation["select"] = {
        "context": context,
        "contextCard": None,
        "deck": None,
        "effect": None,
        "maxCount": 1,
        "minCount": 1,
        "option": [option],
        "remainDamageCounter": 0,
        "remainEnergyCost": 0,
        "type": select_type,
    }
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["active"][0]["energyCards"] = [_card(401, 4001, 0)]
    own["active"][0]["tools"] = [_card(501, 5001, 0)]
    return observation


def test_resolver_table_is_total_for_all_official_option_variants_and_areas() -> None:
    """Fails if a new resolver guesses or omits an official Option union member."""
    assert set(OPTION_RESOLVER_TABLE_V1) == set(range(17))
    assert set().union(*(row.legal_source_areas for row in OPTION_RESOLVER_TABLE_V1.values())) == set(range(1, 13))
    assert OPTION_RESOLVER_TABLE_V1[4].source_resolver == "attached-tool"
    assert OPTION_RESOLVER_TABLE_V1[4].target_resolver == "in-play-pokemon"
    assert OPTION_RESOLVER_TABLE_V1[4].host_resolver == "in-play-pokemon"
    assert OPTION_RESOLVER_TABLE_V1[8].source_resolver == "area-index"
    assert OPTION_RESOLVER_TABLE_V1[8].target_resolver == "in-play-pokemon"
    assert OPTION_RESOLVER_TABLE_V1[8].host_resolver == "not-applicable"
    assert OPTION_RESOLVER_TABLE_V1[3].source_missing_reasons == frozenset({
        "hidden-zone", "not-addressable",
    })


def test_resolver_table_literal_snapshot_covers_every_contract_field() -> None:
    """Fails on any unreviewed semantic drift in the closed Option resolver table."""
    expected = {
        0: ("NUMBER", "unavailable", "unavailable", "number", "not-applicable", "not-applicable", (), (), (), (), ()),
        1: ("YES", "unavailable", "unavailable", "not-applicable", "not-applicable", "not-applicable", (), (), (), (), ()),
        2: ("NO", "unavailable", "unavailable", "not-applicable", "not-applicable", "not-applicable", (), (), (), (), ()),
        3: ("CARD", "option.playerIndex", "unavailable", "area-index", "not-applicable", "not-applicable", tuple(range(1, 13)), (), ("hidden-zone", "not-addressable"), (), ()),
        4: ("TOOL_CARD", "option.playerIndex", "option.playerIndex", "attached-tool", "in-play-pokemon", "in-play-pokemon", (4, 5), (4, 5), (), (), ()),
        5: ("ENERGY_CARD", "option.playerIndex", "option.playerIndex", "attached-energy", "in-play-pokemon", "in-play-pokemon", (4, 5), (4, 5), (), (), ()),
        6: ("ENERGY", "option.playerIndex", "option.playerIndex", "attached-energy", "in-play-pokemon", "in-play-pokemon", (4, 5), (4, 5), (), (), ()),
        7: ("PLAY", "actor", "unavailable", "actor-hand", "not-applicable", "not-applicable", (2,), (), (), (), ()),
        8: ("ATTACH", "actor", "actor", "area-index", "in-play-pokemon", "not-applicable", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12), (4, 5), ("hidden-zone", "not-addressable"), (), ()),
        9: ("EVOLVE", "actor", "actor", "area-index", "in-play-pokemon", "not-applicable", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12), (4, 5), ("hidden-zone", "not-addressable"), (), ()),
        10: ("ABILITY", "area-dependent:4,5=actor;7=stadium-card.playerIndex", "unavailable", "area-index", "not-applicable", "not-applicable", (4, 5, 7), (), (), (), ()),
        11: ("DISCARD", "area-dependent:4,5=actor;7=stadium-card.playerIndex", "unavailable", "area-index", "not-applicable", "not-applicable", (4, 5, 7), (), (), (), ()),
        12: ("RETREAT", "actor", "unavailable", "actor-active", "not-applicable", "not-applicable", (), (), ("not-addressable",), (), ()),
        13: ("ATTACK", "actor", "unavailable", "actor-active", "not-applicable", "not-applicable", (), (), ("not-addressable",), (), ()),
        14: ("END", "unavailable", "unavailable", "not-applicable", "not-applicable", "not-applicable", (), (), (), (), ()),
        15: ("SKILL", "registry", "unavailable", "bounded-card-registry", "not-applicable", "not-applicable", (), (), ("ambiguous-registry", "not-addressable"), (), ()),
        16: ("SPECIAL_CONDITION", "unavailable", "unavailable", "special-condition", "not-applicable", "not-applicable", (), (), (), (), ()),
    }

    actual = {
        option_type: (
            row.operation,
            row.source_owner,
            row.target_owner,
            row.source_resolver,
            row.target_resolver,
            row.host_resolver,
            tuple(sorted(row.legal_source_areas)),
            tuple(sorted(row.legal_target_areas)),
            tuple(sorted(row.source_missing_reasons)),
            tuple(sorted(row.target_missing_reasons)),
            tuple(sorted(row.host_missing_reasons)),
        )
        for option_type, row in OPTION_RESOLVER_TABLE_V1.items()
    }
    assert actual == expected


def test_binding_endpoint_resolution_kinds_have_disjoint_exact_shapes() -> None:
    """Fails if one endpoint payload can satisfy two resolution meanings."""
    card = actor_visible_v2.BoundCardRefV1(card_id=101, serial=1001, player_index=0)
    endpoint = actor_visible_v2.ActorVisibleBindingEndpointV1

    endpoint("not-applicable", None, "not-applicable", None, None)
    endpoint("special-condition", None, "not-applicable", None, None)
    endpoint("owner-resolved", 0, "player", None, None)
    endpoint("actor-visible", 0, "hand", card, None)
    endpoint("public-visible", 0, "active", card, None)
    endpoint("hidden-unresolved", 1, "deck", None, "hidden-zone")

    invalid_shapes = (
        ("not-applicable", 0, "hand", None, None),
        ("special-condition", None, "hidden", None, None),
        ("owner-resolved", 0, "active", None, None),
        ("owner-resolved", 0, "player", card, None),
        ("actor-visible", 0, "hand", None, None),
        ("public-visible", 1, "active", card, None),
        ("hidden-unresolved", 1, "deck", card, "hidden-zone"),
        ("hidden-unresolved", 1, "player", None, "hidden-zone"),
    )
    for values in invalid_shapes:
        with pytest.raises(ActorVisibleV2Error):
            endpoint(*values)


def test_resolver_binds_all_seventeen_official_option_types_without_option_ordinal_identity() -> None:
    """Fails if a v2 resolver is partial or uses the list ordinal as identity."""
    cases = (
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
    )
    for option, select_type, context in cases:
        state = build_actor_visible_decision_state_v2(
            _resolver_observation(option, select_type=select_type, context=context)
        )
        action = state.legal_actions[0]
        assert action.binding.core.source.resolution_kind in {
            "actor-visible", "public-visible", "hidden-unresolved", "not-applicable",
            "special-condition",
        }
        assert action.local_action_id != action.action_key_digest


def test_card_resolver_covers_every_official_area_with_closed_endpoint_meaning() -> None:
    """Fails if an AreaType branch is untested or maps a hidden locator to a card."""
    expected_zones = {
        1: "deck",
        2: "hand",
        3: "discard",
        4: "active",
        5: "bench",
        6: "prize",
        7: "stadium",
        8: "energy",
        9: "tool",
        10: "pre-evolution",
        11: "player",
        12: "looking",
    }
    for area, expected_zone in expected_zones.items():
        observation = _resolver_observation(
            {"type": 3, "area": area, "index": 0, "playerIndex": 0},
            select_type=1,
            context=1,
        )
        own = observation["current"]["players"][0]  # type: ignore[index]
        if area == 3:
            own["discard"] = [_card(601, 6001, 0)]
        elif area == 5:
            own["bench"] = [_pokemon(602, 6002)]
        elif area == 7:
            observation["current"]["stadium"] = [_card(603, 6003, 0)]  # type: ignore[index]

        endpoint = build_actor_visible_decision_state_v2(
            observation
        ).legal_actions[0].binding.core.source
        assert endpoint.semantic_zone == expected_zone
        if area == 11:
            assert endpoint.resolution_kind == "owner-resolved"
            assert endpoint.owner_player_index == 0
            assert endpoint.bound_card is None
            assert endpoint.missing_reason is None
        elif area in {1, 6, 8, 9, 10, 12}:
            assert endpoint.bound_card is None
            assert endpoint.missing_reason in {"hidden-zone", "not-addressable"}
        else:
            assert endpoint.bound_card is not None
            assert endpoint.missing_reason is None


def test_visibility_kind_is_determined_by_information_source_not_card_owner() -> None:
    """Public board cards remain public and selection-only cards remain actor-visible."""
    active_action = build_actor_visible_decision_state_v2(_resolver_observation(
        {"type": 10, "area": 4, "index": 0}, select_type=0, context=0
    )).legal_actions[0]
    tool_action = build_actor_visible_decision_state_v2(_resolver_observation(
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        select_type=2,
        context=26,
    )).legal_actions[0]

    assert active_action.binding.core.source.resolution_kind == "public-visible"
    assert tool_action.binding.core.source.resolution_kind == "public-visible"
    assert tool_action.binding.core.target.resolution_kind == "public-visible"
    assert tool_action.binding.core.host.resolution_kind == "public-visible"


def test_opponent_owned_looking_card_is_actor_visible_and_owner_checked() -> None:
    """LOOKING visibility comes from the supplied list, not ownership."""
    observation = _resolver_observation(
        {"type": 3, "area": 12, "index": 0, "playerIndex": 1},
        select_type=1,
        context=1,
    )
    observation["current"]["looking"] = [_card(850, 8500, 1)]  # type: ignore[index]

    endpoint = build_actor_visible_decision_state_v2(
        observation
    ).legal_actions[0].binding.core.source
    assert endpoint.resolution_kind == "actor-visible"
    assert endpoint.owner_player_index == 1
    assert endpoint.bound_card is not None
    assert endpoint.bound_card.card_id == 850

    observation["select"]["option"][0]["playerIndex"] = 0  # type: ignore[index]
    with pytest.raises(ActorVisibleV2Error, match="owner"):
        build_actor_visible_decision_state_v2(observation)


@pytest.mark.parametrize("selection_field", ("contextCard", "effect"))
def test_opponent_owned_selection_card_skill_is_actor_visible(selection_field: str) -> None:
    """Context/effect cards are visible because CABT supplied them to this actor."""
    observation = _resolver_observation(
        {"type": 15, "cardId": 851, "serial": 8510}, select_type=5, context=34
    )
    observation["select"][selection_field] = _card(851, 8510, 1)  # type: ignore[index]

    endpoint = build_actor_visible_decision_state_v2(
        observation
    ).legal_actions[0].binding.core.source
    assert endpoint.resolution_kind == "actor-visible"
    assert endpoint.owner_player_index == 1


def test_attached_card_binding_uses_the_same_public_pokemon_for_target_and_host() -> None:
    """Fails if attachment source loses its exact host or leaks an implicit owner."""
    action = build_actor_visible_decision_state_v2(_resolver_observation(
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        select_type=2,
        context=26,
    )).legal_actions[0]

    core = action.binding.core
    assert core.source.semantic_zone == "active-tool"
    assert core.source.bound_card is not None
    assert core.source.bound_card.card_id == 501
    assert core.target == core.host
    assert core.target.semantic_zone == "active"
    assert core.target.bound_card is not None
    assert core.target.bound_card.card_id == 201


def test_option_permutation_preserves_local_binding_id_and_remaps_execution_position() -> None:
    """Fails if private identity incorporates the transient CABT option ordinal."""
    first = _observation()
    second = _observation()
    second["select"]["option"] = list(reversed(second["select"]["option"]))  # type: ignore[index]

    first_state = build_actor_visible_decision_state_v2(first)
    second_state = build_actor_visible_decision_state_v2(second)

    assert {item.local_action_id for item in first_state.legal_actions} == {
        item.local_action_id for item in second_state.legal_actions
    }
    assert [item.action_key_digest for item in first_state.legal_actions] == list(reversed(
        [item.action_key_digest for item in second_state.legal_actions]
    ))


def test_two_tool_skill_order_keeps_private_candidates_distinct_while_public_sources_stay_redacted() -> None:
    """Fails if Skill binding loses its host zone or public collisions force fallback."""
    observation = _resolver_observation(
        {"type": 15, "cardId": 501, "serial": 5001}, select_type=5, context=34
    )
    observation["select"]["minCount"] = 2  # type: ignore[index]
    observation["select"]["maxCount"] = 2  # type: ignore[index]
    observation["select"]["option"] = [  # type: ignore[index]
        {"type": 15, "cardId": 501, "serial": 5001},
        {"type": 15, "cardId": 502, "serial": 5002},
    ]
    observation["current"]["players"][0]["active"][0]["tools"].append(_card(502, 5002, 0))  # type: ignore[index]

    state = build_actor_visible_decision_state_v2(observation)

    assert state.information_view.selection_type == 5
    assert state.information_view.selection_context == 34
    assert [action.binding.core.source.semantic_zone for action in state.legal_actions] == [
        "active-tool", "active-tool",
    ]
    assert len({action.local_action_id for action in state.legal_actions}) == 2
    assert len({action.public_action_id for action in state.legal_actions}) == 1


def test_hidden_prize_card_locator_is_explicitly_unresolved_but_visible_hand_overflow_fails() -> None:
    """Fails if the resolver guesses hidden cards or tolerates a bad visible index."""
    hidden = _resolver_observation(
        {"type": 3, "area": 6, "index": 99, "playerIndex": 1}, select_type=1, context=1
    )
    hidden_state = build_actor_visible_decision_state_v2(hidden)
    assert hidden_state.legal_actions[0].binding.core.source.bound_card is None
    assert hidden_state.legal_actions[0].binding.core.source.resolution_kind == "hidden-unresolved"
    assert hidden_state.legal_actions[0].binding.core.source.missing_reason == "hidden-zone"

    visible_overflow = _resolver_observation(
        {"type": 3, "area": 2, "index": 2, "playerIndex": 0}, select_type=1, context=1
    )
    with pytest.raises(ActorVisibleV2Error, match="actor-visible hand"):
        build_actor_visible_decision_state_v2(visible_overflow)


def test_resolver_rejects_an_area_outside_its_frozen_option_row() -> None:
    """Fails if a resolver can bypass its table by calling a generic locator."""
    ability_from_hand = _resolver_observation(
        {"type": 10, "area": 2, "index": 0}, select_type=0, context=0
    )

    with pytest.raises(ActorVisibleV2Error, match="not allowed for this OptionType"):
        build_actor_visible_decision_state_v2(ability_from_hand)


def test_ability_and_discard_use_the_generic_stadium_locator_when_offered() -> None:
    """Fails on observed Type 10/11 AreaType.STADIUM candidates if table is too narrow."""
    for option_type in (10, 11):
        observation = _resolver_observation(
            {"type": option_type, "area": 7, "index": 0}, select_type=0, context=0
        )
        observation["current"]["stadium"] = [_card(801, 8001, 1)]  # type: ignore[index]

        endpoint = build_actor_visible_decision_state_v2(
            observation
        ).legal_actions[0].binding.core.source
        assert (
            OPTION_RESOLVER_TABLE_V1[option_type].source_owner
            == "area-dependent:4,5=actor;7=stadium-card.playerIndex"
        )
        assert endpoint.semantic_zone == "stadium"
        assert endpoint.resolution_kind == "public-visible"
        assert endpoint.owner_player_index == 1
        assert endpoint.bound_card is not None
        assert endpoint.bound_card.card_id == 801


@pytest.mark.parametrize(
    "option",
    (
        {"type": 3, "area": 7, "index": 0, "playerIndex": 0},
        {"type": 8, "area": 7, "index": 0, "inPlayArea": 4, "inPlayIndex": 0},
        {"type": 9, "area": 7, "index": 0, "inPlayArea": 4, "inPlayIndex": 0},
    ),
)
def test_stadium_source_rejects_card_owner_that_conflicts_with_its_resolver_row(
    option: dict[str, object],
) -> None:
    """CARD uses playerIndex while ATTACH/EVOLVE use the actor as stadium owner."""
    observation = _resolver_observation(option, select_type=1 if option["type"] == 3 else 0, context=1 if option["type"] == 3 else 0)
    observation["current"]["stadium"] = [_card(801, 8001, 1)]  # type: ignore[index]
    with pytest.raises(ActorVisibleV2Error, match="stadium.*owner|owner.*stadium"):
        build_actor_visible_decision_state_v2(observation)


def test_card_resolver_rejects_a_non_player_owner_instead_of_hiding_it() -> None:
    """Fails if invalid explicit ownership is mistaken for opponent-hidden hand."""
    invalid_owner = _resolver_observation(
        {"type": 3, "area": 2, "index": 0, "playerIndex": 2}, select_type=1, context=1
    )

    with pytest.raises(ActorVisibleV2Error, match="not a valid C1 v1 decision"):
        build_actor_visible_decision_state_v2(invalid_owner)


def test_local_action_id_is_derived_from_binding_core_and_rejects_tampering() -> None:
    """Fails if a binding can self-authorize an ID or public projection leaks into it."""
    state = build_actor_visible_decision_state_v2(_observation())
    action = state.legal_actions[0]

    assert action.binding.core.source.bound_card is not None
    assert action.local_action_id != action.public_action_id
    assert set(action.binding.core.to_identity_dict()) == {
        "schema_version",
        "source",
        "target",
        "host",
    }
    assert action.binding.core.schema_version == "actor-visible-action-binding-v1"
    assert set(action.binding.core.source.to_identity_dict()) == {
        "resolution_kind",
        "owner_player_index",
        "semantic_zone",
        "bound_card",
        "missing_reason",
    }
    with pytest.raises(TypeError, match="local_action_id"):
        type(action.binding.core)(
            schema_version=action.binding.core.schema_version,
            source=action.binding.core.source,
            target=action.binding.core.target,
            host=action.binding.core.host,
            local_action_id=action.local_action_id,
        )

    assert action.binding.core.source.bound_card is not None
    tampered_source = replace(
        action.binding.core.source,
        bound_card=replace(action.binding.core.source.bound_card, serial=9001),
    )
    tampered_core = replace(action.binding.core, source=tampered_source)
    tampered_local_action_id = derive_local_action_id_v1(
        action_key_digest=action.action_key_digest,
        binding_core=tampered_core,
    )
    assert tampered_local_action_id != action.local_action_id
    tampered_binding = replace(
        action.binding,
        core=tampered_core,
        local_action_id=tampered_local_action_id,
    )
    tampered_action = replace(action, binding=tampered_binding)
    assert tampered_action.local_action_id == tampered_local_action_id
    with pytest.raises(ActorVisibleV2Error, match="typed decision state"):
        validate_actor_visible_legal_action_v2(
            state.information_view,
            tampered_action,
        )
    with pytest.raises(ActorVisibleV2Error, match="typed decision state"):
        replace(
            state,
            legal_actions=(tampered_action, *state.legal_actions[1:]),
        )

    with pytest.raises(ActorVisibleV2Error, match="local_action_id"):
        replace(action.binding, local_action_id="0" * 64)
    with pytest.raises(ActorVisibleV2Error, match="public_action_id"):
        replace(
            action,
            binding=replace(action.binding, public_action_id="0" * 64),
        )


def test_binding_rebuilder_uses_actionkey_actor_payload_not_stored_binding() -> None:
    """The exported Task5 boundary deterministically rebuilds the authoritative core."""
    state = build_actor_visible_decision_state_v2(_observation())
    action = state.legal_actions[0]

    assert rebuild_actor_visible_action_binding_core_v1(
        state.information_view,
        action.action_key,
    ) == action.binding.core

    with pytest.raises(ActorVisibleV2Error):
        replace(state, public_collision_groups=())


def test_public_state_validator_rechecks_the_complete_persisted_state() -> None:
    """The exported loader boundary re-runs IDs, bindings, counts, and collisions."""
    state = build_actor_visible_decision_state_v2(_observation())

    assert actor_visible_v2.validate_actor_visible_decision_state_v2(state) is state
    with pytest.raises(ActorVisibleV2Error, match="wrong type"):
        actor_visible_v2.validate_actor_visible_decision_state_v2(object())


def test_typed_tree_rejects_replace_list_bool_and_invalid_nested_values() -> None:
    """Frozen wrappers cannot admit mutable collections or bool-shaped scalar state."""
    state = build_actor_visible_decision_state_v2(_observation())
    view = state.information_view
    with pytest.raises(ActorVisibleV2Error, match="non-bool int|0 or 1"):
        actor_visible_v2.CardRefV2(1, 1, True)
    with pytest.raises(ActorVisibleV2Error, match="non-bool int|0 or 1"):
        actor_visible_v2.BoundCardRefV1(1, 1, False)
    with pytest.raises(ActorVisibleV2Error, match="tuple"):
        replace(view.private_state, own_hand=list(view.private_state.own_hand))
    with pytest.raises(ActorVisibleV2Error, match="bool"):
        replace(view, step=True)
    with pytest.raises(ActorVisibleV2Error, match="bool"):
        replace(view, stadium_played=1)
    with pytest.raises(ActorVisibleV2Error, match="tuple"):
        replace(view.self_player, bench=[])
    with pytest.raises(ActorVisibleV2Error, match="at least"):
        replace(view.self_player, deck_count=-1)
    with pytest.raises(ActorVisibleV2Error, match="tuple"):
        object.__setattr__(view.private_state, "own_hand", [view.private_state.own_hand[0]])
        validate_actor_visible_decision_state_v2(state)


def test_information_view_cross_owner_and_reveal_cardinality_invariants_are_eager() -> None:
    """Direct replacement cannot swap an opponent card into actor-owned typed zones."""
    state = build_actor_visible_decision_state_v2(_observation())
    view = state.information_view
    opponent_card = actor_visible_v2.BoundCardRefV1(901, 9001, 1)
    with pytest.raises(ActorVisibleV2Error, match="discard owner"):
        replace(view, self_player=replace(view.self_player, discard=(opponent_card,)))
    opponent_pokemon = replace(view.opponent_player.active[0], owner=1)
    with pytest.raises(ActorVisibleV2Error, match="Pokemon owner"):
        replace(view, self_player=replace(view.self_player, active=(opponent_pokemon,)))
    reveal = (actor_visible_v2.CardRefV2(902, 9002, 0),)
    with pytest.raises(ActorVisibleV2Error, match="deck_reveal.*deck_count"):
        replace(view, private_state=replace(
            view.private_state,
            selection_view=replace(view.private_state.selection_view, deck_reveal=reveal),
        ))


def test_exact_local_state_serializer_round_trips_and_rejects_open_or_non_strict_payloads() -> None:
    """The local persistence boundary is closed, canonical, typed, and revalidated."""
    state = build_actor_visible_decision_state_v2(_observation())
    payload = serialize_actor_visible_decision_state_v2(state)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    restored = deserialize_actor_visible_decision_state_v2(json.loads(encoded))
    assert serialize_actor_visible_decision_state_v2(restored) == payload
    assert json.dumps(serialize_actor_visible_decision_state_v2(restored), sort_keys=True, separators=(",", ":")) == encoded
    assert set(payload) == {"schema_version", "information_view", "legal_actions", "public_collision_groups"}
    assert set(payload["information_view"]) == {
        "actor", "self_player", "opponent_player", "private_state", "board_stadium",
        "stadium_played", "supporter_played", "energy_attached", "retreated", "first_player",
        "observed_result", "step", "turn", "turn_action_count", "remain_damage_counter",
        "remain_energy_cost", "selection_type", "selection_context", "min_count", "max_count",
    }
    assert set(payload["information_view"]["private_state"]["selection_view"]) == {
        "schema_version", "context_card", "effect", "deck_reveal", "looking",
    }
    assert set(payload["legal_actions"][0]) == {"binding", "action_key"}
    assert set(payload["legal_actions"][0]["binding"]) == {
        "core", "action_key_digest", "public_action_id", "local_action_id",
    }

    unknown = dict(payload)
    unknown["unknown"] = None
    with pytest.raises(ActorVisibleV2Error, match="exact keys"):
        deserialize_actor_visible_decision_state_v2(unknown)
    missing = dict(payload)
    del missing["legal_actions"]
    with pytest.raises(ActorVisibleV2Error, match="exact keys"):
        deserialize_actor_visible_decision_state_v2(missing)
    non_strict = json.loads(encoded)
    non_strict["information_view"]["step"] = True
    with pytest.raises(ActorVisibleV2Error, match="non-bool int"):
        deserialize_actor_visible_decision_state_v2(non_strict)


def test_exact_local_state_parser_rechecks_nonredacted_tool_actionkey_against_public_board() -> None:
    """A persisted ToolCard identity must still resolve against this typed public board."""
    state = build_actor_visible_decision_state_v2(_resolver_observation(
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        select_type=2, context=26,
    ))
    restored = deserialize_actor_visible_decision_state_v2(
        json.loads(json.dumps(serialize_actor_visible_decision_state_v2(state)))
    )
    assert restored.legal_actions[0].action_key.digest == state.legal_actions[0].action_key.digest


def test_state_post_init_rejects_count_context_id_and_schema_drift() -> None:
    """Persisted states cannot bypass their closed selection and identity schema."""
    state = build_actor_visible_decision_state_v2(_observation())

    with pytest.raises(ActorVisibleV2Error, match="selection counts"):
        replace(
            state,
            information_view=replace(state.information_view, max_count=3),
        )
    with pytest.raises(ActorVisibleV2Error, match="selection type/context"):
        replace(
            state,
            information_view=replace(state.information_view, selection_context=999),
        )
    with pytest.raises(ActorVisibleV2Error, match="globally unique ActionKey"):
        replace(
            state,
            legal_actions=(state.legal_actions[0], state.legal_actions[0]),
        )
    with pytest.raises(ActorVisibleV2Error, match="schema_version"):
        replace(state.legal_actions[0].binding.core, schema_version="forged")


def test_schema_versions_and_candidate_limit_are_public_contract_values() -> None:
    state = build_actor_visible_decision_state_v2(_observation())

    assert state.schema_version == actor_visible_v2.C1_V2_SCHEMA_VERSION == 2
    assert (
        state.information_view.private_state.selection_view.schema_version
        == actor_visible_v2.ACTOR_VISIBLE_SELECTION_SCHEMA_VERSION
        == "actor-visible-selection-v1"
    )
    assert actor_visible_v2.MAX_LEGAL_CANDIDATES_V2 == 512


def test_legitimate_resolver_input_change_produces_a_different_local_action_id() -> None:
    """A real visible-card change is authenticated, not merely a forged-core change."""
    original = build_actor_visible_decision_state_v2(_observation())
    changed_observation = _observation()
    changed_observation["current"]["players"][0]["hand"][0]["serial"] = 9001  # type: ignore[index]
    changed = build_actor_visible_decision_state_v2(changed_observation)

    assert original.legal_actions[0].action_key_digest == changed.legal_actions[0].action_key_digest
    assert original.legal_actions[0].local_action_id != changed.legal_actions[0].local_action_id


def test_private_reveals_are_not_retained_in_public_trace_or_redacted_repr() -> None:
    """Fails if actor-visible C1v2 leaks a hand/reveal serial through a public surface."""
    observation = _observation()
    observation["current"]["looking"] = [_card(777001, 777002, 1)]  # type: ignore[index]
    observation["select"]["contextCard"] = _card(777003, 777004, 1)  # type: ignore[index]
    observation["select"]["effect"] = _card(777005, 777006, 0)  # type: ignore[index]
    observation["logs"] = {"opaque": object()}
    observation["search_begin_input"] = object()
    observation["current"]["players"][1]["prize"] = [object()] * 6  # type: ignore[index]

    state = build_actor_visible_decision_state_v2(observation)
    public = json.dumps(state.to_public_trace_payload(), sort_keys=True)

    for private_value in ("777001", "777002", "777003", "777004", "777005", "777006", "1001"):
        assert private_value not in public
        assert private_value not in repr(state)
