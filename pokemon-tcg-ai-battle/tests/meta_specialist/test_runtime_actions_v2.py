"""TDD coverage for the private actor-visible runtime action envelope."""

from __future__ import annotations

import math
import json

import pytest

from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.meta_specialist.actions import (
    CompleteAction,
    DecisionEnvelope,
    DecisionEnvelopeError,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistStepLogitsV1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    PRIVATE_ENVELOPE_SERIALIZATION_ERROR,
    RuntimeActionError,
    RuntimeCompleteAction,
    RuntimeDecisionEnvelope,
    RuntimeEnumerationError,
    RuntimeEnvelopeError,
    RuntimeScoredCompleteActionV2,
    SemanticRuntimeCompleteActionV2,
    beam_search_runtime_actions_v2,
    enumerate_runtime_complete_actions_v2,
    greedy_decode_runtime_action_v2,
    RuntimePolicyError,
    runtime_complete_action_log_probability_v2,
    runtime_semantic_complete_action_log_probability_v2,
    sample_runtime_action_v2,
    semantic_runtime_complete_action_from_runtime_action_v2,
)


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": 100, "maxHp": 120,
        "appearThisTurn": False, "energies": [1], "energyCards": [],
        "tools": [], "preEvolution": [],
    }


def _player(hand: object, *, active: list[object] | None = None) -> dict[str, object]:
    return {
        "active": [] if active is None else active, "asleep": False, "bench": [],
        "benchMax": 5, "burned": False, "confused": False, "deckCount": 53,
        "discard": [], "hand": hand, "handCount": len(hand) if isinstance(hand, list) else 0,
        "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }


def _observation() -> dict[str, object]:
    hand = [_card(101, 1001, 0), _card(102, 1002, 0)]
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                _player(hand, active=[_pokemon(201, 2001)]),
                _player(None, active=[_pokemon(301, 3001)]),
            ],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 0,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _envelope(observation: dict[str, object] | None = None) -> RuntimeDecisionEnvelope:
    return RuntimeDecisionEnvelope.from_actor_visible_state(
        build_actor_visible_decision_state_v2(_observation() if observation is None else observation),
        vocabulary=make_test_card_vocabulary_v1(range(1, 2_000)),
    )


def _large_main_observation(candidate_count: int) -> dict[str, object]:
    observation = _observation()
    own = observation["current"]["players"][0]  # type: ignore[index]
    own["hand"] = [_card(500 + index, 1_500 + index, 0) for index in range(60)]
    own["handCount"] = 60
    own["bench"] = [_pokemon(801, 8_001)]
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
        "maxCount": 1, "minCount": 1, "option": options,
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    return observation


def _max_domain_observation() -> dict[str, object]:
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 0, "number": index} for index in range(512)],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    return observation


def test_runtime_envelope_exposes_only_semantic_classes_to_policy() -> None:
    """Fails if runtime scoring receives local IDs or execution indices rather than semantic classes."""
    envelope = _envelope()

    step = envelope.build_step_input(())

    assert step.stop_available is True
    assert len(step.allowed_semantic_classes) == 2
    assert not hasattr(step, "local_action_ids")


def test_runtime_complete_action_canonicalizes_local_identity_but_decodes_numeric_indices() -> None:
    """Fails if unordered local keys lose their canonical identity or current CABT index mapping."""
    observation = _observation()
    observation["select"]["maxCount"] = 2  # type: ignore[index]
    state = build_actor_visible_decision_state_v2(observation)
    envelope = RuntimeDecisionEnvelope.from_actor_visible_state(
        state, vocabulary=make_test_card_vocabulary_v1(range(1, 2_000))
    )
    expected = tuple(sorted(
        (action.local_action_id, action.action_key_digest, index)
        for index, action in enumerate(state.legal_actions)
    ))

    action = envelope.complete_action((expected[1][0], expected[0][0]))

    assert action.local_action_ids == (expected[0][0], expected[1][0])
    assert envelope.decode_option_indices(action) == (0, 1)


def test_runtime_action_rejects_cross_envelope_and_public_serializer_is_redacted() -> None:
    """Fails if private local keys can execute against a newer decision or serialize publicly."""
    first = _envelope()
    second = _envelope()
    first_id = build_actor_visible_decision_state_v2(_observation()).legal_actions[0].local_action_id
    action = first.complete_action((first_id,))

    with pytest.raises(RuntimeActionError, match="stale"):
        second.decode_option_indices(action)
    with pytest.raises(RuntimeEnvelopeError) as error:
        first.to_public_trace_payload(action)
    assert str(error.value) == PRIVATE_ENVELOPE_SERIALIZATION_ERROR
    with pytest.raises(RuntimeActionError) as action_error:
        action.to_public_trace_payload()
    assert str(action_error.value) == PRIVATE_ENVELOPE_SERIALIZATION_ERROR


def test_duplicate_public_candidates_decode_privately_without_public_fallback() -> None:
    """Fails if legitimate private aliases still force the frozen public envelope fallback."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(101, 10, 0), _card(101, 11, 0),
    ]
    observation["select"]["option"] = [  # type: ignore[index]
        {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
        {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
    ]
    state = build_actor_visible_decision_state_v2(observation)
    envelope = RuntimeDecisionEnvelope.from_actor_visible_state(
        state, vocabulary=make_test_card_vocabulary_v1(range(1, 2_000))
    )

    class PreferAliasClass:
        calls = 0

        def logits(self, _model_input, step_input):
            self.calls += 1
            return SpecialistStepLogitsV1(
                semantic_logits=(1.0,) * len(step_input.allowed_semantic_classes),
                stop_logit=0.0 if step_input.stop_available else None,
            )

    policy = PreferAliasClass()
    action = greedy_decode_runtime_action_v2(envelope, policy=policy)

    assert envelope.decode_option_indices(action) in ((0,), (1,))
    assert policy.calls == 1
    with pytest.raises(DecisionEnvelopeError, match="indistinguishable public"):
        DecisionEnvelope.from_decision_state(build_decision_state(observation))
    with pytest.raises(RuntimeEnvelopeError, match="unique"):
        envelope.to_public_envelope()


def test_explicit_unique_public_conversion_matches_frozen_public_trace_bytes() -> None:
    """Fails if the private-to-public bridge reconstructs a different frozen action trace."""
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 0,
        "option": [{"type": 0, "number": 1}, {"type": 0, "number": 2}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    private_state = build_actor_visible_decision_state_v2(observation)
    runtime = RuntimeDecisionEnvelope.from_actor_visible_state(
        private_state, vocabulary=make_test_card_vocabulary_v1(range(1, 2_000))
    )
    selected = private_state.legal_actions[1]
    runtime_action = runtime.complete_action((selected.local_action_id,))
    direct_decision = build_decision_state(observation)
    direct = DecisionEnvelope.from_decision_state(direct_decision)
    direct_action = CompleteAction(
        envelope=direct,
        keys=(selected.action_key_digest,),
        option_indices=(1,),
    )

    public_envelope, public_action = runtime.convert_to_public(runtime_action)

    runtime_trace = public_envelope.to_public_trace_payload(public_action)
    direct_trace = direct.to_public_trace_payload(direct_action)
    assert runtime_trace == direct_trace
    assert json.dumps(runtime_trace, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8") == json.dumps(
        direct_trace, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert public_action.keys == direct_action.keys
    assert public_envelope.decision_digest == direct_decision.digest


@pytest.mark.parametrize("candidate_count", (61, 64, 67))
def test_large_main_domains_decode_privately_but_never_widen_public_v1(candidate_count: int) -> None:
    """Fails if observed 61+/Main decisions hit a fallback or silently widen frozen public limits."""
    envelope = _envelope(_large_main_observation(candidate_count))

    class FirstClass:
        def logits(self, _model_input, step_input):
            return SpecialistStepLogitsV1(
                semantic_logits=(1.0,) + (0.0,) * (len(step_input.allowed_semantic_classes) - 1),
                stop_logit=0.0 if step_input.stop_available else None,
            )

    action = greedy_decode_runtime_action_v2(envelope, policy=FirstClass())

    assert envelope.candidate_count == candidate_count
    assert len(envelope.decode_option_indices(action)) == 1
    with pytest.raises(RuntimeEnvelopeError, match="public-v1 limits"):
        envelope.to_public_envelope()
    with pytest.raises(DecisionEnvelopeError, match="at most 60"):
        DecisionEnvelope.from_decision_state(build_decision_state(_large_main_observation(candidate_count)))


def test_maximum_512_candidate_domain_stays_private_and_semantic() -> None:
    """Fails if the private runtime regresses to the frozen public 60-candidate cap."""
    envelope = _envelope(_max_domain_observation())
    step = envelope.build_step_input(())

    assert envelope.candidate_count == 512
    assert len(step.allowed_semantic_classes) == 512
    with pytest.raises(RuntimeEnvelopeError, match="public-v1 limits"):
        envelope.to_public_envelope()


def test_exact_enumeration_alone_obeys_the_65536_materialization_cap() -> None:
    """Fails if inference-sized domains are capped, or exact teacher enumeration allocates past 65,536."""
    small = _envelope()
    assert len(enumerate_runtime_complete_actions_v2(small, limit=3)) == 3

    large_observation = _max_domain_observation()
    large_observation["select"]["maxCount"] = 512  # type: ignore[index]
    large = _envelope(large_observation)
    with pytest.raises(RuntimeEnumerationError, match="exceeds limit"):
        enumerate_runtime_complete_actions_v2(large, limit=65_536)
    with pytest.raises(RuntimeEnumerationError, match="65,536"):
        enumerate_runtime_complete_actions_v2(small, limit=65_537)


def test_ordered_skill_order_keeps_both_private_permutations_while_unordered_is_canonical() -> None:
    """Fails if the sole ordered CABT schema is collapsed to a local-ID set."""
    ordered_observation = _observation()
    ordered_observation["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 0,
        "option": [
            {"type": 15, "cardId": 101, "serial": 1001},
            {"type": 15, "cardId": 102, "serial": 1002},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    ordered_state = build_actor_visible_decision_state_v2(ordered_observation)
    ordered = RuntimeDecisionEnvelope.from_actor_visible_state(
        ordered_state, vocabulary=make_test_card_vocabulary_v1(range(1, 2_000))
    )
    first_id, second_id = (action.local_action_id for action in ordered_state.legal_actions)

    forward = ordered.complete_action((first_id, second_id))
    backward = ordered.complete_action((second_id, first_id))

    assert ordered.decode_option_indices(forward) == (0, 1)
    assert ordered.decode_option_indices(backward) == (1, 0)
    assert forward != backward

    unordered_observation = _observation()
    unordered_observation["select"]["maxCount"] = 2  # type: ignore[index]
    unordered_state = build_actor_visible_decision_state_v2(unordered_observation)
    unordered = RuntimeDecisionEnvelope.from_actor_visible_state(
        unordered_state, vocabulary=make_test_card_vocabulary_v1(range(1, 2_000))
    )
    unordered_ids = tuple(action.local_action_id for action in unordered_state.legal_actions)
    canonical = unordered.complete_action(unordered_ids)
    reversed_input = unordered.complete_action(unordered_ids[::-1])

    assert canonical == reversed_input
    assert canonical.local_action_ids == tuple(sorted(unordered_ids))
    assert unordered.decode_option_indices(canonical) == (0, 1)


def test_zero_option_and_minimum_stop_boundaries_use_the_shared_forced_stop_path() -> None:
    """Fails if zero options invoke a policy or STOP appears before min_count."""
    zero_observation = _observation()
    zero_observation["select"]["option"] = []  # type: ignore[index]
    zero_observation["select"]["minCount"] = 0  # type: ignore[index]
    zero_observation["select"]["maxCount"] = 0  # type: ignore[index]
    zero = _envelope(zero_observation)

    class NeverCalled:
        def logits(self, _model_input, _step_input):  # pragma: no cover - failure path
            raise AssertionError("forced STOP must not call the policy")

    action = greedy_decode_runtime_action_v2(zero, policy=NeverCalled())
    assert zero.build_step_input(()).stop_available is True
    assert action.local_action_ids == ()
    assert zero.decode_option_indices(action) == ()

    minimum_one = _observation()
    minimum_one["select"]["minCount"] = 1  # type: ignore[index]
    minimum = _envelope(minimum_one)
    first_id = build_actor_visible_decision_state_v2(minimum_one).legal_actions[0].local_action_id
    assert minimum.build_step_input(()).stop_available is False
    assert minimum.build_step_input((first_id,)).stop_available is True


def test_runtime_rejects_wrong_or_nonfinite_semantic_logit_domains() -> None:
    """Fails if a decoder accepts a malformed shared v2 policy output."""
    envelope = _envelope()

    class WrongArity:
        def logits(self, _model_input, step_input):
            return SpecialistStepLogitsV1(
                semantic_logits=(),
                stop_logit=0.0 if step_input.stop_available else None,
            )

    with pytest.raises(RuntimePolicyError, match="semantic step domain"):
        greedy_decode_runtime_action_v2(envelope, policy=WrongArity())

    class NonFinite:
        def logits(self, _model_input, step_input):
            result = SpecialistStepLogitsV1(
                semantic_logits=(0.0,) * len(step_input.allowed_semantic_classes),
                stop_logit=0.0 if step_input.stop_available else None,
            )
            object.__setattr__(result, "semantic_logits", (float("nan"),) * len(result.semantic_logits))
            return result

    with pytest.raises(RuntimePolicyError, match="semantic step domain"):
        greedy_decode_runtime_action_v2(envelope, policy=NonFinite())


def test_alias_selection_removes_one_local_alias_then_permits_legal_reselection() -> None:
    """Fails if a semantic alias is one-hot selected or a remaining same-class alias becomes unavailable."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(101, 10, 0), _card(101, 11, 0),
    ]
    observation["select"] = {  # type: ignore[index]
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [
            {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    envelope = _envelope(observation)

    class AliasPolicy:
        calls = 0

        def logits(self, _model_input, step_input):
            self.calls += 1
            assert len(step_input.allowed_semantic_classes) == 1
            return SpecialistStepLogitsV1(semantic_logits=(1.0,), stop_logit=None)

    policy = AliasPolicy()
    action = greedy_decode_runtime_action_v2(envelope, policy=policy)

    assert policy.calls == 2
    assert len(action.local_action_ids) == 2
    assert envelope.decode_option_indices(action) == (0, 1)


def test_collision_telemetry_has_exact_safe_fields_without_recursive_private_leaks() -> None:
    """Fails if collision reporting carries an ID, actor payload, serial, reveal, or execution index."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(101, 10, 0), _card(101, 11, 0),
    ]
    duplicate = _envelope(observation)
    local_id = build_actor_visible_decision_state_v2(observation).legal_actions[0].local_action_id
    telemetry = duplicate.collision_telemetry(duplicate.complete_action((local_id,)))

    assert telemetry == {
        "status": "duplicate-public-identity",
        "selected_count": 1,
        "collision_group_sizes": [2],
    }

    forbidden = {"id", "payload", "serial", "index", "local", "public_action", "reveal", "actor"}

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                assert not any(token in key.lower() for token in forbidden)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            assert "10" not in value and "11" not in value

    walk(telemetry)


def test_semantic_class_log_probability_greedy_beam_and_sampling_agree_without_alias_one_hot() -> None:
    """Fails if an alias count changes policy class arity, probability, or class-first decoding."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(101, 10, 0), _card(101, 11, 0), _card(102, 12, 0),
    ]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["select"] = {  # type: ignore[index]
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [
            {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            {"type": 3, "area": 2, "index": 2, "playerIndex": 0},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    state = build_actor_visible_decision_state_v2(observation)
    envelope = _envelope(observation)

    class ClassPolicy:
        def logits(self, _model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            assert tuple(item.allowed_alias_count for item in step_input.allowed_semantic_classes) == (2, 1)
            return SpecialistStepLogitsV1(
                semantic_logits=(math.log(0.6), math.log(0.4)), stop_logit=None
            )

    policy = ClassPolicy()
    greedy = greedy_decode_runtime_action_v2(envelope, policy=policy)
    beams = beam_search_runtime_actions_v2(envelope, policy=policy, beam_width=2)

    class FixedRandom:
        def random(self) -> float:
            return 0.8

    sampled = sample_runtime_action_v2(envelope, policy=policy, rng=FixedRandom())

    assert isinstance(beams[0], RuntimeScoredCompleteActionV2)
    assert envelope.decode_option_indices(greedy) in ((0,), (1,))
    assert envelope.decode_option_indices(sampled) == (2,)
    assert beams[0].action == greedy
    assert beams[0].log_probability == pytest.approx(math.log(0.6))
    assert beams[1].log_probability == pytest.approx(math.log(0.4))
    greedy_semantic = semantic_runtime_complete_action_from_runtime_action_v2(envelope, greedy)
    assert isinstance(greedy_semantic, SemanticRuntimeCompleteActionV2)
    assert beams[0].semantic_action == greedy_semantic
    assert runtime_semantic_complete_action_log_probability_v2(
        envelope, greedy_semantic, policy=policy
    ) == pytest.approx(math.log(0.6))
    with pytest.raises(RuntimeActionError, match="semantic-complete-action"):
        runtime_complete_action_log_probability_v2(envelope, greedy, policy=policy)
    assert len({state.legal_actions[0].local_action_id, state.legal_actions[1].local_action_id}) == 2


def test_envelope_and_action_replay_validation_rejects_direct_or_object_setattr_forgery() -> None:
    """Fails if a frozen direct constructor or nested replacement bypasses state, feature, digest, or index checks."""
    state = build_actor_visible_decision_state_v2(_observation())
    vocabulary = make_test_card_vocabulary_v1(range(1, 2_000))
    envelope = RuntimeDecisionEnvelope.from_actor_visible_state(state, vocabulary=vocabulary)

    with pytest.raises(RuntimeEnvelopeError, match="lowercase"):
        RuntimeDecisionEnvelope(
            _state=state,
            _extracted=envelope._extracted,
            _vocabulary=vocabulary,
            decision_digest="A" * 64,
        )

    object.__setattr__(envelope, "decision_digest", "0" * 64)
    with pytest.raises(RuntimeEnvelopeError, match="does not bind"):
        envelope.build_step_input(())

    clean = _envelope()
    selected = build_actor_visible_decision_state_v2(_observation()).legal_actions[0].local_action_id
    action = clean.complete_action((selected,))
    object.__setattr__(action, "option_indices", (99,))
    with pytest.raises(RuntimeActionError, match="execution order"):
        clean.decode_option_indices(action)

    other_observation = _observation()
    other_observation["select"] = {  # type: ignore[index]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 0, "option": [{"type": 0, "number": 1}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    other = _envelope(other_observation)
    object.__setattr__(clean, "_extracted", other._extracted)
    with pytest.raises(RuntimeEnvelopeError, match="does not match"):
        clean.build_step_input(())


def test_runtime_step_cache_rejects_a_mutated_frozen_step_object() -> None:
    """Optimization must not turn a returned cached step into mutable authority."""
    envelope = _envelope()
    step = envelope.build_step_input(())
    object.__setattr__(step, "stop_available", not step.stop_available)
    with pytest.raises(RuntimeEnvelopeError, match="mutated"):
        envelope.build_step_input(())


def test_runtime_action_provenance_is_issued_once_and_rejects_mutation_or_repost_init() -> None:
    """Fails if object.__setattr__, origin deletion, or repost-init can rebind an action."""
    first = _envelope()
    second = _envelope()
    local_id = first._state.legal_actions[0].local_action_id
    action = first.complete_action((local_id,))
    origin = action._origin_commitment

    # Revalidation is side-effect-free for a legitimate action.
    RuntimeCompleteAction.__post_init__(action)
    assert action._origin_commitment is origin
    assert first.decode_option_indices(action) == (0,)
    with pytest.raises(RuntimeActionError, match="stale"):
        second.decode_option_indices(action)

    object.__setattr__(action, "envelope", second)
    with pytest.raises(RuntimeActionError, match="stale"):
        RuntimeCompleteAction.__post_init__(action)
    with pytest.raises(RuntimeActionError, match="stale"):
        second.decode_option_indices(action)

    pristine = first.complete_action((local_id,))
    object.__delattr__(pristine, "_origin_commitment")
    with pytest.raises(RuntimeActionError, match="provenance"):
        first.decode_option_indices(pristine)

    forged = first.complete_action((local_id,))
    object.__setattr__(forged, "_origin_commitment", object())
    with pytest.raises(RuntimeActionError, match="provenance"):
        first.decode_option_indices(forged)

    with pytest.raises(RuntimeActionError, match="issued"):
        RuntimeCompleteAction(first, (local_id,), (0,))


def test_scored_semantic_action_rechecks_unrewritable_runtime_provenance() -> None:
    """Fails if wrapping a scored result replays action construction or accepts stale provenance."""
    envelope = _envelope()
    local_id = envelope._state.legal_actions[0].local_action_id
    action = envelope.complete_action((local_id,))
    semantic = semantic_runtime_complete_action_from_runtime_action_v2(envelope, action)
    scored = RuntimeScoredCompleteActionV2(
        semantic_action=semantic,
        representative_action=action,
        log_probability=0.0,
    )
    assert scored.action is action
    object.__setattr__(action, "option_indices", (1,))
    with pytest.raises(RuntimeActionError, match="provenance"):
        RuntimeScoredCompleteActionV2(
            semantic_action=semantic,
            representative_action=action,
            log_probability=0.0,
        )


def test_semantic_probability_is_normalized_once_per_alias_class_including_repeated_class() -> None:
    """Fails if probability is exposed over physical aliases rather than semantic A,A / A,B events."""
    observation = _observation()
    observation["current"]["players"][0]["hand"] = [  # type: ignore[index]
        _card(101, 10, 0), _card(101, 11, 0), _card(102, 12, 0),
    ]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["select"] = {  # type: ignore[index]
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [
            {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            {"type": 3, "area": 2, "index": 2, "playerIndex": 0},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    envelope = _envelope(observation)

    class RepeatedClassPolicy:
        def logits(self, _model_input, step_input):
            if len(step_input.semantic_prefix) == 0:
                # B cannot reach min_count under nondecreasing unordered semantics.
                assert len(step_input.allowed_semantic_classes) == 1
                return SpecialistStepLogitsV1(semantic_logits=(0.0,), stop_logit=None)
            assert len(step_input.allowed_semantic_classes) == 2
            return SpecialistStepLogitsV1(
                semantic_logits=(math.log(0.6), math.log(0.4)), stop_logit=None
            )

    policy = RepeatedClassPolicy()
    physical = enumerate_runtime_complete_actions_v2(envelope, limit=3)
    semantic_by_physical = {
        semantic_runtime_complete_action_from_runtime_action_v2(envelope, action): action
        for action in physical
    }
    assert len(physical) == 3  # A1,A2; A1,B; A2,B
    assert len(semantic_by_physical) == 2  # semantic A,A and A,B
    semantic_log_probabilities = [
        runtime_semantic_complete_action_log_probability_v2(envelope, semantic, policy=policy)
        for semantic in semantic_by_physical
    ]
    assert sorted(math.exp(value) for value in semantic_log_probabilities) == pytest.approx([0.4, 0.6])
    assert math.fsum(math.exp(value) for value in semantic_log_probabilities) == pytest.approx(1.0)
    for action in physical:
        with pytest.raises(RuntimeActionError, match="semantic-complete-action"):
            runtime_complete_action_log_probability_v2(envelope, action, policy=policy)


def test_sampler_fails_closed_before_rng_when_finite_semantic_mass_underflows() -> None:
    """Fails if a finite legal class silently becomes unsampleable at float precision."""
    observation = _observation()
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    envelope = _envelope(observation)

    class ExtremeFinitePolicy:
        def logits(self, _model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            return SpecialistStepLogitsV1(
                semantic_logits=(-10_000.0, 0.0), stop_logit=None
            )

    class NeverDraw:
        def random(self) -> float:  # pragma: no cover - failure path
            raise AssertionError("underflow must reject before consuming RNG")

    with pytest.raises(RuntimePolicyError, match="nonrepresentable"):
        sample_runtime_action_v2(
            envelope, policy=ExtremeFinitePolicy(), rng=NeverDraw()
        )


def test_sampler_retains_representable_positive_subnormal_semantic_mass() -> None:
    """Fails if the underflow guard discards a legal mass float can still represent."""
    observation = _observation()
    observation["select"]["minCount"] = 1  # type: ignore[index]
    observation["select"]["maxCount"] = 1  # type: ignore[index]
    envelope = _envelope(observation)

    class NearSubnormalPolicy:
        def logits(self, _model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            return SpecialistStepLogitsV1(
                semantic_logits=(-744.4, 0.0), stop_logit=None
            )

    class FirstDraw:
        def random(self) -> float:
            return 0.0

    action = sample_runtime_action_v2(
        envelope, policy=NearSubnormalPolicy(), rng=FirstDraw()
    )
    assert envelope.decode_option_indices(action) == (0,)
    assert runtime_semantic_complete_action_log_probability_v2(
        envelope,
        semantic_runtime_complete_action_from_runtime_action_v2(envelope, action),
        policy=NearSubnormalPolicy(),
    ) < -744.0


def test_sampler_fails_closed_before_rng_when_stop_mass_underflows() -> None:
    """Fails if the dedicated STOP token can silently lose finite legal support."""
    envelope = _envelope()

    class ExtremeStopPolicy:
        def logits(self, _model_input, step_input):
            assert len(step_input.allowed_semantic_classes) == 2
            assert step_input.stop_available is True
            return SpecialistStepLogitsV1(
                semantic_logits=(0.0, 0.0), stop_logit=-10_000.0
            )

    class NeverDraw:
        def random(self) -> float:  # pragma: no cover - failure path
            raise AssertionError("underflow must reject before consuming RNG")

    with pytest.raises(RuntimePolicyError, match="nonrepresentable"):
        sample_runtime_action_v2(
            envelope, policy=ExtremeStopPolicy(), rng=NeverDraw()
        )


@pytest.mark.parametrize("draw", (True, float("nan"), -0.01, 1.0))
def test_sampler_keeps_injected_rng_domain_validation(draw: object) -> None:
    """Fails if the underflow guard bypasses the existing injected-RNG contract."""
    envelope = _envelope()

    class OrdinaryPolicy:
        def logits(self, _model_input, step_input):
            return SpecialistStepLogitsV1(
                semantic_logits=(0.0,) * len(step_input.allowed_semantic_classes),
                stop_logit=0.0 if step_input.stop_available else None,
            )

    class InvalidDraw:
        def random(self) -> object:
            return draw

    with pytest.raises(RuntimePolicyError, match="rng.random"):
        sample_runtime_action_v2(envelope, policy=OrdinaryPolicy(), rng=InvalidDraw())
