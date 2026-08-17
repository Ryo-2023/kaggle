"""Complete-action contracts for variable-cardinality CABT selections.

This module deliberately depends only on the privacy-safe ``decision_state``
boundary and Python's standard library.  A complete action is always built
from the current legal candidates; it never preserves an engine option order
as its stable identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
from itertools import combinations, permutations
import json
from math import comb, exp, isfinite, log, perm
from numbers import Real
from typing import Literal

from mage_ptcg.decision_state import (
    ACTION_KEY_SCHEMA_VERSION,
    DecisionState,
    LegalAction,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1,
    CABT_AGENT_JSON_SELECTION_CONTEXTS_V1,
    CABT_AGENT_JSON_UNORDERED_SELECTION_SCHEMAS_V1,
    is_ordered_selection,
)


OrderSemantics = Literal["unordered_set", "ordered_sequence"]
STOP_TOKEN = "__STOP__"
StepLogits = Callable[[tuple[str, ...], tuple[str, ...]], Mapping[str, object]]
_PUBLIC_TRACE_SCHEMA_VERSION = 1
_PUBLIC_TRACE_IDENTITY_PREFIX = b"mage_ptcg.meta_specialist.complete_action_trace:v1\0"
_MAX_LEGAL_CANDIDATES = 60
_MAX_COMPLETE_ACTIONS = 65_536

_ORDERED_SELECTION_SCHEMAS = CABT_AGENT_JSON_ORDERED_SELECTION_SCHEMAS_V1
_UNORDERED_SELECTION_SCHEMAS = CABT_AGENT_JSON_UNORDERED_SELECTION_SCHEMAS_V1


class DecisionEnvelopeError(ValueError):
    """Raised when a legal-candidate envelope is malformed or unsafe."""


class CompleteActionError(ValueError):
    """Raised when a complete action is not legal for its current envelope."""


class CompleteActionEnumerationError(ValueError):
    """Raised before a complete-action enumeration exceeds its hard limit."""


class CompleteActionProbabilityError(ValueError):
    """Raised when a complete-action policy score is malformed or nonfinite."""


def resolve_order_semantics(
    selection_type: object,
    selection_context: object,
) -> OrderSemantics:
    """Resolve a CABT schema's order contract from the audited native mapping."""
    if type(selection_type) is not int or type(selection_context) is not int:
        raise DecisionEnvelopeError("selection type and context must be non-bool ints")
    try:
        return "ordered_sequence" if is_ordered_selection(
            selection_type, selection_context
        ) else "unordered_set"
    except ValueError as exc:
        raise DecisionEnvelopeError(
            f"selection schema {(selection_type, selection_context)!r} is unclassified for order semantics"
        ) from exc


def _canonical_public_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionEnvelopeError("public projection is not canonical JSON") from exc


def _public_trace_identity(
    *,
    public_state_digest: str,
    public_action_set_digest: str,
    selection_type: object,
    selection_context: object,
    min_count: int,
    max_count: int,
    order_semantics: OrderSemantics,
) -> str:
    """Hash only the order-independent public decision subset for persistence."""
    identity_payload = {
        "schema_version": _PUBLIC_TRACE_SCHEMA_VERSION,
        "public_state_digest": public_state_digest,
        "public_action_set_digest": public_action_set_digest,
        "selection_type": selection_type,
        "selection_context": selection_context,
        "min_count": min_count,
        "max_count": max_count,
        "order_semantics": order_semantics,
    }
    return hashlib.sha256(
        _PUBLIC_TRACE_IDENTITY_PREFIX
        + _canonical_public_json(identity_payload).encode("utf-8")
    ).hexdigest()


def _require_non_bool_int(value: object, *, field_name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DecisionEnvelopeError(
            f"{field_name} must be a non-bool int at least {minimum}"
        )
    return value


def _require_digest(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DecisionEnvelopeError(f"{field_name} must be a lowercase 64-hex digest")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class Candidate:
    """A stable legal candidate paired with its current CABT option index."""

    stable_key: str
    option_index: int
    _synthetic: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_digest(self.stable_key, field_name="stable_key")
        _require_non_bool_int(self.option_index, field_name="option_index")

    def __repr__(self) -> str:
        return "Candidate(<redacted>)"

    @classmethod
    def _for_test(cls, stable_key: object, option_index: object) -> "Candidate":
        """Build a short-key fixture candidate without weakening production input."""
        if type(stable_key) is not str or not stable_key:
            raise DecisionEnvelopeError("test stable_key must be a nonempty string")
        option_index = _require_non_bool_int(option_index, field_name="option_index")
        candidate = object.__new__(cls)
        object.__setattr__(candidate, "stable_key", stable_key)
        object.__setattr__(candidate, "option_index", option_index)
        object.__setattr__(candidate, "_synthetic", True)
        return candidate


@dataclass(frozen=True, slots=True, repr=False)
class DecisionEnvelope:
    """Immutable complete-selection domain for one current CABT decision."""

    selection_type: object
    decision_digest: str
    action_set_digest: str
    candidates: tuple[Candidate, ...]
    min_count: int
    max_count: int
    order_semantics: OrderSemantics
    selection_context: object = 0
    _identity: object = field(default_factory=object, init=False, repr=False, compare=False)
    _candidate_public_projection_json: tuple[tuple[str, str], ...] = field(
        default=(), init=False, repr=False, compare=False
    )
    _public_state_digest: str | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _public_action_set_digest: str | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _public_decision_identity: str | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _require_digest(self.decision_digest, field_name="decision_digest")
        _require_digest(self.action_set_digest, field_name="action_set_digest")
        if self.order_semantics not in ("unordered_set", "ordered_sequence"):
            raise DecisionEnvelopeError("order_semantics must be unordered_set or ordered_sequence")
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(candidate, Candidate) for candidate in self.candidates
        ):
            raise DecisionEnvelopeError("candidates must be a tuple of Candidate instances")
        if len(self.candidates) > _MAX_LEGAL_CANDIDATES:
            raise DecisionEnvelopeError("DecisionEnvelope accepts at most 60 legal candidates")
        keys = tuple(candidate.stable_key for candidate in self.candidates)
        indices = tuple(candidate.option_index for candidate in self.candidates)
        if len(set(keys)) != len(keys):
            raise DecisionEnvelopeError("candidate stable keys must be unique")
        if len(set(indices)) != len(indices):
            raise DecisionEnvelopeError("candidate option indices must be unique")
        minimum = _require_non_bool_int(self.min_count, field_name="min_count")
        maximum = _require_non_bool_int(self.max_count, field_name="max_count")
        if not minimum <= maximum <= len(self.candidates):
            raise DecisionEnvelopeError("selection bounds are inconsistent with candidates")

    @classmethod
    def for_test(
        cls,
        *,
        selection_type: object,
        candidates: tuple[tuple[str, int], ...],
        min_count: int,
        max_count: int,
        order_semantics: OrderSemantics,
        selection_context: object = 0,
    ) -> "DecisionEnvelope":
        """Create a non-persistable fixture envelope with short stable keys."""
        if not isinstance(candidates, tuple) or any(
            not isinstance(candidate, tuple) or len(candidate) != 2
            for candidate in candidates
        ):
            raise DecisionEnvelopeError("test candidates must be (stable_key, option_index) tuples")
        return cls(
            selection_type=selection_type,
            decision_digest="0" * 64,
            action_set_digest="f" * 64,
            candidates=tuple(
                Candidate._for_test(stable_key, option_index)
                for stable_key, option_index in candidates
            ),
            min_count=min_count,
            max_count=max_count,
            order_semantics=order_semantics,
            selection_context=selection_context,
        )

    @classmethod
    def from_decision_state(
        cls,
        decision: DecisionState,
        *,
        min_count: int | None = None,
        max_count: int | None = None,
        order_semantics: OrderSemantics | None = None,
    ) -> "DecisionEnvelope":
        """Adapt the current legal CABT candidates without trusting caller bounds."""
        if not isinstance(decision, DecisionState):
            raise DecisionEnvelopeError("decision must be a DecisionState")
        public = decision.normalized_public_observation
        try:
            select = public["select"]
            authoritative_minimum = select["min_count"]
            authoritative_maximum = select["max_count"]
            authoritative_type = select["type"]
            authoritative_context = select["context"]
            authoritative_option_count = select["option_count"]
        except (KeyError, TypeError) as exc:
            raise DecisionEnvelopeError("decision has no authoritative select projection") from exc
        authoritative_minimum = _require_non_bool_int(
            authoritative_minimum, field_name="authoritative min_count"
        )
        authoritative_maximum = _require_non_bool_int(
            authoritative_maximum, field_name="authoritative max_count"
        )
        authoritative_option_count = _require_non_bool_int(
            authoritative_option_count, field_name="authoritative option_count"
        )
        for supplied, authoritative, name in (
            (min_count, authoritative_minimum, "min_count"),
            (max_count, authoritative_maximum, "max_count"),
        ):
            if supplied is not None:
                _require_non_bool_int(supplied, field_name=name)
                if supplied != authoritative:
                    raise DecisionEnvelopeError(
                        f"{name} must equal the authoritative CABT value"
                    )
        if authoritative_option_count != len(decision.legal_actions):
            raise DecisionEnvelopeError(
                "authoritative option_count does not match legal_actions"
            )
        if not all(isinstance(action, LegalAction) for action in decision.legal_actions):
            raise DecisionEnvelopeError("legal_actions must contain LegalAction instances")
        if any(
            action.action_key.action_key_schema_version != ACTION_KEY_SCHEMA_VERSION
            or action.action_key.feature_only_legacy_v1
            for action in decision.legal_actions
        ):
            raise DecisionEnvelopeError(
                "DecisionEnvelope requires only ActionKey schema version 2"
            )
        if any(
            type(action.option_index) is not int
            or not 0 <= action.option_index < authoritative_option_count
            for action in decision.legal_actions
        ):
            raise DecisionEnvelopeError(
                "every LegalAction option_index must be in the current legal range"
            )
        if any(
            action.action_key.selection_type != authoritative_type
            for action in decision.legal_actions
        ):
            raise DecisionEnvelopeError(
                "every LegalAction selection_type must match the authoritative select type"
            )
        if any(
            action.action_key.context != authoritative_context
            for action in decision.legal_actions
        ):
            raise DecisionEnvelopeError(
                "every LegalAction context must match the authoritative select context"
            )
        authoritative_order_semantics = resolve_order_semantics(
            authoritative_type,
            authoritative_context,
        )
        if order_semantics is not None and order_semantics != authoritative_order_semantics:
            raise DecisionEnvelopeError(
                "order_semantics must equal the authoritative CABT JSON schema"
            )
        envelope = cls(
            selection_type=authoritative_type,
            decision_digest=decision.digest,
            action_set_digest=decision.metadata.action_set_digest,
            candidates=tuple(
                Candidate(action.action_key.digest, action.option_index)
                for action in decision.legal_actions
            ),
            min_count=authoritative_minimum,
            max_count=authoritative_maximum,
            order_semantics=authoritative_order_semantics,
            selection_context=authoritative_context,
        )
        decision_trace = decision.to_trace_payload()
        try:
            metadata = decision_trace["metadata"]
            public_state_digest = metadata["public_state_digest"]
            public_action_set_digest = metadata["public_action_set_digest"]
        except (KeyError, TypeError) as exc:
            raise DecisionEnvelopeError("decision trace metadata is incomplete") from exc
        _require_digest(public_state_digest, field_name="public_state_digest")
        _require_digest(public_action_set_digest, field_name="public_action_set_digest")
        projections = tuple(
            (
                action.action_key.digest,
                _canonical_public_json(action.action_key.to_public_trace_payload()),
            )
            for action in decision.legal_actions
        )
        if len({projection for _, projection in projections}) != len(projections):
            raise DecisionEnvelopeError(
                "production candidates have indistinguishable public projections"
            )
        identity = _public_trace_identity(
            public_state_digest=public_state_digest,
            public_action_set_digest=public_action_set_digest,
            selection_type=authoritative_type,
            selection_context=authoritative_context,
            min_count=authoritative_minimum,
            max_count=authoritative_maximum,
            order_semantics=authoritative_order_semantics,
        )
        object.__setattr__(envelope, "_candidate_public_projection_json", projections)
        object.__setattr__(envelope, "_public_state_digest", public_state_digest)
        object.__setattr__(envelope, "_public_action_set_digest", public_action_set_digest)
        object.__setattr__(envelope, "_public_decision_identity", identity)
        return envelope

    def __repr__(self) -> str:
        return (
            "DecisionEnvelope("
            f"selection_type={self.selection_type!r}, candidate_count={len(self.candidates)}, "
            f"selection_context={self.selection_context!r}, "
            f"min_count={self.min_count}, max_count={self.max_count}, "
            f"order_semantics={self.order_semantics!r}, "
            "decision_digest=<redacted>, action_set_digest=<redacted>, "
            "candidates=<redacted>)"
        )

    @property
    def canonical_keys(self) -> tuple[str, ...]:
        return tuple(sorted(candidate.stable_key for candidate in self.candidates))

    def index_for_key(self, stable_key: str) -> int:
        for candidate in self.candidates:
            if candidate.stable_key == stable_key:
                return candidate.option_index
        raise CompleteActionError("complete action contains an unknown stable key")

    def to_public_trace_payload(self, action: "CompleteAction") -> dict[str, object]:
        """Return a positive public trace without private digest or index material."""
        _require_current_action(self, action)
        if (
            self._public_decision_identity is None
            or self._public_state_digest is None
            or self._public_action_set_digest is None
            or len(self._candidate_public_projection_json) != len(self.candidates)
        ):
            raise DecisionEnvelopeError("non-persistable envelope has no safe public projections")
        projection_for_key = dict(self._candidate_public_projection_json)
        try:
            selected_projection_json = [projection_for_key[key] for key in action.keys]
        except KeyError as exc:
            raise CompleteActionError("complete action contains an unknown stable key") from exc
        if self.order_semantics == "unordered_set":
            selected_projection_json.sort()
        return {
            "schema_version": _PUBLIC_TRACE_SCHEMA_VERSION,
            "public_decision_identity": self._public_decision_identity,
            "public_state_digest": self._public_state_digest,
            "public_action_set_digest": self._public_action_set_digest,
            "selection_type": self.selection_type,
            "selection_context": self.selection_context,
            "min_count": self.min_count,
            "max_count": self.max_count,
            "order_semantics": self.order_semantics,
            "selected_count": len(action.keys),
            "selected_public_actions": [
                json.loads(projection) for projection in selected_projection_json
            ],
        }


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class CompleteAction:
    """A validated complete set or sequence executable by the current CABT call."""

    envelope: DecisionEnvelope
    keys: tuple[str, ...]
    option_indices: tuple[int, ...]
    _origin_identity: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, DecisionEnvelope):
            raise CompleteActionError("complete action requires a DecisionEnvelope")
        object.__setattr__(self, "_origin_identity", self.envelope._identity)
        if not isinstance(self.keys, tuple) or not all(type(key) is str for key in self.keys):
            raise CompleteActionError("complete action keys must be a tuple of strings")
        if not isinstance(self.option_indices, tuple) or any(
            type(index) is not int or index < 0 for index in self.option_indices
        ):
            raise CompleteActionError("complete action indices must be non-bool nonnegative ints")
        if len(self.keys) != len(self.option_indices):
            raise CompleteActionError("complete action keys and indices must have equal length")
        if not self.envelope.min_count <= len(self.keys) <= self.envelope.max_count:
            raise CompleteActionError("complete action cardinality is outside envelope bounds")
        if len(set(self.keys)) != len(self.keys):
            raise CompleteActionError("complete action keys must be unique")
        if len(set(self.option_indices)) != len(self.option_indices):
            raise CompleteActionError("complete action indices must be unique")
        expected_indices = tuple(self.envelope.index_for_key(key) for key in self.keys)
        if self.envelope.order_semantics == "unordered_set":
            if self.keys != tuple(sorted(self.keys)):
                raise CompleteActionError("unordered complete action keys must be ascending")
            if self.option_indices != tuple(sorted(expected_indices)):
                raise CompleteActionError(
                    "unordered complete action indices must be current numeric execution order"
                )
        elif self.option_indices != expected_indices:
            raise CompleteActionError(
                "ordered complete action indices must preserve key sequence order"
            )

    def __repr__(self) -> str:
        return (
            "CompleteAction(selection=<redacted>, envelope=<redacted>)"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CompleteAction) and self.keys == other.keys

    def __hash__(self) -> int:
        return hash(self.keys)


def _make_complete_action(envelope: DecisionEnvelope, keys: tuple[str, ...]) -> CompleteAction:
    if envelope.order_semantics == "unordered_set":
        option_indices = tuple(sorted(envelope.index_for_key(key) for key in keys))
    else:
        option_indices = tuple(envelope.index_for_key(key) for key in keys)
    return CompleteAction(envelope=envelope, keys=keys, option_indices=option_indices)


def _enumeration_count(envelope: DecisionEnvelope, *, cap: int) -> int:
    """Count only up to ``cap`` and stop after the first overflowing term."""
    candidate_count = len(envelope.candidates)
    counter = comb if envelope.order_semantics == "unordered_set" else perm
    total = 0
    for selection_count in range(envelope.min_count, envelope.max_count + 1):
        term = counter(candidate_count, selection_count)
        if term >= cap - total:
            return cap
        total += term
    return total


def _require_enumeration_limit(value: object) -> int:
    if type(value) is not int or value < 1:
        raise CompleteActionEnumerationError(
            "enumeration limit must be a positive non-bool int"
        )
    if value > _MAX_COMPLETE_ACTIONS:
        raise CompleteActionEnumerationError(
            "enumeration limit exceeds the hard maximum of 65,536 complete actions"
        )
    return value


def enumerate_complete_actions(
    envelope: DecisionEnvelope,
    *,
    limit: int,
) -> tuple[CompleteAction, ...]:
    """Return every legal action, rejecting oversized domains before materialization."""
    if not isinstance(envelope, DecisionEnvelope):
        raise DecisionEnvelopeError("envelope must be a DecisionEnvelope")
    limit = _require_enumeration_limit(limit)
    count = _enumeration_count(envelope, cap=limit + 1)
    if count > limit:
        raise CompleteActionEnumerationError(
            f"complete-action enumeration count {count} exceeds limit {limit}"
        )
    keys = envelope.canonical_keys
    generator = (
        combinations(keys, selection_count)
        if envelope.order_semantics == "unordered_set"
        else permutations(keys, selection_count)
        for selection_count in range(envelope.min_count, envelope.max_count + 1)
    )
    return tuple(
        _make_complete_action(envelope, selection)
        for selections_at_count in generator
        for selection in selections_at_count
    )


def _validate_prefix(envelope: DecisionEnvelope, prefix: object) -> tuple[str, ...]:
    if not isinstance(prefix, tuple) or any(type(key) is not str for key in prefix):
        raise CompleteActionError("prefix must be a tuple of stable-key strings")
    if len(prefix) > envelope.max_count:
        raise CompleteActionError("prefix exceeds envelope maximum cardinality")
    if len(set(prefix)) != len(prefix):
        raise CompleteActionError("prefix keys must be unique")
    known = set(envelope.canonical_keys)
    if any(key not in known for key in prefix):
        raise CompleteActionError("prefix contains an unknown stable key")
    if envelope.order_semantics == "unordered_set" and prefix != tuple(sorted(prefix)):
        raise CompleteActionError("unordered prefix keys must be ascending")
    return prefix


def legal_next_tokens(envelope: DecisionEnvelope, prefix: tuple[str, ...]) -> tuple[str, ...]:
    """Return every legal canonical next token, including ``STOP`` when allowed."""
    if not isinstance(envelope, DecisionEnvelope):
        raise DecisionEnvelopeError("envelope must be a DecisionEnvelope")
    prefix = _validate_prefix(envelope, prefix)
    selected_count = len(prefix)
    if selected_count == envelope.max_count:
        return (STOP_TOKEN,)

    keys = envelope.canonical_keys
    selected = set(prefix)
    if envelope.order_semantics == "unordered_set":
        remaining = tuple(key for key in keys if not prefix or key > prefix[-1])
        candidates = tuple(
            key
            for key in remaining
            if key not in selected
            and selected_count + 1 + sum(other > key for other in remaining) >= envelope.min_count
        )
    else:
        remaining = tuple(key for key in keys if key not in selected)
        candidates = tuple(
            key
            for key in remaining
            if selected_count + 1 + (len(remaining) - 1) >= envelope.min_count
        )
    if selected_count >= envelope.min_count:
        return (*candidates, STOP_TOKEN)
    return candidates


def _step_logit_mapping(
    step_logits: StepLogits,
    prefix: tuple[str, ...],
    allowed: tuple[str, ...],
) -> dict[str, float]:
    value = step_logits(prefix, allowed)
    if not isinstance(value, Mapping):
        raise CompleteActionProbabilityError("step logits must be a mapping")
    if set(value) != set(allowed) or len(value) != len(allowed):
        raise CompleteActionProbabilityError("step logits must contain exactly the allowed tokens")
    result: dict[str, float] = {}
    for token in allowed:
        score = value[token]
        if type(score) is bool or not isinstance(score, Real) or not isfinite(float(score)):
            raise CompleteActionProbabilityError(
                "step logits must be finite non-bool numeric values"
            )
        result[token] = float(score)
    return result


def _log_probability_of_token(
    step_logits: StepLogits,
    prefix: tuple[str, ...],
    allowed: tuple[str, ...],
    token: str,
) -> float:
    if allowed == (STOP_TOKEN,):
        return 0.0
    scores = _step_logit_mapping(step_logits, prefix, allowed)
    maximum = max(scores.values())
    log_normalizer = maximum + log(sum(exp(score - maximum) for score in scores.values()))
    return scores[token] - log_normalizer


def _require_current_action(
    envelope: DecisionEnvelope,
    action: object,
) -> CompleteAction:
    if not isinstance(action, CompleteAction):
        raise CompleteActionError("complete action must be a CompleteAction")
    if action.envelope is not envelope or action._origin_identity is not envelope._identity:
        raise CompleteActionError("complete action is stale for this envelope")
    return action


def complete_action_log_probability(
    envelope: DecisionEnvelope,
    action: CompleteAction,
    *,
    step_logits: StepLogits,
) -> float:
    """Return the exact canonical autoregressive log probability of one action."""
    _require_current_action(envelope, action)
    if not callable(step_logits):
        raise CompleteActionProbabilityError("step_logits must be callable")
    prefix: tuple[str, ...] = ()
    result = 0.0
    for token in action.keys:
        allowed = legal_next_tokens(envelope, prefix)
        if token not in allowed or token == STOP_TOKEN:
            raise CompleteActionError("complete action token is illegal for its prefix")
        result += _log_probability_of_token(step_logits, prefix, allowed, token)
        prefix = (*prefix, token)
    allowed = legal_next_tokens(envelope, prefix)
    if STOP_TOKEN not in allowed:
        raise CompleteActionError("complete action cannot terminate at this cardinality")
    return result + _log_probability_of_token(step_logits, prefix, allowed, STOP_TOKEN)


def complete_action_distribution(
    envelope: DecisionEnvelope,
    *,
    step_logits: StepLogits,
    enumeration_limit: int,
) -> dict[CompleteAction, float]:
    """Return the normalized distribution over all legal complete actions."""
    enumeration_limit = _require_enumeration_limit(enumeration_limit)
    actions = enumerate_complete_actions(envelope, limit=enumeration_limit)
    distribution: dict[CompleteAction, float] = {}
    for action in actions:
        probability = exp(
            complete_action_log_probability(
                envelope,
                action,
                step_logits=step_logits,
            )
        )
        if probability == 0.0:
            raise CompleteActionProbabilityError(
                "finite-logit legal action probability underflowed to zero"
            )
        distribution[action] = probability
    total = sum(distribution.values())
    if not isfinite(total) or abs(total - 1.0) > 1e-10:
        raise CompleteActionProbabilityError("complete-action distribution is not normalized")
    return distribution


def greedy_decode(
    envelope: DecisionEnvelope,
    *,
    step_logits: StepLogits,
) -> CompleteAction:
    """Greedily decode a legal complete action with stable-token tie-breaking."""
    if not isinstance(envelope, DecisionEnvelope):
        raise DecisionEnvelopeError("envelope must be a DecisionEnvelope")
    if not callable(step_logits):
        raise CompleteActionProbabilityError("step_logits must be callable")
    prefix: tuple[str, ...] = ()
    while True:
        allowed = legal_next_tokens(envelope, prefix)
        if allowed == (STOP_TOKEN,):
            return _make_complete_action(envelope, prefix)
        scores = _step_logit_mapping(step_logits, prefix, allowed)
        maximum = max(scores.values())
        token = min(
            candidate for candidate in allowed if scores[candidate] == maximum
        )
        if token == STOP_TOKEN:
            return _make_complete_action(envelope, prefix)
        prefix = (*prefix, token)


def q_argmax(
    envelope: DecisionEnvelope,
    q_values: Mapping[tuple[str, ...], object],
    *,
    enumeration_limit: int,
) -> CompleteAction:
    """Return the finite exact-domain legal Q maximum with stable tuple ties."""
    enumeration_limit = _require_enumeration_limit(enumeration_limit)
    if not isinstance(q_values, Mapping):
        raise CompleteActionProbabilityError("Q values must be a mapping")
    actions = enumerate_complete_actions(envelope, limit=enumeration_limit)
    expected = {action.keys for action in actions}
    if set(q_values) != expected or len(q_values) != len(expected):
        raise CompleteActionProbabilityError(
            "Q values must contain exactly every enumerated complete action"
        )
    normalized: dict[tuple[str, ...], float] = {}
    for keys in expected:
        value = q_values[keys]
        if type(value) is bool or not isinstance(value, Real) or not isfinite(float(value)):
            raise CompleteActionProbabilityError(
                "Q values must be finite non-bool numeric values"
            )
        normalized[keys] = float(value)
    best_score = max(normalized.values())
    best_keys = min(keys for keys, value in normalized.items() if value == best_score)
    return _make_complete_action(envelope, best_keys)


__all__ = [
    "Candidate",
    "CABT_AGENT_JSON_SELECTION_CONTEXTS_V1",
    "CompleteAction",
    "CompleteActionEnumerationError",
    "CompleteActionError",
    "CompleteActionProbabilityError",
    "DecisionEnvelope",
    "DecisionEnvelopeError",
    "OrderSemantics",
    "STOP_TOKEN",
    "complete_action_distribution",
    "complete_action_log_probability",
    "enumerate_complete_actions",
    "greedy_decode",
    "legal_next_tokens",
    "q_argmax",
    "resolve_order_semantics",
]
