"""Public transition contract for bounded search.

The protocol accepts only the privacy-safe :class:`DecisionState` projection.
It deliberately has no field for cabt's opaque ``search_begin_input`` token or
for a raw engine state. Public ``Environment.clone``/``step`` applies only to
an environment already owned by an external evaluator; the submission
``agent(obs)`` contract has no documented arbitrary-state reconstruction API.
An adapter must satisfy that boundary before it can be enabled in submission.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, runtime_checkable

from mage_ptcg.decision_state import DecisionState


class EngineAdapterError(RuntimeError):
    """Raised when an adapter violates the bounded-search transition contract."""


@dataclass(frozen=True, slots=True)
class EngineTransition:
    """One deterministic public transition and its root-player value estimate."""

    value: float
    terminal: bool
    next_state: DecisionState | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise EngineAdapterError("transition value must be numeric and must not be bool")
        if not math.isfinite(float(self.value)):
            raise EngineAdapterError("transition value must be finite")
        object.__setattr__(self, "value", float(self.value))
        if type(self.terminal) is not bool:
            raise EngineAdapterError("transition terminal must be a bool")
        if self.terminal and self.next_state is not None:
            raise EngineAdapterError("terminal transition must not contain next_state")
        if not self.terminal and not isinstance(self.next_state, DecisionState):
            raise EngineAdapterError("non-terminal transition must contain DecisionState")


@runtime_checkable
class EngineAdapter(Protocol):
    """Forward model constrained to actor-visible state and an absolute deadline.

    Implementations must be deterministic and must return before
    ``deadline_ns``.  The search checks the deadline before and after every
    call and falls back to Rule Agent v0 on an overrun or exception.
    """

    def step(
        self,
        state: DecisionState,
        selection: tuple[int, ...],
        *,
        deadline_ns: int,
    ) -> EngineTransition:
        """Apply one cabt-legal selection to a privacy-safe state."""


__all__ = ["EngineAdapter", "EngineAdapterError", "EngineTransition"]
