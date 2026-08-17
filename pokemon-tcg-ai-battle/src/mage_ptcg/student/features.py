"""Deterministic, bounded feature extraction for Student v0.

The vectors intentionally derive only from :class:`ActorInformationView` and
the existing Stable ``ActionKey``.  Python's process-randomized ``hash`` is
never used, so exported models are reproducible across processes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from mage_ptcg.decision_state import (
    ActionKey,
    ActorInformationView,
    DecisionStateError,
    SerializedActionFeatureView,
    public_action_id_v1,
    validate_public_action_feature_payload,
)
from mage_ptcg.observability.cabt_trace import OPTION_SCALAR_FIELDS, OPTION_TYPE_NAMES


STATE_FEATURE_DIM = 32
ACTION_FEATURE_DIM = 64
FEATURE_VERSION = "student-v0-features-v1"
PUBLIC_ACTION_FEATURE_DOMAIN = "public-action-v1"
PRIVATE_ACTIONKEY_FEATURE_DOMAIN = "private-actionkey-v2"
LEGACY_ACTIONKEY_FEATURE_DOMAIN = "legacy-actionkey-v1"
ACTION_FEATURE_DOMAINS = frozenset(
    {
        PUBLIC_ACTION_FEATURE_DOMAIN,
        PRIVATE_ACTIONKEY_FEATURE_DOMAIN,
        LEGACY_ACTIONKEY_FEATURE_DOMAIN,
    }
)
_PUBLIC_ACTION_TRACE_FIELDS = frozenset(
    {
        "action_key_schema_version",
        "context",
        "option_type",
        "public_identity",
        "selection_type",
        "semantic_operation",
    }
)
_LEGACY_ACTION_FIELDS = frozenset((*OPTION_SCALAR_FIELDS, "damage", "hp", "energyAttached"))
_LEGACY_ACTION_KEY_HASH_PREFIX = b"mage_ptcg.decision_state:v1\0"


def _slot(value: str, dimension: int) -> tuple[int, float]:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % dimension
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def _add_hashed(vector: list[float], token: str, value: float = 1.0) -> None:
    index, sign = _slot(token, len(vector))
    vector[index] += sign * value


def _flatten_scalars(value: object, *, prefix: str = "", depth: int = 0) -> list[str]:
    """Bound feature tokens even if a future schema adds nested public values."""
    if depth > 4:
        return []
    if value is None or type(value) in (bool, int, float, str):
        if type(value) is float and not math.isfinite(value):
            return []
        return [f"{prefix}={value!r}"]
    if isinstance(value, Mapping):
        tokens: list[str] = []
        for key in sorted(value):
            if isinstance(key, str):
                tokens.extend(_flatten_scalars(value[key], prefix=f"{prefix}.{key}", depth=depth + 1))
        return tokens
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        tokens = [f"{prefix}.count={len(value)}"]
        for index, item in enumerate(value[:8]):
            tokens.extend(_flatten_scalars(item, prefix=f"{prefix}[{index}]", depth=depth + 1))
        return tokens
    return []


def state_features_payload(
    public_state: object,
    own_private_state: object,
    visible_history: Sequence[str],
) -> list[float]:
    """Encode only the three serialized information-state components."""
    vector = [0.0] * STATE_FEATURE_DIM
    payload = {
        "private": own_private_state,
        "public": public_state,
        "history": list(visible_history),
    }
    for token in _flatten_scalars(payload):
        _add_hashed(vector, f"state:{token}")
    return vector


def state_features(view: ActorInformationView) -> list[float]:
    """Encode public state, own observed private state, and bounded history."""
    return state_features_payload(
        json.loads(view.public_state_json),
        json.loads(view.own_private_state_json),
        view.visible_history,
    )


def _finite_number(value: object) -> float | None:
    if type(value) in (int, float) and math.isfinite(float(value)):
        return float(value)
    return None


def _canonical_feature_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_feature_vector(
    *,
    selection_type: object,
    context: object,
    option_type: object,
    semantic_operation: str,
    canonical_payload: Sequence[tuple[str, object]],
    card_id: int | None,
) -> list[float]:
    vector = [0.0] * ACTION_FEATURE_DIM
    _add_hashed(vector, f"selection_type:{selection_type!r}")
    _add_hashed(vector, f"context:{context!r}")
    _add_hashed(vector, f"option_type:{option_type!r}")
    _add_hashed(vector, f"operation:{semantic_operation}")
    for name, value in canonical_payload:
        _add_hashed(vector, f"field:{name}")
        if isinstance(value, (str, bool)) or value is None:
            _add_hashed(vector, f"value:{name}:{value!r}")
        numeric = _finite_number(value)
        if numeric is not None:
            # Numeric values use a stable small range; the model also receives
            # an explicit knockout indicator below.
            _add_hashed(vector, f"numeric:{name}", max(-10.0, min(10.0, numeric / 100.0)))

    fields = dict(canonical_payload)
    damage = _finite_number(fields.get("damage")) or 0.0
    hp = _finite_number(fields.get("hp"))
    if hp is not None and damage >= hp:
        _add_hashed(vector, "derived:knockout")
    if card_id is not None:
        _add_hashed(vector, f"own_card:{card_id}")
    return vector


def action_features(action_key: ActionKey | SerializedActionFeatureView) -> list[float]:
    """Encode a candidate semantically; no candidate position is represented."""
    return _action_feature_vector(
        selection_type=action_key.selection_type,
        context=action_key.context,
        option_type=action_key.option_type,
        semantic_operation=action_key.semantic_operation,
        canonical_payload=action_key.canonical_payload,
        card_id=action_key.card_id,
    )


def _legacy_runtime_action_components(
    action_key: ActionKey,
) -> tuple[object, object, object, str, tuple[tuple[str, object], ...], int | None, dict[str, object]]:
    """Recreate the exact v1 ActionKey core for an already-validated live key.

    The original Student format predated Tool/Skill/SpecialCondition labels and
    did not include their newer locator fields.  Keeping that projection here
    lets a model-v1 artifact run honestly instead of silently scoring in the
    incompatible v2 private-key space.
    """
    payload = tuple(
        (name, value)
        for name, value in action_key.canonical_payload
        if name in _LEGACY_ACTION_FIELDS
    )
    fields = dict(payload)
    option_type = action_key.option_type
    semantic_operation = OPTION_TYPE_NAMES.get(
        option_type if type(option_type) is int else None,
        f"OPTION_{option_type}",
    )
    source_entity_key = _canonical_feature_json(
        {name: fields[name] for name in ("area", "index", "energyIndex") if name in fields}
    ) if any(name in fields for name in ("area", "index", "energyIndex")) else None
    target_entity_key = _canonical_feature_json(
        {
            name: fields[name]
            for name in ("playerIndex", "inPlayArea", "inPlayIndex")
            if name in fields
        }
    ) if any(name in fields for name in ("playerIndex", "inPlayArea", "inPlayIndex")) else None
    core = {
        "canonical_payload": [list(item) for item in payload],
        "card_id": action_key.card_id,
        "context": action_key.context,
        "option_type": option_type,
        "selection_type": action_key.selection_type,
        "semantic_operation": semantic_operation,
        "source_entity_key": source_entity_key,
        "target_entity_key": target_entity_key,
    }
    return (
        action_key.selection_type,
        action_key.context,
        option_type,
        semantic_operation,
        payload,
        action_key.card_id,
        core,
    )


def _legacy_runtime_action_features(action_key: ActionKey) -> list[float]:
    selection_type, context, option_type, operation, payload, card_id, _core = (
        _legacy_runtime_action_components(action_key)
    )
    return _action_feature_vector(
        selection_type=selection_type,
        context=context,
        option_type=option_type,
        semantic_operation=operation,
        canonical_payload=payload,
        card_id=card_id,
    )


def _legacy_runtime_action_id(action_key: ActionKey) -> str:
    *_components, core = _legacy_runtime_action_components(action_key)
    return hashlib.sha256(
        _LEGACY_ACTION_KEY_HASH_PREFIX
        + _canonical_feature_json(core).encode("utf-8")
    ).hexdigest()


def serialized_action_feature_domain(payload: object) -> str:
    """Classify a stored feature action without promoting it to a live key."""
    if not isinstance(payload, Mapping):
        raise DecisionStateError("serialized action feature payload must be a mapping")
    if set(payload) == _PUBLIC_ACTION_TRACE_FIELDS:
        return PUBLIC_ACTION_FEATURE_DOMAIN
    if payload.get("action_key_schema_version") == 2:
        return PRIVATE_ACTIONKEY_FEATURE_DOMAIN
    return LEGACY_ACTIONKEY_FEATURE_DOMAIN


def runtime_action_features(action_key: ActionKey, *, domain: object) -> list[float]:
    """Use the exact domain bound into the model artifact for live candidates."""
    if not isinstance(action_key, ActionKey):
        raise DecisionStateError("runtime feature extraction requires a live ActionKey")
    if domain == PUBLIC_ACTION_FEATURE_DOMAIN:
        payload = action_key.to_public_trace_payload()
        return public_action_features(payload, digest=public_action_id_v1(payload))
    if domain == PRIVATE_ACTIONKEY_FEATURE_DOMAIN:
        return action_features(action_key)
    if domain == LEGACY_ACTIONKEY_FEATURE_DOMAIN:
        return _legacy_runtime_action_features(action_key)
    raise DecisionStateError("unsupported Student feature domain")


def runtime_action_id(action_key: ActionKey, *, domain: object) -> str:
    """Return the candidate identity used to resolve runtime score ties."""
    if not isinstance(action_key, ActionKey):
        raise DecisionStateError("runtime feature extraction requires a live ActionKey")
    if domain == PUBLIC_ACTION_FEATURE_DOMAIN:
        return public_action_id_v1(action_key.to_public_trace_payload())
    if domain == PRIVATE_ACTIONKEY_FEATURE_DOMAIN:
        return action_key.digest
    if domain == LEGACY_ACTIONKEY_FEATURE_DOMAIN:
        return _legacy_runtime_action_id(action_key)
    raise DecisionStateError("unsupported Student feature domain")


def public_action_features(payload: object, *, digest: object) -> list[float]:
    """Encode a validated C5 public projection without making an ActionKey.

    A C5 public-action ID is a different identity domain from the private
    ActionKey v2 digest.  The public payload is therefore validated and hashed
    independently, and only its already-redacted fields enter the vector.
    """
    data = validate_public_action_feature_payload(payload)
    if type(digest) is not str or digest != public_action_id_v1(data):
        raise DecisionStateError("public action feature digest does not verify")
    identity = data["public_identity"]
    assert isinstance(identity, Mapping)  # validated above
    fields = identity.get("fields")
    if isinstance(fields, Mapping):
        pairs = tuple(
            (
                name,
                value
                if value is None or type(value) in (str, int, float, bool)
                else _canonical_feature_json(value),
            )
            for name, value in sorted(fields.items())
            if isinstance(name, str)
        )
    else:
        pairs = tuple(
            (f"public_identity[{index}]", token)
            for index, token in enumerate(
                _flatten_scalars(identity, prefix="public_identity")
            )
        )
    return _action_feature_vector(
        selection_type=data["selection_type"],
        context=data["context"],
        option_type=data["option_type"],
        semantic_operation=str(data["semantic_operation"]),
        canonical_payload=pairs,
        card_id=None,
    )


def serialized_action_features(payload: object, *, digest: object) -> list[float]:
    """Read one explicit private-v2, public-v2, or feature-only-v1 artifact."""
    if not isinstance(payload, Mapping):
        raise DecisionStateError("serialized action feature payload must be a mapping")
    fields = set(payload)
    if fields == _PUBLIC_ACTION_TRACE_FIELDS:
        return public_action_features(payload, digest=digest)
    domain = serialized_action_feature_domain(payload)
    if domain == PRIVATE_ACTIONKEY_FEATURE_DOMAIN:
        view = ActionKey.from_serialized_v2_feature_payload(payload, digest=digest)
        return action_features(view)
    if domain == LEGACY_ACTIONKEY_FEATURE_DOMAIN:
        key = ActionKey.from_legacy_v1_feature_payload(payload, digest=digest)
    else:
        raise DecisionStateError("unsupported serialized Student feature domain")
    return action_features(key)


def combined_features(view: ActorInformationView, action_key: ActionKey) -> list[float]:
    return [*state_features(view), *action_features(action_key)]


__all__ = [
    "ACTION_FEATURE_DIM",
    "ACTION_FEATURE_DOMAINS",
    "FEATURE_VERSION",
    "LEGACY_ACTIONKEY_FEATURE_DOMAIN",
    "PRIVATE_ACTIONKEY_FEATURE_DOMAIN",
    "PUBLIC_ACTION_FEATURE_DOMAIN",
    "STATE_FEATURE_DIM",
    "action_features",
    "combined_features",
    "public_action_features",
    "runtime_action_id",
    "runtime_action_features",
    "serialized_action_feature_domain",
    "serialized_action_features",
    "state_features",
    "state_features_payload",
]
