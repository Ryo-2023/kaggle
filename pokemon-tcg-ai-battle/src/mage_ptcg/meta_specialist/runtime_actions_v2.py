"""Private runtime complete-action envelopes for actor-visible C1 v2 decisions.

The policy boundary in this module is deliberately semantic-only.  Physical
local identities and CABT execution indices remain inside a per-decision
envelope and are rechecked immediately before execution.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
from itertools import combinations, permutations
import json
from math import comb, exp, isfinite, log, perm
from numbers import Real
from typing import Protocol, runtime_checkable
from weakref import ReferenceType, ref

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    ExtractedSpecialistModelInputV1,
    SemanticActionV1,
    SpecialistFeatureError,
    SpecialistStepLogitPolicyV1,
    SpecialistStepInputV1,
    build_specialist_step_input_v1,
    canonical_model_input_bytes_v1,
    canonical_step_input_bytes_v1,
    choose_lexicographic_alias_v1,
    evaluate_specialist_step_v1,
    extract_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorVisibleDecisionStateV2,
    project_c1v2_to_c1v1_own_private_state,
    project_c1v2_to_c1v1_public_state,
    serialize_actor_visible_decision_state_v2,
    validate_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.actions import Candidate, CompleteAction, DecisionEnvelope


MAX_LEGAL_CANDIDATES_V2 = 512
MAX_EXACT_COMPLETE_ACTIONS_V2 = 65_536
PRIVATE_ENVELOPE_SERIALIZATION_ERROR = "private runtime envelope cannot be serialized publicly"
_NONREPRESENTABLE_SAMPLING_MASS_ERROR = "runtime semantic token mass is nonrepresentable"
_PRIVATE_DECISION_DIGEST_PREFIX = b"mage_ptcg:runtime-decision-envelope:v2\0"
_V1_DECISION_DIGEST_PREFIX = b"mage_ptcg.decision_state:v1\0"
_PUBLIC_TRACE_IDENTITY_PREFIX = b"mage_ptcg.meta_specialist.complete_action_trace:v1\0"
_ACTION_ISSUANCE_SENTINEL = object()
_ACTION_ISSUANCE_CONTEXT: ContextVar[object | None] = ContextVar(
    "mage_ptcg_runtime_action_issuance_v2", default=None
)


class RuntimeEnvelopeError(ValueError):
    """Raised when a private runtime envelope is malformed or crosses a boundary."""


class RuntimeActionError(ValueError):
    """Raised when a private complete action is not current or executable."""

class RuntimePolicyError(ValueError):
    """Raised when a semantic-only policy violates the shared v2 step contract."""

class RuntimeEnumerationError(ValueError):
    """Raised before an exact private complete-action enumeration is too large."""

@runtime_checkable
class RuntimeRandomV1(Protocol):
    """The injected source of entropy used by semantic-class sampling only."""

    def random(self) -> float:
        """Return one finite uniform value in the half-open interval [0, 1)."""





def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _private_decision_digest(state: ActorVisibleDecisionStateV2) -> str:
    return hashlib.sha256(
        _PRIVATE_DECISION_DIGEST_PREFIX
        + _canonical_bytes(serialize_actor_visible_decision_state_v2(state))
    ).hexdigest()


def _v1_digest(value: object) -> str:
    return hashlib.sha256(_V1_DECISION_DIGEST_PREFIX + _canonical_bytes(value)).hexdigest()


def _frozen_v1_decision_digest(state: ActorVisibleDecisionStateV2) -> str:
    """Reconstruct the exact default-context C1 v1 decision digest from typed C1 v2."""
    public_state = project_c1v2_to_c1v1_public_state(state)
    own_private_state = project_c1v2_to_c1v1_own_private_state(state)
    sorted_snapshot = tuple(sorted(
        (action.action_key for action in state.legal_actions), key=lambda action_key: action_key.digest
    ))
    actor_view_digest = _v1_digest({
        "action_snapshot": [action_key.to_canonical_payload() for action_key in sorted_snapshot],
        "actor": state.information_view.actor,
        "limited_knowledge": {},
        "own_private_state": own_private_state,
        "public_state": public_state,
        "remaining_time_ms": None,
        "visible_history": [],
    })
    action_set_digest = _v1_digest(sorted(action_key.digest for action_key in sorted_snapshot))
    return _v1_digest({
        "actor_view_digest": actor_view_digest,
        "belief_summary": None,
        "legal_action_keys": sorted(action.action_key_digest for action in state.legal_actions),
        "metadata": {
            "action_set_digest": action_set_digest,
            "public_state_digest": _v1_digest(public_state),
            "schema_version": 1,
        },
    })


@dataclass(frozen=True, slots=True, repr=False, weakref_slot=True)
class RuntimeDecisionEnvelope:
    """A current C1 v2 decision with semantic-only policy access."""

    _state: ActorVisibleDecisionStateV2
    _extracted: ExtractedSpecialistModelInputV1
    _vocabulary: CardVocabularyV1
    decision_digest: str
    _identity: object = field(default_factory=object, init=False, repr=False, compare=False)

    @classmethod
    def from_actor_visible_state(
        cls,
        state: ActorVisibleDecisionStateV2,
        *,
        vocabulary: CardVocabularyV1,
    ) -> "RuntimeDecisionEnvelope":
        if not isinstance(state, ActorVisibleDecisionStateV2):
            raise RuntimeEnvelopeError("state must be a validated ActorVisibleDecisionStateV2")
        try:
            validate_actor_visible_decision_state_v2(state)
            extracted = extract_specialist_model_input_v1(state, vocabulary)
        except ValueError as exc:
            raise RuntimeEnvelopeError("state must be a validated ActorVisibleDecisionStateV2") from exc
        return cls(
            _state=state,
            _extracted=extracted,
            _vocabulary=vocabulary,
            decision_digest=_private_decision_digest(state),
        )

    def __post_init__(self) -> None:
        if type(self._state) is not ActorVisibleDecisionStateV2:
            raise RuntimeEnvelopeError("runtime envelope requires ActorVisibleDecisionStateV2")
        if type(self._extracted) is not ExtractedSpecialistModelInputV1:
            raise RuntimeEnvelopeError("runtime envelope requires extracted semantic model input")
        if type(self._vocabulary) is not CardVocabularyV1:
            raise RuntimeEnvelopeError("runtime envelope requires CardVocabularyV1")
        if (
            type(self.decision_digest) is not str
            or len(self.decision_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.decision_digest)
        ):
            raise RuntimeEnvelopeError("runtime decision digest must be lowercase 64-hex")
        try:
            validate_actor_visible_decision_state_v2(self._state)
            ExtractedSpecialistModelInputV1.__post_init__(self._extracted)
            expected = extract_specialist_model_input_v1(self._state, self._vocabulary)
        except ValueError as exc:
            raise RuntimeEnvelopeError("runtime envelope state/extracted input does not replay validate") from exc
        if self.decision_digest != _private_decision_digest(self._state):
            raise RuntimeEnvelopeError("runtime decision digest does not bind the full C1 v2 state")
        if (
            canonical_model_input_bytes_v1(expected.model_input)
            != canonical_model_input_bytes_v1(self._extracted.model_input)
            or expected.model_input_id != self._extracted.model_input_id
            or dict(expected.local_action_id_to_candidate_row_index)
            != dict(self._extracted.local_action_id_to_candidate_row_index)
        ):
            raise RuntimeEnvelopeError("runtime extracted input does not match the validated C1 v2 state")
        if len(self._state.legal_actions) > MAX_LEGAL_CANDIDATES_V2:
            raise RuntimeEnvelopeError("runtime envelope exceeds MAX_LEGAL_CANDIDATES_V2")
        _register_runtime_envelope_attestation(self)

    @property
    def candidate_count(self) -> int:
        _validate_runtime_envelope(self)
        return len(self._state.legal_actions)

    def build_step_input(self, selected_local_action_ids: tuple[str, ...]) -> SpecialistStepInputV1:
        _validate_runtime_envelope(self)
        if not isinstance(selected_local_action_ids, tuple) or any(type(value) is not str for value in selected_local_action_ids):
            raise RuntimeActionError("selected local action IDs must be a tuple of strings")
        selected = (
            tuple(sorted(selected_local_action_ids))
            if self._order_semantics == "unordered_set"
            else selected_local_action_ids
        )
        return _cached_runtime_step_input(self, selected)

    def _current_index_for_local_action_id(self, local_action_id: str) -> int:
        _validate_runtime_envelope(self)
        for index, candidate in enumerate(self._state.legal_actions):
            if candidate.local_action_id == local_action_id:
                return index
        raise RuntimeActionError("runtime complete action contains an unknown local action ID")

    def complete_action(self, local_action_ids: tuple[str, ...]) -> "RuntimeCompleteAction":
        """Build one current private action from trusted local candidate identities."""
        _validate_runtime_envelope(self)
        if not isinstance(local_action_ids, tuple) or any(type(value) is not str for value in local_action_ids):
            raise RuntimeActionError("local_action_ids must be a tuple of strings")
        canonical_ids = (
            tuple(sorted(local_action_ids))
            if self._order_semantics == "unordered_set"
            else local_action_ids
        )
        indices = (
            tuple(sorted(self._current_index_for_local_action_id(value) for value in canonical_ids))
            if self._order_semantics == "unordered_set"
            else tuple(self._current_index_for_local_action_id(value) for value in canonical_ids)
        )
        return RuntimeCompleteAction._issue(self, canonical_ids, indices)

    @property
    def _order_semantics(self) -> str:
        _validate_runtime_envelope(self)
        return _cached_runtime_step_input(self, ()).order_semantics

    def decode_option_indices(self, action: "RuntimeCompleteAction") -> tuple[int, ...]:
        """Return only the current CABT option indices after strict origin checks."""
        _validate_runtime_envelope(self)
        _require_current_runtime_action(self, action)
        expected = tuple(self._current_index_for_local_action_id(value) for value in action.local_action_ids)
        if self._order_semantics == "unordered_set":
            expected = tuple(sorted(expected))
        if action.option_indices != expected:
            raise RuntimeActionError("runtime complete action indices are stale for this envelope")
        return action.option_indices

    def to_public_trace_payload(self, action: object = None) -> dict[str, object]:
        """Refuse to turn a private envelope into an implicit public serializer."""
        del action
        raise RuntimeEnvelopeError(PRIVATE_ENVELOPE_SERIALIZATION_ERROR)

    def to_public_envelope(self) -> DecisionEnvelope:
        """Explicitly bridge only an injective, v1-sized decision to frozen C5."""
        _validate_runtime_envelope(self)
        if len(self._state.legal_actions) > 60:
            raise RuntimeEnvelopeError("public conversion requires frozen public-v1 limits")
        if self._state.public_collision_groups:
            raise RuntimeEnvelopeError("public conversion requires unique public projections")
        trace = self._state.to_public_trace_payload()
        try:
            metadata = trace["metadata"]
            public_state_digest = metadata["public_state_digest"]  # type: ignore[index]
            public_action_set_digest = metadata["public_action_set_digest"]  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise RuntimeEnvelopeError("validated C1 v2 state has incomplete public trace metadata") from exc
        view = self._state.information_view
        action_set_digest = hashlib.sha256(
            _V1_DECISION_DIGEST_PREFIX
            + _canonical_bytes(sorted(action.action_key_digest for action in self._state.legal_actions))
        ).hexdigest()
        envelope = DecisionEnvelope(
            selection_type=view.selection_type,
            decision_digest=_frozen_v1_decision_digest(self._state),
            action_set_digest=action_set_digest,
            candidates=tuple(
                Candidate(action.action_key_digest, option_index)
                for option_index, action in enumerate(self._state.legal_actions)
            ),
            min_count=view.min_count,
            max_count=view.max_count,
            order_semantics=self._order_semantics,  # type: ignore[arg-type]
            selection_context=view.selection_context,
        )
        projections = tuple(
            (
                action.action_key_digest,
                _canonical_bytes(action.action_key.to_public_trace_payload()).decode("utf-8"),
            )
            for action in self._state.legal_actions
        )
        identity_core = {
            "schema_version": 1,
            "public_state_digest": public_state_digest,
            "public_action_set_digest": public_action_set_digest,
            "selection_type": view.selection_type,
            "selection_context": view.selection_context,
            "min_count": view.min_count,
            "max_count": view.max_count,
            "order_semantics": self._order_semantics,
        }
        object.__setattr__(envelope, "_candidate_public_projection_json", projections)
        object.__setattr__(envelope, "_public_state_digest", public_state_digest)
        object.__setattr__(envelope, "_public_action_set_digest", public_action_set_digest)
        object.__setattr__(envelope, "_public_decision_identity", hashlib.sha256(
            _PUBLIC_TRACE_IDENTITY_PREFIX + _canonical_bytes(identity_core)
        ).hexdigest())
        return envelope

    def convert_to_public(
        self,
        action: "RuntimeCompleteAction",
    ) -> tuple[DecisionEnvelope, CompleteAction]:
        """Return the frozen public envelope/action only after explicit checks."""
        action = _require_current_runtime_action(self, action)
        self.decode_option_indices(action)
        public_envelope = self.to_public_envelope()
        private_to_public = {
            candidate.local_action_id: candidate.action_key_digest
            for candidate in self._state.legal_actions
        }
        public_keys = tuple(private_to_public[local_id] for local_id in action.local_action_ids)
        if self._order_semantics == "unordered_set":
            public_keys = tuple(sorted(public_keys))
            indices = tuple(sorted(action.option_indices))
        else:
            indices = action.option_indices
        return public_envelope, CompleteAction(
            envelope=public_envelope,
            keys=public_keys,
            option_indices=indices,
        )

    def collision_telemetry(self, action: "RuntimeCompleteAction") -> dict[str, object]:
        """Return the sole permitted aggregate collision telemetry shape."""
        _validate_runtime_envelope(self)
        action = _require_current_runtime_action(self, action)
        self.decode_option_indices(action)
        group_sizes = sorted(count for _public_id, count in self._state.public_collision_groups)
        return {
            "status": "duplicate-public-identity" if group_sizes else "representable",
            "selected_count": len(action.local_action_ids),
            "collision_group_sizes": group_sizes,
        }

    def __repr__(self) -> str:
        return (
            "RuntimeDecisionEnvelope("
            f"candidate_count={self.candidate_count}, decision_digest=<redacted>, "
            "private_state=<redacted>)"
        )


_RUNTIME_ENVELOPE_ATTESTATIONS: dict[int, tuple[object, ...]] = {}
_RUNTIME_ENVELOPE_STEP_INPUTS: dict[
    int, dict[tuple[str, ...], tuple[SpecialistStepInputV1, bytes]]
] = {}
_MAX_CACHED_STEP_INPUTS_PER_ENVELOPE = 1_024


def _vocabulary_attestation(vocabulary: CardVocabularyV1) -> tuple[object, ...]:
    return (
        vocabulary.recognized_card_ids, vocabulary.source_sha256,
        vocabulary.environment_version, vocabulary.usage_decision,
        vocabulary.test_only, vocabulary.permission_decision,
    )


def _runtime_envelope_attestation(envelope: RuntimeDecisionEnvelope) -> tuple[object, ...]:
    return (
        envelope,
        envelope._state,
        envelope._extracted,
        envelope._vocabulary,
        envelope._identity,
        envelope.decision_digest,
        envelope._extracted.model_input,
        envelope._extracted.model_input_id,
        envelope._extracted.local_action_id_to_candidate_row_index,
        _vocabulary_attestation(envelope._vocabulary),
    )


def _register_runtime_envelope_attestation(envelope: RuntimeDecisionEnvelope) -> None:
    envelope_id = id(envelope)
    current = _RUNTIME_ENVELOPE_ATTESTATIONS.get(envelope_id)
    if current is None:
        snapshot = _runtime_envelope_attestation(envelope)

        def release(finished_ref: object, *, claimed_id: int = envelope_id) -> None:
            current_attestation = _RUNTIME_ENVELOPE_ATTESTATIONS.get(claimed_id)
            if current_attestation is not None and current_attestation[0] is finished_ref:
                _RUNTIME_ENVELOPE_ATTESTATIONS.pop(claimed_id, None)
                _RUNTIME_ENVELOPE_STEP_INPUTS.pop(claimed_id, None)

        _RUNTIME_ENVELOPE_ATTESTATIONS[envelope_id] = (
            ref(envelope, release), *snapshot[1:]
        )
        _RUNTIME_ENVELOPE_STEP_INPUTS[envelope_id] = {}
    elif current[0]() is not envelope:  # type: ignore[operator]
        raise RuntimeEnvelopeError("runtime envelope identity registry collision")


def _validate_runtime_envelope_attestation(envelope: RuntimeDecisionEnvelope) -> None:
    expected = _RUNTIME_ENVELOPE_ATTESTATIONS.get(id(envelope))
    if expected is None or expected[0]() is not envelope:  # type: ignore[operator]
        raise RuntimeEnvelopeError("runtime envelope has no construction attestation")
    try:
        current = _runtime_envelope_attestation(envelope)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeEnvelopeError("runtime envelope attestation cannot be replayed") from exc
    if current[1] is not expected[1] or current[2] is not expected[2] or current[3] is not expected[3] or current[4] is not expected[4]:
        raise RuntimeEnvelopeError("runtime extracted input does not match the validated C1 v2 state")
    if current[5] != expected[5]:
        raise RuntimeEnvelopeError("runtime decision digest does not bind the full C1 v2 state")
    if current[6] is not expected[6] or current[7] != expected[7] or current[8] is not expected[8] or current[9] != expected[9]:
        raise RuntimeEnvelopeError("runtime extracted input does not match the validated C1 v2 state")


def _validate_runtime_envelope(envelope: RuntimeDecisionEnvelope) -> RuntimeDecisionEnvelope:
    if type(envelope) is not RuntimeDecisionEnvelope:
        raise RuntimeEnvelopeError("envelope must be a RuntimeDecisionEnvelope")
    _validate_runtime_envelope_attestation(envelope)
    return envelope


def _cached_runtime_step_input(
    envelope: RuntimeDecisionEnvelope,
    selected_local_action_ids: tuple[str, ...],
) -> SpecialistStepInputV1:
    """Build each reachable prefix once while detecting cache-object mutation.

    The shared legality primitive is quadratic for a forced unordered prefix.
    Runtime decode, probability replay, execution validation, and tracing query
    the same one or two prefixes repeatedly, so replaying that primitive on
    every query can exceed the frozen one-second deadline at the observed
    61/64/67-candidate tail.  This process-local cache is weak-lifecycle-bound
    to the attested envelope and stores canonical bytes alongside each frozen
    step so an ``object.__setattr__`` edit is rejected rather than reused.
    """
    _validate_runtime_envelope(envelope)
    cache = _RUNTIME_ENVELOPE_STEP_INPUTS.get(id(envelope))
    if cache is None:
        raise RuntimeEnvelopeError("runtime envelope step cache is not attested")
    cached = cache.get(selected_local_action_ids)
    if cached is not None:
        step_input, expected_bytes = cached
        try:
            current_bytes = canonical_step_input_bytes_v1(step_input)
        except SpecialistFeatureError as exc:
            raise RuntimeEnvelopeError("cached runtime step input was mutated") from exc
        if current_bytes != expected_bytes:
            raise RuntimeEnvelopeError("cached runtime step input was mutated")
        return step_input
    try:
        step_input = build_specialist_step_input_v1(
            envelope._extracted, selected_local_action_ids,
        )
        step_bytes = canonical_step_input_bytes_v1(step_input)
    except SpecialistFeatureError as exc:
        raise RuntimeActionError(
            "private action prefix is not legal for this envelope"
        ) from exc
    if len(cache) < _MAX_CACHED_STEP_INPUTS_PER_ENVELOPE:
        cache[selected_local_action_ids] = (step_input, step_bytes)
    return step_input


@dataclass(frozen=True, slots=True)
class _RuntimeActionProvenanceV2:
    """Private, out-of-object immutable commitment recorded only at issuance."""

    action_ref: ReferenceType["RuntimeCompleteAction"]
    envelope: RuntimeDecisionEnvelope
    envelope_identity: object
    decision_digest: str
    local_action_ids: tuple[str, ...]
    option_indices: tuple[int, ...]
    commitment: object


# The map is deliberately keyed by object identity rather than RuntimeCompleteAction
# equality/hash: callers can mutate a frozen object via object.__setattr__, whereas an
# identity-keyed record lets validation compare every original field without rewriting it.
_ISSUED_RUNTIME_ACTIONS: dict[int, _RuntimeActionProvenanceV2] = {}


def _drop_issued_runtime_action(action_ref: ReferenceType["RuntimeCompleteAction"], action_id: int) -> None:
    """Release the side-table record when its action is no longer live."""
    provenance = _ISSUED_RUNTIME_ACTIONS.get(action_id)
    if provenance is not None and provenance.action_ref is action_ref:
        _ISSUED_RUNTIME_ACTIONS.pop(action_id, None)


def _validate_runtime_action_shape(
    envelope: RuntimeDecisionEnvelope,
    local_action_ids: object,
    option_indices: object,
) -> None:
    """Validate one action's structural/execution shape without mutating provenance."""
    if type(envelope) is not RuntimeDecisionEnvelope:
        raise RuntimeActionError("runtime complete action requires RuntimeDecisionEnvelope")
    _validate_runtime_envelope(envelope)
    if not isinstance(local_action_ids, tuple) or any(type(value) is not str for value in local_action_ids):
        raise RuntimeActionError("runtime complete action local IDs must be a tuple of strings")
    if not isinstance(option_indices, tuple) or any(type(value) is not int or value < 0 for value in option_indices):
        raise RuntimeActionError("runtime complete action indices must be non-bool nonnegative ints")
    if len(local_action_ids) != len(option_indices):
        raise RuntimeActionError("runtime complete action IDs and indices must have equal length")
    if not envelope._state.information_view.min_count <= len(local_action_ids) <= envelope._state.information_view.max_count:
        raise RuntimeActionError("runtime complete action cardinality is outside envelope bounds")
    if len(set(local_action_ids)) != len(local_action_ids):
        raise RuntimeActionError("runtime complete action local IDs must be unique")
    if len(set(option_indices)) != len(option_indices):
        raise RuntimeActionError("runtime complete action indices must be unique")
    expected = tuple(envelope._current_index_for_local_action_id(value) for value in local_action_ids)
    if envelope._order_semantics == "unordered_set":
        if local_action_ids != tuple(sorted(local_action_ids)):
            raise RuntimeActionError("unordered runtime complete action local IDs must be ascending")
        if option_indices != tuple(sorted(expected)):
            raise RuntimeActionError("unordered runtime complete action indices must be numeric execution order")
    elif option_indices != expected:
        raise RuntimeActionError("ordered runtime complete action indices must preserve local-ID sequence order")


@dataclass(frozen=True, slots=True, weakref_slot=True, repr=False, eq=False)
class RuntimeCompleteAction:
    """A private execution selection that can only be issued once by its envelope.

    The private commitment is both stored on the action and mirrored in an
    identity-keyed registry.  Revalidation only compares against that original
    record; it never restores or rebases provenance after an object-level edit.
    """

    envelope: RuntimeDecisionEnvelope
    local_action_ids: tuple[str, ...]
    option_indices: tuple[int, ...]
    _origin_commitment: object = field(init=False, repr=False, compare=False)

    @classmethod
    def _issue(
        cls,
        envelope: RuntimeDecisionEnvelope,
        local_action_ids: tuple[str, ...],
        option_indices: tuple[int, ...],
    ) -> "RuntimeCompleteAction":
        """Create and register an action through the only trusted issuance path."""
        token = _ACTION_ISSUANCE_CONTEXT.set(_ACTION_ISSUANCE_SENTINEL)
        try:
            action = cls(envelope, local_action_ids, option_indices)
        finally:
            _ACTION_ISSUANCE_CONTEXT.reset(token)
        commitment = object()
        object.__setattr__(action, "_origin_commitment", commitment)
        action_id = id(action)
        action_ref = ref(
            action,
            lambda finished_ref: _drop_issued_runtime_action(finished_ref, action_id),
        )
        _ISSUED_RUNTIME_ACTIONS[action_id] = _RuntimeActionProvenanceV2(
            action_ref=action_ref, envelope=envelope, envelope_identity=envelope._identity,
            decision_digest=envelope.decision_digest, local_action_ids=local_action_ids,
            option_indices=option_indices, commitment=commitment,
        )
        return action

    def __post_init__(self) -> None:
        if _ACTION_ISSUANCE_CONTEXT.get() is _ACTION_ISSUANCE_SENTINEL:
            _validate_runtime_action_shape(self.envelope, self.local_action_ids, self.option_indices)
            return
        # Calling __post_init__ directly is revalidation only.  It must never
        # initialize, reset, or otherwise repair the issuance commitment.
        _require_issued_runtime_action(self)

    def __repr__(self) -> str:
        return "RuntimeCompleteAction(selection=<redacted>, envelope=<redacted>)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RuntimeCompleteAction) and self.local_action_ids == other.local_action_ids

    def __hash__(self) -> int:
        return hash(self.local_action_ids)

    def to_public_trace_payload(self) -> dict[str, object]:
        raise RuntimeActionError(PRIVATE_ENVELOPE_SERIALIZATION_ERROR)


def _require_issued_runtime_action(action: RuntimeCompleteAction) -> _RuntimeActionProvenanceV2:
    """Return the exact issuance record or reject missing/rebound/mutated state."""
    provenance = _ISSUED_RUNTIME_ACTIONS.get(id(action))
    if provenance is None or provenance.action_ref() is not action:
        # Avoid retaining an old record if CPython has reused an object id.
        if provenance is not None:
            _ISSUED_RUNTIME_ACTIONS.pop(id(action), None)
        raise RuntimeActionError("runtime complete action was not issued by its envelope")
    try:
        commitment = action._origin_commitment
    except AttributeError as exc:
        raise RuntimeActionError("runtime complete action provenance is missing") from exc
    if commitment is not provenance.commitment:
        raise RuntimeActionError("runtime complete action provenance does not match its issuance")
    if action.envelope is not provenance.envelope or provenance.envelope_identity is not provenance.envelope._identity:
        raise RuntimeActionError("runtime complete action is stale for its issuing envelope")
    if provenance.decision_digest != provenance.envelope.decision_digest:
        raise RuntimeActionError("runtime complete action provenance has a stale decision digest")
    if (
        action.local_action_ids != provenance.local_action_ids
        or action.option_indices != provenance.option_indices
    ):
        raise RuntimeActionError(
            "runtime complete action provenance no longer matches its execution order"
        )
    _validate_runtime_action_shape(
        provenance.envelope, action.local_action_ids, action.option_indices
    )
    return provenance


def _require_current_runtime_action(
    envelope: RuntimeDecisionEnvelope,
    action: object,
) -> RuntimeCompleteAction:
    _validate_runtime_envelope(envelope)
    if type(action) is not RuntimeCompleteAction:
        raise RuntimeActionError("runtime complete action must be a RuntimeCompleteAction")
    provenance = _require_issued_runtime_action(action)
    if provenance.envelope is not envelope or provenance.envelope_identity is not envelope._identity:
        raise RuntimeActionError("runtime complete action is stale for this envelope")
    return action


@dataclass(frozen=True, slots=True, repr=False)
class SemanticRuntimeCompleteActionV2:
    """Serial-free semantic event whose probability is normalized class-first.

    It intentionally has no local action IDs, CABT indices, decision digest, or
    envelope reference.  A runtime envelope is supplied separately for legality
    and evaluation, so alias multiplicity can never create extra probability mass.
    """

    order_semantics: str
    semantic_selection: tuple[SemanticActionV1, ...]

    def __post_init__(self) -> None:
        if self.order_semantics not in {"unordered_set", "ordered_sequence"}:
            raise RuntimeActionError("semantic runtime complete action has invalid order semantics")
        if type(self.semantic_selection) is not tuple or any(
            type(row) is not SemanticActionV1 for row in self.semantic_selection
        ):
            raise RuntimeActionError("semantic runtime complete action must contain semantic rows")
        for row in self.semantic_selection:
            SemanticActionV1.__post_init__(row)
        if self.order_semantics == "unordered_set" and tuple(
            sorted(self.semantic_selection, key=lambda row: row.canonical_bytes)
        ) != self.semantic_selection:
            raise RuntimeActionError("unordered semantic runtime selection must be canonical")

    def __repr__(self) -> str:
        return "SemanticRuntimeCompleteActionV2(selection=<serial-free>)"


def semantic_runtime_complete_action_from_runtime_action_v2(
    envelope: RuntimeDecisionEnvelope,
    action: RuntimeCompleteAction,
) -> SemanticRuntimeCompleteActionV2:
    """Erase private aliases only after exact issuance and execution validation."""
    action = _require_current_runtime_action(envelope, action)
    envelope.decode_option_indices(action)
    local_ids = _semantic_execution_order(envelope, action.local_action_ids)
    return SemanticRuntimeCompleteActionV2(
        order_semantics=envelope._order_semantics,
        semantic_selection=tuple(_semantic_row_for_local_id(envelope, local_id) for local_id in local_ids),
    )


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeScoredSemanticCompleteActionV2:
    """A class-level scored event plus its deterministic private execution representative.

    ``log_probability`` belongs exclusively to ``semantic_action``.  The
    representative is selected only after the semantic class sequence has been
    chosen and must not be interpreted as a separately normalized physical event.
    """

    semantic_action: SemanticRuntimeCompleteActionV2
    representative_action: RuntimeCompleteAction
    log_probability: float

    def __post_init__(self) -> None:
        if type(self.semantic_action) is not SemanticRuntimeCompleteActionV2:
            raise RuntimeActionError("scored runtime action must contain SemanticRuntimeCompleteActionV2")
        if type(self.representative_action) is not RuntimeCompleteAction:
            raise RuntimeActionError("scored runtime action must contain RuntimeCompleteAction")
        envelope = self.representative_action.envelope
        _require_current_runtime_action(envelope, self.representative_action)
        if (
            semantic_runtime_complete_action_from_runtime_action_v2(
                envelope, self.representative_action
            )
            != self.semantic_action
        ):
            raise RuntimeActionError("scored semantic action does not match its execution representative")
        if (
            type(self.log_probability) is bool
            or not isinstance(self.log_probability, Real)
            or not isfinite(float(self.log_probability))
        ):
            raise RuntimePolicyError("runtime log probability must be finite numeric")
        object.__setattr__(self, "log_probability", float(self.log_probability))

    @property
    def action(self) -> RuntimeCompleteAction:
        """Compatibility accessor for the deterministic execution representative only."""
        return self.representative_action

    def __repr__(self) -> str:
        return "RuntimeScoredSemanticCompleteActionV2(action=<redacted>, log_probability=<semantic>)"


# The compatibility name retains existing call sites while its fields/documentation
# make the probability domain explicit.
RuntimeScoredCompleteActionV2 = RuntimeScoredSemanticCompleteActionV2


def _evaluate_runtime_step(
    envelope: RuntimeDecisionEnvelope,
    prefix: tuple[str, ...],
    policy: SpecialistStepLogitPolicyV1,
):
    if not isinstance(policy, SpecialistStepLogitPolicyV1):
        raise RuntimePolicyError("policy must implement SpecialistStepLogitPolicyV1")
    step = envelope.build_step_input(prefix)
    try:
        return evaluate_specialist_step_v1(policy, envelope._extracted, step)
    except SpecialistFeatureError as exc:
        raise RuntimePolicyError("policy logits violate the semantic step domain") from exc


def _step_log_normalizer(
    *,
    semantic_logits: tuple[float, ...],
    stop_logit: float | None,
) -> tuple[float, tuple[float, ...]]:
    scores = semantic_logits if stop_logit is None else (*semantic_logits, stop_logit)
    if not scores:
        raise RuntimePolicyError("non-forced semantic step has no token logits")
    maximum = max(scores)
    normalizer = maximum + log(sum(exp(score - maximum) for score in scores))
    if not isfinite(normalizer):
        raise RuntimePolicyError("semantic token normalization is not finite")
    return normalizer, scores


def _semantic_row_for_local_id(envelope: RuntimeDecisionEnvelope, local_action_id: str):
    try:
        index = envelope._extracted.local_action_id_to_candidate_row_index[local_action_id]
        return envelope._extracted.model_input.candidate_rows[index]
    except (KeyError, IndexError) as exc:
        raise RuntimeActionError("runtime complete action contains an unknown local action ID") from exc


def _semantic_execution_order(
    envelope: RuntimeDecisionEnvelope,
    local_action_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if envelope._order_semantics == "ordered_sequence":
        return local_action_ids
    return tuple(sorted(
        local_action_ids,
        key=lambda local_id: (_semantic_row_for_local_id(envelope, local_id).canonical_bytes, local_id),
    ))


def _class_log_probability(
    evaluated,
    *,
    semantic_row: object | None,
    stop: bool,
) -> float:
    if evaluated.forced_stop:
        if stop:
            return 0.0
        raise RuntimeActionError("a forced STOP step cannot select a semantic class")
    normalizer, _scores = _step_log_normalizer(
        semantic_logits=evaluated.semantic_logits, stop_logit=evaluated.stop_logit,
    )
    if stop:
        if evaluated.stop_logit is None:
            raise RuntimeActionError("STOP is illegal for this runtime prefix")
        return evaluated.stop_logit - normalizer
    for semantic_class, score in zip(
        evaluated.step_input.allowed_semantic_classes, evaluated.semantic_logits
    ):
        if semantic_class.semantic_row == semantic_row:
            return score - normalizer
    raise RuntimeActionError("runtime complete action selects an illegal semantic class")


def greedy_decode_runtime_action_v2(
    envelope: RuntimeDecisionEnvelope,
    *,
    policy: SpecialistStepLogitPolicyV1,
) -> RuntimeCompleteAction:
    """Decode semantic classes greedily, retaining physical aliases only locally."""
    _validate_runtime_envelope(envelope)
    prefix: tuple[str, ...] = ()
    while True:
        evaluated = _evaluate_runtime_step(envelope, prefix, policy)
        step = evaluated.step_input
        if evaluated.forced_stop:
            return envelope.complete_action(prefix)
        choices: list[tuple[float, int, bytes, object]] = [
            (score, 0, semantic_class.semantic_row.canonical_bytes, semantic_class.semantic_row)
            for score, semantic_class in zip(evaluated.semantic_logits, step.allowed_semantic_classes)
        ]
        if step.stop_available:
            assert evaluated.stop_logit is not None
            choices.append((evaluated.stop_logit, 1, b"", None))
        if not choices:
            raise RuntimePolicyError("semantic step has neither an allowed class nor STOP")
        best_score = max(score for score, _kind, _key, _value in choices)
        _score, kind, _key, semantic_row = min(
            choice for choice in choices if choice[0] == best_score
        )
        if kind == 1:
            return envelope.complete_action(prefix)
        assert semantic_row is not None
        try:
            alias = choose_lexicographic_alias_v1(envelope._extracted, prefix, semantic_row)
        except SpecialistFeatureError as exc:
            raise RuntimePolicyError("selected semantic class has no legal private alias") from exc
        prefix = (*prefix, alias)


def runtime_semantic_complete_action_log_probability_v2(
    envelope: RuntimeDecisionEnvelope,
    semantic_action: SemanticRuntimeCompleteActionV2,
    *,
    policy: SpecialistStepLogitPolicyV1,
) -> float:
    """Return normalized class-level log mass for one serial-free semantic event.

    This is the only probability API.  It replay-validates each semantic token
    against the shared step primitive, then chooses the lexicographic private
    alias solely to continue that semantic path.  Consequently A1/A2 aliases
    share one event and an unordered A,A selection is one canonical multiset.
    """
    _validate_runtime_envelope(envelope)
    if type(semantic_action) is not SemanticRuntimeCompleteActionV2:
        raise RuntimeActionError("log probability requires SemanticRuntimeCompleteActionV2")
    SemanticRuntimeCompleteActionV2.__post_init__(semantic_action)
    if semantic_action.order_semantics != envelope._order_semantics:
        raise RuntimeActionError("semantic runtime complete action order semantics do not match envelope")
    prefix: tuple[str, ...] = ()
    result = 0.0
    for semantic_row in semantic_action.semantic_selection:
        evaluated = _evaluate_runtime_step(envelope, prefix, policy)
        result += _class_log_probability(evaluated, semantic_row=semantic_row, stop=False)
        try:
            alias = choose_lexicographic_alias_v1(
                envelope._extracted, prefix, semantic_row
            )
        except SpecialistFeatureError as exc:
            raise RuntimeActionError(
                "semantic runtime complete action has no legal deterministic alias"
            ) from exc
        prefix = (*prefix, alias)
    final = _evaluate_runtime_step(envelope, prefix, policy)
    result += _class_log_probability(final, semantic_row=None, stop=True)
    if not isfinite(result):
        raise RuntimePolicyError("runtime semantic-complete-action log probability is not finite")
    return result


def runtime_complete_action_log_probability_v2(
    envelope: RuntimeDecisionEnvelope,
    action: RuntimeCompleteAction,
    *,
    policy: SpecialistStepLogitPolicyV1,
) -> float:
    """Reject physical-action probability requests; aliases are not probability events.

    Use :func:`semantic_runtime_complete_action_from_runtime_action_v2` followed
    by :func:`runtime_semantic_complete_action_log_probability_v2`.  This
    explicit conversion prevents callers from summing duplicate alias mass.
    """
    del envelope, action, policy
    raise RuntimeActionError(
        "physical RuntimeCompleteAction has no probability; use a semantic-complete-action identity"
    )


def sample_runtime_action_v2(
    envelope: RuntimeDecisionEnvelope,
    *,
    policy: SpecialistStepLogitPolicyV1,
    rng: RuntimeRandomV1,
) -> RuntimeCompleteAction:
    """Sample semantic classes with caller-injected entropy, then choose one local alias."""
    _validate_runtime_envelope(envelope)
    if not isinstance(rng, RuntimeRandomV1):
        raise RuntimePolicyError("rng must implement RuntimeRandomV1")
    prefix: tuple[str, ...] = ()
    while True:
        evaluated = _evaluate_runtime_step(envelope, prefix, policy)
        step = evaluated.step_input
        if evaluated.forced_stop:
            return envelope.complete_action(prefix)
        normalizer, scores = _step_log_normalizer(
            semantic_logits=evaluated.semantic_logits, stop_logit=evaluated.stop_logit,
        )
        probabilities = tuple(exp(score - normalizer) for score in scores)
        if any(not isfinite(probability) or probability <= 0.0 for probability in probabilities):
            raise RuntimePolicyError(_NONREPRESENTABLE_SAMPLING_MASS_ERROR)
        sample = rng.random()
        if type(sample) is bool or not isinstance(sample, Real) or not isfinite(float(sample)) or not 0.0 <= float(sample) < 1.0:
            raise RuntimePolicyError("rng.random() must return a finite non-bool number in [0, 1)")
        draw = float(sample)
        cumulative = 0.0
        selected_index = len(scores) - 1
        for index, probability in enumerate(probabilities):
            cumulative += probability
            if draw < cumulative:
                selected_index = index
                break
        if selected_index == len(evaluated.semantic_logits):
            return envelope.complete_action(prefix)
        semantic_row = step.allowed_semantic_classes[selected_index].semantic_row
        try:
            alias = choose_lexicographic_alias_v1(envelope._extracted, prefix, semantic_row)
        except SpecialistFeatureError as exc:
            raise RuntimePolicyError("selected semantic class has no legal private alias") from exc
        prefix = (*prefix, alias)


def _require_beam_width(width: object) -> int:
    if type(width) is not int or not 1 <= width <= MAX_LEGAL_CANDIDATES_V2:
        raise RuntimePolicyError("beam_width must be a non-bool int in 1..512")
    return width


def _beam_node_sort_key(node: tuple[tuple[str, ...], float]) -> tuple[float, tuple[str, ...]]:
    return (-node[1], node[0])


def beam_search_runtime_actions_v2(
    envelope: RuntimeDecisionEnvelope,
    *,
    policy: SpecialistStepLogitPolicyV1,
    beam_width: int,
) -> tuple[RuntimeScoredCompleteActionV2, ...]:
    """Perform bounded class-first beam decoding without complete-action enumeration."""
    _validate_runtime_envelope(envelope)
    beam_width = _require_beam_width(beam_width)
    active: list[tuple[tuple[str, ...], float]] = [((), 0.0)]
    complete: list[RuntimeScoredCompleteActionV2] = []
    while active:
        next_active: list[tuple[tuple[str, ...], float]] = []
        for prefix, prefix_log_probability in active:
            evaluated = _evaluate_runtime_step(envelope, prefix, policy)
            step = evaluated.step_input
            if evaluated.forced_stop:
                representative_action = envelope.complete_action(prefix)
                complete.append(RuntimeScoredSemanticCompleteActionV2(
                    semantic_action=semantic_runtime_complete_action_from_runtime_action_v2(
                        envelope, representative_action
                    ),
                    representative_action=representative_action,
                    log_probability=prefix_log_probability,
                ))
                continue
            normalizer, _scores = _step_log_normalizer(
                semantic_logits=evaluated.semantic_logits, stop_logit=evaluated.stop_logit,
            )
            for semantic_class, score in zip(step.allowed_semantic_classes, evaluated.semantic_logits):
                try:
                    alias = choose_lexicographic_alias_v1(
                        envelope._extracted, prefix, semantic_class.semantic_row
                    )
                except SpecialistFeatureError as exc:
                    raise RuntimePolicyError("beam semantic class has no legal private alias") from exc
                next_active.append(((*prefix, alias), prefix_log_probability + score - normalizer))
            if evaluated.stop_logit is not None:
                representative_action = envelope.complete_action(prefix)
                complete.append(RuntimeScoredSemanticCompleteActionV2(
                    semantic_action=semantic_runtime_complete_action_from_runtime_action_v2(
                        envelope, representative_action
                    ),
                    representative_action=representative_action,
                    log_probability=prefix_log_probability + evaluated.stop_logit - normalizer,
                ))
        complete.sort(key=lambda item: (-item.log_probability, item.action.local_action_ids))
        del complete[beam_width:]
        next_active.sort(key=_beam_node_sort_key)
        active = next_active[:beam_width]
    return tuple(complete)


def _require_enumeration_limit(limit: object) -> int:
    if type(limit) is not int or limit < 1:
        raise RuntimeEnumerationError("enumeration limit must be a positive non-bool int")
    if limit > MAX_EXACT_COMPLETE_ACTIONS_V2:
        raise RuntimeEnumerationError("enumeration limit exceeds the hard maximum of 65,536 complete actions")
    return limit


def _runtime_enumeration_count(envelope: RuntimeDecisionEnvelope, *, cap: int) -> int:
    view = envelope._state.information_view
    count = 0
    counter = perm if envelope._order_semantics == "ordered_sequence" else comb
    for selection_count in range(view.min_count, view.max_count + 1):
        term = counter(envelope.candidate_count, selection_count)
        if term >= cap - count:
            return cap
        count += term
    return count


def enumerate_runtime_complete_actions_v2(
    envelope: RuntimeDecisionEnvelope,
    *,
    limit: int,
) -> tuple[RuntimeCompleteAction, ...]:
    """Materialize only a bounded exact local teacher domain, never inference."""
    if not isinstance(envelope, RuntimeDecisionEnvelope):
        raise RuntimeEnvelopeError("envelope must be a RuntimeDecisionEnvelope")
    limit = _require_enumeration_limit(limit)
    count = _runtime_enumeration_count(envelope, cap=limit + 1)
    if count > limit:
        raise RuntimeEnumerationError(
            f"complete-action enumeration count {count} exceeds limit {limit}"
        )
    local_action_ids = tuple(sorted(action.local_action_id for action in envelope._state.legal_actions))
    generator = (
        permutations(local_action_ids, selection_count)
        if envelope._order_semantics == "ordered_sequence"
        else combinations(local_action_ids, selection_count)
        for selection_count in range(
            envelope._state.information_view.min_count,
            envelope._state.information_view.max_count + 1,
        )
    )
    return tuple(
        envelope.complete_action(selection)
        for selections_at_count in generator
        for selection in selections_at_count
    )


__all__ = [
    "MAX_LEGAL_CANDIDATES_V2",
    "MAX_EXACT_COMPLETE_ACTIONS_V2",
    "PRIVATE_ENVELOPE_SERIALIZATION_ERROR",
    "RuntimeActionError",
    "RuntimeCompleteAction",
    "RuntimeDecisionEnvelope",
    "RuntimeEnumerationError",
    "RuntimeEnvelopeError",
    "RuntimePolicyError",
    "RuntimeRandomV1",
    "RuntimeScoredCompleteActionV2",
    "RuntimeScoredSemanticCompleteActionV2",
    "SemanticRuntimeCompleteActionV2",
    "beam_search_runtime_actions_v2",
    "greedy_decode_runtime_action_v2",
    "enumerate_runtime_complete_actions_v2",
    "runtime_semantic_complete_action_log_probability_v2",
    "sample_runtime_action_v2",
    "semantic_runtime_complete_action_from_runtime_action_v2",
]
