"""TDD coverage for one-CABT-decision-equals-one-transition actor trajectories.

Fixtures are built through the real extraction pipeline
(``extract_specialist_model_input_v1``/``build_specialist_step_input_v1``),
the same one ``test_actor_visible_features_v1.py`` and
``test_neural_adapter_v1.py`` use, so these tests exercise genuine
``SpecialistModelInputV1``/``SpecialistStepInputV1`` legality rather than a
hand-rolled shortcut.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SemanticActionV1,
    SpecialistStepInputV1,
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error, canonical_json_bytes_v2
from mage_ptcg.meta_specialist.trajectory_v1 import (
    ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1,
    ActorTrajectoryTransitionV1,
    TrajectoryPrefixStepV1,
    TrajectoryV1Error,
    build_actor_trajectory_transition_v1,
    canonical_actor_trajectory_transition_bytes_v1,
    masked_behavior_log_probability_v1,
    parse_actor_trajectory_transition_bytes_v1,
    validate_actor_trajectory_transition_payload_v1,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import reject_forbidden_private_fields_v2


_SUBJECT_VERSION = "a" * 64
_OPPONENT_VERSION = "b" * 64


def _card(card_id: int, serial: int, owner: int) -> dict[str, int]:
    return {"id": card_id, "serial": serial, "playerIndex": owner}


def _pokemon(card_id: int, serial: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": serial, "hp": 100, "maxHp": 120,
        "appearThisTurn": False, "energies": [1, 1, 3],
        "energyCards": [], "tools": [], "preEvolution": [],
    }


def _player(hand: object, *, active: list[object] | None = None) -> dict[str, object]:
    return {
        "active": [] if active is None else active, "asleep": False, "bench": [],
        "benchMax": 5, "burned": False, "confused": False, "deckCount": 53,
        "discard": [], "hand": hand, "handCount": len(hand) if isinstance(hand, list) else 0,
        "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }


def _observation(*, min_count: int, max_count: int, option_count: int = 3) -> dict[str, object]:
    hand = [_card(100 + index, 1000 + index, 0) for index in range(option_count)]
    options = [{"type": 3, "area": 2, "index": index, "playerIndex": 0} for index in range(option_count)]
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
            "maxCount": max_count, "minCount": min_count, "option": options,
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _extracted(*, min_count: int, max_count: int, option_count: int = 3):
    observation = _observation(min_count=min_count, max_count=max_count, option_count=option_count)
    state = build_actor_visible_decision_state_v2(observation)
    return extract_specialist_model_input_v1(state, make_test_card_vocabulary_v1(range(1, 1000)))


def _semantic_step(
    extracted, prefix_local_ids: tuple[str, ...], *, choose_index: int, log_probability: float,
) -> tuple[TrajectoryPrefixStepV1, str]:
    step_input = build_specialist_step_input_v1(extracted, prefix_local_ids)
    chosen_row = step_input.allowed_semantic_classes[choose_index].semantic_row
    chosen_local_id = next(
        local_id
        for local_id, index in extracted.local_action_id_to_candidate_row_index.items()
        if extracted.model_input.candidate_rows[index] == chosen_row and local_id not in prefix_local_ids
    )
    step = TrajectoryPrefixStepV1(
        step_input=step_input, forced_stop=False, chosen_is_stop=False,
        chosen_semantic_action=chosen_row, behavior_log_probability=log_probability,
    )
    return step, chosen_local_id


def _stop_step(extracted, prefix_local_ids: tuple[str, ...], *, log_probability: float) -> TrajectoryPrefixStepV1:
    step_input = build_specialist_step_input_v1(extracted, prefix_local_ids)
    forced = not step_input.allowed_semantic_classes
    return TrajectoryPrefixStepV1(
        step_input=step_input, forced_stop=forced, chosen_is_stop=True,
        chosen_semantic_action=None,
        behavior_log_probability=(0.0 if forced else log_probability),
    )


def _immediate_stop_transition(*, terminal: bool = False, reward: float = 0.0) -> ActorTrajectoryTransitionV1:
    """A one-step transition: min_count=0, the actor immediately chooses STOP."""
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.3)
    return build_actor_trajectory_transition_v1(
        model_input=extracted.model_input,
        order_semantics=stop.step_input.order_semantics,
        prefix_steps=(stop,),
        value=0.1, reward=reward, discount=(0.0 if terminal else 0.99), terminal=terminal,
        subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="pool-member-1",
        opponent_version=_OPPONENT_VERSION, pool_epoch=3, policy_lag=1,
    )


def _two_choice_forced_stop_transition() -> tuple[ActorTrajectoryTransitionV1, tuple]:
    """min_count=1, max_count=2, option_count=3: choose 2 tokens, then a FORCED STOP.

    The third prefix step reaches ``len(prefix) == max_count``, so
    ``allowed_semantic_classes`` is legitimately empty and STOP is the model-free
    sole continuation -- reproducing ``EvaluatedSpecialistStepV1.forced_stop``.
    """
    extracted = _extracted(min_count=1, max_count=2)
    step0, id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.6)
    step1, id1 = _semantic_step(extracted, (id0,), choose_index=0, log_probability=-0.4)
    step2 = _stop_step(extracted, (id0, id1), log_probability=-99.0)  # forced; must resolve to 0.0
    assert step2.forced_stop is True
    transition = build_actor_trajectory_transition_v1(
        model_input=extracted.model_input,
        order_semantics=step0.step_input.order_semantics,
        prefix_steps=(step0, step1, step2),
        value=0.4, reward=0.0, discount=0.97, terminal=False,
        subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="pool-member-7",
        opponent_version=_OPPONENT_VERSION, pool_epoch=5, policy_lag=2,
    )
    return transition, (step0.chosen_semantic_action, step1.chosen_semantic_action)


# --- Structural: one transition, one reward, one discount -----------------


def test_prefix_step_type_has_no_reward_or_discount_field() -> None:
    """The whole point of the split: a prefix step cannot carry its own reward/discount."""
    field_names = {field.name for field in dataclasses.fields(TrajectoryPrefixStepV1)}
    assert "reward" not in field_names
    assert "discount" not in field_names


def test_transition_type_has_exactly_one_reward_and_discount_field() -> None:
    field_names = [field.name for field in dataclasses.fields(ActorTrajectoryTransitionV1)]
    assert field_names.count("reward") == 1
    assert field_names.count("discount") == 1


def test_multi_select_transition_groups_three_decode_steps_under_one_reward() -> None:
    transition, (row0, row1) = _two_choice_forced_stop_transition()

    assert len(transition.prefix_steps) == 3
    assert transition.reward == 0.0
    assert transition.discount == 0.97
    assert transition.chosen_semantic_complete_action == (row0, row1)
    # Each nested prefix step still has no reward/discount attribute of its own.
    for step in transition.prefix_steps:
        assert not hasattr(step, "reward")
        assert not hasattr(step, "discount")


def test_forced_stop_step_must_carry_exactly_zero_log_probability() -> None:
    extracted = _extracted(min_count=1, max_count=2)
    step0, id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.6)
    step1, id1 = _semantic_step(extracted, (id0,), choose_index=0, log_probability=-0.4)
    step_input = build_specialist_step_input_v1(extracted, (id0, id1))
    assert not step_input.allowed_semantic_classes and step_input.stop_available
    with pytest.raises(TrajectoryV1Error, match="forced STOP"):
        TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=True, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=-0.01,
        )


def test_forced_stop_requires_an_empty_domain() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    step_input = build_specialist_step_input_v1(extracted, ())
    assert step_input.allowed_semantic_classes  # not actually forced
    with pytest.raises(TrajectoryV1Error, match="forced_stop requires"):
        TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=True, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=0.0,
        )


def test_stop_illegal_when_min_count_not_reached() -> None:
    extracted = _extracted(min_count=1, max_count=2)
    step_input = build_specialist_step_input_v1(extracted, ())
    assert step_input.stop_available is False
    with pytest.raises(TrajectoryV1Error, match="STOP is illegal"):
        TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=False, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=-0.2,
        )


def test_non_stop_step_outside_legal_domain_rejected() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    step0, id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.5)
    later_step_input = build_specialist_step_input_v1(extracted, (id0,))
    # step0's chosen row has alias_count 1 and is now exhausted: it must not be
    # legal again at the next step's domain.
    assert not any(
        step0.chosen_semantic_action == item.semantic_row
        for item in later_step_input.allowed_semantic_classes
    )
    with pytest.raises(TrajectoryV1Error, match="outside this step's legal domain"):
        TrajectoryPrefixStepV1(
            step_input=later_step_input, forced_stop=False, chosen_is_stop=False,
            chosen_semantic_action=step0.chosen_semantic_action, behavior_log_probability=-0.5,
        )


def test_only_the_final_prefix_step_may_choose_stop() -> None:
    extracted = _extracted(min_count=1, max_count=2)
    step0, id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.6)
    early_stop = _stop_step(extracted, (id0,), log_probability=-0.2)
    step1, id1 = _semantic_step(extracted, (id0,), choose_index=0, log_probability=-0.4)
    with pytest.raises(TrajectoryV1Error, match="only the final prefix step"):
        ActorTrajectoryTransitionV1(
            schema_version=ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1,
            model_input=extracted.model_input, order_semantics=step0.step_input.order_semantics,
            prefix_steps=(step0, early_stop, step1),
            chosen_semantic_complete_action=(step0.chosen_semantic_action,),
            behavior_log_probability=masked_behavior_log_probability_v1((step0, early_stop, step1)),
            value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


def test_final_prefix_step_must_choose_stop() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    step0, _id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.6)
    with pytest.raises(TrajectoryV1Error, match="final prefix step must choose STOP"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=step0.step_input.order_semantics,
            prefix_steps=(step0,),
            value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


def test_prefix_chaining_mismatch_rejected() -> None:
    extracted = _extracted(min_count=1, max_count=2)
    step0, id0 = _semantic_step(extracted, (), choose_index=0, log_probability=-0.6)
    # step1 built from the empty prefix, but placed second -- does not chain.
    bogus_step1 = TrajectoryPrefixStepV1(
        step_input=build_specialist_step_input_v1(extracted, ()),
        forced_stop=False, chosen_is_stop=False,
        chosen_semantic_action=step0.step_input.allowed_semantic_classes[1].semantic_row,
        behavior_log_probability=-0.4,
    )
    stop = _stop_step(extracted, (id0,), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error, match="does not chain"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=step0.step_input.order_semantics,
            prefix_steps=(step0, bogus_step1, stop),
            value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


# --- Behavior log-probability = sum of masked per-prefix log-probabilities -


def test_masked_behavior_log_probability_v1_is_the_ordered_sum() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    manual_sum = math.fsum(
        sorted(step.behavior_log_probability for step in transition.prefix_steps)
    )
    assert masked_behavior_log_probability_v1(transition.prefix_steps) == manual_sum
    assert transition.behavior_log_probability == manual_sum
    # -0.6 + -0.4 + 0.0 (forced) == -1.0
    assert transition.behavior_log_probability == pytest.approx(-1.0)


def test_builder_derives_log_probability_so_callers_cannot_drift_it() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.12345)
    transition = build_actor_trajectory_transition_v1(
        model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
        prefix_steps=(stop,),
        value=0.0, reward=0.0, discount=0.99, terminal=False,
        subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
        opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
    )
    assert transition.behavior_log_probability == -0.12345


def test_stored_log_probability_inconsistent_with_steps_is_rejected() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.5)
    with pytest.raises(TrajectoryV1Error, match="sum of masked per-prefix"):
        ActorTrajectoryTransitionV1(
            schema_version=ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1,
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,), chosen_semantic_complete_action=(),
            behavior_log_probability=-0.4999,  # wrong: should be -0.5
            value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


# --- Privacy -----------------------------------------------------------


def test_to_dict_contains_no_forbidden_private_field() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    reject_forbidden_private_fields_v2(payload)  # must not raise


@pytest.mark.parametrize(
    "forbidden_key",
    ["local_action_id", "action_key_digest", "action_key_payload", "actor_binding", "serial", "index"],
)
def test_reject_forbidden_private_fields_v2_catches_nested_private_keys(forbidden_key: str) -> None:
    tainted = {"prefix_steps": [{"step_input": {forbidden_key: "leak"}}]}
    with pytest.raises(LocalDatasetV2Error):
        reject_forbidden_private_fields_v2(tainted)


def test_payload_with_extra_forbidden_top_level_key_fails_closed_shape() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    tainted = dict(payload)
    tainted["local_action_id"] = "0" * 64
    with pytest.raises(TrajectoryV1Error, match="closed field set"):
        validate_actor_trajectory_transition_payload_v1(tainted)


# --- Non-finite rejection -----------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_reward_rejected(bad_value: float) -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,),
            value=0.0, reward=bad_value, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_prefix_log_probability_rejected(bad_value: float) -> None:
    extracted = _extracted(min_count=0, max_count=2)
    step_input = build_specialist_step_input_v1(extracted, ())
    with pytest.raises(TrajectoryV1Error):
        TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=False, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=bad_value,
        )


def test_positive_log_probability_rejected() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    step_input = build_specialist_step_input_v1(extracted, ())
    with pytest.raises(TrajectoryV1Error, match="cannot be positive"):
        TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=False, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=0.5,
        )


# --- Terminal <=> discount == 0.0 ----------------------------------------


def test_terminal_transition_requires_zero_discount() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error, match="discount exactly 0.0"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,),
            value=0.0, reward=1.0, discount=0.5, terminal=True,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


def test_non_terminal_transition_cannot_carry_zero_discount() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error, match="cannot carry discount 0.0"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,),
            value=0.0, reward=0.0, discount=0.0, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


def test_terminal_transition_with_zero_discount_is_accepted() -> None:
    transition = _immediate_stop_transition(terminal=True, reward=1.0)
    assert transition.terminal is True
    assert transition.discount == 0.0
    assert transition.reward == 1.0


# --- Value/reward bounds and identity field validation --------------------


@pytest.mark.parametrize("field", ["value", "reward"])
def test_scalar_out_of_bounds_rejected(field: str) -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    kwargs = {"value": 0.0, "reward": 0.0}
    kwargs[field] = 1.5
    with pytest.raises(TrajectoryV1Error, match=r"must be in \[-1.0, 1.0\]"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,), discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
            **kwargs,
        )


def test_pool_epoch_and_policy_lag_must_be_nonnegative_ints() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error, match="pool_epoch"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,), value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version=_SUBJECT_VERSION, opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=-1, policy_lag=0,
        )


def test_subject_behavior_version_must_be_hex64() -> None:
    extracted = _extracted(min_count=0, max_count=2)
    stop = _stop_step(extracted, (), log_probability=-0.2)
    with pytest.raises(TrajectoryV1Error, match="subject_behavior_version"):
        build_actor_trajectory_transition_v1(
            model_input=extracted.model_input, order_semantics=stop.step_input.order_semantics,
            prefix_steps=(stop,), value=0.0, reward=0.0, discount=0.99, terminal=False,
            subject_behavior_version="not-a-hash", opponent_instance_id="x",
            opponent_version=_OPPONENT_VERSION, pool_epoch=0, policy_lag=0,
        )


# --- Canonical serialization, content hash, closed-set read validation ----


def test_to_dict_content_hash_is_recomputed_deterministically() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    again = transition.to_dict()
    assert payload == again
    assert len(payload["content_hash"]) == 64


def test_tampering_with_content_hash_is_detected_on_read() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    tampered = dict(payload)
    tampered["content_hash"] = "0" * 64
    # content_hash is recomputed from the rest of the payload and compared,
    # never trusted: a stored hash that does not match its own content fails
    # closed rather than being silently repaired.
    with pytest.raises(TrajectoryV1Error, match="not canonical"):
        validate_actor_trajectory_transition_payload_v1(tampered)


def test_tampering_with_reward_after_hashing_is_detected() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    payload = transition.to_dict()
    tampered = dict(payload)
    tampered["reward"] = 0.9999  # still in-bounds, but the stored hash won't match
    with pytest.raises(TrajectoryV1Error, match="not canonical"):
        validate_actor_trajectory_transition_payload_v1(tampered)


def test_canonical_bytes_round_trip() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    raw = canonical_actor_trajectory_transition_bytes_v1(transition)
    parsed = parse_actor_trajectory_transition_bytes_v1(raw)
    assert parsed == transition.to_dict()
    assert canonical_json_bytes_v2(parsed) == raw


def test_non_canonical_bytes_are_rejected() -> None:
    transition, _rows = _two_choice_forced_stop_transition()
    raw = canonical_actor_trajectory_transition_bytes_v1(transition)
    # Trailing whitespace is caught by the shared canonical-JSON parser itself
    # (LocalDatasetV2Error) before this module's own checks ever run; both are
    # ValueError subclasses and either is an acceptable fail-closed outcome.
    with pytest.raises(ValueError):
        parse_actor_trajectory_transition_bytes_v1(raw + b" ")


def test_validate_payload_rebuilds_full_multi_select_structure() -> None:
    transition, (row0, row1) = _two_choice_forced_stop_transition()
    payload = validate_actor_trajectory_transition_payload_v1(transition.to_dict())
    assert len(payload["prefix_steps"]) == 3
    assert payload["chosen_semantic_complete_action"] == [row0.to_dict(), row1.to_dict()]
    assert payload["prefix_steps"][2]["forced_stop"] is True
    assert payload["prefix_steps"][2]["behavior_log_probability"] == 0.0
