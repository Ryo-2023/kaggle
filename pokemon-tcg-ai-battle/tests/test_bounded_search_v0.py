"""Focused contracts for C3 Bounded Search v0."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from agents import choose_rule_indices
from mage_ptcg.decision_state import DecisionState, build_decision_state
from mage_ptcg.solver import (
    BoundedSearchConfig,
    BoundedSearchError,
    EngineTransition,
    SearchTelemetry,
    search_bounded,
)
from main import make_bounded_search_agent, make_rule_agent
from scripts.evaluate_bounded_search import run_fixture_evaluation


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DECK = [1] * 60


def _card(card_id: int) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": card_id,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _player(*, hand_ids: tuple[int, ...] = (100,)) -> dict[str, Any]:
    hand = [_card(card_id) for card_id in hand_ids]
    return {
        "active": [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": hand,
        "handCount": len(hand),
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }


def _observation(
    options: list[object],
    *,
    actor: int = 0,
    turn: int = 2,
    select_type: int = 0,
    selection_context: int = 0,
    minimum: int = 1,
    maximum: int = 1,
    opponent_hand: tuple[int, ...] = (700,),
    opaque_token: object = "OPAQUE_ENGINE_TOKEN",
) -> dict[str, Any]:
    players = [_player(hand_ids=(100,)), _player(hand_ids=opponent_hand)]
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": players,
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": turn,
            "turnActionCount": 3,
            "yourIndex": actor,
        },
        "logs": [{"private": "UNREAD_LOG"}],
        "search_begin_input": opaque_token,
        "select": {
            "context": selection_context,
            "maxCount": maximum,
            "minCount": minimum,
            "option": options,
            "type": select_type,
        },
        "step": turn,
    }


def _assert_trace_excludes_exact_private_values(
    value: object,
    *,
    forbidden_keys: frozenset[str],
    forbidden_scalars: frozenset[tuple[type[object], object]],
) -> None:
    """Check structural privacy without treating unrelated digits as secrets."""
    if isinstance(value, dict):
        for name, child in value.items():
            assert name not in forbidden_keys
            _assert_trace_excludes_exact_private_values(
                child,
                forbidden_keys=forbidden_keys,
                forbidden_scalars=forbidden_scalars,
            )
        return
    if isinstance(value, list):
        for child in value:
            _assert_trace_excludes_exact_private_values(
                child,
                forbidden_keys=forbidden_keys,
                forbidden_scalars=forbidden_scalars,
            )
        return
    assert (type(value), value) not in forbidden_scalars


class _FakeAdapter:
    def __init__(
        self,
        transition: Callable[[DecisionState, tuple[int, ...]], EngineTransition],
    ) -> None:
        self._transition = transition
        self.calls: list[tuple[str, tuple[int, ...], int]] = []

    def step(
        self,
        state: DecisionState,
        selection: tuple[int, ...],
        *,
        deadline_ns: int,
    ) -> EngineTransition:
        self.calls.append((state.digest, selection, deadline_ns))
        return self._transition(state, selection)


def _terminal_values(values: dict[tuple[int, ...], float]) -> _FakeAdapter:
    return _FakeAdapter(
        lambda _state, selection: EngineTransition(
            value=values[selection], terminal=True
        )
    )


def _search(
    observation: dict[str, Any],
    adapter: _FakeAdapter | None,
    **kwargs: object,
):
    fallback = choose_rule_indices(observation)
    assert fallback is not None
    return search_bounded(
        observation,
        fallback_selection=fallback,
        adapter=adapter,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_depth", 0),
        ("max_expansions", 0),
        ("max_engine_calls", 0),
        ("wall_clock_budget_ms", 0),
        ("hard_deadline_margin_ms", 20),
        ("primitive_exploration_fraction", 0.5),
    ],
)
def test_config_rejects_unbounded_or_partial_coverage_values(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        "max_depth": 2,
        "max_expansions": 64,
        "max_engine_calls": 64,
        "wall_clock_budget_ms": 20,
        "hard_deadline_margin_ms": 1,
        "primitive_exploration_fraction": 1.0,
    }
    values[field] = value

    with pytest.raises(ValueError):
        BoundedSearchConfig(**values)  # type: ignore[arg-type]


def test_missing_adapter_falls_back_to_rule_with_full_primitive_escape() -> None:
    observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}, {"type": 7, "index": 0}])

    result = _search(observation, None)

    assert list(result.selection) == choose_rule_indices(observation)
    assert result.fallback_reason == "engine_adapter_unavailable"
    assert result.engine_calls == result.expansions == 0
    assert result.primitive_coverage == 1.0
    assert {index for response in result.primitive_escape_responses for index in response} == {
        0,
        1,
        2,
    }


def test_invalid_rule_fallback_raises_without_returning_a_nonlegal_selection() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])
    adapter = _terminal_values({(0,): 1})
    messages: list[str] = []

    for _ in range(2):
        with pytest.raises(BoundedSearchError, match="^invalid_rule_fallback$") as exc_info:
            search_bounded(
                observation,
                fallback_selection=[99],
                adapter=adapter,
            )
        messages.append(str(exc_info.value))

    assert messages == ["invalid_rule_fallback", "invalid_rule_fallback"]
    assert adapter.calls == []


def test_unknown_selection_type_falls_back_without_calling_adapter() -> None:
    observation = _observation([{"type": 0}], select_type=99)
    adapter = _terminal_values({(0,): 1})

    result = _search(observation, adapter)

    assert result.fallback_reason == "invalid_observation:DecisionStateError"
    assert result.selection == (0,)
    assert adapter.calls == []


def test_guidance_orders_but_never_prunes_cabt_options() -> None:
    observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}, {"type": 7, "index": 0}])
    prior_calls: list[str] = []

    def prior(action_key: object) -> float:
        prior_calls.append(action_key.digest)  # type: ignore[attr-defined]
        return 10.0

    guided_adapter = _terminal_values({(0,): 0, (1,): 0, (2,): 0})
    unguided_adapter = _terminal_values({(0,): 0, (1,): 0, (2,): 0})

    guided = _search(
        observation, guided_adapter, guided=True, knowledge_prior=prior
    )
    unguided = _search(observation, unguided_adapter, guided=False)

    assert guided.selection == (2,)  # Rule v0 soft tie-break prefers PLAY.
    assert unguided.selection == (0,)  # Stable index tie-break only.
    assert guided.primitive_coverage == unguided.primitive_coverage == 1.0
    assert {call[1] for call in guided_adapter.calls} == {(0,), (1,), (2,)}
    assert {call[1] for call in unguided_adapter.calls} == {(0,), (1,), (2,)}
    assert len(prior_calls) == 3


def test_engine_value_overrides_rule_and_knowledge_priors() -> None:
    observation = _observation(
        [{"type": 7, "index": 0}, {"type": 7, "index": 1}]
    )
    adapter = _terminal_values({(0,): 0, (1,): 5})

    result = _search(
        observation,
        adapter,
        guided=True,
        knowledge_prior=lambda key: 1_000 if dict(key.canonical_payload)["index"] == 0 else 0,
    )

    assert result.selection == (1,)
    assert result.fallback_selection == (0,)
    assert result.fallback_reason is None


def test_depth_two_uses_opponent_minimum_and_respects_all_root_actions() -> None:
    root_observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}], turn=2)
    root = build_decision_state(root_observation)
    child_a = build_decision_state(
        _observation([{"type": 1}, {"type": 2}], actor=1, turn=3, select_type=9, selection_context=41)
    )
    child_b = build_decision_state(
        _observation([{"type": 1}, {"type": 2}], actor=1, turn=4, select_type=9, selection_context=41)
    )

    def transition(state: DecisionState, selection: tuple[int, ...]) -> EngineTransition:
        if state.digest == root.digest:
            return EngineTransition(
                value=0,
                terminal=False,
                next_state=child_a if selection == (0,) else child_b,
            )
        if state.digest == child_a.digest:
            return EngineTransition(value={(0,): 4, (1,): -2}[selection], terminal=True)
        if state.digest == child_b.digest:
            return EngineTransition(value={(0,): 1, (1,): 2}[selection], terminal=True)
        raise AssertionError("unexpected deterministic fixture state")

    adapter = _FakeAdapter(transition)
    result = _search(root_observation, adapter)

    assert result.selection == (1,)
    assert result.action_values[0].value == -2
    assert result.action_values[1].value == 1
    assert result.engine_calls == result.expansions == 6
    assert result.max_depth_reached == 2


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        (BoundedSearchConfig(max_engine_calls=2), "max_engine_calls"),
        (BoundedSearchConfig(max_expansions=2), "max_expansions"),
    ],
)
def test_root_requires_budget_for_every_primitive_response(
    config: BoundedSearchConfig, reason: str
) -> None:
    observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}, {"type": 7, "index": 0}])
    adapter = _terminal_values({(0,): 0, (1,): 0, (2,): 0})

    result = _search(observation, adapter, config=config)

    assert result.fallback_reason == "insufficient_primitive_budget"
    assert result.budget_exhaustion_reason == reason
    assert result.primitive_coverage == 1.0
    assert adapter.calls == []


def test_depth_budget_is_reported_without_unbounded_recursion() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])
    next_state = build_decision_state(
        _observation([{"type": 1}], actor=1, turn=3, select_type=9, selection_context=41)
    )
    adapter = _FakeAdapter(
        lambda _state, _selection: EngineTransition(
            value=7, terminal=False, next_state=next_state
        )
    )

    result = _search(
        observation, adapter, config=BoundedSearchConfig(max_depth=1)
    )

    assert result.selection == (0,)
    assert result.engine_calls == 1
    assert result.truncated
    assert result.budget_exhaustion_reason == "max_depth"


class _TickClock:
    def __init__(self, tick_ns: int) -> None:
        self.value = 0
        self.tick_ns = tick_ns

    def __call__(self) -> int:
        self.value += self.tick_ns
        return self.value


def test_wall_clock_timeout_deterministically_falls_back() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])
    adapter = _terminal_values({(0,): 10})

    result = _search(
        observation,
        adapter,
        config=BoundedSearchConfig(
            wall_clock_budget_ms=5, hard_deadline_margin_ms=0
        ),
        clock_ns=_TickClock(2_000_000),
    )

    assert result.selection == (0,)
    assert result.fallback_reason == "wall_clock_timeout"
    assert result.budget_exhaustion_reason == "wall_clock"
    assert result.timed_out
    assert result.engine_calls == 1
    assert result.max_depth_reached == 1


def test_adapter_exception_deterministically_falls_back() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])

    def fail(_state: DecisionState, _selection: tuple[int, ...]) -> EngineTransition:
        raise LookupError("fixture failure")

    result = _search(observation, _FakeAdapter(fail))

    assert result.selection == (0,)
    assert result.fallback_reason == "engine_adapter_exception:LookupError"
    assert result.engine_calls == 1
    assert result.max_depth_reached == 1


def test_child_prior_exception_deterministically_falls_back() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])
    root_state = build_decision_state(observation)
    child_state = build_decision_state(
        _observation([{"type": 1}], actor=1, turn=3, select_type=9, selection_context=41)
    )
    adapter = _FakeAdapter(
        lambda state, _selection: EngineTransition(
            value=0,
            terminal=state.digest != root_state.digest,
            next_state=child_state if state.digest == root_state.digest else None,
        )
    )
    prior_calls = 0

    def prior(_key: object) -> float:
        nonlocal prior_calls
        prior_calls += 1
        if prior_calls > 1:
            raise ArithmeticError("fixture prior failure")
        return 0

    result = _search(observation, adapter, knowledge_prior=prior)

    assert result.selection == (0,)
    assert result.fallback_reason == "prior_exception:ArithmeticError"
    assert result.engine_calls == 1


def test_same_input_and_adapter_have_same_decision_signature() -> None:
    observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}])

    first = _search(observation, _terminal_values({(0,): 0, (1,): 1}))
    second = _search(observation, _terminal_values({(0,): 0, (1,): 1}))

    assert first.deterministic_signature() == second.deterministic_signature()


def test_hidden_opponent_fields_and_opaque_token_do_not_change_search() -> None:
    first = _observation(
        [{"type": 14}, {"type": 13, "attackId": 1}],
        opponent_hand=(701, 702),
        opaque_token="FIRST_PRIVATE_TOKEN",
    )
    second = _observation(
        [{"type": 14}, {"type": 13, "attackId": 1}],
        opponent_hand=(801, 802),
        opaque_token="SECOND_PRIVATE_TOKEN",
    )
    first_adapter = _terminal_values({(0,): 0, (1,): 1})
    second_adapter = _terminal_values({(0,): 0, (1,): 1})

    first_result = _search(first, first_adapter)
    second_result = _search(second, second_adapter)

    assert first_result.deterministic_signature() == second_result.deterministic_signature()
    assert [call[0] for call in first_adapter.calls] == [
        call[0] for call in second_adapter.calls
    ]
    _assert_trace_excludes_exact_private_values(
        first_result.to_trace_payload(),
        forbidden_keys=frozenset({"logs", "search_begin_input"}),
        forbidden_scalars=frozenset(
            {
                (str, "FIRST_PRIVATE_TOKEN"),
                (int, 701),
                (int, 702),
            }
        ),
    )


def test_public_factory_without_adapter_matches_rule_v0_and_records_trace() -> None:
    observation = _observation([{"type": 14}, {"type": 13, "attackId": 0}, {"type": 7, "index": 0}])
    bounded = make_bounded_search_agent(deck=DECK)
    rule = make_rule_agent(deck=DECK)

    assert bounded(observation) == rule(observation)
    trace = bounded.search_telemetry.last_trace  # type: ignore[attr-defined]
    assert trace is not None
    assert trace["fallback_reason"] == "engine_adapter_unavailable"


def test_pack_is_not_imported_when_omitted() -> None:
    command = """
import json
import sys
from main import make_bounded_search_agent
agent = make_bounded_search_agent(deck=[1] * 60)
print(json.dumps(sorted(name for name in sys.modules if name.startswith('mage_ptcg.knowledge'))))
"""

    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []


def test_telemetry_reports_required_reliability_metrics() -> None:
    observation = _observation([{"type": 13, "attackId": 0}])
    searched = _search(observation, _terminal_values({(0,): 1}))
    fallback = _search(observation, None)
    telemetry = SearchTelemetry(trace_capacity=4)

    telemetry.record(searched)
    telemetry.record(fallback)
    snapshot = telemetry.snapshot()

    assert snapshot["decisions"] == 2
    assert snapshot["fallback_rate"] == 0.5
    assert snapshot["timeout_rate"] == 0
    assert snapshot["engine_calls_per_decision"] == 0.5
    assert snapshot["latency_ms"]["p50"] is not None
    assert snapshot["selection_type_counts"] == {"0": 2}


def test_fixture_evaluation_labels_scope_and_writes_counterexample(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "bounded-search-evaluation"

    summary = run_fixture_evaluation(output_dir=output_dir)

    assert summary["evaluation_scope"] == {
        "kind": "deterministic_fake_adapter_contract_fixture",
        "synthetic_match": False,
        "actual_cabt_paired_evaluation": "NOT_RUN",
        "actual_cabt_performance_improvement_confirmed": False,
        "reason": (
            "Environment.clone/step requires an evaluator-owned Environment; "
            "agent(obs) has no documented arbitrary-state reconstruction API"
        ),
    }
    assert summary["conditions"]["guided"]["legal_action_rate"] == 1.0
    assert summary["conditions"]["guided"]["primitive_coverage_rate"] == 1.0
    assert summary["conditions"]["unguided"]["legal_action_rate"] == 1.0
    assert summary["reproducibility"]["decision_signatures_identical"] is True
    assert summary["counterexample_count"] > 0
    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "decisions.jsonl").is_file()
    assert (output_dir / "counterexamples.json").is_file()
    serialized = json.dumps(summary, sort_keys=True)
    assert "win_rate" not in serialized
