from __future__ import annotations

import json

import pytest

from mage_ptcg.opponents.public_trajectory_projection import (
    PublicSchemaUnknownFieldError,
    build_public_trajectory_events,
)


def _player(**overrides):
    base = {
        "active": [None, None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 52, "discard": [], "hand": [{"id": 1}, {"id": 2}], "handCount": 2,
        "paralyzed": False, "poisoned": False, "prize": [{"id": 9}, {"id": 10}, {"id": 11}, {"id": 12}, {"id": 13}, {"id": 14}],
    }
    base.update(overrides)
    return base


def _observation(*, your_index=0, players=None, result=None, select=None, extra_current=None, extra_top=None):
    current = {
        "yourIndex": your_index, "players": players or [_player(), _player()],
        "energyAttached": False, "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False,
    }
    if result is not None:
        current["result"] = result
    if extra_current:
        current.update(extra_current)
    obs = {"current": current, "logs": [], "search_begin_input": "opaque-token", "select": select, "step": 1}
    if extra_top:
        obs.update(extra_top)
    return obs


def _step(seat0_action=None, seat0_select=None, seat1_action=None, seat1_select=None, **kw):
    return [
        {"observation": _observation(your_index=0, select=seat0_select, **kw), "action": seat0_action, "status": "ACTIVE"},
        {"observation": _observation(your_index=1, select=seat1_select, **kw), "action": seat1_action, "status": "ACTIVE"},
    ]


def _select(*options):
    # Real cabt shape: select["option"] (singular key) already IS the candidate list.
    return {"type": 0, "option": list(options)}


def _end_option():
    # Real cabt shape: option fields are flat, not nested under "fields".
    return {"type": 14, "index": 0}


def test_three_step_game_produces_initial_action_terminal_events():
    # Real engine pairing: a seat's decision prompt (select) at raw step i is answered by that
    # seat's action at raw step i + 1 -- and the decision attaches to the *response* event, not
    # the prompt event, so the very first (INITIAL) event never carries an action.
    steps = [
        _step(seat0_select=_select(_end_option())),
        _step(seat0_action=[0]),
        _step(result=0),
    ]
    events = build_public_trajectory_events(steps)
    assert [e["event_type"] for e in events] == ["INITIAL_PUBLIC_STATE", "PUBLIC_ACTION", "TERMINAL_PUBLIC_STATE"]
    assert [e["step_index"] for e in events] == [0, 1, 2]
    assert events[2]["public_payload"]["result"] == 0
    assert events[0]["public_payload"]["action"] is None
    assert events[1]["public_payload"]["action"] is not None


def test_action_event_projects_selected_option_and_seat_direction():
    steps = [_step(seat0_select=_select(_end_option())), _step(seat0_action=[0])]
    events = build_public_trajectory_events(steps)
    action_event = events[1]
    assert action_event["seat_direction"] == "SEAT_0"
    assert action_event["public_payload"]["action"] == {
        "option_type": 14, "option_type_name": "END", "player_index": None, "attack_id": None, "count": None, "number": None,
    }


def test_no_action_event_has_null_seat_direction_and_null_action():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    assert events[0]["seat_direction"] is None
    assert events[0]["public_payload"]["action"] is None


def test_hand_contents_never_appear_only_count():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    blob = json.dumps(events)
    assert '"id": 1' not in blob and '"id": 2' not in blob  # hand card ids from _player()
    assert events[0]["public_payload"]["players"][0]["hand_count"] == 2


def test_prize_contents_never_appear_only_count():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    assert events[0]["public_payload"]["players"][0]["prize_count"] == 6
    assert '"id": 9' not in json.dumps(events)


def test_logs_and_search_begin_input_never_appear():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    blob = json.dumps(events)
    assert "opaque-token" not in blob and '"logs"' not in blob and '"search_begin_input"' not in blob


def test_active_card_projects_known_fields_only():
    card = {"id": 5, "serial": 7, "playerIndex": 0, "hp": 60, "maxHp": 60, "appearThisTurn": True,
            "energyCards": [{"id": 1}], "tools": [], "preEvolution": [{"id": 2}]}
    players = [_player(active=[card, None]), _player()]
    steps = [_step(players=players), _step(players=players)]
    events = build_public_trajectory_events(steps)
    projected = events[0]["public_payload"]["players"][0]["active"][0]
    assert projected == {
        "card_id": 5, "serial": 7, "player_index": 0, "current_hp": 60, "max_hp": 60, "appear_this_turn": True,
        "attached_energy_count": 1, "tool_count": 0, "evolution_depth": 1,
    }


def test_stadium_projected_as_card_slot_list():
    stadium_card = {"id": 99, "serial": 1, "playerIndex": None, "hp": None, "maxHp": None, "appearThisTurn": None,
                     "energies": [], "tools": [], "preEvolution": []}
    steps = [_step(extra_current={"stadium": [stadium_card]}), _step()]
    events = build_public_trajectory_events(steps)
    board = events[0]["public_payload"]["board"]
    assert board["stadium"] == [{
        "card_id": 99, "serial": 1, "player_index": None, "current_hp": None, "max_hp": None, "appear_this_turn": None,
        "attached_energy_count": 0, "tool_count": 0, "evolution_depth": 0,
    }]


def test_empty_stadium_list_projects_to_empty_list():
    steps = [_step(extra_current={"stadium": []}), _step()]
    events = build_public_trajectory_events(steps)
    assert events[0]["public_payload"]["board"]["stadium"] == []


def test_unknown_top_level_observation_field_fails_closed():
    steps = [_step(extra_top={"totally_new_field": 1}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_unknown_nested_player_field_fails_closed():
    players = [_player(newly_added_field="x"), _player()]
    steps = [_step(players=players), _step(players=players)]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_renamed_unknown_nested_field_under_card_fails_closed():
    card = {"id": 5, "hidden_engine_blob": "should not exist"}
    players = [_player(active=[card, None]), _player()]
    steps = [_step(players=players), _step(players=players)]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_unknown_option_field_fails_closed():
    option = {"type": 14, "index": 0, "mystery_field": 1}
    steps = [_step(seat0_select=_select(option)), _step(seat0_action=[0])]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_tool_index_is_recognized_but_not_forwarded():
    option = {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}
    steps = [_step(seat0_select=_select(option)), _step(seat0_action=[0])]
    events = build_public_trajectory_events(steps)
    assert events[1]["public_payload"]["action"]["option_type"] == 4
    assert "toolIndex" not in json.dumps(events)


def test_non_null_stadium_with_unexpected_shape_fails_closed():
    steps = [_step(extra_current={"stadium": {"unexpected": True}}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_stadium_card_with_unknown_field_fails_closed():
    steps = [_step(extra_current={"stadium": [{"id": 1, "unknown_stadium_field": True}]}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)


def test_non_null_looking_is_silently_dropped_not_fail_closed():
    """current.looking commonly carries real deck-search results in real games (confirmed by
    direct engine observation) -- its presence must never abort the whole game; its content
    must never appear in the projected output."""
    steps = [_step(extra_current={"looking": [{"id": 999, "serial": 1, "playerIndex": 0}]}), _step()]
    events = build_public_trajectory_events(steps)
    assert "999" not in json.dumps(events)
    assert "looking" not in json.dumps(events)


def test_empty_steps_rejected():
    from mage_ptcg.opponents.errors import OpponentError
    with pytest.raises(OpponentError):
        build_public_trajectory_events([])


def test_deck_registration_only_step_is_dropped():
    """A raw step where both seats' observation.current is still null (deck registration,
    before any board exists) must be dropped, not projected or fail-closed."""
    registration_step = [
        {"observation": {"current": None, "logs": [], "search_begin_input": None, "select": None, "step": 0}, "action": [], "status": "ACTIVE"},
        {"observation": {"current": None, "logs": [], "search_begin_input": None, "select": None, "step": 0}, "action": [], "status": "ACTIVE"},
    ]
    steps = [registration_step, _step(), _step()]
    events = build_public_trajectory_events(steps)
    assert len(events) == 2
    assert [e["step_index"] for e in events] == [0, 1]


def test_events_validate_against_shared_json_schema():
    from pathlib import Path

    import jsonschema

    schema = json.loads((Path("src/mage_ptcg/opponents/public_trajectory_schema_v1.json")).read_text())
    steps = [_step(seat0_select=_select(_end_option())), _step(seat0_action=[0]), _step(result=0)]
    events = build_public_trajectory_events(steps)
    validator = jsonschema.Draft202012Validator(schema)
    for event in events:
        validator.validate(event)
