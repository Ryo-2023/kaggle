"""CPU-only transactional runtime for C1 v2 specialist policies.

The runtime is deliberately the narrow point where private C1-v2 action
identities are allowed to meet CABT's current option indices.  Policies only
receive the frozen serial-free feature input through ``logits``; persistent
telemetry is either an explicit frozen public-v1 projection or an aggregate
collision/oversize record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from math import isfinite
import re
from threading import Lock
import time
from typing import Protocol, runtime_checkable
from weakref import ReferenceType, ref

from mage_ptcg.decision_state import (
    DecisionStateError,
    validate_public_action_feature_payload,
)
from mage_ptcg.knowledge.model import KnowledgeValidationError, deck_identity_from_card_ids
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitPolicyV1,
    SpecialistStepLogitsV1,
    canonical_step_input_bytes_v1,
    derive_model_input_id_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorVisibleDecisionStateV2,
    ActorVisibleV2Error,
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.decks import (
    DeckLineageError, DeckQualificationError,
    DeckLockDecision,
    QualifiedDeckAsset,
    require_qualified_deck_asset,
    require_lineage_deck,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    RuntimeActionError,
    RuntimeDecisionEnvelope,
    RuntimeEnvelopeError,
    RuntimePolicyError,
    SemanticRuntimeCompleteActionV2,
    greedy_decode_runtime_action_v2,
    runtime_semantic_complete_action_log_probability_v2,
    semantic_runtime_complete_action_from_runtime_action_v2,
)
from mage_ptcg.meta_specialist.actions import DecisionEnvelopeError, resolve_order_semantics


_TRACE_SCHEMA = "meta-specialist-runtime-decision-trace-v2"
_PACKAGE_TELEMETRY_SCHEMA = "meta-specialist-package-telemetry-v1"
_CONSTRAINT_SCHEMA = "meta-specialist-runtime-constraints-v1"
_CONSTRAINT_ID_PREFIX = b"meta-specialist-runtime-constraints-v1\0"
_PUBLIC_TRACE_IDENTITY_PREFIX = b"mage_ptcg.meta_specialist.complete_action_trace:v1\0"
_TRACE_CAPACITY = 4_096
_CANDIDATE_CLASSES = frozenset({"checkpointed_specialist", "static_rule_bundle"})


class RuntimeContractError(ValueError):
    """Raised when an agent callback or its production binding is invalid."""


class RuntimeDecisionTimeoutError(RuntimeContractError):
    """Raised before commit when cooperative elapsed-time measurement exceeds v1."""


def _sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RuntimeContractError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeContractError("runtime value is not canonical JSON") from exc


def _strict_nonnegative(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeContractError(f"{field} must be a non-bool nonnegative int")
    return value


def _strict_positive(value: object, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise RuntimeContractError(f"{field} must be a non-bool positive int")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class PolicyTelemetrySnapshot:
    """Validated policy facts, deliberately separate from mutable runtime counters."""

    policy_identity: str
    candidate_class: str
    model_loaded: bool
    checkpoint_lineage_id: str | None
    checkpoint_lineage_reason: str | None
    fallback_count: int = 0

    def __post_init__(self) -> None:
        _sha256(self.policy_identity, field="policy_identity")
        if type(self.candidate_class) is not str or self.candidate_class not in _CANDIDATE_CLASSES or type(self.model_loaded) is not bool:
            raise RuntimeContractError("policy telemetry candidate class/model state is invalid")
        _strict_nonnegative(self.fallback_count, field="fallback_count")
        if self.candidate_class == "checkpointed_specialist":
            if not self.model_loaded or self.checkpoint_lineage_reason is not None:
                raise RuntimeContractError("checkpointed policy telemetry is invalid")
            _sha256(self.checkpoint_lineage_id, field="checkpoint_lineage_id")
        else:
            if self.model_loaded or self.checkpoint_lineage_id is not None or type(self.checkpoint_lineage_reason) is not str or self.checkpoint_lineage_reason != "not_applicable_static_policy":
                raise RuntimeContractError("static policy telemetry is invalid")

    def __repr__(self) -> str:
        return "PolicyTelemetrySnapshot(policy_identity=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CommittedSemanticDecisionV2:
    """The only decision event passed back to a recurrent policy session."""

    semantic_action: SemanticRuntimeCompleteActionV2
    semantic_log_probability: float
    next_recurrent_state_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.semantic_action) is not SemanticRuntimeCompleteActionV2:
            raise RuntimeContractError("committed outcome must contain a semantic complete action")
        if type(self.semantic_log_probability) not in (int, float) or type(self.semantic_log_probability) is bool:
            raise RuntimeContractError("semantic log probability must be a finite number")
        probability = float(self.semantic_log_probability)
        if not isfinite(probability) or probability > 0.0:
            raise RuntimeContractError("semantic log probability must be finite and nonpositive")
        object.__setattr__(self, "semantic_log_probability", probability)

    def __repr__(self) -> str:
        return "CommittedSemanticDecisionV2(semantic_action=<redacted>, next_state=<redacted>)"


@runtime_checkable
class SpecialistDecisionSessionV2(SpecialistStepLogitPolicyV1, Protocol):
    def commit(self, outcome: CommittedSemanticDecisionV2) -> None: ...
    def abort(self) -> None: ...


@runtime_checkable
class SpecialistDecisionPolicyV2(Protocol):
    def reset(self) -> None: ...
    def begin_decision(self) -> SpecialistDecisionSessionV2: ...
    def policy_telemetry(self) -> PolicyTelemetrySnapshot: ...


@runtime_checkable
class StepLogitPolicyFactory(Protocol):
    def new_policy(self) -> SpecialistDecisionPolicyV2: ...


@dataclass(frozen=True, slots=True)
class RuntimeConstraintManifest:
    """Frozen conservative local runtime limits, recomputable by package verification."""

    schema_version: str
    python_version: str
    verifier_dependency: str
    host_dependencies: tuple[str, ...]
    decision_p95_target_ms: int
    decision_p99_target_ms: int
    decision_hard_timeout_ms: int
    game_hard_timeout_ms: int
    peak_rss_limit_kib: int
    trace_capacity: int
    runtime_constraints_id: str

    def __post_init__(self) -> None:
        expected = {
            "schema_version": _CONSTRAINT_SCHEMA, "python_version": "3.11.11",
            "verifier_dependency": "kaggle-environments==1.32.0", "host_dependencies": (),
            "decision_p95_target_ms": 100, "decision_p99_target_ms": 250,
            "decision_hard_timeout_ms": 1_000, "game_hard_timeout_ms": 300_000,
            "peak_rss_limit_kib": 8_388_608, "trace_capacity": _TRACE_CAPACITY,
        }
        for name in ("schema_version", "python_version", "verifier_dependency"):
            if type(getattr(self, name)) is not str:
                raise RuntimeContractError(f"runtime constraint {name} must be an exact built-in string")
        for name in (
            "decision_p95_target_ms", "decision_p99_target_ms",
            "decision_hard_timeout_ms", "game_hard_timeout_ms",
            "peak_rss_limit_kib", "trace_capacity",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise RuntimeContractError(
                    f"runtime constraint {name} must be an exact positive built-in integer"
                )
        if not (
            self.decision_p95_target_ms
            <= self.decision_p99_target_ms
            <= self.decision_hard_timeout_ms
            <= self.game_hard_timeout_ms
        ):
            raise RuntimeContractError("runtime timing constraints are inconsistently ordered")
        for name, value in expected.items():
            if type(getattr(self, name)) is not type(value) or getattr(self, name) != value:
                raise RuntimeContractError(f"runtime constraint {name} is not the frozen v1 value")
        if type(self.host_dependencies) is not tuple or any(type(item) is not str for item in self.host_dependencies):
            raise RuntimeContractError("host_dependencies must be an immutable string tuple")
        payload = {name: getattr(self, name) for name in expected}
        expected_id = hashlib.sha256(_CONSTRAINT_ID_PREFIX + _canonical_bytes(payload)).hexdigest()
        _sha256(self.runtime_constraints_id, field="runtime_constraints_id")
        if self.runtime_constraints_id != expected_id:
            raise RuntimeContractError("runtime_constraints_id does not match frozen payload")

    @classmethod
    def frozen_v1(cls) -> "RuntimeConstraintManifest":
        payload = {
            "schema_version": _CONSTRAINT_SCHEMA, "python_version": "3.11.11",
            "verifier_dependency": "kaggle-environments==1.32.0", "host_dependencies": (),
            "decision_p95_target_ms": 100, "decision_p99_target_ms": 250,
            "decision_hard_timeout_ms": 1_000, "game_hard_timeout_ms": 300_000,
            "peak_rss_limit_kib": 8_388_608, "trace_capacity": _TRACE_CAPACITY,
        }
        return cls(**payload, runtime_constraints_id=hashlib.sha256(_CONSTRAINT_ID_PREFIX + _canonical_bytes(payload)).hexdigest())

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "python_version": self.python_version,
            "verifier_dependency": self.verifier_dependency, "host_dependencies": list(self.host_dependencies),
            "decision_p95_target_ms": self.decision_p95_target_ms, "decision_p99_target_ms": self.decision_p99_target_ms,
            "decision_hard_timeout_ms": self.decision_hard_timeout_ms, "game_hard_timeout_ms": self.game_hard_timeout_ms,
            "peak_rss_limit_kib": self.peak_rss_limit_kib, "trace_capacity": self.trace_capacity,
            "runtime_constraints_id": self.runtime_constraints_id,
        }


_PUBLIC_PROJECTION_KEYS = frozenset({
    "schema_version", "public_decision_identity", "public_state_digest",
    "public_action_set_digest", "selection_type", "selection_context",
    "min_count", "max_count", "order_semantics", "selected_count",
    "selected_public_actions",
})
_ALLOWED_PUBLIC_DIGEST_KEYS = frozenset({
    "public_decision_identity", "public_state_digest", "public_action_set_digest",
})
_FORBIDDEN_TRACE_KEYS = frozenset({
    "observation", "raw_observation", "private_state", "card_id", "serial",
    "private_digest", "production_digest", "decision_digest", "action_digest",
    "action_key_digest", "local_action_id", "stable_key", "option_index",
    "option_indices", "current_index", "current_indices", "actor_payload",
    "actor_binding",
})


def _normalized_trace_key(value: str) -> str:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"[^a-z0-9]+", "_", snake).strip("_")


def _reject_private_trace_tree(value: object, *, key: str | None = None, depth: int = 0) -> None:
    if depth > 16:
        raise RuntimeContractError("public projection exceeds its bounded nesting depth")
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise RuntimeContractError("public projection mapping is oversized")
        for raw_key, item in value.items():
            if type(raw_key) is not str or len(raw_key) > 128:
                raise RuntimeContractError("public projection keys must be bounded built-in strings")
            normalized = _normalized_trace_key(raw_key)
            if (
                normalized in _FORBIDDEN_TRACE_KEYS
                or normalized.startswith("serial_")
                or normalized.endswith("_serial")
            ):
                raise RuntimeContractError("public projection contains a private trace key")
            _reject_private_trace_tree(item, key=normalized, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            raise RuntimeContractError("public projection sequence is oversized")
        for item in value:
            _reject_private_trace_tree(item, depth=depth + 1)
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not isfinite(value):
            raise RuntimeContractError("public projection contains a nonfinite number")
        return
    if type(value) is str:
        if len(value) > 16_384:
            raise RuntimeContractError("public projection string is oversized")
        return
    raise RuntimeContractError("public projection contains a non-JSON value")


def _reject_duplicate_projection_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeContractError("public projection contains duplicate keys")
        result[key] = value
    return result


def _seal_public_projection(value: object) -> bytes:
    _reject_private_trace_tree(value)
    canonical = _canonical_bytes(value)
    if len(canonical) > 1_048_576:
        raise RuntimeContractError("public projection exceeds one MiB")
    try:
        parsed = json.loads(
            canonical.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_projection_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                RuntimeContractError(f"public projection contains {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError("public projection canonical bytes are invalid") from exc
    if type(parsed) is not dict or set(parsed) != _PUBLIC_PROJECTION_KEYS:
        raise RuntimeContractError("public projection has an invalid closed root schema")
    if type(parsed["schema_version"]) is not int or parsed["schema_version"] != 1:
        raise RuntimeContractError("public projection schema_version must be exact integer one")
    for name in _ALLOWED_PUBLIC_DIGEST_KEYS:
        _sha256(parsed[name], field=f"public projection {name}")
    for name in ("selection_type", "selection_context", "min_count", "max_count", "selected_count"):
        _strict_nonnegative(parsed[name], field=f"public projection {name}")
    if parsed["order_semantics"] not in {"unordered_set", "ordered_sequence"} or type(parsed["order_semantics"]) is not str:
        raise RuntimeContractError("public projection order semantics are invalid")
    try:
        expected_order = resolve_order_semantics(
            parsed["selection_type"], parsed["selection_context"],
        )
    except DecisionEnvelopeError as exc:
        raise RuntimeContractError("public projection selection schema is not frozen C5") from exc
    if parsed["order_semantics"] != expected_order:
        raise RuntimeContractError("public projection order semantics disagree with frozen C5")
    if (
        parsed["min_count"] > parsed["max_count"]
        or parsed["selected_count"] < parsed["min_count"]
        or parsed["selected_count"] > parsed["max_count"]
        or parsed["max_count"] > 60
    ):
        raise RuntimeContractError("public projection selection bounds are inconsistent")
    identity_core = {
        "schema_version": parsed["schema_version"],
        "public_state_digest": parsed["public_state_digest"],
        "public_action_set_digest": parsed["public_action_set_digest"],
        "selection_type": parsed["selection_type"],
        "selection_context": parsed["selection_context"],
        "min_count": parsed["min_count"],
        "max_count": parsed["max_count"],
        "order_semantics": parsed["order_semantics"],
    }
    expected_identity = hashlib.sha256(
        _PUBLIC_TRACE_IDENTITY_PREFIX + _canonical_bytes(identity_core)
    ).hexdigest()
    if parsed["public_decision_identity"] != expected_identity:
        raise RuntimeContractError(
            "public projection public_decision_identity does not bind its stable root"
        )
    actions = parsed["selected_public_actions"]
    if type(actions) is not list or len(actions) != parsed["selected_count"] or any(type(item) is not dict for item in actions):
        raise RuntimeContractError("public projection selected actions are invalid")
    action_bytes: list[bytes] = []
    for action in actions:
        try:
            validated = validate_public_action_feature_payload(action)
        except (DecisionStateError, TypeError, ValueError) as exc:
            raise RuntimeContractError(
                "public projection selected public action is not frozen C5"
            ) from exc
        if (
            validated["selection_type"] != parsed["selection_type"]
            or validated["context"] != parsed["selection_context"]
        ):
            raise RuntimeContractError(
                "public projection selected public action disagrees with root selection"
            )
        encoded_action = _canonical_bytes(validated)
        if encoded_action != _canonical_bytes(action):
            raise RuntimeContractError(
                "public projection selected public action is not exact frozen C5"
            )
        action_bytes.append(encoded_action)
    if len(action_bytes) != len(set(action_bytes)):
        raise RuntimeContractError("public projection selected public actions must be unique")
    if parsed["order_semantics"] == "unordered_set" and action_bytes != sorted(action_bytes):
        raise RuntimeContractError(
            "public projection unordered selected public actions are not in canonical order"
        )
    if _canonical_bytes(parsed) != canonical:
        raise RuntimeContractError("public projection is not in exact canonical form")
    return canonical


def _parse_owned_public_projection(value: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError("owned public projection bytes are invalid") from exc
    if type(parsed) is not dict:
        raise RuntimeContractError("owned public projection root is invalid")
    return parsed


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeDecisionTraceV2:
    """One immutable privacy-safe runtime record backed by owned canonical bytes."""

    trace_variant: str
    policy_identity: str
    candidate_class: str
    selection_type: int
    selection_context: int
    min_count: int
    max_count: int
    order_semantics: str
    selected_count: int
    complete_action_log_probability: float
    candidate_count: int | None = None
    collision_group_sizes: tuple[int, ...] = ()
    _public_projection_bytes: bytes | None = field(default=None, repr=False, compare=True)

    @classmethod
    def from_public_projection(cls, *, public_trace: object, **fields: object) -> "RuntimeDecisionTraceV2":
        return cls(_public_projection_bytes=_seal_public_projection(public_trace), **fields)  # type: ignore[arg-type]

    def __post_init__(self) -> None:
        if type(self.trace_variant) is not str or self.trace_variant not in {"public-v1-representable", "duplicate-public-identity", "public-v1-option-limit-exceeded"}:
            raise RuntimeContractError("runtime trace has an unknown variant")
        _sha256(self.policy_identity, field="trace policy_identity")
        if type(self.candidate_class) is not str or self.candidate_class not in _CANDIDATE_CLASSES:
            raise RuntimeContractError("runtime trace has an unknown candidate class")
        for name in ("selection_type", "selection_context", "min_count", "max_count", "selected_count"):
            _strict_nonnegative(getattr(self, name), field=name)
        if not self.min_count <= self.max_count or not self.min_count <= self.selected_count <= self.max_count:
            raise RuntimeContractError("runtime trace selection bounds are inconsistent")
        if type(self.order_semantics) is not str or self.order_semantics not in {"unordered_set", "ordered_sequence"}:
            raise RuntimeContractError("runtime trace order semantics are invalid")
        if type(self.complete_action_log_probability) is not float or not isfinite(self.complete_action_log_probability) or self.complete_action_log_probability > 0.0:
            raise RuntimeContractError("runtime trace log probability is invalid")
        if type(self.collision_group_sizes) is not tuple:
            raise RuntimeContractError("runtime trace collision sizes must be an immutable tuple")
        if self.trace_variant == "public-v1-representable":
            if type(self._public_projection_bytes) is not bytes or self.candidate_count is not None or self.collision_group_sizes:
                raise RuntimeContractError("representable runtime trace shape is invalid")
            projection = _parse_owned_public_projection(
                _seal_public_projection(_parse_owned_public_projection(self._public_projection_bytes))
            )
            for key, expected in (
                ("selection_type", self.selection_type), ("selection_context", self.selection_context),
                ("min_count", self.min_count), ("max_count", self.max_count),
                ("order_semantics", self.order_semantics), ("selected_count", self.selected_count),
            ):
                if type(projection[key]) is not type(expected) or projection[key] != expected:
                    raise RuntimeContractError("public projection disagrees with authoritative trace metadata")
        elif self.trace_variant == "duplicate-public-identity":
            if self._public_projection_bytes is not None or self.candidate_count is not None:
                raise RuntimeContractError("duplicate aggregate runtime trace shape is invalid")
        else:
            if self._public_projection_bytes is not None or type(self.candidate_count) is not int or self.candidate_count < 61:
                raise RuntimeContractError("oversize aggregate runtime trace shape is invalid")
        if self.trace_variant != "public-v1-representable":
            if tuple(sorted(self.collision_group_sizes)) != self.collision_group_sizes or any(type(size) is not int or size < 2 for size in self.collision_group_sizes):
                raise RuntimeContractError("aggregate collision sizes are invalid")

    @property
    def public_trace(self) -> dict[str, object] | None:
        """Return a new detached projection; mutations never touch stored bytes."""
        if self._public_projection_bytes is None:
            return None
        parsed = _parse_owned_public_projection(self._public_projection_bytes)
        validated = _seal_public_projection(parsed)
        if validated != self._public_projection_bytes:
            raise RuntimeContractError("owned public projection bytes no longer verify")
        return _parse_owned_public_projection(validated)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": _TRACE_SCHEMA, "trace_variant": self.trace_variant,
            "policy_identity": self.policy_identity, "candidate_class": self.candidate_class,
            "selection_type": self.selection_type, "selection_context": self.selection_context,
            "min_count": self.min_count, "max_count": self.max_count,
            "order_semantics": self.order_semantics, "selected_count": self.selected_count,
            "complete_action_log_probability": self.complete_action_log_probability,
        }
        if self.trace_variant == "public-v1-representable":
            payload["public_projection"] = self.public_trace
        elif self.trace_variant == "duplicate-public-identity":
            payload["collision_group_sizes"] = list(self.collision_group_sizes)
        else:
            payload["candidate_count"] = self.candidate_count
            payload["collision_group_sizes"] = list(self.collision_group_sizes)
        return payload

    def __repr__(self) -> str:
        return "RuntimeDecisionTraceV2(<redacted>)"


class _CachedSessionPolicy(SpecialistStepLogitPolicyV1):
    """Makes decode and probability replay observe one session inference per prefix."""

    def __init__(self, session: SpecialistDecisionSessionV2) -> None:
        self._session = session
        self._cache: dict[tuple[bytes, bytes], SpecialistStepLogitsV1] = {}

    def logits(self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1) -> SpecialistStepLogitsV1:
        key = (derive_model_input_id_v1(model_input).encode("ascii"), canonical_step_input_bytes_v1(step_input))
        if key not in self._cache:
            self._cache[key] = self._session.logits(model_input, step_input)
        return self._cache[key]


def _validate_callback(observation: object) -> Mapping[str, object]:
    if not isinstance(observation, Mapping):
        raise RuntimeContractError("observation must be a mapping")
    try:
        callback = dict(observation)
    except Exception as exc:
        raise RuntimeContractError("observation mapping cannot be snapshotted") from exc
    if "select" not in callback:
        raise RuntimeContractError("observation must explicitly contain select")
    select = callback["select"]
    if select is None:
        return callback
    if not isinstance(select, Mapping):
        raise RuntimeContractError("select must be a mapping or null")
    try:
        select = dict(select)
    except Exception as exc:
        raise RuntimeContractError("select mapping cannot be snapshotted") from exc
    callback["select"] = select
    options = select.get("option")
    if not isinstance(options, list):
        raise RuntimeContractError("select.option must be a list")
    options = list(options)
    select["option"] = options
    minimum, maximum = select.get("minCount"), select.get("maxCount")
    if type(minimum) is not int or type(maximum) is not int or minimum < 0 or maximum < 0 or minimum > maximum or maximum > len(options):
        raise RuntimeContractError("select bounds are invalid")
    for name in ("type", "context"):
        if type(select.get(name)) is not int or select[name] < 0:
            raise RuntimeContractError(f"select.{name} must be a nonnegative non-bool int")
    if type(callback.get("step")) is not int or callback["step"] < 0:
        raise RuntimeContractError("step must be a nonnegative non-bool int")
    return callback


def _validate_binding(deck_asset: object, deck_lock: object, vocabulary: object) -> tuple[QualifiedDeckAsset, DeckLockDecision, CardVocabularyV1]:
    if type(deck_asset) is not QualifiedDeckAsset or type(deck_lock) is not DeckLockDecision or type(vocabulary) is not CardVocabularyV1:
        raise RuntimeContractError("runtime requires exact qualified deck, DeckLock, and card vocabulary")
    try:
        require_qualified_deck_asset(deck_asset)
    except DeckQualificationError as exc:
        raise RuntimeContractError("qualified deck attestation does not verify") from exc
    for name in (
        "asset_id", "archetype_id", "source_ref", "source_commit",
        "asset_class", "usage_boundary", "policy_compatibility",
        "card_database_version", "cabt_legality_status", "cabt_legality_evidence",
    ):
        value = getattr(deck_asset, name)
        if type(value) is not str or not value or len(value) > 4_096:
            raise RuntimeContractError(f"qualified deck {name} must be a bounded built-in string")
    if re.fullmatch(r"[0-9a-f]{40}", deck_asset.source_commit) is None:
        raise RuntimeContractError("qualified deck source_commit is invalid")
    if deck_asset.asset_class not in {"deck_only", "runnable_rule", "checkpoint_teacher"}:
        raise RuntimeContractError("qualified deck asset_class is invalid")
    if deck_asset.usage_boundary != "bundle_allowed":
        raise RuntimeContractError("qualified deck is not permissioned for bundle use")
    cards = deck_asset.card_ids
    if type(cards) is not tuple or len(cards) != 60 or any(type(card) is not int or card <= 0 for card in cards):
        raise RuntimeContractError("qualified deck card IDs must be an immutable 60-card positive-int tuple")
    try:
        identity = deck_identity_from_card_ids(cards)
        require_lineage_deck(deck_lock, identity)
    except (KnowledgeValidationError, DeckLineageError) as exc:
        raise RuntimeContractError("deck lock binding does not replay validate") from exc
    if identity != deck_asset.deck_identity or type(deck_asset.card_count) is not int or deck_asset.card_count != 60 or deck_asset.cabt_legality_status != "passed" or not deck_asset.cabt_legality_evidence.strip():
        raise RuntimeContractError("qualified deck fields are not a replayable passed qualification")
    if deck_asset.archetype_id != deck_lock.archetype_id:
        raise RuntimeContractError("qualified deck and lock archetypes differ")
    _sha256(deck_asset.deck_file_sha256, field="deck_file_sha256")
    try:
        CardVocabularyV1.__post_init__(vocabulary)
    except ValueError as exc:
        raise RuntimeContractError("card vocabulary does not replay validate") from exc
    return deck_asset, deck_lock, vocabulary


class MetaSpecialistRuntime:
    """One-game runtime with a single locked deck and transactional decision commits."""

    def __init__(self, *, deck_asset: QualifiedDeckAsset, deck_lock: DeckLockDecision, vocabulary: CardVocabularyV1, policy: SpecialistDecisionPolicyV2, expected_policy_identity: str, constraints: RuntimeConstraintManifest, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._deck_asset, self._deck_lock, self._vocabulary = _validate_binding(deck_asset, deck_lock, vocabulary)
        if not isinstance(policy, SpecialistDecisionPolicyV2):
            raise RuntimeContractError("policy must implement SpecialistDecisionPolicyV2")
        if type(constraints) is not RuntimeConstraintManifest or not callable(monotonic):
            raise RuntimeContractError("runtime constraints and monotonic clock are required")
        RuntimeConstraintManifest.__post_init__(constraints)
        self._expected_policy_identity = _sha256(expected_policy_identity, field="expected_policy_identity")
        self._policy, self._constraints, self._monotonic = policy, constraints, monotonic
        telemetry = self._read_policy_telemetry()
        self._policy_identity_fields = self._telemetry_identity_fields(telemetry)
        self._registered = False
        self._terminal = False
        self._traces: tuple[RuntimeDecisionTraceV2, ...] = ()
        self._dropped_trace_count = self._legal_decision_count = self._invalid_count = self._crash_count = self._timeout_count = 0

    @staticmethod
    def _telemetry_identity_fields(telemetry: PolicyTelemetrySnapshot) -> tuple[object, ...]:
        return (
            telemetry.policy_identity, telemetry.candidate_class, telemetry.model_loaded,
            telemetry.checkpoint_lineage_id, telemetry.checkpoint_lineage_reason,
        )

    def _read_policy_telemetry(self) -> PolicyTelemetrySnapshot:
        try:
            telemetry = self._policy.policy_telemetry()
        except Exception as exc:
            raise RuntimeContractError("policy telemetry callback failed") from exc
        if type(telemetry) is not PolicyTelemetrySnapshot:
            raise RuntimeContractError("policy telemetry must be an exact PolicyTelemetrySnapshot")
        PolicyTelemetrySnapshot.__post_init__(telemetry)
        if telemetry.policy_identity != self._expected_policy_identity:
            raise RuntimeContractError("policy telemetry identity does not match the expected identity")
        if (
            telemetry.candidate_class == "checkpointed_specialist"
            and telemetry.checkpoint_lineage_id != self._deck_lock.policy_lineage_id
        ):
            raise RuntimeContractError("checkpoint lineage does not bind the DeckLock lineage")
        return telemetry

    def _fresh_policy_telemetry(self) -> PolicyTelemetrySnapshot:
        telemetry = self._read_policy_telemetry()
        if self._telemetry_identity_fields(telemetry) != self._policy_identity_fields:
            raise RuntimeContractError("policy telemetry identity fields changed after construction")
        return telemetry

    def _clock_now(self) -> float:
        try:
            value = self._monotonic()
        except Exception as exc:
            raise RuntimeContractError("monotonic clock failed") from exc
        if type(value) not in (int, float) or type(value) is bool or not isfinite(float(value)):
            raise RuntimeContractError("monotonic clock must return a finite non-bool number")
        return float(value)

    def _check_decision_deadline(self, started_at: float) -> None:
        now = self._clock_now()
        if now < started_at:
            raise RuntimeContractError("monotonic clock moved backwards")
        elapsed_ms = (now - started_at) * 1_000.0
        if elapsed_ms > self._constraints.decision_hard_timeout_ms:
            raise RuntimeDecisionTimeoutError("decision exceeded the cooperative hard timeout")

    @property
    def traces(self) -> tuple[RuntimeDecisionTraceV2, ...]:
        return tuple(self._traces)

    @property
    def environment_action_count(self) -> int:
        return self._legal_decision_count

    @property
    def dropped_trace_count(self) -> int:
        return self._dropped_trace_count

    def reset(self) -> None:
        self._policy.reset()
        self._registered = self._terminal = False
        self._traces = ()
        self._dropped_trace_count = self._legal_decision_count = self._invalid_count = self._crash_count = self._timeout_count = 0

    def _failure(self, error: Exception) -> None:
        if isinstance(error, RuntimeDecisionTimeoutError):
            self._timeout_count += 1
        elif isinstance(error, RuntimeContractError):
            self._invalid_count += 1
        else:
            self._crash_count += 1

    def _make_trace(self, state: ActorVisibleDecisionStateV2, envelope: RuntimeDecisionEnvelope, action: object, log_probability: float) -> RuntimeDecisionTraceV2:
        """Project a completed action without inspecting private envelope fields."""
        runtime_action = action
        view = state.information_view
        collision = envelope.collision_telemetry(runtime_action)
        common = dict(policy_identity=self._expected_policy_identity, candidate_class=self._policy_identity_fields[1], selection_type=view.selection_type, selection_context=view.selection_context, min_count=view.min_count, max_count=view.max_count, order_semantics=envelope.build_step_input(()).order_semantics, selected_count=len(envelope.decode_option_indices(runtime_action)), complete_action_log_probability=float(log_probability))
        if envelope.candidate_count > 60:
            return RuntimeDecisionTraceV2(trace_variant="public-v1-option-limit-exceeded", candidate_count=envelope.candidate_count, collision_group_sizes=tuple(collision["collision_group_sizes"]), **common)
        if collision["status"] == "duplicate-public-identity":
            return RuntimeDecisionTraceV2(trace_variant="duplicate-public-identity", collision_group_sizes=tuple(collision["collision_group_sizes"]), **common)
        try:
            public_envelope, public_action = envelope.convert_to_public(runtime_action)
            public_trace = public_envelope.to_public_trace_payload(public_action)
        except Exception as exc:
            raise RuntimeContractError("explicit public-v1 trace bridge failed") from exc
        return RuntimeDecisionTraceV2.from_public_projection(
            trace_variant="public-v1-representable", public_trace=public_trace, **common
        )

    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        del configuration
        try:
            started_at = self._clock_now()
        except RuntimeContractError as exc:
            self._failure(exc)
            raise
        try:
            callback = _validate_callback(observation)
        except Exception as exc:
            try:
                self._check_decision_deadline(started_at)
            except RuntimeContractError as timing_error:
                self._failure(timing_error)
                raise timing_error from exc
            if isinstance(exc, RuntimeContractError):
                self._failure(exc)
                raise
            error = RuntimeContractError("callback validation failed")
            self._failure(error)
            raise error from exc
        if callback["select"] is None:
            if not self._registered:
                self._registered = True
                return list(self._deck_asset.card_ids)
            self._terminal = True
            return []
        if not self._registered:
            error = RuntimeContractError("decision callback before deck registration")
            self._failure(error)
            raise error
        if self._terminal:
            return []
        try:
            # Callback mapping access and structural validation are part of the
            # same per-decision cooperative deadline as feature construction.
            self._check_decision_deadline(started_at)
            state = build_actor_visible_decision_state_v2(callback)
            envelope = RuntimeDecisionEnvelope.from_actor_visible_state(state, vocabulary=self._vocabulary)
            self._check_decision_deadline(started_at)
        except RuntimeDecisionTimeoutError as exc:
            self._failure(exc)
            raise
        except RuntimeContractError as exc:
            self._failure(exc)
            raise
        except (ActorVisibleV2Error, RuntimeEnvelopeError, ValueError) as exc:
            error = RuntimeContractError("decision callback cannot build a valid C1 v2 envelope")
            self._failure(error)
            raise error from exc
        session: SpecialistDecisionSessionV2 | None = None
        commit_started = False
        try:
            # Identity-bearing policy facts are re-read for every transaction;
            # only mutable counters such as fallback_count may change.
            self._fresh_policy_telemetry()
            session = self._policy.begin_decision()
            # ``begin_decision`` is policy-controlled and may mutate a live
            # checkpoint binding before returning a session.
            self._fresh_policy_telemetry()
            if not isinstance(session, SpecialistDecisionSessionV2):
                raise RuntimeContractError("policy begin_decision returned no SpecialistDecisionSessionV2")
            cached = _CachedSessionPolicy(session)
            action = greedy_decode_runtime_action_v2(envelope, policy=cached)
            indices = envelope.decode_option_indices(action)
            semantic_action = semantic_runtime_complete_action_from_runtime_action_v2(envelope, action)
            log_probability = runtime_semantic_complete_action_log_probability_v2(envelope, semantic_action, policy=cached)
            if not isfinite(log_probability) or log_probability > 0.0:
                raise RuntimeContractError("semantic action log probability is invalid")
            trace = self._make_trace(state, envelope, action, log_probability)
            next_traces = self._traces
            next_dropped = self._dropped_trace_count
            if len(next_traces) < self._constraints.trace_capacity:
                next_traces = (*next_traces, trace)
            else:
                next_dropped += 1
            outcome = CommittedSemanticDecisionV2(semantic_action=semantic_action, semantic_log_probability=log_probability, next_recurrent_state_token=getattr(session, "next_recurrent_state_token", None))
            # Logits and next-state access are also policy-controlled.  This is
            # the last identity/lineage read before the point-of-no-return
            # recurrent commit.  Drift inside commit itself is quarantined at
            # the next telemetry/transaction boundary; an already committed
            # action is still returned and is never subsequently aborted.
            self._fresh_policy_telemetry()
            # This cooperative deadline detects all work before recurrent commit.
            # It cannot preempt a blocking Python/C extension; Task 5's outer
            # process watchdog remains the authoritative wall-clock kill gate.
            self._check_decision_deadline(started_at)
            # ``commit`` is contractually no-throw.  Mark its invocation before
            # calling it so a violating policy can never cause both commit and
            # abort to run against the same session.
            commit_started = True
            session.commit(outcome)
            self._traces, self._dropped_trace_count = next_traces, next_dropped
            self._legal_decision_count += 1
            return list(indices)
        except Exception as exc:
            if session is not None and not commit_started:
                try:
                    session.abort()
                except Exception:
                    pass
            if isinstance(exc, RuntimeContractError):
                self._failure(exc)
                raise
            self._failure(exc)
            raise RuntimeContractError("policy/runtime decision transaction failed") from exc

    def package_telemetry(self) -> dict[str, object]:
        telemetry = self._fresh_policy_telemetry()
        return {
            "schema_version": _PACKAGE_TELEMETRY_SCHEMA, "candidate_class": telemetry.candidate_class,
            "expected_policy_identity": self._expected_policy_identity, "loaded_policy_identity": telemetry.policy_identity,
            "model_loaded": telemetry.model_loaded, "checkpoint_lineage_id": telemetry.checkpoint_lineage_id,
            "checkpoint_lineage_reason": telemetry.checkpoint_lineage_reason, "fallback_count": telemetry.fallback_count,
            "invalid_count": self._invalid_count, "crash_count": self._crash_count, "timeout_count": self._timeout_count,
            "legal_decision_count": self._legal_decision_count, "legal_action_count": self._legal_decision_count,
        }


@dataclass(frozen=True, slots=True, repr=False)
class PackagedAgentBinding:
    agent: Callable[[object, object], list[int]]
    package_telemetry: Callable[[], dict[str, object]]

    def __repr__(self) -> str:
        return "PackagedAgentBinding(<redacted>)"


_BOUND_POLICY_OBJECTS: dict[int, ReferenceType[object]] = {}
_BOUND_POLICY_OBJECTS_LOCK = Lock()


def _claim_fresh_policy_object(policy: object) -> None:
    """Prevent live binding reuse without retaining dead policy objects forever."""
    policy_id = id(policy)

    def release(finished_ref: ReferenceType[object], *, claimed_id: int = policy_id) -> None:
        with _BOUND_POLICY_OBJECTS_LOCK:
            current = _BOUND_POLICY_OBJECTS.get(claimed_id)
            # The identity guard makes a delayed callback safe even if CPython
            # has already reused the numeric object ID for a newer policy.
            if current is finished_ref:
                _BOUND_POLICY_OBJECTS.pop(claimed_id, None)

    try:
        policy_ref = ref(policy, release)
    except TypeError as exc:
        raise RuntimeContractError(
            "policy factory must return a weak-referenceable fresh policy object"
        ) from exc
    with _BOUND_POLICY_OBJECTS_LOCK:
        previous_ref = _BOUND_POLICY_OBJECTS.get(policy_id)
        previous = None if previous_ref is None else previous_ref()
        if previous is policy:
            raise RuntimeContractError("policy factory returned a previously bound policy object")
        if previous is not None:
            raise RuntimeContractError("live policy identity registry collision")
        _BOUND_POLICY_OBJECTS[policy_id] = policy_ref


def make_agent(*, deck_asset: QualifiedDeckAsset, deck_lock: DeckLockDecision, vocabulary: CardVocabularyV1, policy_factory: StepLogitPolicyFactory, expected_policy_identity: str, constraints: RuntimeConstraintManifest, monotonic: Callable[[], float] = time.monotonic) -> PackagedAgentBinding:
    if not isinstance(policy_factory, StepLogitPolicyFactory):
        raise RuntimeContractError("policy_factory must implement StepLogitPolicyFactory")
    try:
        policy = policy_factory.new_policy()
    except Exception as exc:
        raise RuntimeContractError("policy factory failed to construct a fresh policy") from exc
    if not isinstance(policy, SpecialistDecisionPolicyV2):
        raise RuntimeContractError("policy factory returned no SpecialistDecisionPolicyV2")
    runtime = MetaSpecialistRuntime(deck_asset=deck_asset, deck_lock=deck_lock, vocabulary=vocabulary, policy=policy, expected_policy_identity=expected_policy_identity, constraints=constraints, monotonic=monotonic)
    _claim_fresh_policy_object(policy)
    return PackagedAgentBinding(agent=runtime, package_telemetry=runtime.package_telemetry)


__all__ = [
    "CommittedSemanticDecisionV2", "MetaSpecialistRuntime", "PackagedAgentBinding",
    "PolicyTelemetrySnapshot", "RuntimeConstraintManifest",
    "RuntimeDecisionTimeoutError", "RuntimeDecisionTraceV2", "RuntimeContractError", "SpecialistDecisionPolicyV2",
    "SpecialistDecisionSessionV2", "StepLogitPolicyFactory", "make_agent",
]
