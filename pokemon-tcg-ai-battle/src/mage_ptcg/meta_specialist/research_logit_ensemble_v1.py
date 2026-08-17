"""Research-only frozen-base logit ensemble and recurrent reset adapter.

This module is deliberately outside the packaged V4 policy/runtime path.  It
is a small experiment boundary for comparing frozen policies without changing
the semantic decoder or the production recurrent contract:

* every member receives the same ``(model_input, step_input)``;
* finite semantic logits (and STOP when legal) are averaged before decoding;
* every member has an independent decision session and receives its own hidden
  token on commit;
* ``normal``, ``action`` and ``turn`` reset modes are explicit and distinct.

The adapter is not a training implementation and is not intended to be passed
to ``make_agent``.  Long-running CABT evaluation remains an external caller's
responsibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum
from typing import Protocol, runtime_checkable

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitPolicyV1,
    SpecialistStepLogitsV1,
    derive_model_input_id_v1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionSessionV2,
)


class ResearchLogitEnsembleError(ValueError):
    """Raised when a research ensemble cannot preserve decoder/session invariants."""


@runtime_checkable
class ResearchMemberPolicyV1(Protocol):
    """Minimal policy boundary accepted by this research adapter."""

    def reset(self) -> None: ...

    def begin_decision(self) -> SpecialistDecisionSessionV2: ...


_RESET_MODES = frozenset({"normal", "action", "turn"})
_HEX64 = frozenset("0123456789abcdef")


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ResearchLogitEnsembleError(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _require_members(members: Sequence[ResearchMemberPolicyV1]) -> tuple[ResearchMemberPolicyV1, ...]:
    if isinstance(members, (str, bytes)):
        raise ResearchLogitEnsembleError("members must be a non-empty policy sequence")
    try:
        result = tuple(members)
    except TypeError as exc:
        raise ResearchLogitEnsembleError("members must be a non-empty policy sequence") from exc
    if not result:
        raise ResearchLogitEnsembleError("members must be non-empty")
    if any(not isinstance(member, ResearchMemberPolicyV1) for member in result):
        raise ResearchLogitEnsembleError("every ensemble member must implement reset/begin_decision")
    return result


class ResearchLogitEnsemblePolicyV1:
    """Average frozen member logits while keeping recurrent state per member."""

    def __init__(
        self,
        members: Sequence[ResearchMemberPolicyV1],
        *,
        reset_mode: str = "normal",
        policy_identity: str | None = None,
        checkpoint_lineage_id: str | None = None,
    ) -> None:
        if type(reset_mode) is not str or reset_mode not in _RESET_MODES:
            raise ResearchLogitEnsembleError(
                f"reset_mode must be one of {sorted(_RESET_MODES)}"
            )
        self._members = _require_members(members)
        self._reset_mode = reset_mode
        self._policy_identity = _require_hex64(
            policy_identity or "0" * 64, "policy_identity",
        )
        self._checkpoint_lineage_id = _require_hex64(
            checkpoint_lineage_id or "0" * 64, "checkpoint_lineage_id",
        )
        self._last_turn: int | None = None
        self._active = False

    @property
    def reset_mode(self) -> str:
        return self._reset_mode

    @property
    def member_count(self) -> int:
        return len(self._members)

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity=self._policy_identity,
            candidate_class="checkpointed_specialist",
            model_loaded=True,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
            checkpoint_lineage_reason=None,
            fallback_count=0,
        )

    def _reset_members(self) -> None:
        for member in self._members:
            member.reset()

    def reset(self) -> None:
        """Reset the full game state; this is the only normal game boundary."""
        if self._active:
            raise ResearchLogitEnsembleError("cannot reset while an action session is active")
        self._reset_members()
        self._last_turn = None

    def begin_decision(self) -> "ResearchLogitEnsembleSessionV1":
        if self._active:
            raise ResearchLogitEnsembleError("previous action must commit or abort before begin_decision")
        if self._reset_mode == "action":
            self._reset_members()
        self._active = True
        return ResearchLogitEnsembleSessionV1(self)

    def _finish(self) -> None:
        self._active = False


class ResearchLogitEnsembleSessionV1:
    """One complete action shared by independently recurrent member sessions."""

    def __init__(self, owner: ResearchLogitEnsemblePolicyV1) -> None:
        self._owner = owner
        self._member_sessions: tuple[SpecialistDecisionSessionV2, ...] | None = None
        self._model_input_id: str | None = None
        self._turn: int | None = None
        self._finished = False

    @property
    def next_recurrent_state_token(self) -> tuple[object | None, ...]:
        sessions = self._member_sessions
        if sessions is None:
            return tuple(None for _ in self._owner._members)
        return tuple(getattr(session, "next_recurrent_state_token", None) for session in sessions)

    def _start_members(self, model_input: SpecialistModelInputV1) -> None:
        if type(model_input) is not SpecialistModelInputV1:
            raise ResearchLogitEnsembleError("ensemble model_input must be SpecialistModelInputV1")
        model_input_id = derive_model_input_id_v1(model_input)
        if self._member_sessions is not None:
            if self._model_input_id != model_input_id:
                raise ResearchLogitEnsembleError(
                    "one complete action must use one model_input across all decode prefixes"
                )
            return
        if self._owner._reset_mode == "turn":
            turn = model_input.state_scalars[2]
            if self._owner._last_turn is not None and turn != self._owner._last_turn:
                self._owner._reset_members()
            self._owner._last_turn = turn
            self._turn = turn
        self._member_sessions = tuple(member.begin_decision() for member in self._owner._members)
        if any(not isinstance(session, SpecialistDecisionSessionV2) for session in self._member_sessions):
            raise ResearchLogitEnsembleError("member begin_decision returned an invalid session")
        self._model_input_id = model_input_id

    def logits(
        self,
        model_input: SpecialistModelInputV1,
        step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        if self._finished:
            raise ResearchLogitEnsembleError("cannot score a finished ensemble session")
        if type(step_input) is not SpecialistStepInputV1:
            raise ResearchLogitEnsembleError("ensemble step_input must be SpecialistStepInputV1")
        self._start_members(model_input)
        if self._owner._reset_mode == "turn" and self._turn != model_input.state_scalars[2]:
            raise ResearchLogitEnsembleError("one complete action cannot cross a public turn boundary")
        sessions = self._member_sessions
        assert sessions is not None
        outputs: list[SpecialistStepLogitsV1] = []
        for session in sessions:
            try:
                result = session.logits(model_input, step_input)
            except Exception as exc:
                raise ResearchLogitEnsembleError("ensemble member logits call failed") from exc
            if type(result) is not SpecialistStepLogitsV1:
                raise ResearchLogitEnsembleError("ensemble member returned a non-canonical logit object")
            outputs.append(result)
        expected = len(step_input.allowed_semantic_classes)
        if any(len(result.semantic_logits) != expected for result in outputs):
            raise ResearchLogitEnsembleError("ensemble member semantic logit arity mismatch")
        if step_input.stop_available:
            if any(result.stop_logit is None for result in outputs):
                raise ResearchLogitEnsembleError("ensemble member STOP logit is missing while STOP is legal")
            stop = fsum(float(result.stop_logit) for result in outputs) / len(outputs)  # type: ignore[arg-type]
        else:
            if any(result.stop_logit is not None for result in outputs):
                raise ResearchLogitEnsembleError("ensemble member returned STOP while STOP is illegal")
            stop = None
        semantic = tuple(
            fsum(result.semantic_logits[index] for result in outputs) / len(outputs)
            for index in range(expected)
        )
        return SpecialistStepLogitsV1(semantic_logits=semantic, stop_logit=stop)

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        if self._finished:
            raise ResearchLogitEnsembleError("ensemble session was already completed")
        if type(outcome) is not CommittedSemanticDecisionV2:
            raise ResearchLogitEnsembleError("ensemble commit requires CommittedSemanticDecisionV2")
        sessions = self._member_sessions
        if sessions is None:
            raise ResearchLogitEnsembleError("cannot commit before at least one logits call")
        try:
            for session in sessions:
                member_outcome = CommittedSemanticDecisionV2(
                    semantic_action=outcome.semantic_action,
                    semantic_log_probability=outcome.semantic_log_probability,
                    next_recurrent_state_token=getattr(session, "next_recurrent_state_token", None),
                )
                session.commit(member_outcome)
        except Exception as exc:
            raise ResearchLogitEnsembleError("ensemble member commit failed") from exc
        self._finished = True
        self._owner._finish()

    def abort(self) -> None:
        if self._finished:
            return
        sessions = self._member_sessions
        if sessions is not None:
            for session in sessions:
                try:
                    session.abort()
                except Exception:
                    pass
        self._finished = True
        self._owner._finish()


class ResearchLogitEnsemblePolicyFactoryV1:
    """Fresh-per-game factory for the research ensemble boundary.

    The factory is intentionally tiny: it only composes already-loaded member
    factories and does not load checkpoints, infer deck identity, or alter the
    production actor-pool registry.  A caller must provide a hash-bound
    ensemble identity and lineage before passing the resulting policy to the
    research evaluator.
    """

    def __init__(
        self,
        member_factories: Sequence[object],
        *,
        reset_mode: str = "normal",
        policy_identity: str,
        checkpoint_lineage_id: str,
    ) -> None:
        if type(reset_mode) is not str or reset_mode not in _RESET_MODES:
            raise ResearchLogitEnsembleError(
                f"reset_mode must be one of {sorted(_RESET_MODES)}"
            )
        try:
            factories = tuple(member_factories)
        except TypeError as exc:
            raise ResearchLogitEnsembleError("member_factories must be non-empty") from exc
        if not factories:
            raise ResearchLogitEnsembleError("member_factories must be non-empty")
        if any(not callable(getattr(factory, "new_policy", None)) for factory in factories):
            raise ResearchLogitEnsembleError("each member factory must expose new_policy()")
        self._member_factories = factories
        self._reset_mode = reset_mode
        self._policy_identity = _require_hex64(policy_identity, "policy_identity")
        self._checkpoint_lineage_id = _require_hex64(
            checkpoint_lineage_id, "checkpoint_lineage_id",
        )

    def new_policy(self) -> ResearchLogitEnsemblePolicyV1:
        members = tuple(factory.new_policy() for factory in self._member_factories)
        return ResearchLogitEnsemblePolicyV1(
            members,
            reset_mode=self._reset_mode,
            policy_identity=self._policy_identity,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
        )

__all__ = [
    "ResearchLogitEnsembleError",
    "ResearchLogitEnsemblePolicyV1",
    "ResearchLogitEnsemblePolicyFactoryV1",
    "ResearchLogitEnsembleSessionV1",
]
