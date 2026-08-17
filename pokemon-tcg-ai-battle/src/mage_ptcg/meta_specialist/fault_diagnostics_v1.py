"""Structured candidate-side fault evidence and one-retry classification."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
import traceback
from typing import Mapping, Sequence


def _identity_key_v1(payload: Mapping[str, object], *, include_retry: bool) -> str:
    body = dict(payload)
    if not include_retry:
        body.pop("retry_index", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b"mage_ptcg:canonical-game-identity:v1\0" + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CanonicalGameIdentityV1:
    """The immutable recipe for one replayable game attempt.

    ``game_key`` deliberately excludes the retry index: it identifies the game
    being replayed. ``record_key`` includes it so a first attempt and retry
    cannot overwrite one another or be mistaken for the same execution.
    """

    opponent_id: str
    opponent_policy_version: str
    opponent_deck_fingerprint: str
    seat: int
    environment_seed: int
    agent_sampling_seed: int
    retry_index: int = 0

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.opponent_id, self.opponent_policy_version, self.opponent_deck_fingerprint,
        )):
            raise ValueError("canonical game identity strings must be nonempty")
        if type(self.seat) is not int or self.seat not in (0, 1):
            raise ValueError("canonical game identity seat must be 0 or 1")
        if any(type(value) is not int or value < 0 for value in (
            self.environment_seed, self.agent_sampling_seed, self.retry_index,
        )):
            raise ValueError("canonical game identity seeds and retry_index must be nonnegative ints")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CanonicalGameIdentityV1":
        return cls(**dict(payload))

    @property
    def game_key(self) -> str:
        return _identity_key_v1(self.to_dict(), include_retry=False)

    @property
    def record_key(self) -> str:
        return _identity_key_v1(self.to_dict(), include_retry=True)

    def with_retry_index(self, retry_index: int) -> "CanonicalGameIdentityV1":
        return CanonicalGameIdentityV1(
            opponent_id=self.opponent_id, opponent_policy_version=self.opponent_policy_version,
            opponent_deck_fingerprint=self.opponent_deck_fingerprint, seat=self.seat,
            environment_seed=self.environment_seed, agent_sampling_seed=self.agent_sampling_seed,
            retry_index=retry_index,
        )


@dataclass(frozen=True, slots=True)
class FaultDiagnosticsV1:
    exception_class: str
    message: str
    stack_trace: str
    decision_index: int | None
    state_hash: str | None
    legal_action_count: int | None
    elapsed_seconds: float | None
    model_inference_latency_ms: float | None
    environment_latency_ms: float | None
    process_id: int
    game_identity: dict[str, object] | None = None
    last_valid_observation: Mapping[str, object] | None = None
    last_valid_action: Sequence[int] | None = None
    worker_exit_code: int | None = None
    state_hash_sequence: tuple[str, ...] = ()
    action_sequence: tuple[tuple[int, ...], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "FaultDiagnosticsV1":
        """Rehydrate a child diagnostic without replacing its source evidence."""
        body = dict(payload)
        body["last_valid_observation"] = (
            None if body.get("last_valid_observation") is None
            else dict(body["last_valid_observation"])
        )
        body["last_valid_action"] = (
            None if body.get("last_valid_action") is None
            else tuple(body["last_valid_action"])
        )
        body["state_hash_sequence"] = tuple(body.get("state_hash_sequence", ()))
        body["action_sequence"] = tuple(tuple(action) for action in body.get("action_sequence", ()))
        return cls(**body)


def capture_fault_v1(
    exception: BaseException, *, decision_index: int | None = None, state_hash: str | None = None,
    legal_action_count: int | None = None, elapsed_seconds: float | None = None,
    model_latency_ms: float | None = None, environment_latency_ms: float | None = None,
    process_id: int | None = None,
    game_identity: CanonicalGameIdentityV1 | None = None,
    last_valid_observation: Mapping[str, object] | None = None,
    last_valid_action: Sequence[int] | None = None,
    worker_exit_code: int | None = None,
    state_hash_sequence: Sequence[str] = (), action_sequence: Sequence[Sequence[int]] = (),
) -> FaultDiagnosticsV1:
    return FaultDiagnosticsV1(
        exception_class=type(exception).__name__, message=str(exception),
        stack_trace="".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
        decision_index=decision_index, state_hash=state_hash, legal_action_count=legal_action_count,
        elapsed_seconds=elapsed_seconds, model_inference_latency_ms=model_latency_ms,
        environment_latency_ms=environment_latency_ms, process_id=os.getpid() if process_id is None else process_id,
        game_identity=game_identity.to_dict() if game_identity is not None else None,
        last_valid_observation=dict(last_valid_observation) if last_valid_observation is not None else None,
        last_valid_action=list(last_valid_action) if last_valid_action is not None else None,
        worker_exit_code=worker_exit_code,
        state_hash_sequence=tuple(state_hash_sequence),
        action_sequence=tuple(tuple(action) for action in action_sequence),
    )


def classify_retry_v1(first: FaultDiagnosticsV1, second: FaultDiagnosticsV1 | None) -> str:
    if second is None:
        return "transient"
    same_source_location = (
        first.exception_class == second.exception_class and first.message == second.message
        and first.decision_index == second.decision_index and first.state_hash == second.state_hash
    )
    # A flattened engine exception can retain the same class/message and even
    # a stale source location after the game itself has diverged.  The real
    # source-time traces are therefore part of the reproducibility claim; an
    # unavailable trace (both empty) is neutral, but unequal observed traces
    # are affirmative evidence of divergence.
    same_trace = (
        first.state_hash_sequence == second.state_hash_sequence
        and first.action_sequence == second.action_sequence
    )
    return "reproducible" if same_source_location and same_trace else "divergent"


__all__ = ["CanonicalGameIdentityV1", "FaultDiagnosticsV1", "capture_fault_v1", "classify_retry_v1"]
