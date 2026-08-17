"""Episode-safe recurrent replay sequence contracts."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping


def public_prize_potential(public_state: Mapping[str, Any]) -> float:
    """Return a bounded actor-visible potential from public prize counts."""
    from .semantic_state import SemanticStateError, assert_actor_visible
    try:
        assert_actor_visible(public_state)
    except SemanticStateError as exc:
        raise ValueError(f"potential requires actor-visible state: {exc}") from exc
    own = public_state.get("self") if isinstance(public_state.get("self"), Mapping) else {}
    opponent = public_state.get("opponent") if isinstance(public_state.get("opponent"), Mapping) else {}
    own_prizes, opponent_prizes = own.get("prize_count"), opponent.get("prize_count")
    if type(own_prizes) not in (int, float) or type(opponent_prizes) not in (int, float):
        raise ValueError("potential requires public prize counts")
    if not 0 <= float(own_prizes) <= 6 or not 0 <= float(opponent_prizes) <= 6:
        raise ValueError("public prize counts are outside [0, 6]")
    return .10 * (float(opponent_prizes) - float(own_prizes)) / 6.0


def shape_episode_rewards(potentials: Iterable[float], *, outcome: float, gamma: float) -> list[float]:
    """Potential-based shaping with an explicit zero terminal potential."""
    values = [float(value) for value in potentials]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("episode potentials must be finite and non-empty")
    if not math.isfinite(outcome) or not -1.0 <= outcome <= 1.0 or not 0.0 <= gamma <= 1.0:
        raise ValueError("invalid shaping outcome or discount")
    return [gamma * values[index + 1] - value for index, value in enumerate(values[:-1])] + [outcome - values[-1]]


@dataclass(frozen=True, slots=True)
class R2D3Transition:
    public_state: tuple[float, ...]
    legal_actions: tuple[tuple[float, ...], ...]
    selected_action: int
    reward: float
    discount: float
    terminal: bool
    behavior_policy_version: str
    behavior_source: str
    opponent_policy_hash: str
    opponent_deck_hash: str
    opponent_source_lineage: str
    opponent_family: str
    own_deck_hash: str
    hidden_state: tuple[float, ...] | None = None
    demonstration: bool = False

    def __post_init__(self) -> None:
        if not self.legal_actions or not 0 <= self.selected_action < len(self.legal_actions): raise ValueError("selected action must be legal")
        if not 0.0 <= self.discount <= 1.0 or (self.terminal and self.discount != 0.0): raise ValueError("terminal discount is invalid")
        if not all((self.opponent_policy_hash, self.opponent_deck_hash, self.opponent_source_lineage, self.behavior_policy_version)): raise ValueError("trajectory identity is incomplete")


@dataclass(frozen=True, slots=True)
class SequenceBatch:
    burn_in: tuple[R2D3Transition, ...]
    learner: tuple[R2D3Transition, ...]
    priority: float
    sequence_id: str
    episode_id: str = ""
    lookahead: tuple[R2D3Transition, ...] = ()

    def __post_init__(self) -> None:
        if not self.learner or self.priority <= 0.0: raise ValueError("sequence must have learner transitions and positive priority")
        if any(step.terminal for step in self.burn_in): raise ValueError("burn-in cannot cross an episode boundary")
        if any(step.terminal for step in self.lookahead[:-1]): raise ValueError("lookahead cannot cross an episode boundary")


def split_episode(transitions: Iterable[R2D3Transition], *, burn_in: int, unroll: int,
                  stride: int | None = None, prefix: str = "episode",
                  n_step_lookahead: int = 5) -> list[SequenceBatch]:
    values = list(transitions)
    stride = unroll if stride is None else stride
    if burn_in < 0 or unroll < 1 or stride < 1 or n_step_lookahead < 0: raise ValueError("invalid sequence lengths")
    # A trajectory is one episode.  Preserve overlapping starts that also
    # reach its terminal transition, but never let an accidental later record
    # become a second episode in the same replay sequence.
    for terminal_index, step in enumerate(values):
        if step.terminal:
            values = values[:terminal_index + 1]
            break
    output: list[SequenceBatch] = []
    start = 0
    while start < len(values):
        learner = values[start:start + unroll]
        if not learner: break
        prior = values[max(0, start - burn_in):start]
        if any(step.terminal for step in prior): prior = []
        lookahead = values[start + unroll:start + unroll + n_step_lookahead]
        output.append(SequenceBatch(tuple(prior), tuple(learner), 1.0, f"{prefix}-{start}", prefix,
                                    tuple(lookahead)))
        start += stride
    return output


def n_step_returns(transitions: list[R2D3Transition], *, n_step: int) -> list[tuple[float, float, int]]:
    if n_step < 1: raise ValueError("n_step must be positive")
    result: list[tuple[float, float, int]] = []
    for start in range(len(transitions)):
        total = 0.0; multiplier = 1.0; end = start
        for end in range(start, min(len(transitions), start + n_step)):
            step = transitions[end]; total += multiplier * step.reward; multiplier *= step.discount
            if step.terminal: break
        result.append((total, multiplier, end))
    return result
