"""C1 privacy, determinism, lifecycle, and legality regression tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from agents.rule_agent import choose_rule_indices
import agents.rule_agent_v1 as rule_v1_module
from agents.rule_agent_v1 import RuleAgentV1
from mage_ptcg.belief import CardCounts
from mage_ptcg.decision_state import (
    ActorInformationView,
    DecisionStateError,
    _json_scalar,
    build_action_key,
    build_decision_state,
)
from mage_ptcg.observability.cabt_trace import normalize_decision_record
from mage_ptcg.public_belief import PublicBelief, PublicBeliefPrior
from main import make_rule_agent_v1
from scripts.test_sim import _make_agent


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _card(card_id: int, *, serial: int = 0) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": serial,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _player(
    *,
    hand: list[dict[str, Any]] | None = None,
    hand_count: int | None = None,
    deck_count: int = 53,
    prize: list[object] | None = None,
    active: list[object] | None = None,
    bench: list[object] | None = None,
    discard: list[object] | None = None,
) -> dict[str, Any]:
    actual_hand = hand if hand is not None else [_card(100)]
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": bench if bench is not None else [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": deck_count,
        "discard": discard if discard is not None else [],
        "hand": actual_hand,
        "handCount": len(actual_hand) if hand_count is None else hand_count,
        "paralyzed": False,
        "poisoned": False,
        "prize": prize if prize is not None else [object() for _ in range(6)],
    }


def _observation(
    *,
    self_player: dict[str, Any] | None = None,
    opponent: dict[str, Any] | None = None,
    options: list[object] | None = None,
    minimum: int = 1,
    maximum: int = 1,
    select_type: int = 0,
    context: int = 0,
    logs: list[object] | None = None,
    opaque_token: object = "PRIVATE_ENGINE_TOKEN",
) -> dict[str, Any]:
    own = self_player if self_player is not None else _player()
    other = opponent if opponent is not None else _player(hand=[_card(700)])
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [own, other],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "logs": logs if logs is not None else ["PRIVATE_LOG"],
        "remainingOverageTime": 600,
        "search_begin_input": opaque_token,
        "select": {
            "context": context,
            "maxCount": maximum,
            "minCount": minimum,
            "option": options if options is not None else [{"type": 14}],
            "type": select_type,
        },
        "step": 7,
    }


def _paired_hidden_observations() -> tuple[dict[str, Any], dict[str, Any]]:
    first = _observation(
        opponent=_player(
            hand=[_card(701), _card(702)],
            deck_count=52,
            prize=[{"id": 801 + index} for index in range(6)],
        ),
        logs=[{"private": "FIRST_LOG"}],
        opaque_token="FIRST_ENGINE_SECRET",
    )
    second = _observation(
        opponent=_player(
            hand=[_card(711), _card(712)],
            deck_count=52,
            prize=[{"id": 901 + index} for index in range(6)],
        ),
        logs=[{"private": "SECOND_LOG"}],
        opaque_token="SECOND_ENGINE_SECRET",
    )
    return first, second


class _UnreadablePrize(list[object]):
    def __iter__(self):  # type: ignore[no-untyped-def]
        raise AssertionError("prize contents must not be traversed")

    def __getitem__(self, index):  # type: ignore[no-untyped-def]
        raise AssertionError("prize contents must not be indexed")


class _GuardedOpponent(dict[str, Any]):
    def get(self, key: str, default: object = None) -> object:
        if key == "hand":
            raise AssertionError("opponent hand contents must not be read")
        return super().get(key, default)


def test_actor_view_does_not_read_or_serialize_hidden_opponent_zones() -> None:
    opponent = _GuardedOpponent(
        _player(
            hand=[_card(777)],
            prize=_UnreadablePrize([{"id": 888}] * 6),
        )
    )
    observation = _observation(opponent=opponent)

    state = build_decision_state(observation)
    trace = normalize_decision_record(
        observation,
        [0],
        seat=0,
        episode_index=0,
        decision_index=0,
        seat_decision_index=0,
    )
    persisted = {"state": state.to_trace_payload(), "trace": trace}

    def scalar_values(value: object) -> list[object]:
        if isinstance(value, dict):
            return [item for child in value.values() for item in scalar_values(child)]
        if isinstance(value, list):
            return [item for child in value for item in scalar_values(child)]
        return [value]

    assert 777 not in scalar_values(persisted)
    assert 888 not in scalar_values(persisted)
    assert state.actor_view.public_state["opponent"]["hand_count"] == 1
    assert state.actor_view.public_state["opponent"]["prize_count"] == 6


def test_hidden_state_changes_leave_actor_visible_decision_state_identical() -> None:
    first, second = _paired_hidden_observations()

    first_state = build_decision_state(first)
    second_state = build_decision_state(second)

    assert first_state == second_state
    assert first_state.digest == second_state.digest
    assert normalize_decision_record(
        first, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    ) == normalize_decision_record(
        second, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )


def test_action_key_is_deterministic_and_option_order_independent() -> None:
    attack = {"type": 13, "attackId": 4}
    end = {"type": 14}

    direct = build_action_key(selection_type=0, context=0, option=attack)
    first = build_decision_state(_observation(options=[attack, end]))
    shuffled = build_decision_state(
        _observation(options=[end, dict(reversed(list(attack.items())))])
    )

    assert direct == first.legal_actions[0].action_key
    assert first.actor_view.action_snapshot == shuffled.actor_view.action_snapshot
    assert first.metadata.action_set_digest == shuffled.metadata.action_set_digest
    assert first.digest == shuffled.digest


def test_action_key_distinguishes_semantically_different_actions() -> None:
    first = build_action_key(
        selection_type=0, context=0, option={"type": 13, "attackId": 1}
    )
    second = build_action_key(
        selection_type=0, context=0, option={"type": 13, "attackId": 2}
    )

    assert first.digest != second.digest
    assert first != second


def test_action_key_keeps_distinct_own_hand_indices_for_duplicate_card_ids() -> None:
    observation = _observation(
        self_player=_player(hand=[_card(100), _card(100)], hand_count=2),
        options=[{"type": 7, "index": 0}, {"type": 7, "index": 1}],
    )

    state = build_decision_state(observation)
    first_key, second_key = (action.action_key for action in state.legal_actions)

    assert first_key.card_id == second_key.card_id == 100
    assert first_key != second_key
    assert first_key.digest != second_key.digest
    assert dict(first_key.canonical_payload)["index"] == 0
    assert dict(second_key.canonical_payload)["index"] == 1


def test_json_scalar_rejects_structured_named_values_but_keeps_enum_like_scalars() -> None:
    class NamedMapping(dict[str, object]):
        name = "mapping_name"

    class NamedSequence(list[object]):
        name = "sequence_name"

    class EnumLike:
        name = "option.attack"

    assert _json_scalar(NamedMapping()) is None
    assert _json_scalar(NamedSequence()) is None
    assert _json_scalar("plain-text") == "plain-text"
    assert _json_scalar(b"bytes-are-not-json-text") is None
    assert _json_scalar(EnumLike()) == "ATTACK"


def _belief_observation() -> dict[str, Any]:
    opponent = _player(
        hand=[_card(701)],
        deck_count=52,
        active=[_card(1)],
    )
    return _observation(opponent=opponent)


def test_public_belief_normalizes_permitted_prior_mass() -> None:
    belief = PublicBelief(
        (
            PublicBeliefPrior("alpha", CardCounts({1: 4, 2: 56}), 1.0),
            PublicBeliefPrior("beta", CardCounts({1: 1, 3: 59}), 3.0),
        )
    )

    summary = belief.update(build_decision_state(_belief_observation()).actor_view)

    assert not summary.degraded
    assert [mass.probability for mass in summary.masses] == pytest.approx([0.25, 0.75])
    assert sum(mass.probability for mass in summary.masses) == pytest.approx(1.0)


def test_public_belief_assigns_zero_mass_to_impossible_hypothesis() -> None:
    belief = PublicBelief(
        (
            PublicBeliefPrior("possible", CardCounts({1: 1, 2: 59}), 1.0),
            PublicBeliefPrior("impossible", CardCounts({3: 60}), 9.0),
        )
    )

    summary = belief.update(build_decision_state(_belief_observation()).actor_view)

    assert [(mass.hypothesis_id, mass.probability, mass.possible) for mass in summary.masses] == [
        ("possible", 1.0, True),
        ("impossible", 0.0, False),
    ]


def test_public_belief_has_explicit_fallback_for_empty_or_malformed_input() -> None:
    belief = PublicBelief()
    malformed = {"select": {"option": [], "minCount": 0, "maxCount": 0}}

    malformed_result = belief.update_from_observation(malformed)
    valid_result = belief.update_from_observation(_observation())

    assert malformed_result.decision_state is None
    assert malformed_result.summary.degraded
    assert malformed_result.summary.fallback_reason == "malformed_or_partial_observation"
    assert valid_result.summary.degraded
    assert valid_result.summary.fallback_reason == "no_permitted_prior"
    assert valid_result.summary.masses[0].hypothesis_id == "unknown"
    with pytest.raises(TypeError, match="ActorInformationView"):
        belief.update({})  # type: ignore[arg-type]


def test_malformed_observations_advance_belief_updates_without_state_leak() -> None:
    belief = PublicBelief()
    malformed = {"private": "456789", "select": {"option": [], "minCount": 0, "maxCount": 0}}

    first = belief.update_from_observation(malformed)
    second = belief.update_from_observation(malformed)

    assert first.decision_state is None
    assert second.decision_state is None
    assert first.summary.update_count == 1
    assert second.summary.update_count == 2
    assert belief.public_history == ()
    assert [mass.hypothesis_id for mass in second.summary.masses] == ["unknown"]
    assert [mass.probability for mass in second.summary.masses] == [1.0]
    assert "456789" not in second.summary.to_canonical_json()


def test_public_history_digest_excludes_own_private_hand() -> None:
    first = build_decision_state(
        _observation(self_player=_player(hand=[_card(101)], hand_count=1))
    ).actor_view
    second = build_decision_state(
        _observation(self_player=_player(hand=[_card(102)], hand_count=1))
    ).actor_view
    belief = PublicBelief()

    assert first.digest != second.digest
    assert first.public_state_digest == second.public_state_digest
    belief.update(first)
    belief.update(second)
    assert belief.public_history[0] == belief.public_history[1]


def test_rule_v1_resets_between_consecutive_games() -> None:
    first, second = _paired_hidden_observations()
    agent = RuleAgentV1()

    assert agent.choose(first) == [0]
    first_digest = agent.last_state.digest if agent.last_state is not None else None
    assert agent.last_summary is not None and agent.last_summary.update_count == 1
    assert agent.choose({"select": None}) is None
    assert agent.last_state is None
    assert agent.public_belief.public_history == ()
    assert agent.choose(second) == [0]

    assert agent.last_state is not None and agent.last_state.digest == first_digest
    assert agent.last_summary is not None and agent.last_summary.update_count == 1


def test_reused_and_independent_agents_do_not_leak_state() -> None:
    first, second = _paired_hidden_observations()
    reused = RuleAgentV1()
    independent = RuleAgentV1()

    reused.choose(first)
    reused.choose({"select": None})
    reused_action = reused.choose(second)
    independent_action = independent.choose(second)

    assert reused_action == independent_action
    assert reused.last_state == independent.last_state
    assert reused.last_summary == independent.last_summary
    assert reused.public_belief is not independent.public_belief


def test_alternating_factory_seeds_are_deterministic_and_isolated() -> None:
    observation = _observation(
        options=[{"type": 14}, {"type": 7, "index": 0}],
    )
    agents = [make_rule_agent_v1(deck=[1] * 60, seed=seed) for seed in (1, 999, 1)]

    assert [agent(observation) for agent in agents] == [[1], [1], [1]]
    loops = [agent.decision_loop for agent in agents]  # type: ignore[attr-defined]
    assert len({id(loop.public_belief) for loop in loops}) == 3
    assert loops[0].last_state == loops[1].last_state == loops[2].last_state


def test_trace_payload_contains_no_private_card_or_engine_fields() -> None:
    first, _ = _paired_hidden_observations()
    first["current"]["players"][0]["hand"] = [_card(456789)]
    first["select"]["option"] = [{"type": 7, "index": 0}]
    state = build_decision_state(first)

    serialized = json.dumps(state.to_trace_payload(), sort_keys=True)

    assert "own_private_state" not in serialized
    assert "hand_card_ids" not in serialized
    assert "FIRST_ENGINE_SECRET" not in serialized
    assert "FIRST_LOG" not in serialized
    assert "456789" not in serialized
    assert "search_begin_input" not in serialized
    assert "logs" not in serialized


def test_actor_view_and_decision_state_repr_redact_private_state() -> None:
    observation = _observation(
        self_player=_player(hand=[_card(456789)], hand_count=1),
        options=[{"type": 7, "index": 0}],
    )
    state = build_decision_state(observation)
    rendered = "\n".join(
        (
            repr(state),
            repr(state.actor_view),
            repr(state.metadata),
            repr(state.legal_actions),
        )
    )

    assert "456789" not in rendered
    assert "own_private_state_json" not in rendered
    assert state.actor_view.own_private_state_json not in rendered
    assert state.metadata.action_set_digest not in rendered


def test_rule_v1_rechecks_legality_and_uses_first_legal_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation(options=[{"type": 14}, {"type": 7}], minimum=2, maximum=2)
    monkeypatch.setattr(rule_v1_module, "choose_rule_indices", lambda _obs: [99])

    agent = RuleAgentV1()

    assert agent.choose(observation) == [0, 1]
    assert agent.last_source == "deterministic_first_legal"


def test_rule_v1_clamps_malformed_bounds_for_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation(
        options=[{"type": 14}, {"type": 7, "index": 0}], minimum=1, maximum=99
    )
    monkeypatch.setattr(rule_v1_module, "choose_rule_indices", lambda _obs: [99])

    agent = RuleAgentV1()
    selection = agent.choose(observation)

    assert selection == [0]
    assert rule_v1_module._is_legal_selection(observation, selection)
    assert agent.last_source == "deterministic_first_legal"


@pytest.mark.parametrize(
    "observation",
    [
        _observation(options=[{"type": 14}, {"type": 13}, {"type": 7, "index": 0}]),
        _observation(options=[{"type": 3}, {"type": 3}], select_type=4, context=7),
        _observation(options=[{"type": 3}, {"type": 3}], minimum=0, maximum=2, select_type=4),
    ],
)
def test_rule_v1_degraded_path_is_exact_rule_v0_fallback(observation: dict[str, Any]) -> None:
    agent = RuleAgentV1()

    assert agent.choose(observation) == choose_rule_indices(observation)
    assert agent.last_source == "rule_v0_belief_fallback"


def test_rule_v1_final_rule_v0_choice_uses_shared_actor_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = choose_rule_indices
    seen: list[object] = []

    def spy(observation: object) -> list[int] | None:
        seen.append(observation)
        return original(observation)

    first, _ = _paired_hidden_observations()
    monkeypatch.setattr(rule_v1_module, "choose_rule_indices", spy)

    assert RuleAgentV1().choose(first) == [0]
    assert len(seen) == 2
    assert seen[0] is first
    assert set(seen[1]) == {"current", "select"}  # type: ignore[arg-type]
    assert "FIRST_ENGINE_SECRET" not in json.dumps(seen[1], sort_keys=True)
    assert "FIRST_LOG" not in json.dumps(seen[1], sort_keys=True)


def test_rule_v1_timeout_is_deterministic_rule_v0_fallback() -> None:
    readings = iter((10.0, 10.100))
    observation = _observation(options=[{"type": 14}, {"type": 7, "index": 0}])
    agent = RuleAgentV1(decision_timeout_ms=25.0, clock=lambda: next(readings))

    assert agent.choose(observation) == choose_rule_indices(observation) == [1]
    assert agent.last_source == "rule_v0_timeout_fallback"


def test_rule_v0_regression_behavior_is_unchanged() -> None:
    observation = _observation(
        options=[{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}]
    )

    assert choose_rule_indices(observation) == [2]


def test_clean_subprocess_imports_main_and_constructs_both_rule_agents() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = """
import main
deck = [1] * 60
assert main.make_rule_agent(deck=deck)({'select': None}) == deck
assert main.make_rule_agent_v1(deck=deck)({'select': None}) == deck
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_batch_registry_constructs_independent_rule_v1_lifecycles() -> None:
    deck = [1] * 60
    first = _make_agent("rule_v1", deck, 1)
    second = _make_agent("rule_v1", deck, 2)

    assert first({"select": None}) == deck
    assert second({"select": None}) == deck
    first_loop = first.decision_loop  # type: ignore[attr-defined]
    second_loop = second.decision_loop  # type: ignore[attr-defined]
    assert first_loop is not second_loop
    assert first_loop.public_belief is not second_loop.public_belief


def test_exception_fallback_is_cleared_by_next_game_registration() -> None:
    agent = RuleAgentV1()

    assert agent.choose({"select": {"option": [], "minCount": 0, "maxCount": 0}}) == []
    assert agent.last_state is None
    assert agent.last_summary is not None and agent.last_summary.degraded

    assert agent.choose({"select": None}) is None
    assert agent.last_summary is None
    assert agent.last_source == "reset"


def test_actor_information_view_is_immutable_and_instance_local() -> None:
    view = build_decision_state(_observation()).actor_view

    assert isinstance(view, ActorInformationView)
    with pytest.raises(AttributeError):
        view.actor = 1  # type: ignore[misc]
    with pytest.raises(DecisionStateError, match="SHA-256"):
        build_decision_state(_observation(), visible_history=("PRIVATE_HISTORY",))
