"""DecisionState adaptation, execution, scoring, and trace safety properties."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from typing import Any

import pytest

from mage_ptcg.decision_state import build_action_key, build_decision_state
from mage_ptcg.meta_specialist.actions import (
    CompleteAction,
    CompleteActionError,
    DecisionEnvelope,
    DecisionEnvelopeError,
    complete_action_distribution,
    complete_action_log_probability,
    enumerate_complete_actions,
    greedy_decode,
    q_argmax,
)


def _card(card_id: int, *, serial: int = 0) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": serial,
        "hp": 100,
        "maxHp": 100,
        "playerIndex": 0,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _player(
    *,
    hand_id: int,
    active: list[dict[str, Any]] | None = None,
    bench: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": bench if bench is not None else [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": [_card(hand_id)],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }


def _observation(
    *,
    options: list[dict[str, Any]],
    minimum: int,
    maximum: int,
    selection_type: int = 0,
    selection_context: int = 0,
    private_card_id: int = 456789,
    public_active: list[dict[str, Any]] | None = None,
    public_bench: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [
                _player(
                    hand_id=private_card_id,
                    active=public_active,
                    bench=public_bench,
                ),
                _player(hand_id=701),
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
        "logs": ["RAW_LOG_SENTINEL"],
        "search_begin_input": "RAW_OBSERVATION_SENTINEL",
        "select": {
            "context": selection_context,
            "maxCount": maximum,
            "minCount": minimum,
            "option": options,
            "type": selection_type,
        },
        "step": 7,
    }


@pytest.fixture
def decision_state_factory():
    """Build equivalent current decisions with a controllable raw option order."""

    def build(
        *,
        option_order: tuple[int, ...] = (0, 1, 2),
        minimum: int = 1,
        maximum: int = 2,
        selection_type: int = 0,
        selection_context: int = 0,
        private_card_id: int = 456789,
    ):
        if selection_type == 4:
            source_options = (
                {"type": 6, "area": 2, "index": 0, "playerIndex": 0, "energyIndex": 0, "count": 1},
                {"type": 6, "area": 2, "index": 1, "playerIndex": 0, "energyIndex": 1, "count": 1},
                {"type": 6, "area": 2, "index": 2, "playerIndex": 0, "energyIndex": 2, "count": 1},
            )
        elif selection_type == 1:
            source_options = (
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            )
        else:
            source_options = (
                {"type": 7, "index": 0},
                {"type": 13, "attackId": 11},
                {"type": 13, "attackId": 12},
            )
        effective_order = tuple(
            index for index in option_order if index < len(source_options)
        )
        effective_maximum = min(maximum, len(effective_order))
        return build_decision_state(
            _observation(
                options=[dict(source_options[index]) for index in effective_order],
                minimum=minimum,
                maximum=effective_maximum,
                selection_type=selection_type,
                selection_context=selection_context,
                private_card_id=private_card_id,
            )
        )

    return build


def _digest_for_index(state, index: int) -> str:
    return next(action.action_key.digest for action in state.legal_actions if action.option_index == index)


def _action_for_keys(envelope: DecisionEnvelope, keys: tuple[str, ...]):
    return next(action for action in enumerate_complete_actions(envelope, limit=32) if action.keys == keys)


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*( _recursive_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_recursive_keys(item) for item in value)) if value else set()
    return set()


def test_adapter_uses_authoritative_cabt_bounds_type_and_option_count(
    decision_state_factory,
) -> None:
    """Catches caller-provided bounds or a stale legal-action list replacing CABT truth."""
    state = decision_state_factory(
        minimum=1, maximum=2, selection_type=4, selection_context=30
    )

    envelope = DecisionEnvelope.from_decision_state(
        state,
        min_count=1,
        max_count=2,
        order_semantics="unordered_set",
    )

    select = state.normalized_public_observation["select"]
    assert envelope.min_count == select["min_count"] == 1
    assert envelope.max_count == select["max_count"] == 2
    assert envelope.selection_type == select["type"] == 4
    assert envelope.selection_context == select["context"] == 30
    assert len(envelope.candidates) == select["option_count"] == 3
    assert {
        candidate.stable_key: candidate.option_index for candidate in envelope.candidates
    } == {item.action_key.digest: item.option_index for item in state.legal_actions}

    with pytest.raises(DecisionEnvelopeError, match="authoritative"):
        DecisionEnvelope.from_decision_state(
            state, min_count=0, max_count=2, order_semantics="unordered_set"
        )
    with pytest.raises(DecisionEnvelopeError, match="non-bool"):
        DecisionEnvelope.from_decision_state(
            state, min_count=True, max_count=2, order_semantics="unordered_set"
        )
    with pytest.raises(DecisionEnvelopeError, match="option_count"):
        DecisionEnvelope.from_decision_state(
            replace(state, legal_actions=state.legal_actions[:-1]),
            order_semantics="unordered_set",
        )


def test_adapter_rejects_an_action_key_with_a_different_authoritative_type(
    decision_state_factory,
) -> None:
    """Catches a mixed-type action list accepted under one selection envelope."""
    state = decision_state_factory(selection_type=4, selection_context=30)
    changed_action = replace(
        state.legal_actions[0],
        action_key=build_action_key(
            selection_type=0,
            context=0,
            option={"type": 7, "index": 0},
            card_id=456789,
        ),
    )
    malformed = replace(state, legal_actions=(changed_action, *state.legal_actions[1:]))

    with pytest.raises(DecisionEnvelopeError, match="selection_type"):
        DecisionEnvelope.from_decision_state(malformed, order_semantics="unordered_set")


def test_adapter_retains_authoritative_context_and_rejects_mixed_action_context(
    decision_state_factory,
) -> None:
    """Catches a selection context being dropped or mixed across legal candidates."""
    state = decision_state_factory(selection_type=1, selection_context=9)
    envelope = DecisionEnvelope.from_decision_state(state, order_semantics="unordered_set")
    assert envelope.selection_context == 9

    changed_action = replace(
        state.legal_actions[0],
        action_key=build_action_key(
            selection_type=1,
            context=1,
            option={"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            card_id=456789,
        ),
    )
    malformed = replace(state, legal_actions=(changed_action, *state.legal_actions[1:]))
    with pytest.raises(DecisionEnvelopeError, match="context"):
        DecisionEnvelope.from_decision_state(malformed, order_semantics="unordered_set")


def test_adapter_derives_order_semantics_and_rejects_caller_mismatch(
    decision_state_factory,
) -> None:
    """Catches a caller overriding the authoritative JSON selection schema."""
    state = decision_state_factory(selection_type=1, selection_context=9)
    assert DecisionEnvelope.from_decision_state(state).order_semantics == "unordered_set"
    with pytest.raises(DecisionEnvelopeError, match="order_semantics"):
        DecisionEnvelope.from_decision_state(
            state,
            order_semantics="ordered_sequence",
        )


def test_zero_option_zero_bound_production_decision_has_one_persistable_empty_trace() -> None:
    """A real CABT no-op selection is one complete action, not a synthetic envelope."""
    state = build_decision_state(
        _observation(options=[], minimum=0, maximum=0)
    )
    envelope = DecisionEnvelope.from_decision_state(state)
    action = enumerate_complete_actions(envelope, limit=1)[0]

    trace = envelope.to_public_trace_payload(action)

    assert action.keys == ()
    assert trace["selected_count"] == 0
    assert trace["selected_public_actions"] == []


def test_adapter_rejects_duplicate_stable_keys_and_public_projection_collisions(
    decision_state_factory,
) -> None:
    """Catches ambiguous digest-to-index or redacted-trace candidate mappings."""
    duplicate_state = build_decision_state(
        _observation(
            options=[{"type": 13, "attackId": 11}, {"type": 13, "attackId": 11}],
            minimum=1,
            maximum=1,
        )
    )
    with pytest.raises(DecisionEnvelopeError, match="stable keys"):
        DecisionEnvelope.from_decision_state(
            duplicate_state, order_semantics="unordered_set"
        )

    state = decision_state_factory()
    first = state.legal_actions[0]
    colliding = replace(
        first,
        option_index=1,
        action_key=build_action_key(
            selection_type=0,
            context=0,
            option={"type": 7, "index": 0},
            card_id=999999,
        ),
    )
    malformed = replace(state, legal_actions=(first, colliding, state.legal_actions[2]))
    with pytest.raises(DecisionEnvelopeError, match="indistinguishable public"):
        DecisionEnvelope.from_decision_state(malformed, order_semantics="unordered_set")


def test_adapter_rejects_noncurrent_or_duplicate_engine_indices(decision_state_factory) -> None:
    """Catches an adapter emitting an index outside this decision's legal CABT range."""
    state = decision_state_factory()
    out_of_range = replace(state.legal_actions[0], option_index=99)
    duplicate = replace(state.legal_actions[0], option_index=state.legal_actions[1].option_index)

    with pytest.raises(DecisionEnvelopeError, match="current legal range"):
        DecisionEnvelope.from_decision_state(
            replace(state, legal_actions=(out_of_range, *state.legal_actions[1:])),
            order_semantics="unordered_set",
        )
    with pytest.raises(DecisionEnvelopeError, match="option indices must be unique"):
        DecisionEnvelope.from_decision_state(
            replace(state, legal_actions=(duplicate, *state.legal_actions[1:])),
            order_semantics="unordered_set",
        )


def test_candidate_shuffle_preserves_distribution_decode_q_and_engine_mapping(
    decision_state_factory,
) -> None:
    """Catches policy identity, ties, or execution indices depending on raw option order."""
    original_state = decision_state_factory(option_order=(0, 1, 2))
    shuffled_state = decision_state_factory(option_order=(2, 0, 1))
    original = DecisionEnvelope.from_decision_state(
        original_state, order_semantics="unordered_set"
    )
    shuffled = DecisionEnvelope.from_decision_state(
        shuffled_state, order_semantics="unordered_set"
    )

    def logits(_prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        return {
            token: float(int(token[:8], 16) % 17) if token != "__STOP__" else -0.25
            for token in allowed
        }

    left = {
        action.keys: probability
        for action, probability in complete_action_distribution(
            original, step_logits=logits, enumeration_limit=32
        ).items()
    }
    right = {
        action.keys: probability
        for action, probability in complete_action_distribution(
            shuffled, step_logits=logits, enumeration_limit=32
        ).items()
    }
    original_action = greedy_decode(original, step_logits=logits)
    shuffled_action = greedy_decode(shuffled, step_logits=logits)
    q = {keys: float(index) for index, keys in enumerate(sorted(left))}
    original_q_action = q_argmax(original, q, enumeration_limit=32)
    shuffled_q_action = q_argmax(shuffled, q, enumeration_limit=32)

    assert left == right
    assert original_action.keys == shuffled_action.keys
    assert original_q_action.keys == shuffled_q_action.keys
    for state, action, expected_keys in (
        (original_state, original_action, original_action.keys),
        (shuffled_state, shuffled_action, original_action.keys),
        (original_state, original_q_action, original_q_action.keys),
        (shuffled_state, shuffled_q_action, original_q_action.keys),
    ):
        legal_indices = {item.option_index for item in state.legal_actions}
        mapped_digests = {_digest_for_index(state, index) for index in action.option_indices}
        assert mapped_digests == set(expected_keys)
        assert len(action.option_indices) == len(expected_keys)
        assert all(type(index) is int and 0 <= index < len(state.legal_actions) for index in action.option_indices)
        assert set(action.option_indices).issubset(legal_indices)
        assert len(set(action.option_indices)) == len(action.option_indices)
        assert action.option_indices == tuple(sorted(action.option_indices))


def test_equal_logits_and_equal_q_tie_break_on_stable_keys_not_engine_indices(
    decision_state_factory,
) -> None:
    """Catches a raw option-index tie-break after candidates are permuted."""
    first_state = decision_state_factory(option_order=(0, 1, 2), minimum=1, maximum=1)
    second_state = decision_state_factory(option_order=(2, 1, 0), minimum=1, maximum=1)
    first = DecisionEnvelope.from_decision_state(first_state, order_semantics="unordered_set")
    second = DecisionEnvelope.from_decision_state(second_state, order_semantics="unordered_set")
    expected = min(candidate.stable_key for candidate in first.candidates)
    q = {action.keys: 0.0 for action in enumerate_complete_actions(first, limit=3)}

    first_greedy = greedy_decode(
        first, step_logits=lambda _prefix, allowed: {token: 0.0 for token in allowed}
    )
    second_greedy = greedy_decode(
        second, step_logits=lambda _prefix, allowed: {token: 0.0 for token in allowed}
    )

    assert first_greedy.keys == second_greedy.keys == (expected,)
    assert q_argmax(first, q, enumeration_limit=3).keys == (expected,)
    assert q_argmax(second, q, enumeration_limit=3).keys == (expected,)
    assert _digest_for_index(first_state, first_greedy.option_indices[0]) == expected
    assert _digest_for_index(second_state, second_greedy.option_indices[0]) == expected


def test_greedy_and_q_can_choose_minimum_or_maximum_legal_cardinality(
    decision_state_factory,
) -> None:
    """Catches a decoder or Q selector fixed to maximum-cardinality selections."""
    envelope = DecisionEnvelope.from_decision_state(
        decision_state_factory(minimum=1, maximum=2), order_semantics="unordered_set"
    )
    actions = enumerate_complete_actions(envelope, limit=32)
    first_key = envelope.canonical_keys[0]

    def stop_after_minimum(prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        return {token: 2.0 if token == "__STOP__" and prefix else 0.0 for token in allowed}

    def continue_to_maximum(_prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        return {token: -2.0 if token == "__STOP__" else 0.0 for token in allowed}

    q_min = {action.keys: (4.0 if len(action.keys) == 1 else 0.0) for action in actions}
    q_max = {action.keys: (4.0 if len(action.keys) == 2 else 0.0) for action in actions}

    assert len(greedy_decode(envelope, step_logits=stop_after_minimum).keys) == 1
    assert len(greedy_decode(envelope, step_logits=continue_to_maximum).keys) == 2
    assert q_argmax(envelope, q_min, enumeration_limit=32).keys == (first_key,)
    assert len(q_argmax(envelope, q_max, enumeration_limit=32).keys) == 2


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), float("-inf")])
def test_greedy_rejects_nonfinite_or_boolean_logit_values(
    decision_state_factory, invalid: object
) -> None:
    """Catches greedy selection bypassing policy-score validation."""
    envelope = DecisionEnvelope.from_decision_state(
        decision_state_factory(minimum=1, maximum=1), order_semantics="unordered_set"
    )

    with pytest.raises(ValueError, match="finite non-bool"):
        greedy_decode(
            envelope,
            step_logits=lambda _prefix, allowed: {token: invalid for token in allowed},
        )


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_greedy_requires_an_exact_logit_domain(decision_state_factory, mode: str) -> None:
    """Catches greedy decode normalizing over a different token domain than training."""
    envelope = DecisionEnvelope.from_decision_state(
        decision_state_factory(minimum=1, maximum=1), order_semantics="unordered_set"
    )

    def logits(_prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        result = {token: 0.0 for token in allowed}
        if mode == "missing":
            result.pop(allowed[-1])
        else:
            result["not-legal"] = 0.0
        return result

    with pytest.raises(ValueError, match="exactly"):
        greedy_decode(envelope, step_logits=logits)


@pytest.mark.parametrize("mode", ["missing", "extra", "bool", "nan", "positive", "negative"])
def test_q_argmax_requires_an_exact_finite_q_domain(
    decision_state_factory, mode: str
) -> None:
    """Catches Q selection with an omitted, invented, boolean, or nonfinite action value."""
    envelope = DecisionEnvelope.from_decision_state(
        decision_state_factory(minimum=1, maximum=1), order_semantics="unordered_set"
    )
    actions = enumerate_complete_actions(envelope, limit=3)
    values: dict[tuple[str, ...], object] = {action.keys: 0.0 for action in actions}
    if mode == "missing":
        values.pop(actions[0].keys)
    elif mode == "extra":
        values[("not-a-legal-key",)] = 0.0
    else:
        values[actions[0].keys] = {
            "bool": True,
            "nan": float("nan"),
            "positive": float("inf"),
            "negative": float("-inf"),
        }[mode]

    with pytest.raises(ValueError, match="exactly|finite non-bool"):
        q_argmax(envelope, values, enumeration_limit=3)


def test_complete_actions_reject_noncanonical_unknown_and_stale_execution_data(
    decision_state_factory,
) -> None:
    """Catches malformed complete actions crossing an envelope or execution boundary."""
    first = DecisionEnvelope.from_decision_state(
        decision_state_factory(), order_semantics="unordered_set"
    )
    second = DecisionEnvelope.from_decision_state(
        decision_state_factory(), order_semantics="unordered_set"
    )
    keys = first.canonical_keys[:2]

    with pytest.raises(CompleteActionError, match="ascending"):
        CompleteAction(
            first,
            tuple(reversed(keys)),
            tuple(sorted(first.index_for_key(key) for key in keys)),
        )
    with pytest.raises(CompleteActionError, match="unknown"):
        CompleteAction(first, ("f" * 64,), (0,))
    with pytest.raises(CompleteActionError, match="current numeric"):
        CompleteAction(first, keys, tuple(reversed(sorted(first.index_for_key(key) for key in keys))))

    action = _action_for_keys(first, (first.canonical_keys[0],))
    with pytest.raises(CompleteActionError, match="stale"):
        complete_action_log_probability(
            second,
            action,
            step_logits=lambda _prefix, allowed: {token: 0.0 for token in allowed},
        )


def test_public_trace_is_positive_redacted_and_shuffle_invariant(
    decision_state_factory,
) -> None:
    """Catches a persisted trace that leaks private state or fails to identify its action."""
    original_state = decision_state_factory(option_order=(0, 1, 2))
    shuffled_state = decision_state_factory(option_order=(2, 0, 1))
    original = DecisionEnvelope.from_decision_state(
        original_state, order_semantics="unordered_set"
    )
    shuffled = DecisionEnvelope.from_decision_state(
        shuffled_state, order_semantics="unordered_set"
    )
    selected_key = min(original.canonical_keys)
    original_action = _action_for_keys(original, (selected_key,))
    shuffled_action = _action_for_keys(shuffled, (selected_key,))

    payload = original.to_public_trace_payload(original_action)
    shuffled_payload = shuffled.to_public_trace_payload(shuffled_action)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected_projection = next(
        item.action_key.to_public_trace_payload()
        for item in original_state.legal_actions
        if item.action_key.digest == selected_key
    )

    assert payload["schema_version"] == 1
    assert payload["selection_type"] == 0
    assert payload["min_count"] == 1
    assert payload["max_count"] == 2
    assert payload["order_semantics"] == "unordered_set"
    assert payload["selected_count"] == 1
    assert payload["selected_public_actions"] == [expected_projection]
    assert payload != original.to_public_trace_payload(
        _action_for_keys(original, (original.canonical_keys[1],))
    )
    assert serialized == json.dumps(shuffled_payload, sort_keys=True, separators=(",", ":"))
    assert _recursive_keys(payload).isdisjoint({"option_index", "option_indices"})
    for private_value in (
        "RAW_LOG_SENTINEL",
        "RAW_OBSERVATION_SENTINEL",
        "456789",
        original_state.digest,
        original_state.metadata.action_set_digest,
        selected_key,
    ):
        assert private_value not in serialized
    rendered = "\n".join(
        (repr(original.candidates[0]), repr(original), repr(original_action))
    )
    assert "option_index=" not in rendered
    assert "option_indices=" not in rendered
    assert str(original_action.option_indices) not in rendered
    assert original_state.digest not in rendered
    assert original_state.metadata.action_set_digest not in rendered
    assert selected_key not in rendered


def test_public_trace_schema_binds_context_and_is_option_permutation_invariant(
    decision_state_factory,
) -> None:
    """Catches a context-free trace identity or a public trace with implicit fields."""
    original_state = decision_state_factory(
        option_order=(0, 1, 2), selection_type=1, selection_context=9
    )
    shuffled_state = decision_state_factory(
        option_order=(2, 0, 1), selection_type=1, selection_context=9
    )
    changed_context_state = decision_state_factory(
        option_order=(0, 1, 2), selection_type=1, selection_context=10
    )
    original = DecisionEnvelope.from_decision_state(
        original_state, order_semantics="unordered_set"
    )
    shuffled = DecisionEnvelope.from_decision_state(
        shuffled_state, order_semantics="unordered_set"
    )
    changed_context = DecisionEnvelope.from_decision_state(
        changed_context_state, order_semantics="unordered_set"
    )

    payload = original.to_public_trace_payload(
        _action_for_keys(original, (original.canonical_keys[0],))
    )
    shuffled_payload = shuffled.to_public_trace_payload(
        _action_for_keys(shuffled, (shuffled.canonical_keys[0],))
    )
    changed_context_payload = changed_context.to_public_trace_payload(
        _action_for_keys(changed_context, (changed_context.canonical_keys[0],))
    )

    assert set(payload) == {
        "schema_version",
        "public_decision_identity",
        "public_state_digest",
        "public_action_set_digest",
        "selection_type",
        "selection_context",
        "min_count",
        "max_count",
        "order_semantics",
        "selected_count",
        "selected_public_actions",
    }
    assert payload["selection_context"] == 9
    assert payload["public_decision_identity"] == shuffled_payload["public_decision_identity"]
    assert payload["public_decision_identity"] != changed_context_payload["public_decision_identity"]


def test_public_trace_order_and_identity_ignore_private_card_digest_changes(
    decision_state_factory,
) -> None:
    """Catches persisted unordered ordering or identity derived from private stable keys."""
    first_state = decision_state_factory(private_card_id=100001, minimum=2, maximum=2)
    second_state = decision_state_factory(private_card_id=900009, minimum=2, maximum=2)
    first = DecisionEnvelope.from_decision_state(first_state, order_semantics="unordered_set")
    second = DecisionEnvelope.from_decision_state(second_state, order_semantics="unordered_set")
    first_private = next(
        item.action_key.digest for item in first_state.legal_actions if item.action_key.card_id is not None
    )
    second_private = next(
        item.action_key.digest for item in second_state.legal_actions if item.action_key.card_id is not None
    )
    first_attack = next(
        item.action_key.digest for item in first_state.legal_actions if item.action_key.card_id is None
    )
    second_attack = next(
        item.action_key.digest for item in second_state.legal_actions if item.action_key.card_id is None
    )

    first_payload = first.to_public_trace_payload(
        _action_for_keys(first, tuple(sorted((first_private, first_attack))))
    )
    second_payload = second.to_public_trace_payload(
        _action_for_keys(second, tuple(sorted((second_private, second_attack))))
    )

    assert first_payload["public_decision_identity"] == second_payload["public_decision_identity"]
    assert json.dumps(first_payload, sort_keys=True, separators=(",", ":")) == json.dumps(
        second_payload, sort_keys=True, separators=(",", ":")
    )
    assert first_private not in json.dumps(first_payload, sort_keys=True)
    assert second_private not in json.dumps(second_payload, sort_keys=True)


def test_public_trace_preserves_ordered_sequence_semantics_and_rejects_synthetic_envelopes() -> None:
    """Catches a sequence trace sorted as a set or a synthetic envelope persisted unsafely."""
    state = build_decision_state(
        _observation(
            options=[
                {"type": 15, "cardId": 101, "serial": 1001},
                {"type": 15, "cardId": 102, "serial": 1002},
            ],
            minimum=2,
            maximum=2,
            selection_type=5,
            selection_context=34,
            public_active=[_card(101, serial=1001)],
            public_bench=[_card(102, serial=1002)],
        )
    )
    envelope = DecisionEnvelope.from_decision_state(state)
    selected = (envelope.canonical_keys[1], envelope.canonical_keys[0])
    action = _action_for_keys(envelope, selected)
    payload = envelope.to_public_trace_payload(action)
    expected = [
        next(
            item.action_key.to_public_trace_payload()
            for item in state.legal_actions
            if item.action_key.digest == key
        )
        for key in selected
    ]

    assert payload["selected_public_actions"] == expected
    synthetic = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 0),),
        min_count=1,
        max_count=1,
        order_semantics="unordered_set",
    )
    with pytest.raises(DecisionEnvelopeError, match="non-persistable"):
        synthetic.to_public_trace_payload(enumerate_complete_actions(synthetic, limit=1)[0])
