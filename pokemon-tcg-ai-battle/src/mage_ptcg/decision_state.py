"""Privacy-safe, deterministic C1 decision contracts for cabt observations.

The constructors in this module project an official agent observation through
an explicit allowlist.  They never retain the raw observation and never read
opponent hand contents, either player's prize contents, engine-private fields,
or the observation log.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, replace
import hashlib
import json
from typing import Any, TypeAlias

from mage_ptcg.observability.cabt_trace import (
    CARD_LIST_COUNT_FIELDS,
    CARD_SCALAR_FIELDS,
    OPTION_TYPE_NAMES,
    STATUS_FLAG_FIELDS,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_SELECTION_CONTEXTS_V1,
)


SCHEMA_VERSION = 1
_HASH_PREFIX = b"mage_ptcg.decision_state:v1\0"
ACTION_KEY_SCHEMA_VERSION = 2
_ACTION_KEY_HASH_PREFIX = b"mage_ptcg.decision_state.action_key:v2\0"
_PUBLIC_ACTION_ID_HASH_PREFIX = b"mage_ptcg:public-action:v1\0"
_MAX_VISIBLE_HISTORY = 64
_OFFICIAL_OPTION_TYPE_NAMES: dict[int, str] = {
    4: "TOOL_CARD",
    15: "SKILL",
    16: "SPECIAL_CONDITION",
}
_OFFICIAL_OPTION_TYPE_LABELS: dict[int, str] = {
    4: "ToolCard",
    15: "Skill",
    16: "SpecialCondition",
}
_OPTION_TYPES_BY_SELECTION_TYPE: dict[int, frozenset[int]] = {
    0: frozenset({7, 8, 9, 10, 11, 12, 13, 14}),
    1: frozenset({3}),
    2: frozenset({4, 5}),
    3: frozenset({3, 4, 5}),
    4: frozenset({6}),
    5: frozenset({15}),
    6: frozenset({13}),
    7: frozenset({9}),
    8: frozenset({0}),
    9: frozenset({1, 2}),
    10: frozenset({16}),
}
_ACTOR_FIELDS_BY_OPTION_TYPE: dict[int, tuple[str, ...]] = {
    0: ("number",),
    1: (),
    2: (),
    3: ("area", "index", "playerIndex"),
    4: ("area", "index", "playerIndex", "toolIndex"),
    5: ("area", "index", "playerIndex", "energyIndex"),
    6: ("area", "index", "playerIndex", "energyIndex", "count"),
    7: ("index",),
    8: ("area", "index", "inPlayArea", "inPlayIndex"),
    9: ("area", "index", "inPlayArea", "inPlayIndex"),
    10: ("area", "index"),
    11: ("area", "index"),
    12: (),
    13: ("attackId",),
    14: (),
    15: ("cardId", "serial"),
    16: ("specialConditionType",),
}
_NONNEGATIVE_ACTOR_FIELDS = frozenset(
    {
        "area", "index", "playerIndex", "toolIndex", "energyIndex", "count",
        "inPlayArea", "inPlayIndex", "attackId", "cardId", "serial", "number",
    }
)
# The CABT ApiJson option union is closed.  Public generic projections retain
# only fields that the builder intentionally emits for that exact option type;
# raw CABT locators (area/index/playerIndex/toolIndex) stay actor-private.
_GENERIC_PUBLIC_FIELDS_BY_OPTION_TYPE: dict[int, frozenset[str]] = {
    0: frozenset({"number"}),
    1: frozenset(),
    2: frozenset(),
    3: frozenset(),
    5: frozenset({"energyIndex"}),
    6: frozenset({"count", "energyIndex"}),
    7: frozenset(),
    8: frozenset({"inPlayArea", "inPlayIndex"}),
    9: frozenset({"inPlayArea", "inPlayIndex"}),
    10: frozenset(),
    11: frozenset(),
    12: frozenset(),
    13: frozenset({"attackId"}),
    14: frozenset(),
}
_SUPPORTED_OPTION_TYPES = frozenset(_ACTOR_FIELDS_BY_OPTION_TYPE)
_SPECIAL_CONDITION_NAMES: dict[int, str] = {
    0: "POISON",
    1: "BURN",
    2: "SLEEP",
    3: "PARALYZE",
    4: "CONFUSE",
}
_TOOL_HOST_AREAS: dict[int, str] = {4: "active", 5: "bench"}

JsonScalar: TypeAlias = str | int | float | bool | None


class DecisionStateError(ValueError):
    """Raised when a decision observation cannot be projected safely."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionStateError("value is not canonical JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_HASH_PREFIX + _canonical_json(value).encode("utf-8")).hexdigest()


def _action_key_digest(value: object) -> str:
    """Return the versioned private ActionKey digest, never a trace ID."""
    return hashlib.sha256(
        _ACTION_KEY_HASH_PREFIX + _canonical_json(value).encode("utf-8")
    ).hexdigest()


def public_action_id_v1(value: object) -> str:
    """Return the frozen C5 public-action ID for a finite JSON projection."""
    return hashlib.sha256(
        _PUBLIC_ACTION_ID_HASH_PREFIX + _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionStateError(f"{field} must be a mapping")
    return value


def _strict_int(value: object, *, field: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise DecisionStateError(f"{field} must be an int and must not be bool")
    if minimum is not None and value < minimum:
        raise DecisionStateError(f"{field} must be at least {minimum}")
    return value


def _json_scalar(value: object) -> JsonScalar | None:
    if value is None or type(value) in (str, int, float, bool):
        return value
    # A mapping or non-string sequence can carry a convenient-looking ``name``
    # attribute, but it is still structured payload and is never scalar input.
    # Bytes are deliberately excluded: they are a sequence, not JSON text.
    if isinstance(value, Mapping) or isinstance(value, Sequence):
        return None
    enum_name = getattr(value, "name", None)
    return enum_name.rsplit(".", 1)[-1].upper() if isinstance(enum_name, str) else None


def _required_list(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise DecisionStateError(f"{field} must be a list")
    return value


_BASE_CARD_FIELDS = frozenset({"id", "serial", "playerIndex"})
_POKEMON_CARD_FIELDS = frozenset(
    {
        "id",
        "serial",
        "hp",
        "maxHp",
        "appearThisTurn",
        "energies",
        "energyCards",
        "tools",
        "preEvolution",
    }
)


def _visible_card(
    card: object,
    *,
    field: str,
    require_pokemon: bool,
    require_player_index: bool,
    allow_none: bool,
) -> dict[str, object] | None:
    if card is None:
        if allow_none:
            return None
        raise DecisionStateError(f"{field} must contain a card")
    data = _mapping(card, field=field)
    required = _POKEMON_CARD_FIELDS if require_pokemon else _BASE_CARD_FIELDS
    if not require_player_index:
        required = required - {"playerIndex"}
    missing = sorted(required - set(data))
    if missing:
        raise DecisionStateError(f"{field} is missing required card fields: {', '.join(missing)}")
    fields: dict[str, JsonScalar] = {}
    for key in CARD_SCALAR_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key == "appearThisTurn":
            if type(value) is not bool:
                raise DecisionStateError(f"{field}.{key} must be a bool")
            fields[key] = value
        elif key == "playerIndex":
            player_index = _strict_int(value, field=f"{field}.{key}")
            if player_index not in (0, 1):
                raise DecisionStateError(f"{field}.{key} must be 0 or 1")
            fields[key] = player_index
        else:
            fields[key] = _strict_int(value, field=f"{field}.{key}", minimum=0)
    for key in CARD_LIST_COUNT_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, list):
            raise DecisionStateError(f"{field}.{key} must be a list")
        fields[f"{key}_count"] = len(value)
    return {"fields": fields}


def _visible_zone(player: Mapping[str, Any], name: str) -> list[dict[str, object] | None]:
    zone = _required_list(player.get(name), field=f"player.{name}")
    if name == "active":
        if len(zone) > 1:
            raise DecisionStateError("player.active must contain at most one Pokemon or null slot")
        return [
            _visible_card(
                card,
                field="player.active[]",
                require_pokemon=True,
                require_player_index=False,
                allow_none=True,
            )
            for card in zone
        ]
    if name == "bench":
        return [
            _visible_card(
                card,
                field="player.bench[]",
                require_pokemon=True,
                require_player_index=False,
                allow_none=False,
            )
            for card in zone
        ]
    if name == "discard":
        return [
            _visible_card(
                card,
                field="player.discard[]",
                require_pokemon=False,
                require_player_index=True,
                allow_none=False,
            )
            for card in zone
        ]
    raise DecisionStateError(f"unsupported public card zone {name!r}")


def _public_player(player: Mapping[str, Any]) -> dict[str, object]:
    hand_count = _strict_int(player.get("handCount"), field="player.handCount", minimum=0)
    deck_count = _strict_int(player.get("deckCount"), field="player.deckCount", minimum=0)
    prize = _required_list(player.get("prize"), field="player.prize")
    bench_max = _strict_int(player.get("benchMax"), field="player.benchMax", minimum=0)
    status: dict[str, bool] = {}
    for field in STATUS_FLAG_FIELDS:
        value = player.get(field)
        if type(value) is not bool:
            raise DecisionStateError(f"player.{field} must be a bool")
        status[field] = value
    return {
        "active": _visible_zone(player, "active"),
        "bench": _visible_zone(player, "bench"),
        "bench_max": bench_max,
        "deck_count": deck_count,
        "discard": _visible_zone(player, "discard"),
        "hand_count": hand_count,
        "prize_count": len(prize),
        "status": status,
    }


def _own_hand_card_ids(player: Mapping[str, Any]) -> tuple[int, ...]:
    hand = _required_list(player.get("hand"), field="self.hand")
    card_ids: list[int] = []
    for index, card in enumerate(hand):
        data = _mapping(card, field=f"self.hand[{index}]")
        card_ids.append(_strict_int(data.get("id"), field=f"self.hand[{index}].id"))
    return tuple(card_ids)


def _own_private_state(card_ids: Sequence[int]) -> dict[str, object]:
    return {
        "hand_card_ids": sorted(card_ids),
        "visibility_basis": "acting_player_hand",
    }


def _board(current: Mapping[str, Any]) -> dict[str, object]:
    stadium_raw = current.get("stadium")
    if not isinstance(stadium_raw, list) or len(stadium_raw) > 1:
        raise DecisionStateError("current.stadium must be a list with at most one card")
    stadium: dict[str, int] | None = None
    if stadium_raw:
        stadium_card = _mapping(stadium_raw[0], field="current.stadium[0]")
        missing = sorted(_BASE_CARD_FIELDS - set(stadium_card))
        if missing:
            raise DecisionStateError(
                "current.stadium[0] is missing required card fields: "
                + ", ".join(missing)
            )
        _strict_int(stadium_card.get("serial"), field="current.stadium[0].serial", minimum=0)
        stadium_player_index = _strict_int(
            stadium_card.get("playerIndex"), field="current.stadium[0].playerIndex"
        )
        if stadium_player_index not in (0, 1):
            raise DecisionStateError("current.stadium[0].playerIndex must be 0 or 1")
        stadium = {
            "id": _strict_int(
                stadium_card.get("id"), field="current.stadium[0].id", minimum=1
            )
        }

    result: dict[str, object] = {"stadium": stadium}
    for raw_name, output_name in (
        ("stadiumPlayed", "stadium_played"),
        ("supporterPlayed", "supporter_played"),
        ("energyAttached", "energy_attached"),
        ("retreated", "retreated"),
    ):
        value = current.get(raw_name)
        if type(value) is not bool:
            raise DecisionStateError(f"current.{raw_name} must be a bool")
        result[output_name] = value
    return result


def _entity_key(fields: Mapping[str, JsonScalar], names: tuple[str, ...]) -> str | None:
    selected = {name: fields[name] for name in names if name in fields}
    return _canonical_json(selected) if selected else None


@dataclass(frozen=True, slots=True, repr=False)
class ActionKey:
    """Separate transient actor identity from the sole persisted public identity.

    ``canonical_payload`` remains the legacy actor-facing compatibility field.
    It is private and must not be used for trace serialization.  New builders
    also set ``actor_identity_payload`` explicitly and persist only the
    already-built ``public_identity_json`` through :meth:`to_public_trace_payload`.
    """

    selection_type: JsonScalar
    context: JsonScalar
    option_type: JsonScalar
    semantic_operation: str
    source_entity_key: str | None
    target_entity_key: str | None
    card_id: int | None
    canonical_payload: tuple[tuple[str, JsonScalar], ...]
    digest: str
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...] | None = None
    public_identity_json: str | None = None
    action_key_schema_version: int = ACTION_KEY_SCHEMA_VERSION
    feature_only_legacy_v1: bool = False
    public_resolution: InitVar[Mapping[str, object] | None] = None

    def __post_init__(
        self,
        public_resolution: Mapping[str, object] | None,
    ) -> None:
        """Verify the bound private/public identity pair eagerly."""
        _validate_action_key_fields(self)
        if self.action_key_schema_version == 1:
            if self.feature_only_legacy_v1 is not True:
                raise DecisionStateError(
                    "legacy ActionKey v1 requires the explicit feature-only reader"
                )
            if self.public_identity_json is not None:
                raise DecisionStateError(
                    "feature-only ActionKey v1 cannot carry a public identity"
                )
            if self.actor_identity_payload != self.canonical_payload:
                raise DecisionStateError(
                    "feature-only ActionKey v1 actor payload must equal canonical_payload"
                )
            expected_digest = _digest(_legacy_action_key_core(self))
            if self.digest != expected_digest:
                raise DecisionStateError("legacy ActionKey v1 digest does not verify")
            return
        if self.action_key_schema_version != ACTION_KEY_SCHEMA_VERSION:
            raise DecisionStateError("unsupported ActionKey schema version")
        if self.feature_only_legacy_v1 is not False:
            raise DecisionStateError("ActionKey v2 cannot be marked feature-only legacy v1")
        if self.actor_identity_payload is None:
            raise DecisionStateError("ActionKey v2 requires an explicit actor identity payload")
        if self.actor_identity_payload != self.canonical_payload:
            raise DecisionStateError(
                "ActionKey v2 actor identity must equal canonical_payload"
            )
        if self.public_identity_json is None:
            raise DecisionStateError("ActionKey v2 requires an explicit public identity")
        try:
            decoded = json.loads(self.public_identity_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DecisionStateError("ActionKey public identity must be canonical JSON") from exc
        if not isinstance(decoded, dict):
            raise DecisionStateError("ActionKey public identity must be an object")
        if _canonical_json(decoded) != self.public_identity_json:
            raise DecisionStateError("ActionKey public identity must be canonical JSON")
        _validate_v2_metadata(self)
        _validate_official_actor_identity(self)
        _validate_public_trace_payload(
            decoded,
            self,
            public_resolution=public_resolution,
            require_public_resolution=True,
        )
        expected_digest = _action_key_digest(_action_key_v2_core(self, decoded))
        if self.digest != expected_digest:
            raise DecisionStateError("ActionKey v2 digest does not verify")

    def __repr__(self) -> str:
        """Avoid exposing actor-private card identity through diagnostic reprs."""
        return (
            "ActionKey("
            f"selection_type={self.selection_type!r}, context={self.context!r}, "
            f"option_type={self.option_type!r}, semantic_operation={self.semantic_operation!r}, "
            f"action_key_schema_version={self.action_key_schema_version!r}, "
            "card_id=<redacted>, actor_identity_payload=<redacted>, "
            "canonical_payload=<redacted>, digest=<redacted>)"
        )

    def to_canonical_payload(self) -> dict[str, object]:
        if self.action_key_schema_version == 1:
            return _legacy_action_key_core(self)
        payload: dict[str, object] = {
            "action_key_schema_version": self.action_key_schema_version,
            "actor_identity_payload": [
                list(item) for item in (self.actor_identity_payload or ())
            ],
            "canonical_payload": [list(item) for item in self.canonical_payload],
            "card_id": self.card_id,
            "context": self.context,
            "option_type": self.option_type,
            "selection_type": self.selection_type,
            "semantic_operation": self.semantic_operation,
            "source_entity_key": self.source_entity_key,
            "target_entity_key": self.target_entity_key,
        }
        if self.action_key_schema_version == ACTION_KEY_SCHEMA_VERSION:
            payload["public_identity_payload"] = self.to_public_trace_payload()
        return payload

    def to_public_trace_payload(self) -> dict[str, object]:
        """Return only the stored public identity; never redact actor data late."""
        if self.action_key_schema_version != ACTION_KEY_SCHEMA_VERSION:
            raise DecisionStateError(
                "feature-only ActionKey v1 has no public trace projection"
            )
        assert self.public_identity_json is not None  # established by __post_init__
        return json.loads(self.public_identity_json)

    @classmethod
    def from_serialized_payload(
        cls,
        payload: object,
        *,
        digest: object,
        public_resolution: Mapping[str, object] | None = None,
    ) -> "ActionKey":
        """Read an explicitly-versioned v2 record and reverify its digest."""
        data = _mapping(payload, field="serialized ActionKey payload")
        version = data.get("action_key_schema_version")
        if type(version) is not int or version != ACTION_KEY_SCHEMA_VERSION:
            raise DecisionStateError(
                "serialized ActionKey requires v2; use the explicit v1 feature reader"
            )
        expected_fields = {
            "action_key_schema_version",
            "actor_identity_payload",
            "canonical_payload",
            "card_id",
            "context",
            "option_type",
            "public_identity_payload",
            "selection_type",
            "semantic_operation",
            "source_entity_key",
            "target_entity_key",
        }
        _require_exact_keys(data, expected_fields, field="serialized ActionKey v2 payload")
        actor_payload = _serialized_action_pairs(
            data.get("actor_identity_payload"),
            field="serialized ActionKey actor_identity_payload",
        )
        canonical_payload = _serialized_action_pairs(
            data.get("canonical_payload"),
            field="serialized ActionKey canonical_payload",
        )
        public_identity_payload = _mapping(
            data.get("public_identity_payload"),
            field="serialized ActionKey public_identity_payload",
        )
        return cls(
            selection_type=data.get("selection_type"),
            context=data.get("context"),
            option_type=data.get("option_type"),
            semantic_operation=_required_string(
                data.get("semantic_operation"),
                field="serialized ActionKey semantic_operation",
            ),
            source_entity_key=_optional_string(
                data.get("source_entity_key"),
                field="serialized ActionKey source_entity_key",
            ),
            target_entity_key=_optional_string(
                data.get("target_entity_key"),
                field="serialized ActionKey target_entity_key",
            ),
            card_id=_optional_strict_int(
                data.get("card_id"), field="serialized ActionKey card_id"
            ),
            canonical_payload=canonical_payload,
            digest=_required_digest(digest, field="serialized ActionKey digest"),
            actor_identity_payload=actor_payload,
            public_identity_json=_canonical_json(dict(public_identity_payload)),
            action_key_schema_version=ACTION_KEY_SCHEMA_VERSION,
            public_resolution=public_resolution,
        )

    @classmethod
    def from_serialized_v2_feature_payload(
        cls,
        payload: object,
        *,
        digest: object,
    ) -> "SerializedActionFeatureView":
        """Read a private v2 C4 feature artifact without claiming C5 membership.

        This validates the v2 identity/digest and actor/public relational
        checks, but deliberately does not assert that a historical public
        locator still belongs to a board.  Only the C5 persistence reader may
        make that claim, by supplying an exact public resolution context.
        """
        data = _mapping(payload, field="serialized ActionKey feature payload")
        version = data.get("action_key_schema_version")
        if type(version) is not int or version != ACTION_KEY_SCHEMA_VERSION:
            raise DecisionStateError("serialized ActionKey feature payload requires v2")
        _require_exact_keys(
            data,
            {
                "action_key_schema_version",
                "actor_identity_payload",
                "canonical_payload",
                "card_id",
                "context",
                "option_type",
                "public_identity_payload",
                "selection_type",
                "semantic_operation",
                "source_entity_key",
                "target_entity_key",
            },
            field="serialized ActionKey v2 feature payload",
        )
        actor_payload = _serialized_action_pairs(
            data.get("actor_identity_payload"),
            field="serialized ActionKey feature actor_identity_payload",
        )
        canonical_payload = _serialized_action_pairs(
            data.get("canonical_payload"),
            field="serialized ActionKey feature canonical_payload",
        )
        if actor_payload != canonical_payload:
            raise DecisionStateError(
                "serialized ActionKey feature actor identity must equal canonical payload"
            )
        public_payload = _mapping(
            data.get("public_identity_payload"),
            field="serialized ActionKey feature public_identity_payload",
        )
        view = SerializedActionFeatureView(
            selection_type=data.get("selection_type"),
            context=data.get("context"),
            option_type=data.get("option_type"),
            semantic_operation=_required_string(
                data.get("semantic_operation"),
                field="serialized ActionKey feature semantic_operation",
            ),
            source_entity_key=_optional_string(
                data.get("source_entity_key"),
                field="serialized ActionKey feature source_entity_key",
            ),
            target_entity_key=_optional_string(
                data.get("target_entity_key"),
                field="serialized ActionKey feature target_entity_key",
            ),
            canonical_payload=canonical_payload,
            card_id=_optional_strict_int(
                data.get("card_id"), field="serialized ActionKey feature card_id"
            ),
            digest=_required_digest(digest, field="serialized ActionKey feature digest"),
        )
        _validate_feature_view_public_payload(view, public_payload)
        if view.digest != _action_key_digest(
            _action_key_v2_feature_view_core(view, public_payload)
        ):
            raise DecisionStateError("serialized ActionKey feature digest does not verify")
        return view

    @classmethod
    def from_legacy_v1_feature_payload(
        cls,
        payload: object,
        *,
        digest: object,
    ) -> "ActionKey":
        """Read a historical v1 feature artifact without making it persistable."""
        data = _mapping(payload, field="legacy ActionKey v1 feature payload")
        version = data.get("action_key_schema_version", 1)
        if type(version) is not int or version != 1:
            raise DecisionStateError(
                "legacy feature reader requires schema version to be exact integer 1"
            )
        expected_fields = {
            "canonical_payload",
            "card_id",
            "context",
            "option_type",
            "selection_type",
            "semantic_operation",
            "source_entity_key",
            "target_entity_key",
        }
        unexpected = set(data).difference(expected_fields | {"action_key_schema_version"})
        missing = expected_fields.difference(data)
        if unexpected or missing:
            raise DecisionStateError(
                "legacy ActionKey v1 feature payload has unexpected or missing fields"
            )
        canonical_payload = _serialized_action_pairs(
            data.get("canonical_payload"),
            field="legacy ActionKey v1 canonical_payload",
        )
        return cls(
            selection_type=data.get("selection_type"),
            context=data.get("context"),
            option_type=data.get("option_type"),
            semantic_operation=_required_string(
                data.get("semantic_operation"),
                field="legacy ActionKey v1 semantic_operation",
            ),
            source_entity_key=_optional_string(
                data.get("source_entity_key"),
                field="legacy ActionKey v1 source_entity_key",
            ),
            target_entity_key=_optional_string(
                data.get("target_entity_key"),
                field="legacy ActionKey v1 target_entity_key",
            ),
            card_id=_optional_strict_int(
                data.get("card_id"), field="legacy ActionKey v1 card_id"
            ),
            canonical_payload=canonical_payload,
            digest=_required_digest(digest, field="legacy ActionKey v1 digest"),
            actor_identity_payload=canonical_payload,
            public_identity_json=None,
            action_key_schema_version=1,
            feature_only_legacy_v1=True,
        )


@dataclass(frozen=True, slots=True, repr=False)
class SerializedActionFeatureView:
    """Validated private-v2 feature material that cannot enter C1/C5 traces."""

    selection_type: int
    context: int
    option_type: int
    semantic_operation: str
    source_entity_key: str | None
    target_entity_key: str | None
    canonical_payload: tuple[tuple[str, JsonScalar], ...]
    card_id: int | None
    digest: str

    def __post_init__(self) -> None:
        _validate_v2_actor_identity(
            self.selection_type,
            self.context,
            self.option_type,
            self.semantic_operation,
            self.canonical_payload,
            self.source_entity_key,
            self.target_entity_key,
            field="serialized feature view",
        )
        _optional_strict_int(self.card_id, field="serialized feature view card_id")
        _required_digest(self.digest, field="serialized feature view digest")

    def __repr__(self) -> str:
        return "SerializedActionFeatureView(actor_identity_payload=<redacted>)"


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    field: str,
) -> None:
    if set(value) != expected:
        raise DecisionStateError(f"{field} has unexpected or missing fields")


def _required_string(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise DecisionStateError(f"{field} must be a nonempty string")
    return value


def _optional_string(value: object, *, field: str) -> str | None:
    if value is not None and type(value) is not str:
        raise DecisionStateError(f"{field} must be a string or None")
    return value


def _optional_strict_int(value: object, *, field: str) -> int | None:
    if value is not None and type(value) is not int:
        raise DecisionStateError(f"{field} must be an int or None and must not be bool")
    return value


def _required_digest(value: object, *, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DecisionStateError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _serialized_action_pairs(
    value: object,
    *,
    field: str,
) -> tuple[tuple[str, JsonScalar], ...]:
    if not isinstance(value, list):
        raise DecisionStateError(f"{field} must be a list")
    pairs: list[tuple[str, JsonScalar]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2 or type(item[0]) is not str:
            raise DecisionStateError(f"{field} must contain [name, scalar] pairs")
        scalar = item[1]
        if scalar is not None and type(scalar) not in (str, int, float, bool):
            raise DecisionStateError(f"{field} values must be JSON scalars")
        pairs.append((item[0], scalar))
    return tuple(pairs)


def _validate_action_pairs(
    value: object,
    *,
    field: str,
) -> tuple[tuple[str, JsonScalar], ...]:
    if not isinstance(value, tuple):
        raise DecisionStateError(f"{field} must be an immutable tuple")
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or type(item[0]) is not str
            or (
                item[1] is not None
                and type(item[1]) not in (str, int, float, bool)
            )
        ):
            raise DecisionStateError(f"{field} must contain (name, scalar) pairs")
    keys = tuple(item[0] for item in value)
    if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
        raise DecisionStateError(f"{field} keys must be unique and sorted")
    _canonical_json([list(item) for item in value])
    return value


def _validate_action_key_fields(key: ActionKey) -> None:
    if type(key.action_key_schema_version) is not int:
        raise DecisionStateError("ActionKey schema version must be a non-bool int")
    if type(key.feature_only_legacy_v1) is not bool:
        raise DecisionStateError("ActionKey feature-only marker must be a bool")
    for name, value in (
        ("selection_type", key.selection_type),
        ("context", key.context),
        ("option_type", key.option_type),
    ):
        if value is not None and type(value) not in (str, int, float, bool):
            raise DecisionStateError(f"ActionKey {name} must be a JSON scalar")
        _canonical_json(value)
    _required_string(key.semantic_operation, field="ActionKey semantic_operation")
    _optional_string(key.source_entity_key, field="ActionKey source_entity_key")
    _optional_string(key.target_entity_key, field="ActionKey target_entity_key")
    _optional_strict_int(key.card_id, field="ActionKey card_id")
    _validate_action_pairs(key.canonical_payload, field="ActionKey canonical_payload")
    if key.actor_identity_payload is not None:
        _validate_action_pairs(
            key.actor_identity_payload,
            field="ActionKey actor_identity_payload",
        )
    _required_digest(key.digest, field="ActionKey digest")


def _validate_v2_metadata(key: ActionKey) -> None:
    """Require the three CABT enum fields to be exact, non-bool integers."""
    for name, value in (
        ("selection_type", key.selection_type),
        ("context", key.context),
        ("option_type", key.option_type),
    ):
        if type(value) is not int:
            raise DecisionStateError(
                f"ActionKey v2 {name} must be a non-bool int"
            )


def _official_actor_int(
    value: object,
    *,
    action_name: str,
    field: str,
    minimum: int | None = None,
) -> int:
    if type(value) is not int:
        raise DecisionStateError(
            f"{action_name} actor identity {field} must be a non-bool int"
        )
    if minimum is not None and value < minimum:
        raise DecisionStateError(
            f"{action_name} actor identity {field} must be at least {minimum}"
        )
    return value


def _official_actor_fields(
    key: ActionKey,
    *,
    action_name: str,
    expected_names: tuple[str, ...],
) -> dict[str, JsonScalar]:
    assert key.actor_identity_payload is not None
    actual_names = tuple(name for name, _value in key.actor_identity_payload)
    if actual_names != expected_names:
        raise DecisionStateError(
            f"{action_name} actor identity must contain exactly {expected_names!r}"
        )
    return dict(key.actor_identity_payload)


def _validate_selection_schema(
    selection_type: object,
    context: object,
    *,
    field: str,
) -> tuple[int, int]:
    normalized_type = _strict_int(selection_type, field=f"{field}.type")
    normalized_context = _strict_int(context, field=f"{field}.context")
    if normalized_context not in CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.get(
        normalized_type, ()
    ):
        raise DecisionStateError(f"{field} is not a recognized CABT selection schema")
    return normalized_type, normalized_context


def _validate_selection_option_schema(
    selection_type: object,
    context: object,
    option_type: object,
    *,
    field: str,
) -> tuple[int, int, int]:
    normalized_type, normalized_context = _validate_selection_schema(
        selection_type, context, field=field
    )
    normalized_option_type = _strict_int(option_type, field=f"{field}.option.type")
    if normalized_option_type not in _OPTION_TYPES_BY_SELECTION_TYPE[normalized_type]:
        raise DecisionStateError(
            f"{field} option type is not allowed by its CABT selection schema"
        )
    return normalized_type, normalized_context, normalized_option_type


def _actor_fields_from_option(
    option: Mapping[str, Any],
    *,
    option_type: int,
    field: str,
) -> dict[str, int]:
    expected_names = _ACTOR_FIELDS_BY_OPTION_TYPE[option_type]
    if set(option) != {"type", *expected_names}:
        raise DecisionStateError(
            f"{field} actor identity must contain exactly type and {expected_names!r}"
        )
    fields: dict[str, int] = {}
    for name in expected_names:
        minimum = 0 if name in _NONNEGATIVE_ACTOR_FIELDS else None
        fields[name] = _strict_int(option.get(name), field=f"{field}.{name}", minimum=minimum)
        if name == "playerIndex" and fields[name] not in (0, 1):
            raise DecisionStateError(f"{field}.{name} must be 0 or 1")
    if option_type == 16 and fields["specialConditionType"] not in _SPECIAL_CONDITION_NAMES:
        raise DecisionStateError("specialConditionType must be in range 0..4")
    return fields


def _validate_official_actor_identity(key: ActionKey) -> None:
    """Validate the frozen source-literal actor union on every v2 path."""
    assert key.actor_identity_payload is not None
    _validate_v2_actor_identity(
        key.selection_type,
        key.context,
        key.option_type,
        key.semantic_operation,
        key.actor_identity_payload,
        key.source_entity_key,
        key.target_entity_key,
        field="ActionKey",
    )


def _validate_v2_actor_identity(
    selection_type: object,
    context: object,
    option_type: object,
    semantic_operation: object,
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...],
    source_entity_key: object,
    target_entity_key: object,
    *,
    field: str,
) -> None:
    """Validate a v2 actor tuple without materializing a persistable key."""
    _validate_action_pairs(actor_identity_payload, field=f"{field} actor identity")
    _selection_type, _context, normalized_option_type = _validate_selection_option_schema(
        selection_type,
        context,
        option_type,
        field=field,
    )
    action_name = _OFFICIAL_OPTION_TYPE_LABELS.get(normalized_option_type, "Option")
    expected_operation = _OFFICIAL_OPTION_TYPE_NAMES.get(
        normalized_option_type,
        OPTION_TYPE_NAMES.get(normalized_option_type, f"OPTION_{normalized_option_type}"),
    )
    if _required_string(semantic_operation, field=f"{field} semantic_operation") != expected_operation:
        raise DecisionStateError(
            f"{action_name} actor identity requires operation {expected_operation}"
        )
    expected_names = tuple(sorted(_ACTOR_FIELDS_BY_OPTION_TYPE[normalized_option_type]))
    actual_names = tuple(name for name, _value in actor_identity_payload)
    if actual_names != expected_names:
        raise DecisionStateError(
            f"{action_name} actor identity must contain exactly {expected_names!r}"
        )
    fields = dict(actor_identity_payload)
    for name, value in fields.items():
        minimum = 0 if name in _NONNEGATIVE_ACTOR_FIELDS else None
        normalized_value = _official_actor_int(
            value, action_name=action_name, field=name, minimum=minimum
        )
        if name == "playerIndex" and normalized_value not in (0, 1):
            raise DecisionStateError(
                f"{action_name} actor identity playerIndex must be 0 or 1"
            )
    if (
        normalized_option_type == 16
        and fields["specialConditionType"] not in _SPECIAL_CONDITION_NAMES
    ):
        raise DecisionStateError(
            "SpecialCondition actor identity specialConditionType must be in range 0..4"
        )

    expected_source = _entity_key(fields, ("area", "index", "energyIndex"))
    expected_target = _entity_key(
        fields, ("playerIndex", "inPlayArea", "inPlayIndex")
    )
    if (
        _optional_string(source_entity_key, field=f"{field} source_entity_key")
        != expected_source
        or _optional_string(target_entity_key, field=f"{field} target_entity_key")
        != expected_target
    ):
        raise DecisionStateError(
            f"{action_name} actor identity does not match its entity keys"
        )


def _legacy_action_key_core(key: ActionKey) -> dict[str, object]:
    return {
        "canonical_payload": [list(item) for item in key.canonical_payload],
        "card_id": key.card_id,
        "context": key.context,
        "option_type": key.option_type,
        "selection_type": key.selection_type,
        "semantic_operation": key.semantic_operation,
        "source_entity_key": key.source_entity_key,
        "target_entity_key": key.target_entity_key,
    }


def _action_key_v2_core(
    key: ActionKey,
    public_identity_payload: Mapping[str, object],
) -> dict[str, object]:
    assert key.actor_identity_payload is not None
    return {
        "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
        "actor_identity_payload": [list(item) for item in key.actor_identity_payload],
        "card_id": key.card_id,
        "context": key.context,
        "option_type": key.option_type,
        "selection_type": key.selection_type,
        "semantic_operation": key.semantic_operation,
        "source_entity_key": key.source_entity_key,
        "target_entity_key": key.target_entity_key,
        "public_identity_payload": dict(public_identity_payload),
    }


def _action_key_v2_feature_view_core(
    view: SerializedActionFeatureView,
    public_identity_payload: Mapping[str, object],
) -> dict[str, object]:
    """Return the private-v2 digest core for the nonpersistable view."""
    return {
        "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
        "actor_identity_payload": [list(item) for item in view.canonical_payload],
        "card_id": view.card_id,
        "context": view.context,
        "option_type": view.option_type,
        "selection_type": view.selection_type,
        "semantic_operation": view.semantic_operation,
        "source_entity_key": view.source_entity_key,
        "target_entity_key": view.target_entity_key,
        "public_identity_payload": dict(public_identity_payload),
    }


def build_action_key(
    *,
    selection_type: object,
    context: object,
    option: object,
    card_id: int | None = None,
) -> ActionKey:
    """Build a safe public ActionKey projection from actor data alone.

    Callers cannot choose a public projection.  In particular, direct Skill
    construction is deliberately redacted and direct ToolCard construction
    fails closed because only :func:`build_decision_state` holds a raw CABT
    public board from which a non-redacted locator can be resolved.
    """
    return _build_action_key(
        selection_type=selection_type,
        context=context,
        option=option,
        card_id=card_id,
        public_identity=None,
        public_resolution=None,
    )


def _build_resolved_action_key(
    *,
    selection_type: object,
    context: object,
    option: object,
    card_id: int | None,
    public_identity: Mapping[str, object],
    public_resolution: Mapping[str, object],
) -> ActionKey:
    """Build a board-resolved Skill/Tool key inside the live trusted adapter."""
    return _build_action_key(
        selection_type=selection_type,
        context=context,
        option=option,
        card_id=card_id,
        public_identity=public_identity,
        public_resolution=public_resolution,
    )


def _build_action_key(
    *,
    selection_type: object,
    context: object,
    option: object,
    card_id: int | None = None,
    public_identity: Mapping[str, object] | None = None,
    public_resolution: Mapping[str, object] | None = None,
) -> ActionKey:
    """Build a versioned ActionKey without traversing unknown option payloads."""
    if card_id is not None and type(card_id) is not int:
        raise DecisionStateError("ActionKey card_id must be an int or None")
    data = _mapping(option, field="select.option[]")
    normalized_selection_type, normalized_context, numeric_option_type = (
        _validate_selection_option_schema(
            selection_type,
            context,
            data.get("type"),
            field="select",
        )
    )
    option_type = numeric_option_type
    fields = _actor_fields_from_option(
        data,
        option_type=numeric_option_type,
        field="select.option[]",
    )
    semantic_operation = _OFFICIAL_OPTION_TYPE_NAMES.get(
        numeric_option_type,
        OPTION_TYPE_NAMES.get(numeric_option_type, f"OPTION_{option_type}"),
    )
    if numeric_option_type == 4:
        if public_identity is None:
            raise DecisionStateError("ToolCard requires a verified public host locator")

    payload = tuple(sorted(fields.items()))
    source_key = _entity_key(fields, ("area", "index", "energyIndex"))
    target_key = _entity_key(fields, ("playerIndex", "inPlayArea", "inPlayIndex"))
    if public_identity is None:
        if numeric_option_type == 15:
            public_identity = {
                "operation": semantic_operation,
                "source": {"kind": "redacted"},
                "private_source_redacted": True,
            }
        elif numeric_option_type == 16:
            public_identity = {
                "operation": semantic_operation,
                "condition": _SPECIAL_CONDITION_NAMES[fields["specialConditionType"]],
            }
        else:
            allowed_public_fields = _GENERIC_PUBLIC_FIELDS_BY_OPTION_TYPE[
                numeric_option_type
            ]
            public_identity = {
                "operation": semantic_operation,
                "fields": {
                    name: value
                    for name, value in payload
                    if name in allowed_public_fields
                },
                "private_source_redacted": card_id is not None,
            }
    _validate_public_identity(public_identity)
    public_trace_payload: dict[str, object] = {
        "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
        "context": normalized_context,
        "option_type": option_type,
        "public_identity": dict(public_identity),
        "selection_type": normalized_selection_type,
        "semantic_operation": semantic_operation,
    }
    core = {
        "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
        "actor_identity_payload": [list(item) for item in payload],
        "card_id": card_id,
        "context": normalized_context,
        "option_type": option_type,
        "selection_type": normalized_selection_type,
        "semantic_operation": semantic_operation,
        "source_entity_key": source_key,
        "target_entity_key": target_key,
        "public_identity_payload": public_trace_payload,
    }
    return ActionKey(
        selection_type=normalized_selection_type,
        context=normalized_context,
        option_type=option_type,
        semantic_operation=semantic_operation,
        source_entity_key=source_key,
        target_entity_key=target_key,
        card_id=card_id,
        canonical_payload=payload,
        digest=_action_key_digest(core),
        actor_identity_payload=payload,
        public_identity_json=_canonical_json(public_trace_payload),
        public_resolution=public_resolution,
    )


def _validate_skill_option(
    selection_type: object,
    context: object,
    option: Mapping[str, Any],
) -> None:
    _validate_selection_option_schema(
        selection_type, context, option.get("type"), field="Skill.select"
    )
    _actor_fields_from_option(option, option_type=15, field="Skill")


def _validate_special_condition_option(
    selection_type: object,
    context: object,
    option: Mapping[str, Any],
) -> int:
    _validate_selection_option_schema(
        selection_type, context, option.get("type"), field="SpecialCondition.select"
    )
    return _actor_fields_from_option(
        option, option_type=16, field="SpecialCondition"
    )["specialConditionType"]


def _tool_actor_fields(option: Mapping[str, Any]) -> dict[str, int]:
    return _actor_fields_from_option(option, option_type=4, field="ToolCard")


def _validate_public_identity(identity: Mapping[str, object]) -> None:
    """Keep the persisted identity structurally incapable of carrying raw IDs."""
    raw_forbidden = {
        "cardId",
        "serial",
        "card_id",
        "canonical_payload",
        "actor_identity_payload",
        "digest",
        "action_digest",
        "action_key_digest",
        "actorDigest",
        "id",
        "private_card_id",
        "private_digest",
        "secret_serial",
        "stable_key",
        "option_index",
        "option_index_alias",
        "option_indices",
        "optionIndex",
        "optionIndices",
        "selection_index",
        "current_index",
        "current_indices",
        "currentIndex",
        "currentIndices",
        "toolIndex",
        "area",
        "index",
        "playerIndex",
    }

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise DecisionStateError("public ActionKey identity keys must be strings")
                if key in raw_forbidden:
                    raise DecisionStateError(f"public ActionKey identity may not contain {key}")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise DecisionStateError("public ActionKey identity must contain JSON values")

    walk(identity)


def _public_nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise DecisionStateError(f"{field} must be a non-bool nonnegative int")
    return value


def _public_card_ref(card_id: int, serial: int) -> str:
    """Return a deterministic reference for a publicly visible top-level card.

    This is a locator consistency value, not a signature or provenance proof.
    It is emitted only where C5's public observation retains both source fields.
    """
    return hashlib.sha256(
        b"mage_ptcg:public-card-ref:v1\0"
        + _canonical_json({"id": card_id, "serial": serial}).encode("utf-8")
    ).hexdigest()


def _public_resolution_zone_entry(
    public_resolution: Mapping[str, object] | None,
    *,
    player_index: int,
    zone: str,
    slot: int,
    action_name: str,
) -> Mapping[str, object]:
    """Resolve a public locator against the exact C1 public-state projection."""
    if public_resolution is None:
        raise DecisionStateError(
            f"{action_name} non-redacted public identity requires public resolution context"
        )
    actor = _strict_int(public_resolution.get("actor"), field="public resolution actor")
    if actor not in (0, 1):
        raise DecisionStateError("public resolution actor must be 0 or 1")
    if player_index not in (0, 1):
        raise DecisionStateError(f"{action_name} public locator has an invalid player index")
    role = "self" if player_index == actor else "opponent"
    player = _mapping(public_resolution.get(role), field=f"public resolution {role}")
    entries = _required_list(player.get(zone), field=f"public resolution {role}.{zone}")
    if slot >= len(entries):
        raise DecisionStateError(
            f"{action_name} public locator is outside the public {zone} zone"
        )
    entry = entries[slot]
    if entry is None:
        raise DecisionStateError(
            f"{action_name} public locator resolves to an empty public {zone} slot"
        )
    return _mapping(entry, field=f"public resolution {role}.{zone}[{slot}]")


def _validate_public_card_ref_from_entry(
    entry: Mapping[str, object],
    *,
    card_ref: object,
    action_name: str,
) -> None:
    if type(card_ref) is not str or len(card_ref) != 64:
        raise DecisionStateError(f"{action_name} public card_ref must be a lowercase SHA-256 digest")
    fields = _mapping(entry.get("fields"), field=f"{action_name} public card fields")
    card_id = _strict_int(fields.get("id"), field=f"{action_name} public card id")
    serial = _strict_int(fields.get("serial"), field=f"{action_name} public card serial")
    if card_ref != _public_card_ref(card_id, serial):
        raise DecisionStateError(f"{action_name} public card_ref does not match public resolution")


def _validate_public_locator_against_resolution(
    source: Mapping[str, object],
    *,
    public_resolution: Mapping[str, object] | None,
    action_name: str,
    attachment_count_fields: Mapping[str, str],
) -> None:
    """Validate the persistable Skill locator against its C1 board projection."""
    del attachment_count_fields  # Top-level Skill locators are C5-reverifiable only.
    entry = _public_resolution_zone_entry(
        public_resolution,
        player_index=_public_nonnegative_int(
            source.get("player_index"), field=f"{action_name} public identity player_index"
        ),
        zone=_required_string(source.get("zone"), field=f"{action_name} public identity zone"),
        slot=_public_nonnegative_int(
            source.get("slot"), field=f"{action_name} public identity slot"
        ),
        action_name=action_name,
    )
    _validate_public_card_ref_from_entry(
        entry,
        card_ref=source.get("card_ref"),
        action_name=action_name,
    )
    if public_resolution is None:  # established above; narrows for type check
        raise DecisionStateError("Skill public identity requires public resolution context")
    card_ref = source.get("card_ref")
    matches = 0
    actor = _strict_int(public_resolution.get("actor"), field="public resolution actor")
    for player_index in (0, 1):
        role = "self" if player_index == actor else "opponent"
        player = _mapping(public_resolution.get(role), field=f"public resolution {role}")
        for zone in ("active", "bench", "discard"):
            entries = _required_list(player.get(zone), field=f"public resolution {role}.{zone}")
            for candidate in entries:
                if candidate is None:
                    continue
                candidate_entry = _mapping(
                    candidate, field=f"public resolution {role}.{zone}[]"
                )
                fields = _mapping(
                    candidate_entry.get("fields"),
                    field=f"{action_name} public card fields",
                )
                candidate_id = fields.get("id")
                candidate_serial = fields.get("serial")
                if type(candidate_id) is int and type(candidate_serial) is int:
                    if card_ref == _public_card_ref(candidate_id, candidate_serial):
                        matches += 1
    if matches != 1:
        raise DecisionStateError(
            f"{action_name} public locator requires one globally unique public card pair"
        )


def _validate_tool_locator_against_resolution(
    source: Mapping[str, object],
    *,
    public_resolution: Mapping[str, object] | None,
) -> None:
    """Validate an attached-tool slot against the C1 public board/counts."""
    host_zone = _required_string(source.get("host_zone"), field="ToolCard public host zone")
    entry = _public_resolution_zone_entry(
        public_resolution,
        player_index=_public_nonnegative_int(
            source.get("player_index"), field="ToolCard public identity player_index"
        ),
        zone=host_zone,
        slot=_public_nonnegative_int(
            source.get("host_slot"), field="ToolCard public identity host_slot"
        ),
        action_name="ToolCard",
    )
    fields = _mapping(entry.get("fields"), field="ToolCard public host fields")
    count = _public_nonnegative_int(
        fields.get("tools_count"), field="ToolCard public host tools_count"
    )
    attachment_slot = _public_nonnegative_int(
        source.get("attachment_slot"), field="ToolCard public identity attachment_slot"
    )
    if attachment_slot >= count:
        raise DecisionStateError("ToolCard attachment slot is outside the public host")


def _validate_skill_public_identity(
    identity: Mapping[str, object],
    *,
    selection_type: object,
    context: object,
    public_resolution: Mapping[str, object] | None = None,
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...] | None = None,
    require_public_resolution: bool = False,
) -> None:
    if selection_type != 5 or context != 34:
        raise DecisionStateError(
            "Skill public identity requires agent JSON select.type 5 and context 34"
        )
    source = _mapping(identity.get("source"), field="Skill public identity source")
    kind = source.get("kind")
    if kind == "redacted":
        _require_exact_keys(
            identity,
            {"operation", "private_source_redacted", "source"},
            field="Skill public identity",
        )
        _require_exact_keys(source, {"kind"}, field="Skill public identity source")
        if identity.get("private_source_redacted") is not True:
            raise DecisionStateError(
                "Skill public identity redaction marker must be true"
            )
        return
    if kind != "public_card":
        raise DecisionStateError(
            "Skill public identity source must be redacted or a public card locator"
        )
    _require_exact_keys(identity, {"operation", "source"}, field="Skill public identity")
    _require_exact_keys(
        source,
        {"card_ref", "kind", "player_index", "slot", "zone"},
        field="Skill public identity source",
    )
    if source.get("zone") not in ("active", "bench", "discard"):
        raise DecisionStateError("Skill public identity has an unknown public zone")
    player_index = _public_nonnegative_int(
        source.get("player_index"),
        field="Skill public identity player_index",
    )
    if player_index not in (0, 1):
        raise DecisionStateError("Skill public identity player_index must be 0 or 1")
    _public_nonnegative_int(source.get("slot"), field="Skill public identity slot")
    card_ref = source.get("card_ref")
    if type(card_ref) is not str or len(card_ref) != 64 or card_ref != card_ref.lower() or any(
        character not in "0123456789abcdef" for character in card_ref
    ):
        raise DecisionStateError("Skill public identity card_ref must be a lowercase SHA-256 digest")
    if require_public_resolution:
        _validate_public_locator_against_resolution(
            source,
            public_resolution=public_resolution,
            action_name="Skill",
            attachment_count_fields={},
        )
    if actor_identity_payload is not None:
        actor_fields = dict(actor_identity_payload)
        card_id = actor_fields.get("cardId")
        serial = actor_fields.get("serial")
        if type(card_id) is not int or type(serial) is not int:
            raise DecisionStateError("Skill actor identity has an invalid card pair")
        if card_ref != _public_card_ref(card_id, serial):
            raise DecisionStateError("Skill public card_ref does not match actor identity")


def _validate_special_condition_public_identity(
    identity: Mapping[str, object],
    *,
    selection_type: object,
    context: object,
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...] | None = None,
) -> None:
    if selection_type != 10 or context not in (47, 48):
        raise DecisionStateError(
            "SpecialCondition public identity requires agent JSON select.type 10 and context 47 or 48"
        )
    _require_exact_keys(
        identity,
        {"condition", "operation"},
        field="SpecialCondition public identity",
    )
    public_condition = identity.get("condition")
    if public_condition not in _SPECIAL_CONDITION_NAMES.values():
        raise DecisionStateError("SpecialCondition public identity has an unknown condition")
    if actor_identity_payload is None:
        return
    actor_fields = dict(actor_identity_payload)
    condition = actor_fields.get("specialConditionType")
    if type(condition) is not int or condition not in _SPECIAL_CONDITION_NAMES:
        raise DecisionStateError("SpecialCondition actor identity has an unknown condition")
    if identity.get("condition") != _SPECIAL_CONDITION_NAMES[condition]:
        raise DecisionStateError("SpecialCondition public identity has an unknown condition")


def _validate_tool_public_identity(
    identity: Mapping[str, object],
    *,
    selection_type: object,
    context: object,
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...] | None = None,
    public_resolution: Mapping[str, object] | None = None,
    require_public_resolution: bool = False,
) -> None:
    try:
        _validate_selection_option_schema(
            selection_type, context, 4, field="ToolCard public identity"
        )
    except DecisionStateError as exc:
        raise DecisionStateError(
            "ToolCard public identity requires an AttachedCard-compatible CABT schema"
        ) from exc
    _require_exact_keys(identity, {"operation", "source"}, field="ToolCard public identity")
    source = _mapping(identity.get("source"), field="ToolCard public identity source")
    _require_exact_keys(
        source,
        {
            "attachment_slot",
            "host_slot",
            "host_zone",
            "kind",
            "player_index",
        },
        field="ToolCard public identity source",
    )
    if source.get("kind") != "public_attached_tool":
        raise DecisionStateError("ToolCard public identity requires an attached-tool locator")
    player_index = _public_nonnegative_int(
        source.get("player_index"), field="ToolCard public identity player_index"
    )
    if player_index not in (0, 1):
        raise DecisionStateError("ToolCard public identity player_index must be 0 or 1")
    if source.get("host_zone") not in ("active", "bench"):
        raise DecisionStateError("ToolCard public identity host_zone must be active or bench")
    host_slot = _public_nonnegative_int(
        source.get("host_slot"), field="ToolCard public identity host_slot"
    )
    if source.get("host_zone") == "active" and host_slot != 0:
        raise DecisionStateError("ToolCard public identity active host_slot must be zero")
    _public_nonnegative_int(
        source.get("attachment_slot"),
        field="ToolCard public identity attachment_slot",
    )
    if actor_identity_payload is None:
        pass
    else:
        actor_fields = dict(actor_identity_payload)
        expected_zone = _TOOL_HOST_AREAS.get(actor_fields.get("area"))
        if (
            expected_zone != source.get("host_zone")
            or actor_fields.get("playerIndex") != player_index
            or actor_fields.get("index") != host_slot
            or actor_fields.get("toolIndex") != source.get("attachment_slot")
        ):
            raise DecisionStateError(
                "ToolCard public identity does not match its actor locator"
            )
    if require_public_resolution:
        _validate_tool_locator_against_resolution(
            source,
            public_resolution=public_resolution,
        )


def _validate_public_identity_binding(
    payload: Mapping[str, object],
    *,
    selection_type: int,
    context: int,
    option_type: int,
    semantic_operation: str,
    actor_identity_payload: tuple[tuple[str, JsonScalar], ...],
    card_id: int | None,
    public_resolution: Mapping[str, object] | None = None,
    require_public_resolution: bool = False,
) -> None:
    """Bind a typed public projection to validated private-v2 actor fields."""
    validate_public_action_feature_payload(payload)
    for name, expected_value in (
        ("context", context),
        ("option_type", option_type),
        ("selection_type", selection_type),
        ("semantic_operation", semantic_operation),
    ):
        if payload.get(name) != expected_value:
            raise DecisionStateError(
                f"public ActionKey identity {name} does not match its ActionKey"
            )
    identity = _mapping(
        payload.get("public_identity"), field="public ActionKey identity"
    )
    if option_type == 15:
        _validate_skill_public_identity(
            identity,
            selection_type=selection_type,
            context=context,
            public_resolution=public_resolution,
            actor_identity_payload=actor_identity_payload,
            require_public_resolution=require_public_resolution,
        )
    elif option_type == 16:
        _validate_special_condition_public_identity(
            identity,
            selection_type=selection_type,
            context=context,
            actor_identity_payload=actor_identity_payload,
        )
    elif option_type == 4:
        _validate_tool_public_identity(
            identity,
            selection_type=selection_type,
            context=context,
            actor_identity_payload=actor_identity_payload,
            public_resolution=public_resolution,
            require_public_resolution=require_public_resolution,
        )
    else:
        actor_fields = dict(actor_identity_payload)
        allowed_public_fields = _GENERIC_PUBLIC_FIELDS_BY_OPTION_TYPE[option_type]
        expected_identity = {
            "operation": semantic_operation,
            "fields": {
                name: actor_fields[name]
                for name in allowed_public_fields
                if name in actor_fields
            },
            "private_source_redacted": card_id is not None,
        }
        if dict(identity) != expected_identity:
            raise DecisionStateError(
                "generic public identity must be derived exactly from actor identity"
            )


def _validate_feature_view_public_payload(
    view: SerializedActionFeatureView,
    payload: Mapping[str, object],
) -> None:
    """Validate private-v2 feature material without granting C1/C5 membership."""
    _validate_public_identity_binding(
        payload,
        selection_type=view.selection_type,
        context=view.context,
        option_type=view.option_type,
        semantic_operation=view.semantic_operation,
        actor_identity_payload=view.canonical_payload,
        card_id=view.card_id,
    )


def _validate_public_trace_payload(
    payload: Mapping[str, object],
    key: ActionKey,
    *,
    public_resolution: Mapping[str, object] | None,
    require_public_resolution: bool = True,
) -> None:
    assert key.actor_identity_payload is not None
    _validate_public_identity_binding(
        payload,
        selection_type=key.selection_type,
        context=key.context,
        option_type=key.option_type,
        semantic_operation=key.semantic_operation,
        actor_identity_payload=key.actor_identity_payload,
        card_id=key.card_id,
        public_resolution=public_resolution,
        require_public_resolution=require_public_resolution,
    )


def validate_public_action_feature_payload(
    payload: object,
) -> dict[str, object]:
    """Validate a public ActionKey v2 projection without inventing actor identity.

    Public-only C5 feature artifacts carry a public-action digest, not the
    private ActionKey v2 digest.  This structural reader therefore validates
    the exact typed public projection while deliberately never constructing
    an ``ActionKey``.  ``ActionKey.__post_init__`` adds the actor-binding checks.
    """
    data = _mapping(payload, field="public ActionKey feature payload")
    expected = {
        "action_key_schema_version",
        "context",
        "option_type",
        "public_identity",
        "selection_type",
        "semantic_operation",
    }
    _require_exact_keys(data, expected, field="public ActionKey identity")
    schema_version = data.get("action_key_schema_version")
    if type(schema_version) is not int or schema_version != ACTION_KEY_SCHEMA_VERSION:
        raise DecisionStateError("public ActionKey identity must declare schema version 2")
    for name in ("context", "option_type", "selection_type"):
        value = data.get(name)
        if type(value) is not int:
            raise DecisionStateError(
                f"public ActionKey identity {name} must be a non-bool int"
            )
    semantic_operation = _required_string(
        data.get("semantic_operation"),
        field="public ActionKey identity semantic_operation",
    )
    identity = _mapping(
        data.get("public_identity"), field="public ActionKey identity"
    )
    _validate_public_identity(identity)
    if identity.get("operation") != semantic_operation:
        raise DecisionStateError(
            "public ActionKey identity operation does not match its ActionKey"
        )
    option_type = data.get("option_type")
    if option_type not in _SUPPORTED_OPTION_TYPES:
        raise DecisionStateError(
            f"unsupported generic option type for public persistence: {option_type!r}"
        )
    _validate_selection_option_schema(
        data.get("selection_type"),
        data.get("context"),
        option_type,
        field="public ActionKey identity",
    )
    expected_operation = _OFFICIAL_OPTION_TYPE_NAMES.get(
        option_type,
        OPTION_TYPE_NAMES.get(option_type, f"OPTION_{option_type}"),
    )
    if semantic_operation != expected_operation:
        raise DecisionStateError(
            "public ActionKey identity semantic operation does not match option type"
        )
    if option_type == 15:
        _validate_skill_public_identity(
            identity,
            selection_type=data.get("selection_type"),
            context=data.get("context"),
        )
    elif option_type == 16:
        _validate_special_condition_public_identity(
            identity,
            selection_type=data.get("selection_type"),
            context=data.get("context"),
        )
    elif option_type == 4:
        _validate_tool_public_identity(
            identity,
            selection_type=data.get("selection_type"),
            context=data.get("context"),
        )
    else:
        _require_exact_keys(
            identity,
            {"fields", "operation", "private_source_redacted"},
            field="generic public ActionKey identity",
        )
        fields = _mapping(
            identity.get("fields"), field="generic public ActionKey identity fields"
        )
        allowed_fields = _GENERIC_PUBLIC_FIELDS_BY_OPTION_TYPE[option_type]
        if set(fields) != allowed_fields:
            raise DecisionStateError(
                "generic public ActionKey identity fields do not match the frozen option schema"
            )
        for name, value in fields.items():
            _strict_int(
                value,
                field=f"generic public ActionKey identity fields.{name}",
                minimum=0 if name in _NONNEGATIVE_ACTOR_FIELDS else None,
            )
        if type(identity.get("private_source_redacted")) is not bool:
            raise DecisionStateError(
                "generic public ActionKey identity redaction marker must be a bool"
            )
    _canonical_json(data)
    return dict(data)


def validate_persistable_public_action_payload(
    payload: object,
    *,
    public_resolution: Mapping[str, object],
) -> dict[str, object]:
    """Validate a C5 public payload against its exact C1 board projection.

    The structural feature reader intentionally does not make a historical
    board-membership claim.  Persisting an untrusted C5 record does: a
    non-redacted Skill/Tool locator must resolve against this decision's public
    observation rather than a serializable ``verified`` marker or an unkeyed
    content digest.
    """
    data = validate_public_action_feature_payload(payload)
    identity = _mapping(data["public_identity"], field="public ActionKey identity")
    if data["option_type"] == 15:
        _validate_skill_public_identity(
            identity,
            selection_type=data["selection_type"],
            context=data["context"],
            public_resolution=public_resolution,
            require_public_resolution=True,
        )
    elif data["option_type"] == 4:
        _validate_tool_public_identity(
            identity,
            selection_type=data["selection_type"],
            context=data["context"],
            public_resolution=public_resolution,
            require_public_resolution=True,
        )
    return data


def _append_public_card(
    registry: dict[tuple[int, int], list[dict[str, object]]],
    card: object,
    *,
    locator: dict[str, object],
    include_nested: bool,
) -> None:
    """Add only public card locations; never traverse hidden player zones."""
    if card is None:
        return
    data = _mapping(card, field="public card")
    card_id = data.get("id")
    serial = data.get("serial")
    if type(card_id) is int and type(serial) is int:
        registry.setdefault((card_id, serial), []).append(locator)
    if not include_nested:
        return
    for field, label in (
        ("energyCards", "energy_card"),
        ("tools", "tool"),
        ("preEvolution", "pre_evolution"),
    ):
        nested = data.get(field)
        if not isinstance(nested, list):
            continue
        for nested_index, child in enumerate(nested):
            _append_public_card(
                registry,
                child,
                locator={
                    **locator,
                    "attachment_kind": label,
                    "attachment_slot": nested_index,
                },
                include_nested=False,
            )


def _public_card_registry(
    current: Mapping[str, Any],
    players: Sequence[object],
) -> dict[tuple[int, int], list[dict[str, object]]]:
    """Build the B3 allowlisted registry before nested cards are count-reduced."""
    registry: dict[tuple[int, int], list[dict[str, object]]] = {}
    for player_index, raw_player in enumerate(players):
        player = _mapping(raw_player, field=f"current.players[{player_index}]")
        for zone_name in ("active", "bench", "discard"):
            zone = _required_list(player.get(zone_name), field=f"player.{zone_name}")
            for slot, card in enumerate(zone):
                _append_public_card(
                    registry,
                    card,
                    locator={
                        "kind": "public_card",
                        "player_index": player_index,
                        "zone": zone_name,
                        "slot": slot,
                    },
                    include_nested=zone_name in ("active", "bench"),
                )
    stadium = current.get("stadium")
    if isinstance(stadium, list) and len(stadium) == 1:
        _append_public_card(
            registry,
            stadium[0],
            locator={"kind": "public_card", "zone": "stadium", "slot": 0},
            include_nested=False,
        )
    return registry


def _skill_public_identity(
    selection_type: object,
    context: object,
    option: object,
    registry: Mapping[tuple[int, int], Sequence[Mapping[str, object]]],
) -> dict[str, object]:
    data = _mapping(option, field="select.option[]")
    _validate_skill_option(selection_type, context, data)
    pair = (
        _strict_int(data.get("cardId"), field="Skill.cardId"),
        _strict_int(data.get("serial"), field="Skill.serial"),
    )
    matches = tuple(
        locator
        for locator in registry.get(pair, ())
        if (
            locator.get("zone") in ("active", "bench", "discard")
            and "attachment_kind" not in locator
            and "attachment_slot" not in locator
        )
    )
    if len(matches) == 1:
        locator = dict(matches[0])
        # C5's public-state schema retains card id/serial only for top-level
        # active/bench/discard entries.  Nested attachment and stadium Skill
        # sources therefore remain redacted until a future version can
        # re-resolve them without reintroducing raw actor identifiers.
        return {
            "operation": "SKILL",
            "source": {
                "kind": "public_card",
                "player_index": locator["player_index"],
                "zone": locator["zone"],
                "slot": locator["slot"],
                "card_ref": _public_card_ref(*pair),
            },
        }
    return {
        "operation": "SKILL",
        "source": {"kind": "redacted"},
        "private_source_redacted": True,
    }


def _tool_public_identity(option: object, players: Sequence[object]) -> dict[str, object]:
    data = _mapping(option, field="select.option[]")
    fields = _tool_actor_fields(data)
    zone_name = _TOOL_HOST_AREAS.get(fields["area"])
    if zone_name is None:
        raise DecisionStateError("ToolCard host must be in public active or bench")
    player_index = fields["playerIndex"]
    if player_index not in (0, 1):
        raise DecisionStateError("ToolCard.playerIndex must be 0 or 1")
    player = _mapping(players[player_index], field=f"current.players[{player_index}]")
    zone = _required_list(player.get(zone_name), field=f"player.{zone_name}")
    host_slot = fields["index"]
    if host_slot >= len(zone) or (zone_name == "active" and host_slot != 0):
        raise DecisionStateError("ToolCard host does not resolve to a public Pokémon")
    host = zone[host_slot]
    if host is None:
        raise DecisionStateError("ToolCard host does not resolve to a public Pokémon")
    host_data = _mapping(host, field="ToolCard host")
    tools = _required_list(host_data.get("tools"), field="ToolCard host.tools")
    if fields["toolIndex"] >= len(tools):
        raise DecisionStateError("ToolCard.toolIndex is outside the public attached-tool list")
    return {
        "operation": "TOOL_CARD",
        "source": {
            "kind": "public_attached_tool",
            "player_index": player_index,
            "host_zone": zone_name,
            "host_slot": host_slot,
            "attachment_slot": fields["toolIndex"],
        },
    }


def _option_source_card_id(
    option: object,
    hand_card_ids: Sequence[int],
) -> int | None:
    """Apply the provisional hand-source heuristic for actor-known ID enrichment."""
    data = _mapping(option, field="select.option[]")
    option_type = _json_scalar(data.get("type"))
    area = _json_scalar(data.get("area"))
    index = data.get("index")
    is_hand_source = area == 2 or (area is None and option_type in (7, 8, 9))
    if not is_hand_source or type(index) is not int or not 0 <= index < len(hand_card_ids):
        return None
    return hand_card_ids[index]


@dataclass(frozen=True, slots=True, repr=False)
class ActorInformationView:
    """Everything legally available to the acting player at one decision.

    ``public_state_json`` contains public board/zones and zone counts.
    ``own_private_state_json`` contains only the acting player's observed hand.
    ``limited_knowledge_json`` is empty until cabt exposes a verified private
    reveal contract. ``visible_history`` contains only prior public-state
    digests. ``action_snapshot`` is derived exclusively from legal options.
    """

    actor: int
    public_state_json: str
    own_private_state_json: str
    limited_knowledge_json: str
    visible_history: tuple[str, ...]
    action_snapshot: tuple[ActionKey, ...]
    remaining_time_ms: int | None
    digest: str

    def __repr__(self) -> str:
        """Expose diagnostic counts only; never serialize actor-private state."""
        return (
            "ActorInformationView("
            f"actor={self.actor!r}, public_state_digest={self.public_state_digest!r}, "
            f"visible_history_count={len(self.visible_history)}, "
            f"legal_action_count={len(self.action_snapshot)}, "
            f"remaining_time_ms={self.remaining_time_ms!r}, "
            "own_private_state=<redacted>, limited_knowledge=<redacted>, "
            "digest=<redacted>)"
        )

    @property
    def public_state(self) -> dict[str, Any]:
        return json.loads(self.public_state_json)

    @property
    def own_private_state(self) -> dict[str, Any]:
        return json.loads(self.own_private_state_json)

    @property
    def public_state_digest(self) -> str:
        """Digest derived solely from the public projection."""
        return _digest(json.loads(self.public_state_json))


@dataclass(frozen=True, slots=True)
class LegalAction:
    option_index: int
    action_key: ActionKey


@dataclass(frozen=True, slots=True, repr=False)
class DecisionMetadata:
    schema_version: int
    public_state_digest: str
    action_set_digest: str

    def __repr__(self) -> str:
        return (
            "DecisionMetadata("
            f"schema_version={self.schema_version!r}, "
            f"public_state_digest={self.public_state_digest!r}, "
            "action_set_digest=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DecisionState:
    """Immutable decision input; legal action indices remain cabt-owned."""

    actor_view: ActorInformationView
    legal_actions: tuple[LegalAction, ...]
    belief_summary_json: str | None
    metadata: DecisionMetadata
    digest: str

    def __post_init__(self) -> None:
        """Keep feature-only legacy keys outside every v2 decision contract."""
        keys = tuple(action.action_key for action in self.legal_actions) + tuple(
            self.actor_view.action_snapshot
        )
        if any(
            not isinstance(key, ActionKey)
            or key.action_key_schema_version != ACTION_KEY_SCHEMA_VERSION
            or key.feature_only_legacy_v1
            for key in keys
        ):
            raise DecisionStateError(
                "DecisionState requires only ActionKey schema version 2"
            )

    def __repr__(self) -> str:
        """Keep convenient debugging output within the actor privacy boundary."""
        return (
            "DecisionState("
            f"actor={self.actor_view.actor!r}, "
            f"public_state_digest={self.metadata.public_state_digest!r}, "
            f"legal_action_count={len(self.legal_actions)}, "
            f"belief_summary_present={self.belief_summary_json is not None}, "
            "actor_view=<redacted>, metadata=<redacted>, digest=<redacted>)"
        )

    @property
    def normalized_public_observation(self) -> dict[str, Any]:
        return self.actor_view.public_state

    def indices_for_key(self, action_key: ActionKey) -> tuple[int, ...]:
        return tuple(
            action.option_index
            for action in self.legal_actions
            if action.action_key == action_key
        )

    def with_belief_summary_json(self, summary_json: str) -> "DecisionState":
        json.loads(summary_json)
        state = replace(self, belief_summary_json=summary_json, digest="")
        return replace(state, digest=_decision_digest(state))

    def to_trace_payload(self) -> dict[str, object]:
        """Return a public-only trace projection.

        The actor's hand is legal policy input but is intentionally omitted
        from persisted traces so a trace cannot become a private-card export.
        """
        action_payloads = [
            action.action_key.to_public_trace_payload() for action in self.legal_actions
        ]
        payload: dict[str, object] = {
            "action_keys": action_payloads,
            "actor": self.actor_view.actor,
            "belief_summary": (
                json.loads(self.belief_summary_json)
                if self.belief_summary_json is not None
                else None
            ),
            "metadata": {
                "public_action_set_digest": _digest(
                    sorted(action_payloads, key=_canonical_json)
                ),
                "public_state_digest": self.metadata.public_state_digest,
                "schema_version": self.metadata.schema_version,
            },
            "public_state": self.actor_view.public_state,
            "visible_history": list(self.actor_view.visible_history),
        }
        payload["trace_digest"] = _digest(payload)
        return payload


def _decision_digest(state: DecisionState) -> str:
    return _digest(
        {
            "actor_view_digest": state.actor_view.digest,
            "belief_summary": (
                json.loads(state.belief_summary_json)
                if state.belief_summary_json is not None
                else None
            ),
            "legal_action_keys": sorted(action.action_key.digest for action in state.legal_actions),
            "metadata": {
                "action_set_digest": state.metadata.action_set_digest,
                "public_state_digest": state.metadata.public_state_digest,
                "schema_version": state.metadata.schema_version,
            },
        }
    )


def build_decision_state(
    observation: object,
    *,
    visible_history: Sequence[str] = (),
) -> DecisionState:
    """Project one cabt selection observation into the C1 contract."""
    obs = _mapping(observation, field="observation")
    select = _mapping(obs.get("select"), field="select")
    options = _required_list(select.get("option"), field="select.option")
    selection_type, selection_context = _validate_selection_schema(
        select.get("type"), select.get("context"), field="select"
    )
    minimum = _strict_int(select.get("minCount"), field="select.minCount", minimum=0)
    maximum = _strict_int(select.get("maxCount"), field="select.maxCount", minimum=0)
    if not minimum <= maximum <= len(options):
        raise DecisionStateError("selection bounds are inconsistent with legal options")

    current = _mapping(obs.get("current"), field="current")
    actor = _strict_int(current.get("yourIndex"), field="current.yourIndex")
    if actor not in (0, 1):
        raise DecisionStateError("current.yourIndex must be 0 or 1")
    players = _required_list(current.get("players"), field="current.players")
    if len(players) != 2:
        raise DecisionStateError("current.players must contain exactly two players")
    self_player = _mapping(players[actor], field="current.players[self]")
    opponent_player = _mapping(players[1 - actor], field="current.players[opponent]")

    first_player = _strict_int(current.get("firstPlayer"), field="current.firstPlayer")
    if first_player not in (-1, 0, 1):
        raise DecisionStateError("current.firstPlayer must be -1, 0, or 1")
    observed_result = _strict_int(current.get("result"), field="current.result")
    if observed_result not in (-1, 0, 1):
        raise DecisionStateError("current.result must be -1, 0, or 1")
    step = _strict_int(obs.get("step"), field="step", minimum=0)
    turn = _strict_int(current.get("turn"), field="current.turn", minimum=0)
    turn_action_count = _strict_int(
        current.get("turnActionCount"), field="current.turnActionCount", minimum=0
    )
    public_state = {
        "actor": actor,
        "board": _board(current),
        "first_player": first_player,
        "opponent": _public_player(opponent_player),
        "observed_result": observed_result,
        "select": {
            "context": selection_context,
            "max_count": maximum,
            "min_count": minimum,
            "option_count": len(options),
            "type": selection_type,
        },
        "self": _public_player(self_player),
        "step": step,
        "turn": turn,
        "turn_action_count": turn_action_count,
    }
    hand_card_ids = _own_hand_card_ids(self_player)
    own_private_state = _own_private_state(hand_card_ids)
    public_card_registry = _public_card_registry(current, players)
    history = tuple(visible_history)
    if len(history) > _MAX_VISIBLE_HISTORY:
        raise DecisionStateError("visible_history exceeds the bounded public history")
    if any(
        type(item) is not str
        or len(item) != 64
        or item != item.lower()
        or any(character not in "0123456789abcdef" for character in item)
        for item in history
    ):
        raise DecisionStateError("visible_history must contain only SHA-256 digest strings")

    legal_action_values: list[LegalAction] = []
    for index, option in enumerate(options):
        option_data = _mapping(option, field="select.option[]")
        option_type = option_data.get("type")
        public_identity: Mapping[str, object] | None = None
        if option_type == 15:
            public_identity = _skill_public_identity(
                select.get("type"),
                select.get("context"),
                option_data,
                public_card_registry,
            )
        elif option_type == 4:
            public_identity = _tool_public_identity(option_data, players)
        common = {
            "selection_type": select.get("type"),
            "context": select.get("context"),
            "option": option_data,
            "card_id": _option_source_card_id(option_data, hand_card_ids),
        }
        action_key = (
            _build_resolved_action_key(
                **common,
                public_identity=public_identity,
                public_resolution=public_state,
            )
            if public_identity is not None
            else build_action_key(**common)
        )
        legal_action_values.append(LegalAction(option_index=index, action_key=action_key))
    legal_actions = tuple(legal_action_values)
    official_action_digests = [
        action.action_key.digest
        for action in legal_actions
        if action.action_key.option_type in _OFFICIAL_OPTION_TYPE_NAMES
    ]
    if len(official_action_digests) != len(set(official_action_digests)):
        raise DecisionStateError("duplicate stable ActionKey identity in official CABT options")
    sorted_snapshot = tuple(
        sorted((action.action_key for action in legal_actions), key=lambda key: key.digest)
    )
    public_state_json = _canonical_json(public_state)
    own_private_state_json = _canonical_json(own_private_state)
    limited_knowledge_json = _canonical_json({})
    public_digest = _digest(public_state)
    action_set_digest = _digest(sorted(key.digest for key in sorted_snapshot))
    view_core = {
        "action_snapshot": [key.to_canonical_payload() for key in sorted_snapshot],
        "actor": actor,
        "limited_knowledge": {},
        "own_private_state": own_private_state,
        "public_state": public_state,
        "remaining_time_ms": None,
        "visible_history": list(history),
    }
    actor_view = ActorInformationView(
        actor=actor,
        public_state_json=public_state_json,
        own_private_state_json=own_private_state_json,
        limited_knowledge_json=limited_knowledge_json,
        visible_history=history,
        action_snapshot=sorted_snapshot,
        remaining_time_ms=None,
        digest=_digest(view_core),
    )
    metadata = DecisionMetadata(
        schema_version=SCHEMA_VERSION,
        public_state_digest=public_digest,
        action_set_digest=action_set_digest,
    )
    state = DecisionState(
        actor_view=actor_view,
        legal_actions=legal_actions,
        belief_summary_json=None,
        metadata=metadata,
        digest="",
    )
    return replace(state, digest=_decision_digest(state))


__all__ = [
    "ActionKey",
    "ACTION_KEY_SCHEMA_VERSION",
    "ActorInformationView",
    "DecisionMetadata",
    "DecisionState",
    "DecisionStateError",
    "LegalAction",
    "SCHEMA_VERSION",
    "SerializedActionFeatureView",
    "build_action_key",
    "build_decision_state",
    "public_action_id_v1",
    "validate_persistable_public_action_payload",
    "validate_public_action_feature_payload",
]
