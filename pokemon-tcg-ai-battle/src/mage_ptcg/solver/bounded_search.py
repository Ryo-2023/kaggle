"""Deterministic, primitive-complete, short-depth bounded search."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Any, Literal, TypeAlias

from mage_ptcg.decision_state import ActionKey, DecisionState, DecisionStateError, build_decision_state

from .transition import EngineAdapter, EngineAdapterError, EngineTransition


SearchMode: TypeAlias = Literal["guided", "unguided"]
BudgetReason: TypeAlias = Literal[
    "complete",
    "max_depth",
    "max_expansions",
    "max_engine_calls",
    "wall_clock",
]

KNOWN_SELECTION_TYPES = frozenset({0, 1, 4, 8, 9})
_MAIN_ACTION_SCORES = {
    "EVOLVE": 600,
    "ATTACH": 500,
    "PLAY": 400,
    "ABILITY": 300,
    "ATTACK": 200,
    "END": -1_000,
}


class BoundedSearchError(RuntimeError):
    """Raised for an internal bounded-search contract violation."""


class _DeadlineExceeded(BoundedSearchError):
    pass


class _AdapterFailed(BoundedSearchError):
    pass


class _PriorFailed(BoundedSearchError):
    pass


@dataclass(frozen=True, slots=True)
class BoundedSearchConfig:
    """All independent computation caps for C3 Bounded Search v0."""

    max_depth: int = 2
    max_expansions: int = 64
    max_engine_calls: int = 64
    wall_clock_budget_ms: float = 20.0
    hard_deadline_margin_ms: float = 1.0
    prior_floor: float = -1_000_000.0
    primitive_exploration_fraction: float = 1.0

    def __post_init__(self) -> None:
        for name in ("max_depth", "max_expansions", "max_engine_calls"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive int")
        for name in ("wall_clock_budget_ms", "hard_deadline_margin_ms", "prior_floor"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric and must not be bool")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if self.wall_clock_budget_ms <= 0:
            raise ValueError("wall_clock_budget_ms must be positive")
        if not 0 <= self.hard_deadline_margin_ms < self.wall_clock_budget_ms:
            raise ValueError("hard_deadline_margin_ms must be within [0, wall_clock_budget_ms)")
        if self.primitive_exploration_fraction != 1.0:
            raise ValueError("C3 v0 requires primitive_exploration_fraction == 1.0")


@dataclass(frozen=True, slots=True)
class ActionValue:
    selection: tuple[int, ...]
    value: float

    def to_payload(self) -> dict[str, object]:
        return {"selection": list(self.selection), "value": self.value}


@dataclass(frozen=True, slots=True)
class BoundedSearchResult:
    """One auditable decision, including normal exhaustion and fallback reasons."""

    selection: tuple[int, ...]
    fallback_selection: tuple[int, ...]
    selection_type: object
    mode: SearchMode
    action_keys: tuple[str, ...]
    action_values: tuple[ActionValue, ...]
    selected_action_key: str | None
    engine_calls: int
    expansions: int
    action_coverage: float
    primitive_coverage: float
    max_depth_reached: int
    elapsed_ms: float
    truncated: bool
    budget_exhaustion_reason: BudgetReason
    fallback_reason: str | None
    timed_out: bool
    knowledge_enabled: bool
    primitive_escape_responses: tuple[tuple[int, ...], ...]

    @property
    def fell_back(self) -> bool:
        return self.fallback_reason is not None

    def deterministic_signature(self) -> dict[str, object]:
        """Return all decision fields except wall-clock measurement."""
        payload = self.to_trace_payload()
        payload.pop("elapsed_ms")
        return payload

    def to_trace_payload(self) -> dict[str, object]:
        """Return a bounded trace containing no raw state or private ActionKey."""
        return {
            "schema_version": "bounded-search-trace-v0",
            "mode": self.mode,
            "selection_type": self.selection_type,
            "selection": list(self.selection),
            "fallback_selection": list(self.fallback_selection),
            "action_keys": list(self.action_keys),
            "action_values": [item.to_payload() for item in self.action_values],
            "selected_action_key": self.selected_action_key,
            "engine_calls": self.engine_calls,
            "expansions": self.expansions,
            "action_coverage": self.action_coverage,
            "primitive_coverage": self.primitive_coverage,
            "max_depth_reached": self.max_depth_reached,
            "elapsed_ms": self.elapsed_ms,
            "truncated": self.truncated,
            "budget_exhaustion_reason": self.budget_exhaustion_reason,
            "fallback_reason": self.fallback_reason,
            "timed_out": self.timed_out,
            "knowledge_enabled": self.knowledge_enabled,
            "primitive_escape_responses": [
                list(selection) for selection in self.primitive_escape_responses
            ],
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    selection: tuple[int, ...]
    public_key: str
    rule_score: int
    knowledge_score: float


@dataclass(slots=True)
class _Budget:
    config: BoundedSearchConfig
    deadline_ns: int
    clock_ns: Callable[[], int]
    engine_calls: int = 0
    expansions: int = 0
    max_depth_reached: int = 0
    exhaustion_reason: BudgetReason = "complete"

    def check_deadline(self) -> None:
        if self.clock_ns() >= self.deadline_ns:
            self.exhaustion_reason = "wall_clock"
            raise _DeadlineExceeded("wall-clock deadline exhausted")

    def can_expand(self, count: int) -> bool:
        self.check_deadline()
        if self.engine_calls + count > self.config.max_engine_calls:
            self.exhaustion_reason = "max_engine_calls"
            return False
        if self.expansions + count > self.config.max_expansions:
            self.exhaustion_reason = "max_expansions"
            return False
        return True


def _public_action_key(key: ActionKey) -> str:
    payload = json.dumps(
        key.to_public_trace_payload(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"mage_ptcg.bounded_search:public-action-v0\0" + payload).hexdigest()


def _trace_scalar(value: object) -> object:
    """Keep fallback traces JSON-safe without traversing opaque objects."""
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    name = getattr(value, "name", None)
    return name.rsplit(".", 1)[-1].upper() if isinstance(name, str) else None


def _selection_bounds(state: DecisionState) -> tuple[int, int]:
    select = state.normalized_public_observation["select"]
    return int(select["min_count"]), int(select["max_count"])


def _is_legal_selection(selection: Sequence[int], state: DecisionState) -> bool:
    minimum, maximum = _selection_bounds(state)
    indices = list(selection)
    return (
        minimum <= len(indices) <= maximum
        and len(indices) == len(set(indices))
        and all(type(index) is int and 0 <= index < len(state.legal_actions) for index in indices)
    )


def _primitive_escape_responses(
    state: DecisionState,
    fallback: Sequence[int],
) -> tuple[tuple[int, ...], ...]:
    """Represent every cabt option in at least one legal primitive response."""
    minimum, maximum = _selection_bounds(state)
    option_count = len(state.legal_actions)
    candidates: list[tuple[int, ...]] = []

    def add(selection: Sequence[int]) -> None:
        value = tuple(selection)
        if _is_legal_selection(value, state) and value not in candidates:
            candidates.append(value)

    add(fallback)
    if minimum == 0:
        add(())
    if maximum > 0:
        response_size = minimum if minimum > 0 else 1
        for anchor in range(option_count):
            completion = [anchor]
            completion.extend(
                index
                for index in range(option_count)
                if index != anchor and len(completion) < response_size
            )
            add(completion)
    return tuple(candidates)


def _primitive_coverage(
    responses: Sequence[Sequence[int]],
    state: DecisionState,
) -> float:
    _minimum, maximum = _selection_bounds(state)
    primitive_count = len(state.legal_actions) if maximum > 0 else 0
    if primitive_count == 0:
        return 1.0
    covered = {index for response in responses for index in response}
    return len(covered) / primitive_count


def _rule_action_score(state: DecisionState, action_index: int) -> int:
    action = state.legal_actions[action_index].action_key
    if action.selection_type == 0:
        return _MAIN_ACTION_SCORES.get(action.semantic_operation, 0)
    fields = dict(action.canonical_payload)
    damage = fields.get("damage") if type(fields.get("damage")) is int else 0
    hp = fields.get("hp") if type(fields.get("hp")) is int else None
    score = int(damage) * 10
    if hp is not None and hp <= damage:
        score += 1_000
    if fields.get("playerIndex") == state.actor_view.actor:
        score += 1
    return score


def _build_candidates(
    state: DecisionState,
    fallback: Sequence[int],
    *,
    knowledge_prior: Callable[[ActionKey], float] | None,
    prior_floor: float,
) -> tuple[_Candidate, ...]:
    responses = _primitive_escape_responses(state, fallback)
    candidates: list[_Candidate] = []
    for response in responses:
        action_keys = [state.legal_actions[index].action_key for index in response]
        knowledge_score = 0.0
        if knowledge_prior is not None:
            for key in action_keys:
                try:
                    raw_score = knowledge_prior(key)
                except Exception as exc:  # soft-prior plugin boundary
                    raise _PriorFailed(f"{type(exc).__name__}: {exc}") from exc
                if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                    raise BoundedSearchError("Knowledge prior score must be numeric")
                if not math.isfinite(float(raw_score)):
                    raise BoundedSearchError("Knowledge prior score must be finite")
                knowledge_score += max(prior_floor, float(raw_score))
        public_key = hashlib.sha256(
            b"mage_ptcg.bounded_search:response-v0\0"
            + "\0".join(_public_action_key(key) for key in action_keys).encode("ascii")
        ).hexdigest()
        candidates.append(
            _Candidate(
                selection=response,
                public_key=public_key,
                rule_score=sum(_rule_action_score(state, index) for index in response),
                knowledge_score=knowledge_score,
            )
        )
    return tuple(candidates)


def _ordered_candidates(candidates: Sequence[_Candidate], *, guided: bool) -> list[_Candidate]:
    if not guided:
        return sorted(candidates, key=lambda candidate: candidate.selection)
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.rule_score,
            -candidate.knowledge_score,
            candidate.selection,
        ),
    )


def _call_adapter(
    adapter: EngineAdapter,
    state: DecisionState,
    candidate: _Candidate,
    budget: _Budget,
) -> EngineTransition:
    budget.check_deadline()
    budget.engine_calls += 1
    budget.expansions += 1
    try:
        transition = adapter.step(state, candidate.selection, deadline_ns=budget.deadline_ns)
    except Exception as exc:  # adapter is an explicit fail-closed plugin boundary
        raise _AdapterFailed(f"{type(exc).__name__}: {exc}") from exc
    budget.check_deadline()
    if not isinstance(transition, EngineTransition):
        raise _AdapterFailed("adapter must return EngineTransition")
    return transition


def _evaluate_position(
    state: DecisionState,
    *,
    depth: int,
    inherited_value: float,
    root_actor: int,
    adapter: EngineAdapter,
    budget: _Budget,
    config: BoundedSearchConfig,
    guided: bool,
    knowledge_prior: Callable[[ActionKey], float] | None,
) -> float:
    budget.max_depth_reached = max(budget.max_depth_reached, depth)
    if depth >= config.max_depth:
        budget.exhaustion_reason = "max_depth"
        return inherited_value

    candidates = _build_candidates(
        state,
        (),
        knowledge_prior=knowledge_prior,
        prior_floor=config.prior_floor,
    )
    if not candidates or not budget.can_expand(len(candidates)):
        return inherited_value

    ordered = _ordered_candidates(candidates, guided=guided)
    transitions = [
        (candidate, _call_adapter(adapter, state, candidate, budget))
        for candidate in ordered
    ]
    values: list[float] = []
    for _candidate, transition in transitions:
        value = transition.value
        budget.max_depth_reached = max(budget.max_depth_reached, depth + 1)
        if not transition.terminal and transition.next_state is not None:
            value = _evaluate_position(
                transition.next_state,
                depth=depth + 1,
                inherited_value=value,
                root_actor=root_actor,
                adapter=adapter,
                budget=budget,
                config=config,
                guided=guided,
                knowledge_prior=knowledge_prior,
            )
        values.append(value)
    return max(values) if state.actor_view.actor == root_actor else min(values)


def _fallback_result(
    *,
    fallback: Sequence[int],
    selection_type: object,
    mode: SearchMode,
    started_ns: int,
    clock_ns: Callable[[], int],
    reason: str,
    budget_reason: BudgetReason = "complete",
    timed_out: bool = False,
    engine_calls: int = 0,
    expansions: int = 0,
    max_depth_reached: int = 0,
    state: DecisionState | None = None,
    knowledge_enabled: bool = False,
) -> BoundedSearchResult:
    responses = _primitive_escape_responses(state, fallback) if state is not None else ()
    elapsed_ms = max(0.0, (clock_ns() - started_ns) / 1_000_000)
    return BoundedSearchResult(
        selection=tuple(fallback),
        fallback_selection=tuple(fallback),
        selection_type=selection_type,
        mode=mode,
        action_keys=(),
        action_values=(),
        selected_action_key=None,
        engine_calls=engine_calls,
        expansions=expansions,
        action_coverage=0.0,
        primitive_coverage=_primitive_coverage(responses, state) if state is not None else 0.0,
        max_depth_reached=max_depth_reached,
        elapsed_ms=elapsed_ms,
        truncated=budget_reason != "complete",
        budget_exhaustion_reason=budget_reason,
        fallback_reason=reason,
        timed_out=timed_out,
        knowledge_enabled=knowledge_enabled,
        primitive_escape_responses=responses,
    )


def search_bounded(
    observation: object,
    *,
    fallback_selection: Sequence[int],
    adapter: EngineAdapter | None,
    config: BoundedSearchConfig | None = None,
    guided: bool = True,
    knowledge_prior: Callable[[ActionKey], float] | None = None,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> BoundedSearchResult:
    """Search one cabt decision or return the supplied Rule v0 fallback.

    Root admission is all-or-nothing: every primitive escape response must fit
    both the expansion and engine-call caps.  Priors only order and tie-break
    that complete set; they never delete a cabt option.
    """
    active_config = config or BoundedSearchConfig()
    mode: SearchMode = "guided" if guided else "unguided"
    started_ns = clock_ns()
    selection_type: object = None
    if isinstance(observation, Mapping):
        select = observation.get("select")
        if isinstance(select, Mapping):
            selection_type = _trace_scalar(select.get("type"))

    try:
        state = build_decision_state(observation)
    except (DecisionStateError, TypeError, ValueError, KeyError) as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"invalid_observation:{type(exc).__name__}",
            knowledge_enabled=knowledge_prior is not None,
        )

    selection_type = state.normalized_public_observation["select"]["type"]
    if selection_type not in KNOWN_SELECTION_TYPES:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="unknown_selection_type",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    if not _is_legal_selection(fallback_selection, state):
        raise BoundedSearchError("invalid_rule_fallback")
    if adapter is None:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="engine_adapter_unavailable",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )

    try:
        candidates = _build_candidates(
            state,
            fallback_selection,
            knowledge_prior=knowledge_prior,
            prior_floor=active_config.prior_floor,
        )
    except _PriorFailed as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"prior_exception:{type(exc.__cause__ or exc).__name__}",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    except (BoundedSearchError, TypeError, ValueError, KeyError) as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"prior_error:{type(exc).__name__}",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )

    responses = tuple(candidate.selection for candidate in candidates)
    coverage = _primitive_coverage(responses, state)
    if coverage != 1.0:
        raise BoundedSearchError("primitive escape construction did not cover every cabt option")
    if not candidates:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="no_searchable_primitive",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )

    effective_budget_ns = int(
        (active_config.wall_clock_budget_ms - active_config.hard_deadline_margin_ms) * 1_000_000
    )
    budget = _Budget(
        config=active_config,
        deadline_ns=started_ns + effective_budget_ns,
        clock_ns=clock_ns,
    )
    if len(candidates) > active_config.max_engine_calls:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="insufficient_primitive_budget",
            budget_reason="max_engine_calls",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    if len(candidates) > active_config.max_expansions:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="insufficient_primitive_budget",
            budget_reason="max_expansions",
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )

    values: dict[tuple[int, ...], float] = {}
    try:
        if not budget.can_expand(len(candidates)):
            raise BoundedSearchError("root admission unexpectedly failed")
        budget.max_depth_reached = 1
        ordered_root = _ordered_candidates(candidates, guided=guided)
        root_transitions = [
            (candidate, _call_adapter(adapter, state, candidate, budget))
            for candidate in ordered_root
        ]
        for candidate, transition in root_transitions:
            budget.max_depth_reached = max(budget.max_depth_reached, 1)
            value = transition.value
            if not transition.terminal and transition.next_state is not None:
                value = _evaluate_position(
                    transition.next_state,
                    depth=1,
                    inherited_value=value,
                    root_actor=state.actor_view.actor,
                    adapter=adapter,
                    budget=budget,
                    config=active_config,
                    guided=guided,
                    knowledge_prior=knowledge_prior,
                )
            values[candidate.selection] = value
    except _DeadlineExceeded:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason="wall_clock_timeout",
            budget_reason="wall_clock",
            timed_out=True,
            engine_calls=budget.engine_calls,
            expansions=budget.expansions,
            max_depth_reached=budget.max_depth_reached,
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    except (_AdapterFailed, EngineAdapterError) as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"engine_adapter_exception:{type(exc.__cause__ or exc).__name__}",
            budget_reason=budget.exhaustion_reason,
            engine_calls=budget.engine_calls,
            expansions=budget.expansions,
            max_depth_reached=budget.max_depth_reached,
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    except _PriorFailed as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"prior_exception:{type(exc.__cause__ or exc).__name__}",
            budget_reason=budget.exhaustion_reason,
            engine_calls=budget.engine_calls,
            expansions=budget.expansions,
            max_depth_reached=budget.max_depth_reached,
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )
    except (BoundedSearchError, TypeError, ValueError, KeyError) as exc:
        return _fallback_result(
            fallback=fallback_selection,
            selection_type=selection_type,
            mode=mode,
            started_ns=started_ns,
            clock_ns=clock_ns,
            reason=f"search_exception:{type(exc).__name__}",
            budget_reason=budget.exhaustion_reason,
            engine_calls=budget.engine_calls,
            expansions=budget.expansions,
            max_depth_reached=budget.max_depth_reached,
            state=state,
            knowledge_enabled=knowledge_prior is not None,
        )

    ordered = _ordered_candidates(candidates, guided=guided)
    best_value = max(values.values())
    selected = next(candidate for candidate in ordered if values[candidate.selection] == best_value)
    action_values = tuple(
        ActionValue(candidate.selection, values[candidate.selection])
        for candidate in sorted(candidates, key=lambda item: item.selection)
    )
    elapsed_ms = max(0.0, (clock_ns() - started_ns) / 1_000_000)
    result = BoundedSearchResult(
        selection=selected.selection,
        fallback_selection=tuple(fallback_selection),
        selection_type=selection_type,
        mode=mode,
        action_keys=tuple(candidate.public_key for candidate in candidates),
        action_values=action_values,
        selected_action_key=selected.public_key,
        engine_calls=budget.engine_calls,
        expansions=budget.expansions,
        action_coverage=len(values) / len(candidates),
        primitive_coverage=coverage,
        max_depth_reached=budget.max_depth_reached,
        elapsed_ms=elapsed_ms,
        truncated=budget.exhaustion_reason != "complete",
        budget_exhaustion_reason=budget.exhaustion_reason,
        fallback_reason=None,
        timed_out=False,
        knowledge_enabled=knowledge_prior is not None,
        primitive_escape_responses=responses,
    )
    if not _is_legal_selection(result.selection, state):
        raise BoundedSearchError("bounded search produced a non-cabt selection")
    return result


__all__ = [
    "ActionValue",
    "BoundedSearchConfig",
    "BoundedSearchError",
    "BoundedSearchResult",
    "KNOWN_SELECTION_TYPES",
    "search_bounded",
]
