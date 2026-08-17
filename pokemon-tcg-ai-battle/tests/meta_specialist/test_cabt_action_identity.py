"""Official CABT option identity and ordered SkillOrder regression coverage."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mage_ptcg.decision_state import (
    ActionKey,
    DecisionStateError,
    build_action_key,
    build_decision_state,
    public_action_id_v1,
    validate_persistable_public_action_payload,
)
from mage_ptcg.meta_specialist.actions import (
    CompleteAction,
    DecisionEnvelope,
    DecisionEnvelopeError,
    resolve_order_semantics,
)
from mage_ptcg.student.features import public_action_features, serialized_action_features


def _card(
    card_id: int,
    serial: int,
    *,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": serial,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": tools if tools is not None else [],
        "preEvolution": [],
    }


def _player(
    *,
    hand_id: int = 700001,
    active: list[dict[str, Any]] | None = None,
    bench: list[dict[str, Any]] | None = None,
    discard: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": bench if bench is not None else [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": discard if discard is not None else [],
        "hand": [_card(hand_id, 991)],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }


def _state(
    *,
    options: list[dict[str, Any]],
    selection_type: int,
    selection_context: int,
    minimum: int = 1,
    maximum: int = 1,
    public_active: list[dict[str, Any]] | None = None,
    public_bench: list[dict[str, Any]] | None = None,
    public_discard: list[dict[str, Any]] | None = None,
    opponent_active: list[dict[str, Any]] | None = None,
    stadium: list[dict[str, Any]] | None = None,
):
    return build_decision_state(
        {
            "current": {
                "energyAttached": False,
                "firstPlayer": 0,
                "players": [
                    _player(
                        active=public_active,
                        bench=public_bench,
                        discard=public_discard,
                    ),
                    _player(hand_id=800001, active=opponent_active),
                ],
                "result": -1,
                "retreated": False,
                "stadium": [] if stadium is None else stadium,
                "stadiumPlayed": False,
                "supporterPlayed": False,
                "turn": 2,
                "turnActionCount": 3,
                "yourIndex": 0,
            },
            "logs": ["RAW_ENGINE_LOG"],
            "search_begin_input": "RAW_ENGINE_TOKEN",
            "select": {
                "context": selection_context,
                "maxCount": maximum,
                "minCount": minimum,
                "option": options,
                "type": selection_type,
            },
            "step": 7,
        }
    )


def test_official_skill_special_condition_and_tool_options_have_unique_shuffle_stable_keys() -> None:
    """Catches official CABT semantic identity fields being omitted in favor of option index."""
    fixtures = (
        (
            5,
            34,
            [
                {"type": 15, "cardId": 101, "serial": 1001},
                {"type": 15, "cardId": 102, "serial": 1002},
            ],
            [_card(101, 1001)],
        ),
        (
            10,
            47,
            [
                {"type": 16, "specialConditionType": 0},
                {"type": 16, "specialConditionType": 4},
            ],
            None,
        ),
        (
            2,
            28,
            [
                {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
                {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 1},
            ],
            [
                _card(
                    201,
                    2001,
                    tools=[_card(301, 3001), _card(302, 3002)],
                )
            ],
        ),
    )

    for selection_type, context, options, public_active in fixtures:
        original = _state(
            options=options,
            selection_type=selection_type,
            selection_context=context,
            public_active=public_active,
        )
        shuffled = _state(
            options=list(reversed(options)),
            selection_type=selection_type,
            selection_context=context,
            public_active=public_active,
        )

        assert len({item.action_key.digest for item in original.legal_actions}) == 2
        assert {item.action_key.digest for item in original.legal_actions} == {
            item.action_key.digest for item in shuffled.legal_actions
        }
        assert original.actor_view.action_snapshot == shuffled.actor_view.action_snapshot


def test_skill_order_preserves_engine_selected_index_sequence_without_sorting() -> None:
    """Catches CABT SkillOrder execution being normalized into a meaning-changing sorted set.

    Official engine source emits this schema from ``ResolveTriggerStack`` and
    ``SelectedSkillOrder`` consumes ``state.selected`` in reverse input order;
    the source-backed resolver therefore must retain the returned sequence.
    """
    options = [
        {"type": 15, "cardId": 101, "serial": 1001},
        {"type": 15, "cardId": 102, "serial": 1002},
        {"type": 15, "cardId": 103, "serial": 1003},
    ]
    state = _state(
        options=options,
        selection_type=5,
        selection_context=34,
        minimum=3,
        maximum=3,
        public_active=[_card(101, 1001)],
        public_bench=[_card(102, 1002), _card(103, 1003)],
    )
    envelope = DecisionEnvelope.from_decision_state(state)
    with pytest.raises(DecisionEnvelopeError, match="order_semantics"):
        DecisionEnvelope.from_decision_state(state, order_semantics="unordered_set")
    keys_by_index = {
        item.option_index: item.action_key.digest for item in state.legal_actions
    }

    action = CompleteAction(
        envelope,
        (keys_by_index[2], keys_by_index[0], keys_by_index[1]),
        (2, 0, 1),
    )

    assert envelope.order_semantics == "ordered_sequence"
    assert action.option_indices == (2, 0, 1)
    assert action.option_indices != tuple(sorted(action.option_indices))


def test_order_semantics_uses_explicit_source_contract_and_rejects_unknown_schema() -> None:
    """Catches an order mode inferred from option indices, min/max, or unknown enum values."""
    valid_contexts = (
        (0, (0,)),
        (1, tuple(range(1, 26))),
        (2, (26, 27, 28)),
        (3, (29,)),
        (4, (30, 31, 32, 33)),
        (6, (35, 36)),
        (7, (37,)),
        (8, (38, 39, 40)),
        (9, (41, 42, 43, 44, 45, 46)),
        (10, (47, 48)),
    )
    assert resolve_order_semantics(5, 34) == "ordered_sequence"
    for selection_type, contexts in valid_contexts:
        for context in contexts:
            assert resolve_order_semantics(selection_type, context) == "unordered_set"
    for unknown in ((5, 33), (5, 35), (6, 34), (10, 46), (11, 47), (999, 999)):
        with pytest.raises(DecisionEnvelopeError, match="unclassified"):
            resolve_order_semantics(*unknown)
    with pytest.raises(DecisionEnvelopeError, match="non-bool"):
        resolve_order_semantics(True, 34)


def test_checked_in_cabt_1_32_0_skill_order_capture_uses_ordered_json_schema() -> None:
    fixture_path = Path(__file__).with_name("fixtures") / "cabt_1_32_0_skill_order.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    select = fixture["select"]

    assert fixture["kaggle_environments_version"] == "1.32.0"
    assert set(select) == {"context", "maxCount", "minCount", "option", "type"}
    assert (select["type"], select["context"]) == (5, 34)
    assert {option["type"] for option in select["option"]} == {15}

    state = _state(
        options=select["option"],
        selection_type=select["type"],
        selection_context=select["context"],
        minimum=select["minCount"],
        maximum=select["maxCount"],
        public_active=[_card(select["option"][0]["cardId"], select["option"][0]["serial"])],
    )
    envelope = DecisionEnvelope.from_decision_state(state)

    assert envelope.order_semantics == "ordered_sequence"
    assert len({action.action_key.digest for action in state.legal_actions}) == 2


def test_unverified_skill_identity_is_redacted_and_ambiguous_public_trace_fails_closed() -> None:
    """Catches private Skill card identity being persisted without a public-board proof."""
    direct = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 901, "serial": 9001},
    )
    serialized = json.dumps(direct.to_public_trace_payload(), sort_keys=True)
    assert "901" not in serialized
    assert "9001" not in serialized

    state = _state(
        options=[
            {"type": 15, "cardId": 901, "serial": 9001},
            {"type": 15, "cardId": 902, "serial": 9002},
        ],
        selection_type=5,
        selection_context=34,
        minimum=2,
        maximum=2,
        public_active=[],
    )
    with pytest.raises(DecisionEnvelopeError, match="indistinguishable public"):
        DecisionEnvelope.from_decision_state(
            state,
            order_semantics=resolve_order_semantics(5, 34),
        )


def test_skill_trace_uses_public_locator_not_raw_pair_or_actor_digest() -> None:
    """Catches persistence of the raw CABT Skill pair after successful lookup."""
    state = _state(
        options=[
            {"type": 15, "cardId": 1001, "serial": 7},
            {"type": 15, "cardId": 1002, "serial": 8},
        ],
        selection_type=5,
        selection_context=34,
        minimum=2,
        maximum=2,
        public_active=[_card(1001, 7)],
        public_bench=[_card(1002, 8)],
    )

    payloads = [item.action_key.to_public_trace_payload() for item in state.legal_actions]
    serialized = json.dumps(payloads, sort_keys=True)
    assert "1001" not in serialized
    assert "1002" not in serialized
    assert "serial" not in serialized
    assert all(item.action_key.digest not in serialized for item in state.legal_actions)
    assert {payload["public_identity"]["source"]["zone"] for payload in payloads} == {
        "active",
        "bench",
    }
    assert {payload["public_identity"]["source"]["slot"] for payload in payloads} == {0}


def test_hidden_and_ambiguous_skill_pairs_redact_without_hashing_raw_identity() -> None:
    """Catches hidden or multiply-mapped Skills being silently assigned a public seat."""
    hidden_first = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 900001, "serial": 57},
    )
    hidden_second = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 900002, "serial": 58},
    )
    first_public = hidden_first.to_public_trace_payload()
    second_public = hidden_second.to_public_trace_payload()
    assert first_public == second_public
    serialized = json.dumps({"trace": first_public, "repr": repr(hidden_first)}, sort_keys=True)
    raw_pair_hash = hashlib.sha256(
        json.dumps(
            {"cardId": 900001, "serial": 57},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert all(
        token not in serialized
        for token in ("900001", "57", raw_pair_hash, hidden_first.digest)
    )

    ambiguous = _state(
        options=[{"type": 15, "cardId": 1001, "serial": 7}],
        selection_type=5,
        selection_context=34,
        public_active=[_card(1001, 7)],
        opponent_active=[_card(1001, 7)],
    )
    projection = ambiguous.legal_actions[0].action_key.to_public_trace_payload()
    assert projection["public_identity"]["source"] == {"kind": "redacted"}


@pytest.mark.parametrize(
    "option",
    [
        {"type": 15, "cardId": True, "serial": 1},
        {"type": 15, "cardId": 1, "serial": True},
        {"type": 15, "cardId": 1},
        {"type": 15, "serial": 1},
    ],
)
def test_skill_requires_exact_non_boolean_identity_pair(option: dict[str, Any]) -> None:
    with pytest.raises(DecisionStateError, match="Skill"):
        _state(options=[option], selection_type=5, selection_context=34)


@pytest.mark.parametrize(
    "value",
    [True, -1, 5, None],
)
def test_special_condition_requires_bounded_enum(value: object) -> None:
    option = {"type": 16}
    if value is not None:
        option["specialConditionType"] = value
    with pytest.raises(DecisionStateError, match="specialConditionType"):
        _state(options=[option], selection_type=10, selection_context=47)


def test_special_condition_trace_uses_fixed_public_enum_names() -> None:
    state = _state(
        options=[
            {"type": 16, "specialConditionType": 0},
            {"type": 16, "specialConditionType": 4},
        ],
        selection_type=10,
        selection_context=48,
        minimum=2,
        maximum=2,
    )
    assert {
        item.action_key.to_public_trace_payload()["public_identity"]["condition"]
        for item in state.legal_actions
    } == {"POISON", "CONFUSE"}


@pytest.mark.parametrize(
    "option",
    [
        {"type": 4, "area": 4, "index": 1, "playerIndex": 0, "toolIndex": 0},
        {"type": 4, "area": 3, "index": 0, "playerIndex": 0, "toolIndex": 0},
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 2},
        {"type": 4, "area": True, "index": 0, "playerIndex": 0, "toolIndex": 0},
    ],
)
def test_tool_requires_verified_public_active_or_bench_host(option: dict[str, Any]) -> None:
    with pytest.raises(DecisionStateError, match="ToolCard"):
        _state(
            options=[option],
            selection_type=2,
            selection_context=28,
            public_active=[_card(201, 2001, tools=[_card(301, 3001)])],
        )


def test_tool_trace_is_host_locator_not_child_identity_or_option_position() -> None:
    state = _state(
        options=[
            {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
            {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 1},
        ],
        selection_type=2,
        selection_context=28,
        minimum=2,
        maximum=2,
        public_active=[
            _card(
                201,
                2001,
                tools=[_card(301, 3001), _card(302, 3002)],
            )
        ],
    )
    payloads = [item.action_key.to_public_trace_payload() for item in state.legal_actions]
    serialized = json.dumps(payloads, sort_keys=True)
    assert all(token not in serialized for token in ("301", "3001", "302", "3002", "toolIndex"))
    assert {
        item["public_identity"]["source"]["attachment_slot"] for item in payloads
    } == {0, 1}


@pytest.mark.parametrize(
    "selection_type,context,options,public_active",
    [
        (5, 34, [{"type": 15, "cardId": 1001, "serial": 7}] * 2, [_card(1001, 7)]),
        (10, 47, [{"type": 16, "specialConditionType": 0}] * 2, None),
        (
            2,
            28,
            [{"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}] * 2,
            [_card(201, 2001, tools=[_card(301, 3001)])],
        ),
    ],
)
def test_exact_official_payload_duplicates_fail_closed(
    selection_type: int,
    context: int,
    options: list[dict[str, Any]],
    public_active: list[dict[str, Any]] | None,
) -> None:
    with pytest.raises(DecisionStateError, match="duplicate stable ActionKey"):
        _state(
            options=options,
            selection_type=selection_type,
            selection_context=context,
            minimum=2,
            maximum=2,
            public_active=public_active,
        )


def test_actionkey_uses_versioned_v2_actor_hash_domain() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )
    assert key.action_key_schema_version == 2
    legacy_payload = key.to_canonical_payload()
    legacy_digest = hashlib.sha256(
        b"mage_ptcg.decision_state:v1\0"
        + json.dumps(
            legacy_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert key.digest != legacy_digest


@pytest.mark.parametrize(
    "injected_identity",
    [
        {"operation": "SKILL", "source": {"kind": "redacted", "cardId": 1001}},
        {"operation": "SKILL", "source": {"kind": "redacted", "serial": 7}},
        {"operation": "SKILL", "actor_identity_payload": [["cardId", 1001]]},
        {"operation": "SKILL", "metadata": {"digest": "0" * 64}},
        {"operation": "SKILL", "metadata": {"option_index": 0}},
        {"operation": "SKILL", "metadata": {"option_indices": [0]}},
        {"operation": "SKILL", "metadata": {"current_index": 0}},
    ],
)
def test_actionkey_direct_constructor_rejects_recursive_public_identity_injection(
    injected_identity: dict[str, object],
) -> None:
    """Every constructor path must apply the recursive public denylist."""
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )
    payload = key.to_public_trace_payload()
    payload["public_identity"] = injected_identity

    with pytest.raises(DecisionStateError, match="public ActionKey identity"):
        replace(
            key,
            public_identity_json=json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


@pytest.mark.parametrize(
    "alias,value",
    [
        ("cardId", 900001),
        ("serial", 57),
        ("index", 3),
        ("private_digest", "USE_PRIVATE_DIGEST"),
        ("private_card_id", 900001),
        ("secret_serial", 57),
        ("actorDigest", "a" * 64),
        ("id", 900001),
        ("option_index_alias", 3),
        ("selection_index", 3),
        ("number", 9),
        ("damage", 90),
    ],
)
def test_generic_public_field_alias_injection_never_reaches_complete_action_trace(
    alias: str,
    value: object,
) -> None:
    state = _state(
        options=[{"type": 13, "attackId": 1}],
        selection_type=6,
        selection_context=35,
    )
    key = state.legal_actions[0].action_key
    payload = key.to_public_trace_payload()
    payload["public_identity"]["fields"][alias] = (
        key.digest if value == "USE_PRIVATE_DIGEST" else value
    )

    with pytest.raises(DecisionStateError, match="public"):
        injected_key = replace(
            key,
            public_identity_json=json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        injected_state = replace(
            state,
            legal_actions=(
                replace(state.legal_actions[0], action_key=injected_key),
            ),
        )
        envelope = DecisionEnvelope.from_decision_state(injected_state)
        action = CompleteAction(
            envelope=envelope,
            keys=(injected_key.digest,),
            option_indices=(0,),
        )
        envelope.to_public_trace_payload(action)


def test_unknown_option_type_cannot_create_a_persistable_generic_action() -> None:
    with pytest.raises(DecisionStateError, match="not allowed"):
        build_action_key(
            selection_type=0,
            context=0,
            option={"type": 99},
        )


def test_actionkey_direct_constructor_rejects_wrong_typed_public_identity_shape() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )
    payload = key.to_public_trace_payload()
    payload["public_identity"] = {
        "operation": "SKILL",
        "private_source_redacted": True,
        "source": {"kind": "redacted", "unexpected": "field"},
    }

    with pytest.raises(DecisionStateError, match="Skill public identity"):
        replace(
            key,
            public_identity_json=json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


def test_actionkey_v2_constructor_recomputes_and_verifies_digest() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )

    with pytest.raises(DecisionStateError, match="digest"):
        replace(key, digest="0" * 64)


def test_actionkey_v2_constructor_requires_explicit_public_identity() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )

    with pytest.raises(DecisionStateError, match="public identity"):
        ActionKey(
            selection_type=key.selection_type,
            context=key.context,
            option_type=key.option_type,
            semantic_operation=key.semantic_operation,
            source_entity_key=key.source_entity_key,
            target_entity_key=key.target_entity_key,
            card_id=key.card_id,
            canonical_payload=key.canonical_payload,
            digest=key.digest,
            actor_identity_payload=key.actor_identity_payload,
            public_identity_json=None,
        )


def test_actionkey_v2_serialization_is_explicit_and_round_trips_with_verification() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )

    payload = key.to_canonical_payload()

    assert payload["action_key_schema_version"] == 2
    assert ActionKey.from_serialized_payload(payload, digest=key.digest) == key
    with pytest.raises(DecisionStateError, match="explicit v1"):
        ActionKey.from_serialized_payload(
            {name: value for name, value in payload.items() if name != "action_key_schema_version"},
            digest=key.digest,
        )


def _unbound_v2_digest(
    key: ActionKey,
    actor_payload: tuple[tuple[str, object], ...],
    *,
    selection_type: object | None = None,
    context: object | None = None,
    option_type: object | None = None,
) -> str:
    """Reproduce the vulnerable pre-binding v2 digest for adversarial tests."""
    core = {
        "action_key_schema_version": 2,
        "actor_identity_payload": [list(item) for item in actor_payload],
        "card_id": key.card_id,
        "context": key.context if context is None else context,
        "option_type": key.option_type if option_type is None else option_type,
        "selection_type": (
            key.selection_type if selection_type is None else selection_type
        ),
        "semantic_operation": key.semantic_operation,
        "source_entity_key": key.source_entity_key,
        "target_entity_key": key.target_entity_key,
    }
    return hashlib.sha256(
        b"mage_ptcg.decision_state.action_key:v2\0"
        + json.dumps(
            core,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _official_key(kind: str) -> ActionKey:
    if kind == "Skill":
        return build_action_key(
            selection_type=5,
            context=34,
            option={"type": 15, "cardId": 1001, "serial": 7},
        )
    if kind == "SpecialCondition":
        return build_action_key(
            selection_type=10,
            context=47,
            option={"type": 16, "specialConditionType": 0},
        )
    if kind == "ToolCard":
        return _state(
            options=[
                {
                    "type": 4,
                    "area": 4,
                    "index": 0,
                    "playerIndex": 0,
                    "toolIndex": 0,
                }
            ],
            selection_type=2,
            selection_context=28,
            public_active=[_card(201, 2001, tools=[_card(301, 3001)])],
        ).legal_actions[0].action_key
    raise AssertionError(f"unknown official action kind: {kind}")


@pytest.mark.parametrize(
    "kind,forged_actor_payload",
    [
        ("Skill", (("bogus", 9),)),
        ("SpecialCondition", (("specialConditionType", 5),)),
        (
            "ToolCard",
            (("area", 4), ("index", 0), ("playerIndex", 0), ("toolIndex", -1)),
        ),
    ],
)
def test_actionkey_direct_constructor_rejects_forged_official_actor_payload(
    kind: str,
    forged_actor_payload: tuple[tuple[str, object], ...],
) -> None:
    key = _official_key(kind)

    with pytest.raises(DecisionStateError, match=f"{kind} actor identity"):
        ActionKey(
            selection_type=key.selection_type,
            context=key.context,
            option_type=key.option_type,
            semantic_operation=key.semantic_operation,
            source_entity_key=key.source_entity_key,
            target_entity_key=key.target_entity_key,
            card_id=key.card_id,
            canonical_payload=forged_actor_payload,  # type: ignore[arg-type]
            digest=_unbound_v2_digest(key, forged_actor_payload),
            actor_identity_payload=forged_actor_payload,  # type: ignore[arg-type]
            public_identity_json=json.dumps(
                key.to_public_trace_payload(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


@pytest.mark.parametrize(
    "kind,forged_actor_payload",
    [
        ("Skill", (("bogus", 9),)),
        ("SpecialCondition", (("specialConditionType", True),)),
        (
            "ToolCard",
            (("area", 4), ("index", 0), ("playerIndex", 0)),
        ),
    ],
)
def test_actionkey_serialized_reader_rejects_forged_official_actor_payload(
    kind: str,
    forged_actor_payload: tuple[tuple[str, object], ...],
) -> None:
    key = _official_key(kind)
    payload = key.to_canonical_payload()
    payload["actor_identity_payload"] = [list(item) for item in forged_actor_payload]
    payload["canonical_payload"] = [list(item) for item in forged_actor_payload]

    with pytest.raises(DecisionStateError, match=f"{kind} actor identity"):
        ActionKey.from_serialized_payload(
            payload,
            digest=_unbound_v2_digest(key, forged_actor_payload),
        )


@pytest.mark.parametrize("field", ["selection_type", "context", "option_type"])
def test_actionkey_v2_rejects_boolean_agent_json_metadata_aliases(field: str) -> None:
    key = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 14},
    )
    values = {
        "selection_type": key.selection_type,
        "context": key.context,
        "option_type": key.option_type,
    }
    values[field] = True
    public_payload = key.to_public_trace_payload()
    public_payload[field] = True

    with pytest.raises(DecisionStateError, match="non-bool"):
        ActionKey(
            selection_type=values["selection_type"],
            context=values["context"],
            option_type=values["option_type"],
            semantic_operation=key.semantic_operation,
            source_entity_key=key.source_entity_key,
            target_entity_key=key.target_entity_key,
            card_id=key.card_id,
            canonical_payload=key.canonical_payload,
            digest=_unbound_v2_digest(
                key,
                key.canonical_payload,
                selection_type=values["selection_type"],
                context=values["context"],
                option_type=values["option_type"],
            ),
            actor_identity_payload=key.actor_identity_payload,
            public_identity_json=json.dumps(
                public_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


@pytest.mark.parametrize("field", ["selection_type", "context", "option_type"])
def test_actionkey_v2_serialized_reader_rejects_boolean_agent_json_metadata_aliases(
    field: str,
) -> None:
    key = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 14},
    )
    payload = key.to_canonical_payload()
    payload[field] = True
    payload["public_identity_payload"][field] = True

    with pytest.raises(DecisionStateError, match="non-bool"):
        ActionKey.from_serialized_payload(
            payload,
            digest=_unbound_v2_digest(
                key,
                key.canonical_payload,
                selection_type=payload["selection_type"],
                context=payload["context"],
                option_type=payload["option_type"],
            ),
        )


def test_actionkey_v2_digest_binds_verified_skill_public_locator() -> None:
    key = build_action_key(
        selection_type=5,
        context=34,
        option={"type": 15, "cardId": 1001, "serial": 7},
    )
    payload = key.to_canonical_payload()
    public_payload = deepcopy(payload["public_identity_payload"])
    public_payload["public_identity"] = {
        "operation": "SKILL",
        "source": {
            "kind": "public_card",
            "player_index": 0,
            "slot": 0,
            "zone": "active",
        },
    }
    payload["public_identity_payload"] = public_payload

    with pytest.raises(DecisionStateError, match="Skill public identity|digest"):
        ActionKey.from_serialized_payload(payload, digest=key.digest)


def test_actionkey_v2_digest_binds_generic_public_fields() -> None:
    key = build_action_key(
        selection_type=0,
        context=0,
        option={"type": 13, "attackId": 1},
    )
    payload = key.to_canonical_payload()
    public_payload = deepcopy(payload["public_identity_payload"])
    public_payload["public_identity"]["fields"]["attackId"] = 999
    payload["public_identity_payload"] = public_payload

    with pytest.raises(DecisionStateError, match="generic public identity|digest"):
        ActionKey.from_serialized_payload(payload, digest=key.digest)


def _bound_v2_digest_for_test(key: ActionKey, public_payload: dict[str, object]) -> str:
    """Build an attacker-recomputed integrity digest for a boundary regression.

    This deliberately mirrors the public v2 representation: the digest is not
    an authenticity signature, so validation must also derive/check projection
    values and context.
    """
    core = {
        "action_key_schema_version": 2,
        "actor_identity_payload": [list(item) for item in key.canonical_payload],
        "card_id": key.card_id,
        "context": key.context,
        "option_type": key.option_type,
        "selection_type": key.selection_type,
        "semantic_operation": key.semantic_operation,
        "source_entity_key": key.source_entity_key,
        "target_entity_key": key.target_entity_key,
        "public_identity_payload": public_payload,
    }
    return hashlib.sha256(
        b"mage_ptcg.decision_state.action_key:v2\0"
        + json.dumps(
            core,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_exported_builder_rejects_caller_supplied_public_identity() -> None:
    """A caller cannot pick a permitted public value/locator for persistence."""
    with pytest.raises(TypeError, match="public_identity"):
        build_action_key(
            selection_type=6,
            context=35,
            option={"type": 13, "attackId": 1},
            public_identity={
                "operation": "ATTACK",
                "fields": {"attackId": 900001},
                "private_source_redacted": False,
            },
        )


def test_direct_actionkey_rejects_rehashed_allowed_generic_value_substitution() -> None:
    """Closed field names alone must not allow an attacker-selected value."""
    key = build_action_key(
        selection_type=6,
        context=35,
        option={"type": 13, "attackId": 1},
    )
    public_payload = key.to_public_trace_payload()
    public_payload["public_identity"]["fields"]["attackId"] = 900001  # type: ignore[index]

    rehashed_digest = _bound_v2_digest_for_test(key, public_payload)
    encoded_public_payload = json.dumps(
        public_payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(DecisionStateError, match="generic public identity"):
        replace(
            key,
            digest=rehashed_digest,
            public_identity_json=encoded_public_payload,
        )
    serialized = key.to_canonical_payload()
    serialized["public_identity_payload"] = public_payload
    with pytest.raises(DecisionStateError, match="generic public identity"):
        ActionKey.from_serialized_payload(serialized, digest=rehashed_digest)


def test_serialized_public_skill_locator_requires_actual_public_resolution_context() -> None:
    """A shape-valid Skill locator cannot be persisted without its board context."""
    state = _state(
        options=[{"type": 15, "cardId": 1001, "serial": 7}],
        selection_type=5,
        selection_context=34,
        public_active=[_card(1001, 7)],
    )
    key = state.legal_actions[0].action_key

    with pytest.raises(DecisionStateError, match="public resolution"):
        ActionKey.from_serialized_payload(key.to_canonical_payload(), digest=key.digest)


@pytest.mark.parametrize("kind", ["Skill", "ToolCard"])
def test_c4_and_c5_feature_readers_accept_structurally_valid_public_locators(
    kind: str,
) -> None:
    """Feature extraction is non-persistable; C5 membership is checked elsewhere."""
    key = (
        _state(
            options=[{"type": 15, "cardId": 1001, "serial": 7}],
            selection_type=5,
            selection_context=34,
            public_active=[_card(1001, 7)],
        ).legal_actions[0].action_key
        if kind == "Skill"
        else _official_key(kind)
    )
    public_payload = key.to_public_trace_payload()

    assert len(serialized_action_features(key.to_canonical_payload(), digest=key.digest)) == 64
    assert len(
        public_action_features(
            public_payload,
            digest=public_action_id_v1(public_payload),
        )
    ) == 64


@pytest.mark.parametrize("kind", ["Skill", "ToolCard"])
def test_c5_persisted_public_locator_must_belong_to_its_public_observation(
    kind: str,
) -> None:
    """C5 uses current public-board membership, not a forgeable boolean marker."""
    state = (
        _state(
            options=[{"type": 15, "cardId": 1001, "serial": 7}],
            selection_type=5,
            selection_context=34,
            public_active=[_card(1001, 7)],
        )
        if kind == "Skill"
        else _state(
            options=[{"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0}],
            selection_type=2,
            selection_context=28,
            public_active=[_card(201, 2001, tools=[_card(301, 3001)])],
        )
    )
    payload = state.legal_actions[0].action_key.to_public_trace_payload()
    validate_persistable_public_action_payload(
        payload,
        public_resolution=state.actor_view.public_state,
    )
    forged = deepcopy(payload)
    source = forged["public_identity"]["source"]
    if kind == "Skill":
        source["slot"] = 99
    else:
        source["attachment_slot"] = 99

    with pytest.raises(DecisionStateError, match="public|attachment"):
        validate_persistable_public_action_payload(
            forged,
            public_resolution=state.actor_view.public_state,
        )


@pytest.mark.parametrize(
    ("card_id", "serial", "public_active", "stadium"),
    [
        (301, 3001, [_card(201, 2001, tools=[_card(301, 3001)])], None),
        (401, 4001, None, [_card(401, 4001)]),
    ],
)
def test_skill_source_without_a_c1_reverifiable_pair_stays_redacted(
    card_id: int,
    serial: int,
    public_active: list[dict[str, Any]] | None,
    stadium: list[dict[str, Any]] | None,
) -> None:
    """Attachment/stadium pairs are unavailable in C1 and cannot become C5 locators."""
    state = _state(
        options=[{"type": 15, "cardId": card_id, "serial": serial}],
        selection_type=5,
        selection_context=34,
        public_active=public_active,
        stadium=stadium,
    )

    source = state.legal_actions[0].action_key.to_public_trace_payload()[
        "public_identity"
    ]["source"]
    assert source == {"kind": "redacted"}


def test_legacy_v1_reader_rejects_boolean_explicit_schema_version() -> None:
    legacy_payload = {
        "action_key_schema_version": True,
        "canonical_payload": [["cardId", 1001], ["serial", 7]],
        "card_id": None,
        "context": 34,
        "option_type": 15,
        "selection_type": 5,
        "semantic_operation": "SKILL",
        "source_entity_key": None,
        "target_entity_key": None,
    }
    core = {key: value for key, value in legacy_payload.items() if key != "action_key_schema_version"}
    legacy_digest = hashlib.sha256(
        b"mage_ptcg.decision_state:v1\0"
        + json.dumps(
            core,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(DecisionStateError, match="exact integer 1"):
        ActionKey.from_legacy_v1_feature_payload(
            legacy_payload,
            digest=legacy_digest,
        )


def test_legacy_v1_actionkey_is_feature_only_and_cannot_enter_v2_decision_state() -> None:
    legacy_payload = {
        "canonical_payload": [["cardId", 1001], ["serial", 7]],
        "card_id": None,
        "context": 34,
        "option_type": 15,
        "selection_type": 5,
        "semantic_operation": "SKILL",
        "source_entity_key": None,
        "target_entity_key": None,
    }
    legacy_digest = hashlib.sha256(
        b"mage_ptcg.decision_state:v1\0"
        + json.dumps(
            legacy_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    legacy_key = ActionKey.from_legacy_v1_feature_payload(
        legacy_payload,
        digest=legacy_digest,
    )

    assert legacy_key.action_key_schema_version == 1
    with pytest.raises(DecisionStateError, match="feature-only"):
        legacy_key.to_public_trace_payload()

    state = _state(
        options=[{"type": 15, "cardId": 1001, "serial": 7}],
        selection_type=5,
        selection_context=34,
        public_active=[_card(1001, 7)],
    )
    with pytest.raises(DecisionStateError, match="schema version"):
        replace(
            state,
            legal_actions=(replace(state.legal_actions[0], action_key=legacy_key),),
        )
