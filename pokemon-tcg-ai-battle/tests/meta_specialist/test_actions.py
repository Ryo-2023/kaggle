from __future__ import annotations

import hashlib
import math

import pytest
import mage_ptcg.meta_specialist.actions as actions_module
import mage_ptcg.meta_specialist.cabt_json_contract_v1 as contract_module

from mage_ptcg.meta_specialist.actions import (
    Candidate,
    CompleteActionEnumerationError,
    CompleteActionProbabilityError,
    DecisionEnvelopeError,
    DecisionEnvelope,
    STOP_TOKEN,
    complete_action_distribution,
    complete_action_log_probability,
    enumerate_complete_actions,
    legal_next_tokens,
    q_argmax,
)


def _envelope(minimum: int, maximum: int) -> DecisionEnvelope:
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64
    return DecisionEnvelope(
        selection_type=0,
        decision_digest="d" * 64,
        action_set_digest="e" * 64,
        candidates=(
            Candidate(key_c, 2),
            Candidate(key_a, 0),
            Candidate(key_b, 1),
        ),
        min_count=minimum,
        max_count=maximum,
        order_semantics="unordered_set",
    )


def _large_envelope(
    candidate_count: int,
    *,
    order_semantics: str,
) -> DecisionEnvelope:
    return DecisionEnvelope(
        selection_type=0,
        decision_digest="d" * 64,
        action_set_digest="e" * 64,
        candidates=tuple(
            Candidate(f"{index + 1:064x}", index) for index in range(candidate_count)
        ),
        min_count=1,
        max_count=candidate_count,
        order_semantics=order_semantics,  # type: ignore[arg-type]
    )


def test_agent_json_contract_exports_exact_fresh_payload_bytes_and_digest() -> None:
    """Catches schema, pair-order, mutability, canonical-byte, or hash-domain drift."""
    expected_payload = {
        "schema_version": "meta-specialist-cabt-agent-json-contract-v1",
        "selection_schemas": [
            [0, 0],
            [1, 1],
            [1, 2],
            [1, 3],
            [1, 4],
            [1, 5],
            [1, 6],
            [1, 7],
            [1, 8],
            [1, 9],
            [1, 10],
            [1, 11],
            [1, 12],
            [1, 13],
            [1, 14],
            [1, 15],
            [1, 16],
            [1, 17],
            [1, 18],
            [1, 19],
            [1, 20],
            [1, 21],
            [1, 22],
            [1, 23],
            [1, 24],
            [1, 25],
            [2, 26],
            [2, 27],
            [2, 28],
            [3, 29],
            [4, 30],
            [4, 31],
            [4, 32],
            [4, 33],
            [5, 34],
            [6, 35],
            [6, 36],
            [7, 37],
            [8, 38],
            [8, 39],
            [8, 40],
            [9, 41],
            [9, 42],
            [9, 43],
            [9, 44],
            [9, 45],
            [9, 46],
            [10, 47],
            [10, 48],
        ],
        "ordered_selection_schemas": [[5, 34]],
    }
    expected_bytes = (
        b'{"ordered_selection_schemas":[[5,34]],"schema_version":'
        b'"meta-specialist-cabt-agent-json-contract-v1","selection_schemas":'
        b'[[0,0],[1,1],[1,2],[1,3],[1,4],[1,5],[1,6],[1,7],[1,8],[1,9],'
        b'[1,10],[1,11],[1,12],[1,13],[1,14],[1,15],[1,16],[1,17],[1,18],'
        b'[1,19],[1,20],[1,21],[1,22],[1,23],[1,24],[1,25],[2,26],[2,27],'
        b'[2,28],[3,29],[4,30],[4,31],[4,32],[4,33],[5,34],[6,35],[6,36],'
        b'[7,37],[8,38],[8,39],[8,40],[9,41],[9,42],[9,43],[9,44],[9,45],'
        b'[9,46],[10,47],[10,48]]}'
    )
    expected_digest = "7993f5770d088181206c00bac9f959b3c3cbb05e4ca22da38d947ac1c65b9259"

    first = contract_module.cabt_agent_json_contract_payload_v1()
    second = contract_module.cabt_agent_json_contract_payload_v1()

    assert first == expected_payload
    assert second == expected_payload
    assert first is not second
    assert first["selection_schemas"] is not second["selection_schemas"]
    assert len(first["selection_schemas"]) == 49
    assert all(
        type(value) is int
        for pair in first["selection_schemas"]
        for value in pair
    )
    first["selection_schemas"][0][0] = 99
    assert second == expected_payload
    assert contract_module.CABT_AGENT_JSON_CONTRACT_CANONICAL_BYTES_V1 == expected_bytes
    assert contract_module.CABT_AGENT_JSON_CONTRACT_SHA256_V1 == expected_digest
    assert hashlib.sha256(
        b"meta-specialist-cabt-agent-json-contract-v1\0" + expected_bytes
    ).hexdigest() == expected_digest


def test_complete_action_enumerates_every_legal_unordered_set() -> None:
    """Catches a missing legal unordered selection or noncanonical key order."""
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64

    actions = enumerate_complete_actions(_envelope(1, 2), limit=32)

    assert [action.keys for action in actions] == [
        (key_a,),
        (key_b,),
        (key_c,),
        (key_a, key_b),
        (key_a, key_c),
        (key_b, key_c),
    ]


def test_optional_selection_includes_empty_action_once() -> None:
    """Catches an optional selection that omits or duplicates the empty action."""
    actions = enumerate_complete_actions(_envelope(0, 2), limit=32)

    assert actions[0].keys == ()
    assert sum(action.keys == () for action in actions) == 1


def test_enumeration_rejects_oversized_domain_before_materializing_actions() -> None:
    """Catches an enumerator that allocates an action list before enforcing its limit."""
    with pytest.raises(CompleteActionEnumerationError, match="exceeds limit"):
        enumerate_complete_actions(_envelope(1, 2), limit=5)


@pytest.mark.parametrize(
    ("order_semantics", "counter_name"),
    [("unordered_set", "comb"), ("ordered_sequence", "perm")],
)
def test_large_enumeration_count_caps_at_limit_plus_one_and_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
    order_semantics: str,
    counter_name: str,
) -> None:
    envelope = _large_envelope(60, order_semantics=order_semantics)
    original = getattr(actions_module, counter_name)
    calls = 0

    def bounded_counter(candidate_count: int, selection_count: int) -> int:
        nonlocal calls
        calls += 1
        if calls > 1:
            pytest.fail("enumeration count evaluated terms after reaching limit + 1")
        return original(candidate_count, selection_count)

    monkeypatch.setattr(actions_module, counter_name, bounded_counter)

    with pytest.raises(CompleteActionEnumerationError, match="count 2 exceeds limit 1"):
        enumerate_complete_actions(envelope, limit=1)
    assert calls == 1


@pytest.mark.parametrize("entry_point", ["enumerate", "distribution", "q"])
def test_complete_action_entry_points_reject_limit_above_hard_ceiling_before_counting(
    monkeypatch: pytest.MonkeyPatch,
    entry_point: str,
) -> None:
    envelope = _envelope(1, 1)

    def forbidden_count(*_args: object, **_kwargs: object) -> int:
        pytest.fail("hard-ceiling rejection must happen before enumeration counting")

    monkeypatch.setattr(actions_module, "_enumeration_count", forbidden_count)

    with pytest.raises(CompleteActionEnumerationError, match="65,536"):
        if entry_point == "enumerate":
            enumerate_complete_actions(envelope, limit=65_537)
        elif entry_point == "distribution":
            complete_action_distribution(
                envelope,
                step_logits=lambda _prefix, allowed: {
                    token: 0.0 for token in allowed
                },
                enumeration_limit=65_537,
            )
        else:
            q_argmax(envelope, {}, enumeration_limit=65_537)


@pytest.mark.parametrize("invalid_limit", [True, 0, -1])
def test_complete_action_enumeration_limit_is_a_positive_non_bool_int(
    invalid_limit: object,
) -> None:
    with pytest.raises(CompleteActionEnumerationError, match="positive non-bool int"):
        enumerate_complete_actions(_envelope(1, 1), limit=invalid_limit)  # type: ignore[arg-type]


def test_complete_action_hard_ceiling_value_is_accepted_for_a_small_domain() -> None:
    assert len(enumerate_complete_actions(_envelope(1, 1), limit=65_536)) == 3


def test_envelope_rejects_more_than_sixty_legal_candidates() -> None:
    with pytest.raises(DecisionEnvelopeError, match="at most 60"):
        _large_envelope(61, order_semantics="unordered_set")


def test_envelope_rejects_duplicate_candidates_and_inconsistent_bounds() -> None:
    """Catches ambiguous current-index mappings or impossible cardinality bounds."""
    key = "1" * 64
    with pytest.raises(DecisionEnvelopeError, match="stable keys must be unique"):
        DecisionEnvelope(
            selection_type=0,
            decision_digest="d" * 64,
            action_set_digest="e" * 64,
            candidates=(Candidate(key, 0), Candidate(key, 1)),
            min_count=0,
            max_count=1,
            order_semantics="unordered_set",
        )
    with pytest.raises(DecisionEnvelopeError, match="selection bounds"):
        DecisionEnvelope(
            selection_type=0,
            decision_digest="d" * 64,
            action_set_digest="e" * 64,
            candidates=(Candidate(key, 0),),
            min_count=1,
            max_count=2,
            order_semantics="unordered_set",
        )
    with pytest.raises(DecisionEnvelopeError, match="option indices must be unique"):
        DecisionEnvelope(
            selection_type=0,
            decision_digest="d" * 64,
            action_set_digest="e" * 64,
            candidates=(Candidate("1" * 64, 0), Candidate("2" * 64, 0)),
            min_count=0,
            max_count=1,
            order_semantics="unordered_set",
        )


def test_zero_maximum_enumerates_only_the_empty_complete_action() -> None:
    """Catches a zero-maximum selection that produces a nonempty engine request."""
    assert [action.keys for action in enumerate_complete_actions(_envelope(0, 0), limit=1)] == [()]


def test_ordered_sequences_include_each_permutation_once() -> None:
    """Catches an ordered selection treated as an unordered set."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 5), ("b", 2)),
        min_count=1,
        max_count=2,
        order_semantics="ordered_sequence",
    )

    actions = enumerate_complete_actions(envelope, limit=4)

    assert [action.keys for action in actions] == [("a",), ("b",), ("a", "b"), ("b", "a")]
    assert actions[-1].option_indices == (2, 5)


def test_contract_values_are_frozen_after_construction() -> None:
    """Catches a mutable candidate, envelope, or complete action leaking across decisions."""
    envelope = _envelope(1, 1)
    action = enumerate_complete_actions(envelope, limit=3)[0]

    with pytest.raises(AttributeError):
        envelope.candidates[0].option_index = 99  # type: ignore[misc]
    with pytest.raises(AttributeError):
        envelope.min_count = 0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        action.keys = ()  # type: ignore[misc]


def test_canonical_next_token_mask_enforces_reachability_and_stop_boundaries() -> None:
    """Catches logits being normalized over impossible set prefixes or illegal STOP."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 4), ("b", 2), ("c", 0)),
        min_count=2,
        max_count=2,
        order_semantics="unordered_set",
    )

    assert legal_next_tokens(envelope, ()) == ("a", "b")
    assert legal_next_tokens(envelope, ("a",)) == ("b", "c")
    assert legal_next_tokens(envelope, ("a", "b")) == (STOP_TOKEN,)
    with pytest.raises(ValueError, match="ascending"):
        legal_next_tokens(envelope, ("b", "a"))
    with pytest.raises(ValueError, match="unknown"):
        legal_next_tokens(envelope, ("unknown",))


def test_ordered_next_tokens_preserve_all_unselected_sequence_choices() -> None:
    """Catches ordered decoding applying unordered ascending-key masking."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 5), ("b", 1)),
        min_count=1,
        max_count=2,
        order_semantics="ordered_sequence",
    )

    assert legal_next_tokens(envelope, ()) == ("a", "b")
    assert legal_next_tokens(envelope, ("b",)) == ("a", STOP_TOKEN)


def test_canonical_autoregressive_set_distribution_sums_to_one() -> None:
    """Catches set paths that are omitted or receive an unnormalized probability."""
    envelope = _envelope(1, 2)
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64

    def logits(prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        return {token: 0.0 for token in allowed}

    distribution = complete_action_distribution(
        envelope,
        step_logits=logits,
        enumeration_limit=32,
    )

    assert set(action.keys for action in distribution) == {
        (key_a,),
        (key_b,),
        (key_c,),
        (key_a, key_b),
        (key_a, key_c),
        (key_b, key_c),
    }
    assert math.isclose(sum(distribution.values()), 1.0, abs_tol=1e-12)
    assert all(probability > 0.0 for probability in distribution.values())


def test_ordered_sequence_distribution_includes_both_two_item_orders() -> None:
    """Catches an ordered autoregressive decoder that drops one legal permutation."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 1), ("b", 3)),
        min_count=1,
        max_count=2,
        order_semantics="ordered_sequence",
    )

    distribution = complete_action_distribution(
        envelope,
        step_logits=lambda _prefix, allowed: {token: 0.0 for token in allowed},
        enumeration_limit=4,
    )

    assert set(action.keys for action in distribution) == {
        ("a",),
        ("b",),
        ("a", "b"),
        ("b", "a"),
    }
    assert math.isclose(sum(distribution.values()), 1.0, abs_tol=1e-12)


def test_optional_and_zero_maximum_probability_domains_are_normalized() -> None:
    """Catches optional or zero-cardinality actions being outside the policy domain."""
    optional = _envelope(0, 2)
    zero_maximum = _envelope(0, 0)

    optional_distribution = complete_action_distribution(
        optional,
        step_logits=lambda _prefix, allowed: {token: 0.0 for token in allowed},
        enumeration_limit=32,
    )
    zero_distribution = complete_action_distribution(
        zero_maximum,
        step_logits=lambda _prefix, _allowed: pytest.fail("forced STOP must not request logits"),
        enumeration_limit=1,
    )

    assert () in {action.keys for action in optional_distribution}
    assert math.isclose(sum(optional_distribution.values()), 1.0, abs_tol=1e-12)
    assert {action.keys: probability for action, probability in zero_distribution.items()} == {(): 1.0}


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), float("-inf")])
def test_probability_rejects_nonfinite_or_boolean_logits(invalid: object) -> None:
    """Catches an unsafe policy score entering masked categorical normalization."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 0),),
        min_count=1,
        max_count=1,
        order_semantics="unordered_set",
    )

    with pytest.raises(ValueError, match="finite non-bool"):
        complete_action_log_probability(
            envelope,
            enumerate_complete_actions(envelope, limit=1)[0],
            step_logits=lambda _prefix, allowed: {token: invalid for token in allowed},
        )


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_probability_requires_an_exact_logit_token_domain(mode: str) -> None:
    """Catches a model silently omitting a legal token or scoring an illegal token."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 0), ("b", 1)),
        min_count=1,
        max_count=1,
        order_semantics="unordered_set",
    )

    def logits(_prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        scores = {token: 0.0 for token in allowed}
        if mode == "missing":
            scores.pop(allowed[-1])
        else:
            scores["illegal"] = 0.0
        return scores

    with pytest.raises(ValueError, match="exactly"):
        complete_action_distribution(envelope, step_logits=logits, enumeration_limit=2)


def test_distribution_rejects_finite_logits_that_underflow_legal_support() -> None:
    """Every legal action under finite logits must retain positive float support."""
    envelope = DecisionEnvelope.for_test(
        selection_type=0,
        candidates=(("a", 0), ("b", 1)),
        min_count=1,
        max_count=1,
        order_semantics="unordered_set",
    )

    def logits(_prefix: tuple[str, ...], allowed: tuple[str, ...]) -> dict[str, float]:
        return {token: (0.0 if token == "a" else -1_000.0) for token in allowed}

    with pytest.raises(CompleteActionProbabilityError, match="underflowed to zero"):
        complete_action_distribution(
            envelope,
            step_logits=logits,
            enumeration_limit=2,
        )
