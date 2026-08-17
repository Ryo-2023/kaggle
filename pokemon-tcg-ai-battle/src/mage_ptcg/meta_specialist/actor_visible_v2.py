"""Actor-visible C1 v2 decision state and local candidate identities.

This module is intentionally local-only: it keeps the actor's legitimately
visible cards in typed immutable values while retaining the frozen C1 v1
projection as separately canonical JSON.  It never retains the raw CABT
observation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any

from mage_ptcg.decision_state import (
    ActionKey,
    DecisionStateError,
    build_decision_state,
    public_action_id_v1,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_SELECTION_CONTEXTS_V1,
)


C1_V2_SCHEMA_VERSION = 2
ACTOR_VISIBLE_SELECTION_SCHEMA_VERSION = "actor-visible-selection-v1"
ACTOR_VISIBLE_BINDING_SCHEMA_VERSION = "actor-visible-action-binding-v1"
_MAX_CARD_COLLECTION = 60
MAX_LEGAL_CANDIDATES_V2 = 512
_LOCAL_ACTION_ID_PREFIX = b"mage_ptcg:actor-visible-local-action:v1\0"
_C1_V1_HASH_PREFIX = b"mage_ptcg.decision_state:v1\0"
_RESOLUTION_KINDS = frozenset({
    "not-applicable", "actor-visible", "public-visible", "hidden-unresolved",
    "owner-resolved", "special-condition",
})
_SEMANTIC_ZONES = frozenset({
    "not-applicable", "deck", "deck-reveal", "hand", "discard", "active",
    "bench", "stadium", "looking", "energy", "tool", "active-tool",
    "bench-tool", "active-energy", "bench-energy", "pre-evolution",
    "context-card", "effect", "prize", "player", "hidden",
})
_MISSING_REASONS = frozenset({
    "hidden-zone", "not-addressable", "card-id-zero", "ambiguous-registry",
})


class ActorVisibleV2Error(ValueError):
    """Raised when an observation cannot satisfy the C1 v2 local contract."""


@dataclass(frozen=True, slots=True)
class OptionResolverRowV1:
    """One closed official ``Option`` resolver rule.

    The fields describe only documented ownership, area, endpoint, and allowed
    unresolved semantics for one official CABT ``OptionType``.
    """

    option_type: int
    operation: str
    source_owner: str
    target_owner: str
    source_resolver: str
    target_resolver: str
    host_resolver: str
    legal_source_areas: frozenset[int]
    legal_target_areas: frozenset[int]
    source_missing_reasons: frozenset[str]
    target_missing_reasons: frozenset[str]
    host_missing_reasons: frozenset[str]


def _resolver_row(
    option_type: int,
    operation: str,
    source_owner: str,
    target_owner: str,
    source_resolver: str,
    target_resolver: str = "not-applicable",
    host_resolver: str = "not-applicable",
    *,
    source_areas: frozenset[int] = frozenset(),
    target_areas: frozenset[int] = frozenset(),
    source_missing: frozenset[str] = frozenset(),
    target_missing: frozenset[str] = frozenset(),
    host_missing: frozenset[str] = frozenset(),
) -> OptionResolverRowV1:
    return OptionResolverRowV1(
        option_type=option_type,
        operation=operation,
        source_owner=source_owner,
        target_owner=target_owner,
        source_resolver=source_resolver,
        target_resolver=target_resolver,
        host_resolver=host_resolver,
        legal_source_areas=source_areas,
        legal_target_areas=target_areas,
        source_missing_reasons=source_missing,
        target_missing_reasons=target_missing,
        host_missing_reasons=host_missing,
    )


OPTION_RESOLVER_TABLE_V1: Mapping[int, OptionResolverRowV1] = MappingProxyType({
    0: _resolver_row(0, "NUMBER", "unavailable", "unavailable", "number"),
    1: _resolver_row(1, "YES", "unavailable", "unavailable", "not-applicable"),
    2: _resolver_row(2, "NO", "unavailable", "unavailable", "not-applicable"),
    3: _resolver_row(
        3, "CARD", "option.playerIndex", "unavailable", "area-index",
        source_areas=frozenset(range(1, 13)),
        source_missing=frozenset({"hidden-zone", "not-addressable"}),
    ),
    4: _resolver_row(
        4, "TOOL_CARD", "option.playerIndex", "option.playerIndex",
        "attached-tool", "in-play-pokemon", "in-play-pokemon",
        source_areas=frozenset({4, 5}), target_areas=frozenset({4, 5}),
    ),
    5: _resolver_row(
        5, "ENERGY_CARD", "option.playerIndex", "option.playerIndex",
        "attached-energy", "in-play-pokemon", "in-play-pokemon",
        source_areas=frozenset({4, 5}), target_areas=frozenset({4, 5}),
    ),
    6: _resolver_row(
        6, "ENERGY", "option.playerIndex", "option.playerIndex",
        "attached-energy", "in-play-pokemon", "in-play-pokemon",
        source_areas=frozenset({4, 5}), target_areas=frozenset({4, 5}),
    ),
    7: _resolver_row(
        7, "PLAY", "actor", "unavailable", "actor-hand",
        source_areas=frozenset({2}),
    ),
    8: _resolver_row(
        8, "ATTACH", "actor", "actor", "area-index", "in-play-pokemon",
        source_areas=frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12}),
        target_areas=frozenset({4, 5}),
        source_missing=frozenset({"hidden-zone", "not-addressable"}),
    ),
    9: _resolver_row(
        9, "EVOLVE", "actor", "actor", "area-index", "in-play-pokemon",
        source_areas=frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12}),
        target_areas=frozenset({4, 5}),
        source_missing=frozenset({"hidden-zone", "not-addressable"}),
    ),
    10: _resolver_row(
        10, "ABILITY", "area-dependent:4,5=actor;7=stadium-card.playerIndex",
        "unavailable", "area-index",
        source_areas=frozenset({4, 5, 7}),
    ),
    11: _resolver_row(
        11, "DISCARD", "area-dependent:4,5=actor;7=stadium-card.playerIndex",
        "unavailable", "area-index",
        source_areas=frozenset({4, 5, 7}),
    ),
    12: _resolver_row(
        12, "RETREAT", "actor", "unavailable", "actor-active",
        source_missing=frozenset({"not-addressable"}),
    ),
    13: _resolver_row(
        13, "ATTACK", "actor", "unavailable", "actor-active",
        source_missing=frozenset({"not-addressable"}),
    ),
    14: _resolver_row(14, "END", "unavailable", "unavailable", "not-applicable"),
    15: _resolver_row(
        15, "SKILL", "registry", "unavailable", "bounded-card-registry",
        source_missing=frozenset({"not-addressable", "ambiguous-registry"}),
    ),
    16: _resolver_row(
        16, "SPECIAL_CONDITION", "unavailable", "unavailable", "special-condition",
    ),
})


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ActorVisibleV2Error("actor-visible value is not canonical JSON") from exc


def _strict_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int:
        raise ActorVisibleV2Error(f"{field} must be a non-bool int")
    if value < minimum:
        raise ActorVisibleV2Error(f"{field} must be at least {minimum}")
    return value


def _strict_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise ActorVisibleV2Error(f"{field} must be a bool")
    return value


def _strict_owner(value: object, *, field: str) -> int:
    owner = _strict_int(value, field=field)
    if owner not in (0, 1):
        raise ActorVisibleV2Error(f"{field} must be 0 or 1")
    return owner


def _strict_tuple(value: object, *, field: str, maximum: int = _MAX_CARD_COLLECTION) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise ActorVisibleV2Error(f"{field} must be an immutable tuple")
    if len(value) > maximum:
        raise ActorVisibleV2Error(f"{field} exceeds its bounded length")
    return value


def _strict_digest(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ActorVisibleV2Error(f"{field} must be a SHA-256 hex digest")
    return value


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ActorVisibleV2Error(f"{field} must be a mapping")
    return value


def _bounded_list(
    value: object,
    *,
    field: str,
    maximum: int = _MAX_CARD_COLLECTION,
) -> list[Any]:
    if not isinstance(value, list):
        raise ActorVisibleV2Error(f"{field} must be a list")
    if len(value) > maximum:
        raise ActorVisibleV2Error(f"{field} exceeds its bounded length")
    return value


def _sha256(value: object, *, prefix: bytes) -> str:
    return hashlib.sha256(prefix + _canonical_json(value).encode("utf-8")).hexdigest()


def _c1_v1_digest(value: object) -> str:
    return _sha256(value, prefix=_C1_V1_HASH_PREFIX)


@dataclass(frozen=True, slots=True, repr=False)
class CardRefV2:
    """Identity of an official base ``Card`` with its explicit owner."""

    card_id: int
    serial: int
    player_index: int

    def __post_init__(self) -> None:
        _strict_int(self.card_id, field="CardRefV2.card_id", minimum=1)
        _strict_int(self.serial, field="CardRefV2.serial")
        _strict_owner(self.player_index, field="CardRefV2.player_index")

    def __repr__(self) -> str:
        return "CardRefV2(<redacted>)"

    def to_local_dict(self) -> dict[str, int]:
        return {"card_id": self.card_id, "serial": self.serial, "player_index": self.player_index}


@dataclass(frozen=True, slots=True, repr=False)
class PokemonRefV2:
    """Raw official Pokémon identity; ownership is deliberately not intrinsic."""

    card_id: int
    serial: int
    legacy_player_index_extension_present: bool = False

    def __post_init__(self) -> None:
        _strict_int(self.card_id, field="PokemonRefV2.card_id", minimum=1)
        _strict_int(self.serial, field="PokemonRefV2.serial")
        if type(self.legacy_player_index_extension_present) is not bool:
            raise ActorVisibleV2Error(
                "PokemonRefV2.legacy_player_index_extension_present must be a bool"
            )

    def __repr__(self) -> str:
        return "PokemonRefV2(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BoundCardRefV1:
    """A card identity bound to an owner by an official container or Card field."""

    card_id: int
    serial: int
    player_index: int

    def __post_init__(self) -> None:
        _strict_int(self.card_id, field="BoundCardRefV1.card_id", minimum=1)
        _strict_int(self.serial, field="BoundCardRefV1.serial")
        _strict_owner(self.player_index, field="BoundCardRefV1.player_index")

    def __repr__(self) -> str:
        return "BoundCardRefV1(<redacted>)"

    def to_local_dict(self) -> dict[str, int]:
        return {"card_id": self.card_id, "serial": self.serial, "player_index": self.player_index}


@dataclass(frozen=True, slots=True, repr=False)
class PokemonStateV2:
    """Public Pokémon, whose owner is derived from its active/bench container."""

    ref: PokemonRefV2
    owner: int
    hp: int
    max_hp: int
    appear_this_turn: bool
    energies: tuple[int, ...]
    energy_cards: tuple[BoundCardRefV1, ...]
    tools: tuple[BoundCardRefV1, ...]
    pre_evolution: tuple[BoundCardRefV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ref, PokemonRefV2):
            raise ActorVisibleV2Error("PokemonStateV2.ref must be a PokemonRefV2")
        _strict_owner(self.owner, field="PokemonStateV2.owner")
        _strict_int(self.hp, field="PokemonStateV2.hp")
        _strict_int(self.max_hp, field="PokemonStateV2.max_hp")
        if self.hp > self.max_hp:
            raise ActorVisibleV2Error("PokemonStateV2.hp cannot exceed max_hp")
        _strict_bool(self.appear_this_turn, field="PokemonStateV2.appear_this_turn")
        energies = _strict_tuple(self.energies, field="PokemonStateV2.energies")
        if any(type(value) is not int or not 0 <= value <= 11 for value in energies):
            raise ActorVisibleV2Error("PokemonStateV2.energies must be EnergyType values 0..11")
        for name in ("energy_cards", "tools", "pre_evolution"):
            cards = _strict_tuple(getattr(self, name), field=f"PokemonStateV2.{name}")
            if any(type(card) is not BoundCardRefV1 for card in cards):
                raise ActorVisibleV2Error(f"PokemonStateV2.{name} must contain BoundCardRefV1 values")
            if any(card.player_index != self.owner for card in cards):
                raise ActorVisibleV2Error(f"PokemonStateV2.{name} Card owners must match Pokemon owner")

    def __repr__(self) -> str:
        return "PokemonStateV2(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlayerPublicStateV2:
    active: tuple[PokemonStateV2 | None, ...]
    bench: tuple[PokemonStateV2, ...]
    discard: tuple[BoundCardRefV1, ...]
    hand_count: int
    deck_count: int
    prize_count: int
    bench_max: int
    poisoned: bool
    burned: bool
    asleep: bool
    paralyzed: bool
    confused: bool

    def __post_init__(self) -> None:
        active = _strict_tuple(self.active, field="PlayerPublicStateV2.active", maximum=1)
        bench = _strict_tuple(self.bench, field="PlayerPublicStateV2.bench")
        discard = _strict_tuple(self.discard, field="PlayerPublicStateV2.discard")
        if any(item is not None and type(item) is not PokemonStateV2 for item in active):
            raise ActorVisibleV2Error("PlayerPublicStateV2.active must contain PokemonStateV2 or null")
        if any(type(item) is not PokemonStateV2 for item in bench):
            raise ActorVisibleV2Error("PlayerPublicStateV2.bench must contain PokemonStateV2")
        if any(type(item) is not BoundCardRefV1 for item in discard):
            raise ActorVisibleV2Error("PlayerPublicStateV2.discard must contain BoundCardRefV1")
        for name in ("hand_count", "deck_count", "prize_count", "bench_max"):
            _strict_int(getattr(self, name), field=f"PlayerPublicStateV2.{name}")
        if len(bench) > self.bench_max:
            raise ActorVisibleV2Error("PlayerPublicStateV2.bench exceeds bench_max")
        for name in ("poisoned", "burned", "asleep", "paralyzed", "confused"):
            _strict_bool(getattr(self, name), field=f"PlayerPublicStateV2.{name}")

    def __repr__(self) -> str:
        return (
            "PlayerPublicStateV2("
            f"active_count={len(self.active)}, bench_count={len(self.bench)}, "
            f"discard_count={len(self.discard)}, hand_count={self.hand_count}, "
            "cards=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleSelectionViewV1:
    context_card: CardRefV2 | None
    effect: CardRefV2 | None
    deck_reveal: tuple[CardRefV2, ...] | None
    looking: tuple[CardRefV2 | None, ...] | None

    def __post_init__(self) -> None:
        for name in ("context_card", "effect"):
            value = getattr(self, name)
            if value is not None and type(value) is not CardRefV2:
                raise ActorVisibleV2Error(f"ActorVisibleSelectionViewV1.{name} must be CardRefV2 or null")
        for name in ("deck_reveal", "looking"):
            values = getattr(self, name)
            if values is None:
                continue
            values = _strict_tuple(values, field=f"ActorVisibleSelectionViewV1.{name}")
            if any(value is not None and type(value) is not CardRefV2 for value in values):
                raise ActorVisibleV2Error(f"ActorVisibleSelectionViewV1.{name} has an invalid CardRefV2")
        if self.deck_reveal is not None and any(type(value) is not CardRefV2 for value in self.deck_reveal):
            raise ActorVisibleV2Error("ActorVisibleSelectionViewV1.deck_reveal must contain CardRefV2")

    @property
    def schema_version(self) -> str:
        return ACTOR_VISIBLE_SELECTION_SCHEMA_VERSION

    def __repr__(self) -> str:
        return "ActorVisibleSelectionViewV1(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActorPrivateStateV2:
    own_hand: tuple[CardRefV2, ...]
    selection_view: ActorVisibleSelectionViewV1
    visibility_basis: str = "acting_player_hand"

    def __post_init__(self) -> None:
        if self.visibility_basis != "acting_player_hand":
            raise ActorVisibleV2Error("ActorPrivateStateV2 visibility basis is invalid")
        hand = _strict_tuple(self.own_hand, field="ActorPrivateStateV2.own_hand")
        if any(type(card) is not CardRefV2 for card in hand):
            raise ActorVisibleV2Error("ActorPrivateStateV2.own_hand must contain CardRefV2")
        if type(self.selection_view) is not ActorVisibleSelectionViewV1:
            raise ActorVisibleV2Error("ActorPrivateStateV2.selection_view must be ActorVisibleSelectionViewV1")

    def __repr__(self) -> str:
        return "ActorPrivateStateV2(own_hand=<redacted>, selection_view=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActorInformationViewV2:
    actor: int
    self_player: PlayerPublicStateV2
    opponent_player: PlayerPublicStateV2
    private_state: ActorPrivateStateV2
    board_stadium: BoundCardRefV1 | None
    stadium_played: bool
    supporter_played: bool
    energy_attached: bool
    retreated: bool
    first_player: int
    observed_result: int
    step: int
    turn: int
    turn_action_count: int
    remain_damage_counter: int
    remain_energy_cost: int
    selection_type: int
    selection_context: int
    min_count: int
    max_count: int

    def __post_init__(self) -> None:
        _strict_owner(self.actor, field="ActorInformationViewV2.actor")
        for name in ("self_player", "opponent_player"):
            if type(getattr(self, name)) is not PlayerPublicStateV2:
                raise ActorVisibleV2Error(f"ActorInformationViewV2.{name} must be PlayerPublicStateV2")
        if type(self.private_state) is not ActorPrivateStateV2:
            raise ActorVisibleV2Error("ActorInformationViewV2.private_state must be ActorPrivateStateV2")
        PlayerPublicStateV2.__post_init__(self.self_player)
        PlayerPublicStateV2.__post_init__(self.opponent_player)
        ActorPrivateStateV2.__post_init__(self.private_state)
        ActorVisibleSelectionViewV1.__post_init__(self.private_state.selection_view)
        if self.board_stadium is not None and type(self.board_stadium) is not BoundCardRefV1:
            raise ActorVisibleV2Error("ActorInformationViewV2.board_stadium must be BoundCardRefV1 or null")
        for name in ("stadium_played", "supporter_played", "energy_attached", "retreated"):
            _strict_bool(getattr(self, name), field=f"ActorInformationViewV2.{name}")
        for name in ("first_player", "observed_result"):
            value = _strict_int(getattr(self, name), field=f"ActorInformationViewV2.{name}", minimum=-1)
            if value not in (-1, 0, 1):
                raise ActorVisibleV2Error(f"ActorInformationViewV2.{name} must be -1, 0, or 1")
        for name in ("step", "turn", "turn_action_count", "remain_damage_counter", "remain_energy_cost", "selection_type", "selection_context", "min_count", "max_count"):
            _strict_int(getattr(self, name), field=f"ActorInformationViewV2.{name}")
        if self.min_count > self.max_count:
            raise ActorVisibleV2Error("ActorInformationViewV2 selection counts are inconsistent")
        if any(card.player_index != self.actor for card in self.private_state.own_hand):
            raise ActorVisibleV2Error("ActorInformationViewV2 private hand owners must match actor")
        if len(self.private_state.own_hand) != self.self_player.hand_count:
            raise ActorVisibleV2Error("ActorInformationViewV2 private hand must match self hand_count")
        reveal = self.private_state.selection_view.deck_reveal
        if reveal is not None:
            if len(reveal) != self.self_player.deck_count:
                raise ActorVisibleV2Error("ActorInformationViewV2 deck_reveal must match self deck_count")
            if any(card.player_index != self.actor for card in reveal):
                raise ActorVisibleV2Error("ActorInformationViewV2 deck_reveal Card owners must match actor")
        for player, expected_owner in ((self.self_player, self.actor), (self.opponent_player, 1 - self.actor)):
            for pokemon in (*player.active, *player.bench):
                if pokemon is not None and pokemon.owner != expected_owner:
                    raise ActorVisibleV2Error("ActorInformationViewV2 player Pokemon owner does not match its side")
            if any(card.player_index != expected_owner for card in player.discard):
                raise ActorVisibleV2Error("ActorInformationViewV2 player discard owner does not match its side")

    def __repr__(self) -> str:
        return (
            "ActorInformationViewV2("
            f"actor={self.actor}, selection=({self.selection_type},{self.selection_context}), "
            "private_state=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleBindingEndpointV1:
    """One exact local binding endpoint, with no derived candidate identity."""

    resolution_kind: str
    owner_player_index: int | None
    semantic_zone: str
    bound_card: BoundCardRefV1 | None
    missing_reason: str | None

    def __post_init__(self) -> None:
        if self.resolution_kind not in _RESOLUTION_KINDS:
            raise ActorVisibleV2Error("binding endpoint resolution_kind is not in the closed domain")
        if self.semantic_zone not in _SEMANTIC_ZONES:
            raise ActorVisibleV2Error("binding endpoint semantic_zone is not in the closed domain")
        if self.owner_player_index is not None:
            _strict_owner(self.owner_player_index, field="binding endpoint owner_player_index")
        if self.bound_card is not None and type(self.bound_card) is not BoundCardRefV1:
            raise ActorVisibleV2Error("binding endpoint bound_card must be a bound Card or null")
        if self.missing_reason is not None and self.missing_reason not in _MISSING_REASONS:
            raise ActorVisibleV2Error("binding endpoint missing_reason is not in the closed domain")
        exact_null = (
            self.owner_player_index is None
            and self.semantic_zone == "not-applicable"
            and self.bound_card is None
            and self.missing_reason is None
        )
        if self.resolution_kind in {"not-applicable", "special-condition"}:
            if not exact_null:
                raise ActorVisibleV2Error(
                    "not-applicable and special-condition endpoints require the exact null shape"
                )
            return
        if self.resolution_kind == "owner-resolved":
            if (
                self.owner_player_index not in (0, 1)
                or self.semantic_zone != "player"
                or self.bound_card is not None
                or self.missing_reason is not None
            ):
                raise ActorVisibleV2Error("owner-resolved endpoint requires the exact player shape")
            return
        if self.resolution_kind == "hidden-unresolved":
            if self.bound_card is not None or self.missing_reason is None:
                raise ActorVisibleV2Error("hidden endpoint requires no Card and one missing_reason")
            if self.semantic_zone in {"not-applicable", "player"}:
                raise ActorVisibleV2Error("hidden endpoint requires a named hidden source zone")
            return
        if (
            self.bound_card is None
            or self.owner_player_index != self.bound_card.player_index
            or self.missing_reason is not None
            or self.semantic_zone in {"not-applicable", "player", "hidden"}
        ):
            raise ActorVisibleV2Error("visible endpoint requires an exact bound-card shape")

    def __repr__(self) -> str:
        return "ActorVisibleBindingEndpointV1(<redacted>)"

    def to_identity_dict(self) -> dict[str, object]:
        return {
            "resolution_kind": self.resolution_kind,
            "owner_player_index": self.owner_player_index,
            "semantic_zone": self.semantic_zone,
            "bound_card": (
                None if self.bound_card is None else self.bound_card.to_local_dict()
            ),
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleActionBindingCoreV1:
    """Authoritative identity core with exactly source, target, and host endpoints."""

    schema_version: str
    source: ActorVisibleBindingEndpointV1
    target: ActorVisibleBindingEndpointV1
    host: ActorVisibleBindingEndpointV1

    def __post_init__(self) -> None:
        if self.schema_version != ACTOR_VISIBLE_BINDING_SCHEMA_VERSION:
            raise ActorVisibleV2Error("binding core schema_version is invalid")
        for name in ("source", "target", "host"):
            if type(getattr(self, name)) is not ActorVisibleBindingEndpointV1:
                raise ActorVisibleV2Error(f"binding core {name} must be an endpoint")

    def __repr__(self) -> str:
        return "ActorVisibleActionBindingCoreV1(<redacted>)"

    def to_identity_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_identity_dict(),
            "target": self.target.to_identity_dict(),
            "host": self.host.to_identity_dict(),
        }


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleActionBindingV1:
    """Local wrapper that stores derived IDs outside the authoritative core."""

    core: ActorVisibleActionBindingCoreV1
    action_key_digest: str
    public_action_id: str
    local_action_id: str

    def __post_init__(self) -> None:
        if type(self.core) is not ActorVisibleActionBindingCoreV1:
            raise ActorVisibleV2Error("binding core must be ActorVisibleActionBindingCoreV1")
        _strict_digest(self.action_key_digest, field="binding action_key_digest")
        _strict_digest(self.public_action_id, field="binding public_action_id")
        _strict_digest(self.local_action_id, field="binding local_action_id")
        expected_local_action_id = derive_local_action_id_v1(
            action_key_digest=self.action_key_digest,
            binding_core=self.core,
        )
        if self.local_action_id != expected_local_action_id:
            raise ActorVisibleV2Error("local_action_id does not match the binding core")

    def __repr__(self) -> str:
        return "ActorVisibleActionBindingV1(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleLegalActionV2:
    binding: ActorVisibleActionBindingV1
    action_key: ActionKey

    def __post_init__(self) -> None:
        if type(self.binding) is not ActorVisibleActionBindingV1 or type(self.action_key) is not ActionKey:
            raise ActorVisibleV2Error("local action requires exact binding and ActionKey types")
        if self.action_key.digest != self.binding.action_key_digest:
            raise ActorVisibleV2Error("local action ActionKey digest does not match")
        expected_public_action_id = public_action_id_v1(
            self.action_key.to_public_trace_payload()
        )
        if self.binding.public_action_id != expected_public_action_id:
            raise ActorVisibleV2Error("public_action_id does not match the ActionKey")
        _validate_binding_core_for_option_type(
            self.binding.core,
            option_type=self.action_key.option_type,
        )

    @property
    def action_key_digest(self) -> str:
        return self.binding.action_key_digest

    @property
    def public_action_id(self) -> str:
        return self.binding.public_action_id

    @property
    def local_action_id(self) -> str:
        return self.binding.local_action_id

    def __repr__(self) -> str:
        return "ActorVisibleLegalActionV2(<redacted>)"


def _validate_information_view_tree(view: ActorInformationViewV2) -> None:
    """Replay all nested value contracts for an externally reloaded state."""
    ActorInformationViewV2.__post_init__(view)
    for player, expected_owner in ((view.self_player, view.actor), (view.opponent_player, 1 - view.actor)):
        PlayerPublicStateV2.__post_init__(player)
        for pokemon in (*player.active, *player.bench):
            if pokemon is None:
                continue
            PokemonRefV2.__post_init__(pokemon.ref)
            PokemonStateV2.__post_init__(pokemon)
            if pokemon.owner != expected_owner:
                raise ActorVisibleV2Error("typed player Pokémon owner does not match its information-view side")
            for card in (*pokemon.energy_cards, *pokemon.tools, *pokemon.pre_evolution):
                BoundCardRefV1.__post_init__(card)
        for card in player.discard:
            BoundCardRefV1.__post_init__(card)
            if card.player_index != expected_owner:
                raise ActorVisibleV2Error("typed player discard owner does not match its information-view side")
    ActorPrivateStateV2.__post_init__(view.private_state)
    ActorVisibleSelectionViewV1.__post_init__(view.private_state.selection_view)
    for card in view.private_state.own_hand:
        CardRefV2.__post_init__(card)
    selection = view.private_state.selection_view
    for card in (selection.context_card, selection.effect, *(selection.deck_reveal or ()), *(selection.looking or ())):
        if card is not None:
            CardRefV2.__post_init__(card)
    if view.board_stadium is not None:
        BoundCardRefV1.__post_init__(view.board_stadium)


def _not_applicable_endpoint() -> ActorVisibleBindingEndpointV1:
    return ActorVisibleBindingEndpointV1(
        resolution_kind="not-applicable",
        owner_player_index=None,
        semantic_zone="not-applicable",
        bound_card=None,
        missing_reason=None,
    )


def _visible_endpoint(
    card: BoundCardRefV1,
    *,
    semantic_zone: str,
    resolution_kind: str,
) -> ActorVisibleBindingEndpointV1:
    if resolution_kind not in {"actor-visible", "public-visible"}:
        raise ActorVisibleV2Error("visible endpoint requires an explicit visibility source")
    return ActorVisibleBindingEndpointV1(
        resolution_kind=resolution_kind,
        owner_player_index=card.player_index,
        semantic_zone=semantic_zone,
        bound_card=card,
        missing_reason=None,
    )


def _owner_resolved_endpoint(owner_player_index: int) -> ActorVisibleBindingEndpointV1:
    return ActorVisibleBindingEndpointV1(
        resolution_kind="owner-resolved",
        owner_player_index=owner_player_index,
        semantic_zone="player",
        bound_card=None,
        missing_reason=None,
    )


def _hidden_endpoint(
    *,
    owner_player_index: int | None,
    semantic_zone: str,
    missing_reason: str,
) -> ActorVisibleBindingEndpointV1:
    return ActorVisibleBindingEndpointV1(
        resolution_kind="hidden-unresolved",
        owner_player_index=owner_player_index,
        semantic_zone=semantic_zone,
        bound_card=None,
        missing_reason=missing_reason,
    )


def _special_condition_endpoint() -> ActorVisibleBindingEndpointV1:
    return ActorVisibleBindingEndpointV1(
        resolution_kind="special-condition",
        owner_player_index=None,
        semantic_zone="not-applicable",
        bound_card=None,
        missing_reason=None,
    )


def _binding_core(
    source: ActorVisibleBindingEndpointV1,
    *,
    target: ActorVisibleBindingEndpointV1 | None = None,
    host: ActorVisibleBindingEndpointV1 | None = None,
) -> ActorVisibleActionBindingCoreV1:
    return ActorVisibleActionBindingCoreV1(
        schema_version=ACTOR_VISIBLE_BINDING_SCHEMA_VERSION,
        source=source,
        target=_not_applicable_endpoint() if target is None else target,
        host=_not_applicable_endpoint() if host is None else host,
    )


def _validate_binding_core_for_option_type(
    core: ActorVisibleActionBindingCoreV1,
    *,
    option_type: int,
) -> None:
    """Fail closed on endpoint combinations outside an official resolver row."""
    if option_type not in OPTION_RESOLVER_TABLE_V1:
        raise ActorVisibleV2Error("binding refers to an unsupported OptionType")
    row = OPTION_RESOLVER_TABLE_V1[option_type]
    for endpoint, legal_missing_reasons, name in (
        (core.source, row.source_missing_reasons, "source"),
        (core.target, row.target_missing_reasons, "target"),
        (core.host, row.host_missing_reasons, "host"),
    ):
        if (
            endpoint.missing_reason is not None
            and endpoint.missing_reason not in legal_missing_reasons
        ):
            raise ActorVisibleV2Error(
                f"binding core {name} missing_reason is not legal for OptionType"
            )
    none = _not_applicable_endpoint()
    if row.target_resolver == "not-applicable" and core.target != none:
        raise ActorVisibleV2Error("binding core target conflicts with resolver row")
    if row.host_resolver == "not-applicable" and core.host != none:
        raise ActorVisibleV2Error("binding core host conflicts with resolver row")
    if row.target_resolver == "in-play-pokemon" and (
        core.target.resolution_kind != "public-visible"
        or core.target.semantic_zone not in {"active", "bench"}
    ):
        raise ActorVisibleV2Error("binding core target conflicts with in-play resolver row")
    if row.host_resolver == "in-play-pokemon" and (
        core.host != core.target
        or core.host.resolution_kind != "public-visible"
        or core.host.semantic_zone not in {"active", "bench"}
    ):
        raise ActorVisibleV2Error("binding core host conflicts with in-play resolver row")

    source = core.source
    if row.source_resolver in {"number", "not-applicable"} and source != none:
        raise ActorVisibleV2Error("binding core source conflicts with non-card resolver row")
    if row.source_resolver == "special-condition" and source != _special_condition_endpoint():
        raise ActorVisibleV2Error("binding core source conflicts with special-condition row")
    if row.source_resolver == "actor-hand" and (
        source.resolution_kind != "actor-visible" or source.semantic_zone != "hand"
    ):
        raise ActorVisibleV2Error("binding core source conflicts with actor-hand resolver row")
    if row.source_resolver in {"attached-tool", "attached-energy"}:
        expected_zones = (
            {"active-tool", "bench-tool"}
            if row.source_resolver == "attached-tool"
            else {"active-energy", "bench-energy"}
        )
        if (
            source.resolution_kind != "public-visible"
            or source.semantic_zone not in expected_zones
        ):
            raise ActorVisibleV2Error("binding core source conflicts with attachment resolver row")
    if row.source_resolver == "actor-active" and (
        source.semantic_zone != "active"
        or source.resolution_kind not in {"public-visible", "hidden-unresolved"}
    ):
        raise ActorVisibleV2Error("binding core source conflicts with actor-active resolver row")
    if row.source_resolver == "area-index":
        area_zones = {
            1: {"deck", "deck-reveal"},
            2: {"hand"},
            3: {"discard"},
            4: {"active"},
            5: {"bench"},
            6: {"prize"},
            7: {"stadium"},
            8: {"energy"},
            9: {"tool"},
            10: {"pre-evolution"},
            11: {"player"},
            12: {"looking"},
        }
        legal_zones = set().union(
            *(area_zones[area] for area in row.legal_source_areas)
        )
        if source.semantic_zone not in legal_zones:
            raise ActorVisibleV2Error("binding core source conflicts with AreaType resolver row")
    if row.source_resolver == "bounded-card-registry" and (
        source.resolution_kind != "special-condition"
        and source.semantic_zone == "not-applicable"
    ):
        raise ActorVisibleV2Error("binding core source conflicts with Skill registry row")
    if option_type in (0, 1, 2, 14):
        if core != _binding_core(none):
            raise ActorVisibleV2Error("binding core is invalid for a non-card option")
        return
    if option_type == 16:
        if core.source != _special_condition_endpoint() or core.target != none or core.host != none:
            raise ActorVisibleV2Error("binding core is invalid for special condition")
        return
    if option_type == 15 and core.source.resolution_kind == "special-condition":
        if core.target != none or core.host != none:
            raise ActorVisibleV2Error("binding core is invalid for special-condition Skill")
        return
    if option_type in (4, 5, 6):
        expected_source_zones = (
            {"active-tool", "bench-tool"}
            if option_type == 4
            else {"active-energy", "bench-energy"}
        )
        if (
            core.source.semantic_zone not in expected_source_zones
            or core.target != core.host
            or core.target.semantic_zone not in {"active", "bench"}
        ):
            raise ActorVisibleV2Error("binding core is invalid for attached-card option")
        return
    if option_type == 7 and core.source.semantic_zone != "hand":
        raise ActorVisibleV2Error("binding core is invalid for PLAY")
    if option_type in (12, 13) and core.source.semantic_zone != "active":
        raise ActorVisibleV2Error("binding core is invalid for active-Pokemon option")
    if option_type in (8, 9) and core.target.semantic_zone not in {"active", "bench"}:
        raise ActorVisibleV2Error("binding core is invalid for in-play target option")
    if option_type in (3, 7, 10, 11, 12, 13, 15) and core.target != none:
        raise ActorVisibleV2Error("binding core has an unexpected target")
    if option_type not in (4, 5, 6) and core.host != none:
        raise ActorVisibleV2Error("binding core has an unexpected host")


@dataclass(frozen=True, slots=True, repr=False)
class ActorVisibleDecisionStateV2:
    information_view: ActorInformationViewV2
    legal_actions: tuple[ActorVisibleLegalActionV2, ...]
    public_collision_groups: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.information_view) is not ActorInformationViewV2:
            raise ActorVisibleV2Error("C1 v2 information_view has the wrong type")
        # A loader may receive a dataclasses.replace/object-injected instance;
        # replay the full typed-tree constructor contract before any resolver.
        _validate_information_view_tree(self.information_view)
        if type(self.legal_actions) is not tuple:
            raise ActorVisibleV2Error("C1 v2 legal_actions must be a tuple")
        if type(self.public_collision_groups) is not tuple:
            raise ActorVisibleV2Error("C1 v2 collision groups must be a tuple")
        if len(self.legal_actions) > MAX_LEGAL_CANDIDATES_V2:
            raise ActorVisibleV2Error("C1 v2 exceeds MAX_LEGAL_CANDIDATES_V2")
        if any(type(action) is not ActorVisibleLegalActionV2 for action in self.legal_actions):
            raise ActorVisibleV2Error("C1 v2 legal_actions must contain ActorVisibleLegalActionV2")
        for group in self.public_collision_groups:
            if (
                type(group) is not tuple
                or len(group) != 2
                or type(group[0]) is not str
                or type(group[1]) is not int
                or group[1] < 2
            ):
                raise ActorVisibleV2Error("C1 v2 collision groups must contain (public_action_id, count) tuples")
            _strict_digest(group[0], field="C1 v2 collision public_action_id")
        view = self.information_view
        contexts = (
            CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.get(view.selection_type)
            if type(view.selection_type) is int
            else None
        )
        if (
            contexts is None
            or type(view.selection_context) is not int
            or view.selection_context not in contexts
        ):
            raise ActorVisibleV2Error("C1 v2 selection type/context is not recognized")
        if (
            type(view.min_count) is not int
            or type(view.max_count) is not int
            or view.min_count < 0
            or not view.min_count <= view.max_count <= len(self.legal_actions)
        ):
            raise ActorVisibleV2Error("C1 v2 selection counts are inconsistent")
        for action in self.legal_actions:
            ActorVisibleActionBindingCoreV1.__post_init__(action.binding.core)
            for endpoint in (action.binding.core.source, action.binding.core.target, action.binding.core.host):
                ActorVisibleBindingEndpointV1.__post_init__(endpoint)
                if endpoint.bound_card is not None:
                    BoundCardRefV1.__post_init__(endpoint.bound_card)
            ActorVisibleActionBindingV1.__post_init__(action.binding)
            ActorVisibleLegalActionV2.__post_init__(action)
            validate_actor_visible_legal_action_v2(view, action)
        if len({action.action_key_digest for action in self.legal_actions}) != len(
            self.legal_actions
        ):
            raise ActorVisibleV2Error("C1 v2 requires globally unique ActionKey digests")
        if len({action.local_action_id for action in self.legal_actions}) != len(
            self.legal_actions
        ):
            raise ActorVisibleV2Error("C1 v2 requires globally unique local action IDs")
        public_counts: dict[str, int] = {}
        for action in self.legal_actions:
            public_counts[action.public_action_id] = (
                public_counts.get(action.public_action_id, 0) + 1
            )
        expected_collisions = tuple(sorted(
            (public_id, count)
            for public_id, count in public_counts.items()
            if count > 1
        ))
        if self.public_collision_groups != expected_collisions:
            raise ActorVisibleV2Error("C1 v2 public collision groups do not verify")

    @property
    def schema_version(self) -> int:
        return C1_V2_SCHEMA_VERSION

    @property
    def legacy_public_state_digest(self) -> str:
        return _c1_v1_digest(project_c1v2_to_c1v1_public_state(self))

    def __repr__(self) -> str:
        return (
            "ActorVisibleDecisionStateV2("
            f"actor={self.information_view.actor}, legal_action_count={len(self.legal_actions)}, "
            f"collision_group_count={len(self.public_collision_groups)}, "
            "private_state=<redacted>, legal_actions=<redacted>)"
        )

    def to_public_trace_payload(self) -> dict[str, object]:
        """Purely reconstruct the frozen C1 v1 public trace from typed state."""
        action_payloads = [
            action.action_key.to_public_trace_payload() for action in self.legal_actions
        ]
        payload: dict[str, object] = {
            "action_keys": action_payloads,
            "actor": self.information_view.actor,
            "belief_summary": None,
            "metadata": {
                "public_action_set_digest": _c1_v1_digest(
                    sorted(action_payloads, key=_canonical_json)
                ),
                "public_state_digest": self.legacy_public_state_digest,
                "schema_version": 1,
            },
            "public_state": project_c1v2_to_c1v1_public_state(self),
            "visible_history": [],
        }
        payload["trace_digest"] = _c1_v1_digest(payload)
        return payload


def _parse_card(value: object, *, field: str, owner: int | None = None) -> CardRefV2:
    data = _mapping(value, field=field)
    card_id = _strict_int(data.get("id"), field=f"{field}.id", minimum=1)
    serial = _strict_int(data.get("serial"), field=f"{field}.serial")
    explicit_owner = _strict_int(data.get("playerIndex"), field=f"{field}.playerIndex")
    if explicit_owner not in (0, 1):
        raise ActorVisibleV2Error(f"{field}.playerIndex must be 0 or 1")
    if owner is not None and explicit_owner != owner:
        raise ActorVisibleV2Error(f"{field}.playerIndex does not match its derived owner")
    return CardRefV2(card_id=card_id, serial=serial, player_index=explicit_owner)


def _parse_pokemon(value: object, *, field: str, owner: int) -> PokemonStateV2:
    data = _mapping(value, field=field)
    extension_present = "playerIndex" in data
    if extension_present:
        extension_owner = _strict_int(data["playerIndex"], field=f"{field}.playerIndex")
        if extension_owner != owner:
            raise ActorVisibleV2Error(f"{field}.playerIndex does not match its derived owner")
    ref = PokemonRefV2(
        card_id=_strict_int(data.get("id"), field=f"{field}.id", minimum=1),
        serial=_strict_int(data.get("serial"), field=f"{field}.serial"),
        legacy_player_index_extension_present=extension_present,
    )
    energies = tuple(
        _strict_int(item, field=f"{field}.energies[]")
        for item in _bounded_list(data.get("energies"), field=f"{field}.energies")
    )
    if any(value > 11 for value in energies):
        raise ActorVisibleV2Error(f"{field}.energies must contain EnergyType 0..11")

    def nested(name: str) -> tuple[BoundCardRefV1, ...]:
        return tuple(
            BoundCardRefV1(**{
                "card_id": card.card_id, "serial": card.serial, "player_index": card.player_index,
            })
            for card in (
                _parse_card(item, field=f"{field}.{name}[]", owner=owner)
                for item in _bounded_list(data.get(name), field=f"{field}.{name}")
            )
        )

    appear = data.get("appearThisTurn")
    if type(appear) is not bool:
        raise ActorVisibleV2Error(f"{field}.appearThisTurn must be a bool")
    return PokemonStateV2(
        ref=ref,
        owner=owner,
        hp=_strict_int(data.get("hp"), field=f"{field}.hp"),
        max_hp=_strict_int(data.get("maxHp"), field=f"{field}.maxHp"),
        appear_this_turn=appear,
        energies=energies,
        energy_cards=nested("energyCards"),
        tools=nested("tools"),
        pre_evolution=nested("preEvolution"),
    )


def _parse_player(value: object, *, field: str, owner: int, is_actor: bool) -> tuple[PlayerPublicStateV2, tuple[CardRefV2, ...] | None]:
    data = _mapping(value, field=field)
    active_raw = _bounded_list(data.get("active"), field=f"{field}.active")
    if len(active_raw) > 1:
        raise ActorVisibleV2Error(f"{field}.active may contain at most one slot")
    active = tuple(
        None if item is None else _parse_pokemon(item, field=f"{field}.active[]", owner=owner)
        for item in active_raw
    )
    bench = tuple(
        _parse_pokemon(item, field=f"{field}.bench[]", owner=owner)
        for item in _bounded_list(data.get("bench"), field=f"{field}.bench")
    )
    discard = tuple(
        BoundCardRefV1(card.card_id, card.serial, card.player_index)
        for card in (
            _parse_card(item, field=f"{field}.discard[]", owner=owner)
            for item in _bounded_list(data.get("discard"), field=f"{field}.discard")
        )
    )
    hand_count = _strict_int(data.get("handCount"), field=f"{field}.handCount")
    hand_raw = data.get("hand")
    own_hand: tuple[CardRefV2, ...] | None = None
    if is_actor:
        values = _bounded_list(hand_raw, field=f"{field}.hand")
        if len(values) != hand_count:
            raise ActorVisibleV2Error(f"{field}.hand must match handCount")
        own_hand = tuple(
            _parse_card(item, field=f"{field}.hand[]", owner=owner) for item in values
        )
    elif hand_raw is not None:
        raise ActorVisibleV2Error(f"{field}.hand must be null for the opponent")
    flags: dict[str, bool] = {}
    for name in ("poisoned", "burned", "asleep", "paralyzed", "confused"):
        flag = data.get(name)
        if type(flag) is not bool:
            raise ActorVisibleV2Error(f"{field}.{name} must be a bool")
        flags[name] = flag
    prize = _bounded_list(data.get("prize"), field=f"{field}.prize")
    return (
        PlayerPublicStateV2(
            active=active,
            bench=bench,
            discard=discard,
            hand_count=hand_count,
            deck_count=_strict_int(data.get("deckCount"), field=f"{field}.deckCount"),
            prize_count=len(prize),
            bench_max=_strict_int(data.get("benchMax"), field=f"{field}.benchMax"),
            **flags,
        ),
        own_hand,
    )


def _parse_optional_card(value: object, *, field: str, actor: int) -> CardRefV2 | None:
    del actor  # Context/effect cards can be actor-visible while owned by either side.
    return None if value is None else _parse_card(value, field=field)


def _selection_view(select: Mapping[str, Any], current: Mapping[str, Any], *, actor: int, deck_count: int) -> ActorVisibleSelectionViewV1:
    deck_raw = select.get("deck")
    deck_reveal: tuple[CardRefV2, ...] | None
    if deck_raw is None:
        deck_reveal = None
    else:
        values = _bounded_list(deck_raw, field="select.deck")
        if len(values) != deck_count:
            raise ActorVisibleV2Error("select.deck must match the acting player's deckCount")
        deck_reveal = tuple(_parse_card(item, field="select.deck[]", owner=actor) for item in values)
    looking_raw = current.get("looking")
    looking: tuple[CardRefV2 | None, ...] | None
    if looking_raw is None:
        looking = None
    else:
        looking = tuple(
            None if item is None else _parse_card(item, field="current.looking[]")
            for item in _bounded_list(looking_raw, field="current.looking")
        )
    return ActorVisibleSelectionViewV1(
        context_card=_parse_optional_card(select.get("contextCard"), field="select.contextCard", actor=actor),
        effect=_parse_optional_card(select.get("effect"), field="select.effect", actor=actor),
        deck_reveal=deck_reveal,
        looking=looking,
    )


def _bound_pokemon(pokemon: PokemonStateV2) -> BoundCardRefV1:
    return BoundCardRefV1(pokemon.ref.card_id, pokemon.ref.serial, pokemon.owner)


def _player_for_owner(view: ActorInformationViewV2, owner: int) -> PlayerPublicStateV2:
    if owner == view.actor:
        return view.self_player
    if owner == 1 - view.actor:
        return view.opponent_player
    raise ActorVisibleV2Error("option playerIndex must be 0 or 1")


def _visible_pokemon(
    view: ActorInformationViewV2,
    *, owner: int,
    area: int,
    index: int,
    field: str,
) -> tuple[BoundCardRefV1, str]:
    player = _player_for_owner(view, owner)
    if area == 4:
        if index >= len(player.active) or player.active[index] is None:
            raise ActorVisibleV2Error(f"{field} is outside the visible active zone")
        assert player.active[index] is not None
        return _bound_pokemon(player.active[index]), "active"
    if area == 5:
        if index >= len(player.bench):
            raise ActorVisibleV2Error(f"{field} is outside the visible bench zone")
        return _bound_pokemon(player.bench[index]), "bench"
    raise ActorVisibleV2Error(f"{field} must reference active or bench")


def _card_at_area(
    view: ActorInformationViewV2,
    *, owner: int,
    area: int,
    index: int,
    field: str,
) -> ActorVisibleBindingEndpointV1:
    """Resolve only a raw locator whose backing list is legitimately visible.

    Hidden deck/prize/attachment locators are legal missing values, whereas an
    out-of-range locator into a list that is actually visible is an error.
    """
    _player_for_owner(view, owner)
    if area == 2:
        if owner != view.actor:
            return _hidden_endpoint(
                owner_player_index=owner,
                semantic_zone="hand",
                missing_reason="hidden-zone",
            )
        hand = view.private_state.own_hand
        if index >= len(hand):
            raise ActorVisibleV2Error(f"{field} is outside the actor-visible hand")
        card = hand[index]
        return _visible_endpoint(
            BoundCardRefV1(card.card_id, card.serial, card.player_index),
            semantic_zone="hand", resolution_kind="actor-visible",
        )
    if area == 3:
        discard = _player_for_owner(view, owner).discard
        if index >= len(discard):
            raise ActorVisibleV2Error(f"{field} is outside the visible discard")
        return _visible_endpoint(
            discard[index], semantic_zone="discard", resolution_kind="public-visible",
        )
    if area in (4, 5):
        card, zone = _visible_pokemon(view, owner=owner, area=area, index=index, field=field)
        return _visible_endpoint(
            card, semantic_zone=zone, resolution_kind="public-visible",
        )
    if area == 1:
        reveal = view.private_state.selection_view.deck_reveal
        if owner != view.actor or reveal is None:
            return _hidden_endpoint(
                owner_player_index=owner if owner in (0, 1) else None,
                semantic_zone="deck",
                missing_reason="hidden-zone",
            )
        if index >= len(reveal):
            raise ActorVisibleV2Error(f"{field} is outside the actor-visible deck reveal")
        card = reveal[index]
        return _visible_endpoint(
            BoundCardRefV1(card.card_id, card.serial, card.player_index),
            semantic_zone="deck-reveal", resolution_kind="actor-visible",
        )
    if area == 7:
        stadium = view.board_stadium
        if stadium is None or index != 0:
            raise ActorVisibleV2Error(f"{field} is outside the visible stadium")
        if stadium.player_index != owner:
            raise ActorVisibleV2Error(f"{field} stadium Card owner does not match the resolver owner")
        return _visible_endpoint(
            stadium,
            semantic_zone="stadium",
            resolution_kind="public-visible",
        )
    if area == 12:
        looking = view.private_state.selection_view.looking
        if looking is None:
            return _hidden_endpoint(
                owner_player_index=owner if owner in (0, 1) else None,
                semantic_zone="looking",
                missing_reason="hidden-zone",
            )
        if index >= len(looking):
            raise ActorVisibleV2Error(f"{field} is outside the actor-visible looking list")
        card = looking[index]
        if card is None:
            return _hidden_endpoint(
                owner_player_index=owner,
                semantic_zone="looking",
                missing_reason="hidden-zone",
            )
        if card.player_index != owner:
            raise ActorVisibleV2Error(
                f"{field} owner does not match the actor-visible looking Card"
            )
        return _visible_endpoint(
            BoundCardRefV1(card.card_id, card.serial, card.player_index),
            semantic_zone="looking", resolution_kind="actor-visible",
        )
    if area == 6:
        return _hidden_endpoint(
            owner_player_index=owner if owner in (0, 1) else None,
            semantic_zone="prize",
            missing_reason="hidden-zone",
        )
    if area == 10:
        return _hidden_endpoint(
            owner_player_index=owner if owner in (0, 1) else None,
            semantic_zone="pre-evolution",
            missing_reason="not-addressable",
        )
    if area == 8:
        return _hidden_endpoint(
            owner_player_index=owner,
            semantic_zone="energy",
            missing_reason="not-addressable",
        )
    if area == 9:
        return _hidden_endpoint(
            owner_player_index=owner,
            semantic_zone="tool",
            missing_reason="not-addressable",
        )
    if area == 11:
        return _owner_resolved_endpoint(owner)
    raise ActorVisibleV2Error(f"{field} has an unsupported AreaType")


def _attached_card(
    view: ActorInformationViewV2,
    *, owner: int,
    area: int,
    host_index: int,
    attachment_index: int,
    attachment_kind: str,
    field: str,
) -> tuple[BoundCardRefV1, BoundCardRefV1, str]:
    host, host_zone = _visible_pokemon(
        view, owner=owner, area=area, index=host_index, field=f"{field}.host"
    )
    player = _player_for_owner(view, owner)
    if area == 4:
        pokemon = player.active[host_index]
        assert pokemon is not None
    else:
        pokemon = player.bench[host_index]
    attachments = pokemon.tools if attachment_kind == "tool" else pokemon.energy_cards
    if attachment_index >= len(attachments):
        raise ActorVisibleV2Error(f"{field} is outside the visible attached {attachment_kind} list")
    return attachments[attachment_index], host, host_zone


def _skill_registry(
    view: ActorInformationViewV2,
) -> tuple[
    dict[tuple[int, int], tuple[BoundCardRefV1, str, bool]],
    frozenset[tuple[int, int]],
]:
    """Build a bounded registry from already typed actor-visible values only."""
    entries: list[tuple[BoundCardRefV1, str, bool]] = []

    def add(card: BoundCardRefV1, zone: str, actor_visible: bool) -> None:
        entries.append((card, zone, actor_visible))

    for player in (view.self_player, view.opponent_player):
        for zone_name, pokemons in (("active", player.active), ("bench", player.bench)):
            for pokemon in pokemons:
                if pokemon is None:
                    continue
                add(_bound_pokemon(pokemon), zone_name, False)
                for card in pokemon.energy_cards:
                    add(card, f"{zone_name}-energy", False)
                for card in pokemon.tools:
                    add(card, f"{zone_name}-tool", False)
                for card in pokemon.pre_evolution:
                    add(card, "pre-evolution", False)
        for card in player.discard:
            add(card, "discard", False)
    for card in view.private_state.own_hand:
        add(BoundCardRefV1(card.card_id, card.serial, card.player_index), "hand", True)
    selection = view.private_state.selection_view
    for card in selection.deck_reveal or ():
        add(BoundCardRefV1(card.card_id, card.serial, card.player_index), "deck-reveal", True)
    for card in selection.looking or ():
        if card is not None:
            add(BoundCardRefV1(card.card_id, card.serial, card.player_index), "looking", True)
    if selection.context_card is not None:
        card = selection.context_card
        add(BoundCardRefV1(card.card_id, card.serial, card.player_index), "context-card", True)
    if selection.effect is not None:
        card = selection.effect
        add(BoundCardRefV1(card.card_id, card.serial, card.player_index), "effect", True)
    if view.board_stadium is not None:
        add(view.board_stadium, "stadium", False)
    result: dict[tuple[int, int], tuple[BoundCardRefV1, str, bool]] = {}
    ambiguous: set[tuple[int, int]] = set()
    for card, zone, actor_visible in entries:
        key = (card.card_id, card.serial)
        if key in result:
            ambiguous.add(key)
        else:
            result[key] = (card, zone, actor_visible)
    for key in ambiguous:
        result.pop(key, None)
    return result, frozenset(ambiguous)


def _source_from_option(option: Mapping[str, Any], *, view: ActorInformationViewV2) -> ActorVisibleActionBindingCoreV1:
    """Resolve a closed official Option union without guessing hidden data."""
    option_type = _strict_int(option.get("type"), field="select.option.type")
    if option_type not in OPTION_RESOLVER_TABLE_V1:
        raise ActorVisibleV2Error("select.option.type is not an official OptionType")
    row = OPTION_RESOLVER_TABLE_V1[option_type]
    no_source = _binding_core(_not_applicable_endpoint())
    if option_type in (0, 1, 2, 14):
        return no_source
    if option_type == 16:
        return _binding_core(_special_condition_endpoint())
    if option_type in (3, 8, 9):
        area = _strict_int(option.get("area"), field="select.option.area", minimum=1)
        if area > 12 or area not in row.legal_source_areas:
            raise ActorVisibleV2Error("select.option.area is not allowed for this OptionType")
        index = _strict_int(option.get("index"), field="select.option.index")
        owner = (
            _strict_int(option.get("playerIndex"), field="select.option.playerIndex")
            if option_type == 3 else view.actor
        )
        source = _card_at_area(
            view, owner=owner, area=area, index=index, field="select.option.index"
        )
        if option_type in (8, 9):
            target_area = _strict_int(option.get("inPlayArea"), field="select.option.inPlayArea", minimum=1)
            if target_area not in row.legal_target_areas:
                raise ActorVisibleV2Error(
                    "select.option.inPlayArea is not allowed for this OptionType"
                )
            target_index = _strict_int(option.get("inPlayIndex"), field="select.option.inPlayIndex")
            target_card, target_zone = _visible_pokemon(
                view, owner=view.actor, area=target_area, index=target_index,
                field="select.option.inPlayIndex",
            )
            return _binding_core(
                source,
                target=_visible_endpoint(
                    target_card,
                    semantic_zone=target_zone,
                    resolution_kind="public-visible",
                ),
            )
        return _binding_core(source)
    if option_type == 7:
        index = _strict_int(option.get("index"), field="select.option.index")
        return _binding_core(_card_at_area(
            view, owner=view.actor, area=2, index=index, field="select.option.index"
        ))
    if option_type in (4, 5, 6):
        owner = _strict_int(option.get("playerIndex"), field="select.option.playerIndex")
        area = _strict_int(option.get("area"), field="select.option.area", minimum=1)
        if area not in row.legal_source_areas:
            raise ActorVisibleV2Error("select.option.area is not allowed for this OptionType")
        host_index = _strict_int(option.get("index"), field="select.option.index")
        slot_field = "toolIndex" if option_type == 4 else "energyIndex"
        slot = _strict_int(option.get(slot_field), field=f"select.option.{slot_field}")
        kind = "tool" if option_type == 4 else "energy"
        source_card, host_card, host_zone = _attached_card(
            view,
            owner=owner,
            area=area,
            host_index=host_index,
            attachment_index=slot,
            attachment_kind=kind,
            field=f"select.option.{slot_field}",
        )
        host = _visible_endpoint(
            host_card,
            semantic_zone=host_zone,
            resolution_kind="public-visible",
        )
        return _binding_core(
            _visible_endpoint(
                source_card,
                semantic_zone=f"{host_zone}-{kind}",
                resolution_kind="public-visible",
            ),
            target=host,
            host=host,
        )
    if option_type in (10, 11):
        area = _strict_int(option.get("area"), field="select.option.area", minimum=1)
        if area not in row.legal_source_areas:
            raise ActorVisibleV2Error("select.option.area is not allowed for this OptionType")
        index = _strict_int(option.get("index"), field="select.option.index")
        owner = view.actor
        if area == 7:
            stadium = view.board_stadium
            if stadium is None or index != 0:
                raise ActorVisibleV2Error("select.option.index is outside the visible stadium")
            owner = stadium.player_index
        source = _card_at_area(view, owner=owner, area=area, index=index, field="select.option.index")
        return _binding_core(source)
    if option_type in (12, 13):
        if not view.self_player.active or view.self_player.active[0] is None:
            return _binding_core(_hidden_endpoint(
                owner_player_index=view.actor,
                semantic_zone="active",
                missing_reason="not-addressable",
            ))
        source = _bound_pokemon(view.self_player.active[0])
        return _binding_core(_visible_endpoint(
            source, semantic_zone="active", resolution_kind="public-visible",
        ))
    assert option_type == 15
    card_id = _strict_int(option.get("cardId"), field="select.option.cardId")
    serial = _strict_int(option.get("serial"), field="select.option.serial")
    if card_id == 0:
        return _binding_core(_special_condition_endpoint())
    registry, ambiguous = _skill_registry(view)
    source_info = registry.get((card_id, serial))
    if source_info is None:
        return _binding_core(_hidden_endpoint(
            owner_player_index=None,
            semantic_zone="hidden",
            missing_reason=(
                "ambiguous-registry"
                if (card_id, serial) in ambiguous
                else "not-addressable"
            ),
        ))
    source, zone, actor_visible = source_info
    return _binding_core(_visible_endpoint(
        source,
        semantic_zone=zone,
        resolution_kind="actor-visible" if actor_visible else "public-visible",
    ))


def rebuild_actor_visible_action_binding_core_v1(
    information_view: ActorInformationViewV2,
    action_key: ActionKey,
) -> ActorVisibleActionBindingCoreV1:
    """Re-resolve one ActionKey exclusively against its typed C1v2 information view."""
    if not isinstance(information_view, ActorInformationViewV2):
        raise ActorVisibleV2Error("binding rebuild requires an ActorInformationViewV2")
    if not isinstance(action_key, ActionKey) or action_key.actor_identity_payload is None:
        raise ActorVisibleV2Error("binding rebuild requires a private ActionKey v2")
    if (
        action_key.selection_type != information_view.selection_type
        or action_key.context != information_view.selection_context
    ):
        raise ActorVisibleV2Error("ActionKey selection does not match the typed decision state")
    if type(action_key.option_type) is not int:
        raise ActorVisibleV2Error("ActionKey option_type must be an official integer")
    option: dict[str, object] = {"type": action_key.option_type}
    for name, value in action_key.actor_identity_payload:
        if type(name) is not str or name == "type" or name in option:
            raise ActorVisibleV2Error("ActionKey actor payload is not a closed option mapping")
        option[name] = value
    return _source_from_option(option, view=information_view)


def validate_actor_visible_legal_action_v2(
    information_view: ActorInformationViewV2,
    action: ActorVisibleLegalActionV2,
) -> ActorVisibleLegalActionV2:
    """Validate a persisted local candidate against the authoritative typed state."""
    if not isinstance(action, ActorVisibleLegalActionV2):
        raise ActorVisibleV2Error("local candidate has the wrong type")
    expected_core = rebuild_actor_visible_action_binding_core_v1(
        information_view,
        action.action_key,
    )
    if action.binding.core != expected_core:
        raise ActorVisibleV2Error("binding core does not match the typed decision state")
    expected_local_action_id = derive_local_action_id_v1(
        action_key_digest=action.action_key.digest,
        binding_core=expected_core,
    )
    if action.local_action_id != expected_local_action_id:
        raise ActorVisibleV2Error("local_action_id does not match the typed decision state")
    expected_public_action_id = public_action_id_v1(
        action.action_key.to_public_trace_payload()
    )
    if action.public_action_id != expected_public_action_id:
        raise ActorVisibleV2Error("public_action_id does not match the typed decision state")
    return action


def validate_actor_visible_decision_state_v2(
    state: ActorVisibleDecisionStateV2,
) -> ActorVisibleDecisionStateV2:
    """Re-run the complete persisted-state contract at a loader boundary."""
    if not isinstance(state, ActorVisibleDecisionStateV2):
        raise ActorVisibleV2Error("C1 v2 decision state has the wrong type")
    ActorVisibleDecisionStateV2.__post_init__(state)
    return state


def _exact_mapping(value: object, *, field: str, keys: frozenset[str]) -> Mapping[str, Any]:
    data = _mapping(value, field=field)
    if set(data) != keys:
        raise ActorVisibleV2Error(f"{field} must have exact keys")
    return data


def _serialized_list(value: object, *, field: str, maximum: int = _MAX_CARD_COLLECTION) -> list[Any]:
    return _bounded_list(value, field=field, maximum=maximum)


def _serialize_card(card: CardRefV2 | BoundCardRefV1) -> dict[str, int]:
    return {"card_id": card.card_id, "serial": card.serial, "player_index": card.player_index}


def _deserialize_card(value: object, *, field: str, bound: bool) -> CardRefV2 | BoundCardRefV1:
    data = _exact_mapping(value, field=field, keys=frozenset({"card_id", "serial", "player_index"}))
    cls = BoundCardRefV1 if bound else CardRefV2
    return cls(
        _strict_int(data["card_id"], field=f"{field}.card_id", minimum=1),
        _strict_int(data["serial"], field=f"{field}.serial"),
        _strict_owner(data["player_index"], field=f"{field}.player_index"),
    )


def _serialize_pokemon(pokemon: PokemonStateV2) -> dict[str, object]:
    return {
        "ref": {"card_id": pokemon.ref.card_id, "serial": pokemon.ref.serial,
                "legacy_player_index_extension_present": pokemon.ref.legacy_player_index_extension_present},
        "owner": pokemon.owner, "hp": pokemon.hp, "max_hp": pokemon.max_hp,
        "appear_this_turn": pokemon.appear_this_turn, "energies": list(pokemon.energies),
        "energy_cards": [_serialize_card(card) for card in pokemon.energy_cards],
        "tools": [_serialize_card(card) for card in pokemon.tools],
        "pre_evolution": [_serialize_card(card) for card in pokemon.pre_evolution],
    }


def _deserialize_pokemon(value: object, *, field: str) -> PokemonStateV2:
    data = _exact_mapping(value, field=field, keys=frozenset({
        "ref", "owner", "hp", "max_hp", "appear_this_turn", "energies", "energy_cards", "tools", "pre_evolution",
    }))
    ref = _exact_mapping(data["ref"], field=f"{field}.ref", keys=frozenset({"card_id", "serial", "legacy_player_index_extension_present"}))
    def cards(name: str) -> tuple[BoundCardRefV1, ...]:
        return tuple(_deserialize_card(item, field=f"{field}.{name}[]", bound=True) for item in _serialized_list(data[name], field=f"{field}.{name}"))  # type: ignore[return-value]
    return PokemonStateV2(
        ref=PokemonRefV2(_strict_int(ref["card_id"], field=f"{field}.ref.card_id", minimum=1), _strict_int(ref["serial"], field=f"{field}.ref.serial"), _strict_bool(ref["legacy_player_index_extension_present"], field=f"{field}.ref.legacy_player_index_extension_present")),
        owner=_strict_owner(data["owner"], field=f"{field}.owner"), hp=_strict_int(data["hp"], field=f"{field}.hp"), max_hp=_strict_int(data["max_hp"], field=f"{field}.max_hp"),
        appear_this_turn=_strict_bool(data["appear_this_turn"], field=f"{field}.appear_this_turn"),
        energies=tuple(_strict_int(item, field=f"{field}.energies[]") for item in _serialized_list(data["energies"], field=f"{field}.energies")),
        energy_cards=cards("energy_cards"), tools=cards("tools"), pre_evolution=cards("pre_evolution"),
    )


def _serialize_player(player: PlayerPublicStateV2) -> dict[str, object]:
    return {
        "active": [None if item is None else _serialize_pokemon(item) for item in player.active],
        "bench": [_serialize_pokemon(item) for item in player.bench], "discard": [_serialize_card(item) for item in player.discard],
        "hand_count": player.hand_count, "deck_count": player.deck_count, "prize_count": player.prize_count, "bench_max": player.bench_max,
        "poisoned": player.poisoned, "burned": player.burned, "asleep": player.asleep, "paralyzed": player.paralyzed, "confused": player.confused,
    }


def _deserialize_player(value: object, *, field: str) -> PlayerPublicStateV2:
    keys = frozenset({"active", "bench", "discard", "hand_count", "deck_count", "prize_count", "bench_max", "poisoned", "burned", "asleep", "paralyzed", "confused"})
    data = _exact_mapping(value, field=field, keys=keys)
    active = _serialized_list(data["active"], field=f"{field}.active", maximum=1)
    return PlayerPublicStateV2(
        active=tuple(None if item is None else _deserialize_pokemon(item, field=f"{field}.active[]") for item in active),
        bench=tuple(_deserialize_pokemon(item, field=f"{field}.bench[]") for item in _serialized_list(data["bench"], field=f"{field}.bench")),
        discard=tuple(_deserialize_card(item, field=f"{field}.discard[]", bound=True) for item in _serialized_list(data["discard"], field=f"{field}.discard")),  # type: ignore[arg-type]
        hand_count=_strict_int(data["hand_count"], field=f"{field}.hand_count"), deck_count=_strict_int(data["deck_count"], field=f"{field}.deck_count"), prize_count=_strict_int(data["prize_count"], field=f"{field}.prize_count"), bench_max=_strict_int(data["bench_max"], field=f"{field}.bench_max"),
        poisoned=_strict_bool(data["poisoned"], field=f"{field}.poisoned"), burned=_strict_bool(data["burned"], field=f"{field}.burned"), asleep=_strict_bool(data["asleep"], field=f"{field}.asleep"), paralyzed=_strict_bool(data["paralyzed"], field=f"{field}.paralyzed"), confused=_strict_bool(data["confused"], field=f"{field}.confused"),
    )


def _serialize_selection(selection: ActorVisibleSelectionViewV1) -> dict[str, object]:
    return {"schema_version": selection.schema_version, "context_card": None if selection.context_card is None else _serialize_card(selection.context_card), "effect": None if selection.effect is None else _serialize_card(selection.effect), "deck_reveal": None if selection.deck_reveal is None else [_serialize_card(card) for card in selection.deck_reveal], "looking": None if selection.looking is None else [None if card is None else _serialize_card(card) for card in selection.looking]}


def _deserialize_selection(value: object, *, field: str) -> ActorVisibleSelectionViewV1:
    data = _exact_mapping(value, field=field, keys=frozenset({"schema_version", "context_card", "effect", "deck_reveal", "looking"}))
    if data["schema_version"] != ACTOR_VISIBLE_SELECTION_SCHEMA_VERSION:
        raise ActorVisibleV2Error(f"{field}.schema_version is invalid")
    def nullable_card(item: object, name: str) -> CardRefV2 | None:
        return None if item is None else _deserialize_card(item, field=f"{field}.{name}", bound=False)  # type: ignore[return-value]
    deck = data["deck_reveal"]
    looking = data["looking"]
    return ActorVisibleSelectionViewV1(nullable_card(data["context_card"], "context_card"), nullable_card(data["effect"], "effect"), None if deck is None else tuple(_deserialize_card(item, field=f"{field}.deck_reveal[]", bound=False) for item in _serialized_list(deck, field=f"{field}.deck_reveal")), None if looking is None else tuple(nullable_card(item, "looking[]") for item in _serialized_list(looking, field=f"{field}.looking")))  # type: ignore[arg-type]


def _serialize_view(view: ActorInformationViewV2) -> dict[str, object]:
    return {"actor": view.actor, "self_player": _serialize_player(view.self_player), "opponent_player": _serialize_player(view.opponent_player), "private_state": {"own_hand": [_serialize_card(card) for card in view.private_state.own_hand], "selection_view": _serialize_selection(view.private_state.selection_view), "visibility_basis": view.private_state.visibility_basis}, "board_stadium": None if view.board_stadium is None else _serialize_card(view.board_stadium), "stadium_played": view.stadium_played, "supporter_played": view.supporter_played, "energy_attached": view.energy_attached, "retreated": view.retreated, "first_player": view.first_player, "observed_result": view.observed_result, "step": view.step, "turn": view.turn, "turn_action_count": view.turn_action_count, "remain_damage_counter": view.remain_damage_counter, "remain_energy_cost": view.remain_energy_cost, "selection_type": view.selection_type, "selection_context": view.selection_context, "min_count": view.min_count, "max_count": view.max_count}


def _deserialize_view(value: object, *, field: str) -> ActorInformationViewV2:
    keys = frozenset({"actor", "self_player", "opponent_player", "private_state", "board_stadium", "stadium_played", "supporter_played", "energy_attached", "retreated", "first_player", "observed_result", "step", "turn", "turn_action_count", "remain_damage_counter", "remain_energy_cost", "selection_type", "selection_context", "min_count", "max_count"})
    data = _exact_mapping(value, field=field, keys=keys)
    private = _exact_mapping(data["private_state"], field=f"{field}.private_state", keys=frozenset({"own_hand", "selection_view", "visibility_basis"}))
    hand = tuple(_deserialize_card(item, field=f"{field}.private_state.own_hand[]", bound=False) for item in _serialized_list(private["own_hand"], field=f"{field}.private_state.own_hand"))
    view = ActorInformationViewV2(
        actor=_strict_owner(data["actor"], field=f"{field}.actor"), self_player=_deserialize_player(data["self_player"], field=f"{field}.self_player"), opponent_player=_deserialize_player(data["opponent_player"], field=f"{field}.opponent_player"),
        private_state=ActorPrivateStateV2(hand, _deserialize_selection(private["selection_view"], field=f"{field}.private_state.selection_view"), private["visibility_basis"] if type(private["visibility_basis"]) is str else ""),
        board_stadium=None if data["board_stadium"] is None else _deserialize_card(data["board_stadium"], field=f"{field}.board_stadium", bound=True),  # type: ignore[arg-type]
        stadium_played=_strict_bool(data["stadium_played"], field=f"{field}.stadium_played"), supporter_played=_strict_bool(data["supporter_played"], field=f"{field}.supporter_played"), energy_attached=_strict_bool(data["energy_attached"], field=f"{field}.energy_attached"), retreated=_strict_bool(data["retreated"], field=f"{field}.retreated"),
        first_player=_strict_int(data["first_player"], field=f"{field}.first_player", minimum=-1), observed_result=_strict_int(data["observed_result"], field=f"{field}.observed_result", minimum=-1), step=_strict_int(data["step"], field=f"{field}.step"), turn=_strict_int(data["turn"], field=f"{field}.turn"), turn_action_count=_strict_int(data["turn_action_count"], field=f"{field}.turn_action_count"), remain_damage_counter=_strict_int(data["remain_damage_counter"], field=f"{field}.remain_damage_counter"), remain_energy_cost=_strict_int(data["remain_energy_cost"], field=f"{field}.remain_energy_cost"), selection_type=_strict_int(data["selection_type"], field=f"{field}.selection_type"), selection_context=_strict_int(data["selection_context"], field=f"{field}.selection_context"), min_count=_strict_int(data["min_count"], field=f"{field}.min_count"), max_count=_strict_int(data["max_count"], field=f"{field}.max_count"),
    )
    return view


def _serialize_binding(binding: ActorVisibleActionBindingV1) -> dict[str, object]:
    return {"core": binding.core.to_identity_dict(), "action_key_digest": binding.action_key_digest, "public_action_id": binding.public_action_id, "local_action_id": binding.local_action_id}


def _deserialize_binding(value: object, *, field: str) -> ActorVisibleActionBindingV1:
    data = _exact_mapping(value, field=field, keys=frozenset({"core", "action_key_digest", "public_action_id", "local_action_id"}))
    core_data = _exact_mapping(data["core"], field=f"{field}.core", keys=frozenset({"schema_version", "source", "target", "host"}))
    def endpoint(item: object, name: str) -> ActorVisibleBindingEndpointV1:
        endpoint_data = _exact_mapping(item, field=f"{field}.core.{name}", keys=frozenset({"resolution_kind", "owner_player_index", "semantic_zone", "bound_card", "missing_reason"}))
        return ActorVisibleBindingEndpointV1(endpoint_data["resolution_kind"], endpoint_data["owner_player_index"], endpoint_data["semantic_zone"], None if endpoint_data["bound_card"] is None else _deserialize_card(endpoint_data["bound_card"], field=f"{field}.core.{name}.bound_card", bound=True), endpoint_data["missing_reason"])  # type: ignore[arg-type]
    core = ActorVisibleActionBindingCoreV1(core_data["schema_version"], endpoint(core_data["source"], "source"), endpoint(core_data["target"], "target"), endpoint(core_data["host"], "host"))
    return ActorVisibleActionBindingV1(core, _strict_digest(data["action_key_digest"], field=f"{field}.action_key_digest"), _strict_digest(data["public_action_id"], field=f"{field}.public_action_id"), _strict_digest(data["local_action_id"], field=f"{field}.local_action_id"))


def serialize_actor_visible_decision_state_v2(state: ActorVisibleDecisionStateV2) -> dict[str, object]:
    """Serialize the complete typed local state with an exact closed schema."""
    validate_actor_visible_decision_state_v2(state)
    return {"schema_version": C1_V2_SCHEMA_VERSION, "information_view": _serialize_view(state.information_view), "legal_actions": [{"binding": _serialize_binding(action.binding), "action_key": {"payload": action.action_key.to_canonical_payload(), "digest": action.action_key.digest}} for action in state.legal_actions], "public_collision_groups": [[public_id, count] for public_id, count in state.public_collision_groups]}


def deserialize_actor_visible_decision_state_v2(payload: object) -> ActorVisibleDecisionStateV2:
    """Parse the exact local schema, recreate immutable values, and revalidate bindings."""
    data = _exact_mapping(payload, field="serialized C1 v2 state", keys=frozenset({"schema_version", "information_view", "legal_actions", "public_collision_groups"}))
    if _strict_int(data["schema_version"], field="serialized C1 v2 state.schema_version") != C1_V2_SCHEMA_VERSION:
        raise ActorVisibleV2Error("serialized C1 v2 state schema_version is invalid")
    view = _deserialize_view(data["information_view"], field="serialized C1 v2 state.information_view")
    public_resolution = {
        "actor": view.actor,
        "self": _legacy_public_player(view.self_player),
        "opponent": _legacy_public_player(view.opponent_player),
    }
    actions: list[ActorVisibleLegalActionV2] = []
    for item in _serialized_list(data["legal_actions"], field="serialized C1 v2 state.legal_actions", maximum=MAX_LEGAL_CANDIDATES_V2):
        action_data = _exact_mapping(item, field="serialized C1 v2 action", keys=frozenset({"binding", "action_key"}))
        action_key_data = _exact_mapping(action_data["action_key"], field="serialized C1 v2 action.action_key", keys=frozenset({"payload", "digest"}))
        try:
            action_key = ActionKey.from_serialized_payload(
                action_key_data["payload"], digest=action_key_data["digest"], public_resolution=public_resolution,
            )
        except DecisionStateError as exc:
            raise ActorVisibleV2Error("serialized C1 v2 action ActionKey is invalid") from exc
        actions.append(ActorVisibleLegalActionV2(_deserialize_binding(action_data["binding"], field="serialized C1 v2 action.binding"), action_key))
    groups = tuple((item[0], item[1]) for item in _serialized_list(data["public_collision_groups"], field="serialized C1 v2 state.public_collision_groups", maximum=MAX_LEGAL_CANDIDATES_V2) if type(item) is list and len(item) == 2)
    if len(groups) != len(data["public_collision_groups"]):
        raise ActorVisibleV2Error("serialized C1 v2 state.public_collision_groups must contain pairs")
    return validate_actor_visible_decision_state_v2(ActorVisibleDecisionStateV2(view, tuple(actions), groups))


def derive_local_action_id_v1(
    *,
    action_key_digest: str,
    binding_core: ActorVisibleActionBindingCoreV1,
) -> str:
    """Return the binding-aware local candidate ID from its closed core only."""
    if (
        type(action_key_digest) is not str
        or len(action_key_digest) != 64
        or any(character not in "0123456789abcdef" for character in action_key_digest)
    ):
        raise ActorVisibleV2Error("action_key_digest must be a SHA-256 hex digest")
    return _sha256(
        {
            "action_key_digest": action_key_digest,
            "binding_core": binding_core.to_identity_dict(),
        },
        prefix=_LOCAL_ACTION_ID_PREFIX,
    )


def _legacy_card_fields(card: BoundCardRefV1) -> dict[str, int]:
    return {"id": card.card_id, "serial": card.serial, "playerIndex": card.player_index}


def _legacy_pokemon_fields(pokemon: PokemonStateV2) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": pokemon.ref.card_id,
        "serial": pokemon.ref.serial,
    }
    if pokemon.ref.legacy_player_index_extension_present:
        fields["playerIndex"] = pokemon.owner
    fields.update({
        "hp": pokemon.hp,
        "maxHp": pokemon.max_hp,
        "appearThisTurn": pokemon.appear_this_turn,
        "energies_count": len(pokemon.energies),
        "energyCards_count": len(pokemon.energy_cards),
        "tools_count": len(pokemon.tools),
        "preEvolution_count": len(pokemon.pre_evolution),
    })
    return fields


def _legacy_public_player(player: PlayerPublicStateV2) -> dict[str, object]:
    return {
        "active": [
            None if pokemon is None else {"fields": _legacy_pokemon_fields(pokemon)}
            for pokemon in player.active
        ],
        "bench": [{"fields": _legacy_pokemon_fields(pokemon)} for pokemon in player.bench],
        "bench_max": player.bench_max,
        "deck_count": player.deck_count,
        "discard": [{"fields": _legacy_card_fields(card)} for card in player.discard],
        "hand_count": player.hand_count,
        "prize_count": player.prize_count,
        "status": {
            "poisoned": player.poisoned,
            "burned": player.burned,
            "asleep": player.asleep,
            "paralyzed": player.paralyzed,
            "confused": player.confused,
        },
    }


def project_c1v2_to_c1v1_public_state(state: ActorVisibleDecisionStateV2) -> dict[str, object]:
    """Purely reconstruct the frozen C1 v1 state from typed C1 v2 fields."""
    if not isinstance(state, ActorVisibleDecisionStateV2):
        raise ActorVisibleV2Error("projection requires an ActorVisibleDecisionStateV2")
    view = state.information_view
    return {
        "actor": view.actor,
        "board": {
            "stadium": None if view.board_stadium is None else {"id": view.board_stadium.card_id},
            "stadium_played": view.stadium_played,
            "supporter_played": view.supporter_played,
            "energy_attached": view.energy_attached,
            "retreated": view.retreated,
        },
        "first_player": view.first_player,
        "opponent": _legacy_public_player(view.opponent_player),
        "observed_result": view.observed_result,
        "select": {
            "context": view.selection_context,
            "max_count": view.max_count,
            "min_count": view.min_count,
            "option_count": len(state.legal_actions),
            "type": view.selection_type,
        },
        "self": _legacy_public_player(view.self_player),
        "step": view.step,
        "turn": view.turn,
        "turn_action_count": view.turn_action_count,
    }


def project_c1v2_to_c1v1_own_private_state(state: ActorVisibleDecisionStateV2) -> dict[str, object]:
    """Purely reconstruct the frozen C1 v1 actor-private hand-ID projection."""
    if not isinstance(state, ActorVisibleDecisionStateV2):
        raise ActorVisibleV2Error("projection requires an ActorVisibleDecisionStateV2")
    return {
        "hand_card_ids": sorted(card.card_id for card in state.information_view.private_state.own_hand),
        "visibility_basis": "acting_player_hand",
    }


def _preflight_actor_visible_v2(
    observation: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], list[Any]]:
    """Bound every allowlisted topology before frozen v1 can traverse it."""
    obs = _mapping(observation, field="observation")
    current = _mapping(obs.get("current"), field="current")
    select = _mapping(obs.get("select"), field="select")
    options = _bounded_list(
        select.get("option"),
        field="select.option",
        maximum=MAX_LEGAL_CANDIDATES_V2,
    )
    players = _bounded_list(current.get("players"), field="current.players", maximum=2)
    if len(players) != 2:
        raise ActorVisibleV2Error("current.players must contain exactly two players")
    actor = _strict_owner(current.get("yourIndex"), field="current.yourIndex")
    _bounded_list(current.get("stadium"), field="current.stadium", maximum=1)
    looking = current.get("looking")
    if looking is not None:
        _bounded_list(looking, field="current.looking")
    deck = select.get("deck")
    if deck is not None:
        _bounded_list(deck, field="select.deck")

    def player_topology(player_value: object, *, owner: int) -> None:
        player = _mapping(player_value, field=f"current.players[{owner}]")
        active = _bounded_list(player.get("active"), field=f"current.players[{owner}].active", maximum=1)
        bench = _bounded_list(player.get("bench"), field=f"current.players[{owner}].bench")
        _bounded_list(player.get("discard"), field=f"current.players[{owner}].discard")
        _bounded_list(player.get("prize"), field=f"current.players[{owner}].prize")
        hand = player.get("hand")
        if owner == actor:
            _bounded_list(hand, field=f"current.players[{owner}].hand")
        elif hand is not None:
            raise ActorVisibleV2Error(f"current.players[{owner}].opponent hand must be null")
        for zone_name, pokemons in (("active", active), ("bench", bench)):
            for position, pokemon_value in enumerate(pokemons):
                if pokemon_value is None and zone_name == "active":
                    continue
                pokemon = _mapping(pokemon_value, field=f"current.players[{owner}].{zone_name}[{position}]")
                for nested_name in ("energies", "energyCards", "tools", "preEvolution"):
                    _bounded_list(
                        pokemon.get(nested_name),
                        field=f"current.players[{owner}].{zone_name}[{position}].{nested_name}",
                    )

    player_topology(players[0], owner=0)
    player_topology(players[1], owner=1)
    return obs, current, select, options


def build_actor_visible_decision_state_v2(observation: object) -> ActorVisibleDecisionStateV2:
    """Build a typed C1 v2 state without retaining ``observation``.

    A bounded, non-recursive outer preflight rejects oversized candidate lists
    before the C1 v1 builder.  C1 v1 then preserves authoritative validation;
    V2 reads only its own finite allowlisted fields.
    """
    obs, current, select, options = _preflight_actor_visible_v2(observation)
    try:
        legacy = build_decision_state(observation)
    except DecisionStateError as exc:
        raise ActorVisibleV2Error("observation is not a valid C1 v1 decision") from exc
    if len({action.action_key.digest for action in legacy.legal_actions}) != len(legacy.legal_actions):
        raise ActorVisibleV2Error("C1 v2 requires globally unique ActionKey digests")
    actor = _strict_int(current.get("yourIndex"), field="current.yourIndex")
    if actor not in (0, 1):
        raise ActorVisibleV2Error("current.yourIndex must be 0 or 1")
    players = _bounded_list(current.get("players"), field="current.players", maximum=2)
    if len(players) != 2:
        raise ActorVisibleV2Error("current.players must contain exactly two players")
    self_player, own_hand = _parse_player(
        players[actor], field="current.players[self]", owner=actor, is_actor=True,
    )
    opponent_player, _ = _parse_player(
        players[1 - actor], field="current.players[opponent]", owner=1 - actor, is_actor=False,
    )
    assert own_hand is not None
    first_player = _strict_int(current.get("firstPlayer"), field="current.firstPlayer", minimum=-1)
    if first_player not in (-1, 0, 1):
        raise ActorVisibleV2Error("current.firstPlayer must be -1, 0, or 1")
    observed_result = _strict_int(current.get("result"), field="current.result", minimum=-1)
    if observed_result not in (-1, 0, 1):
        raise ActorVisibleV2Error("current.result must be -1, 0, or 1")
    stadium = _bounded_list(current.get("stadium"), field="current.stadium", maximum=1)
    stadium_ref = None
    if stadium:
        raw_stadium = _parse_card(stadium[0], field="current.stadium[0]")
        stadium_ref = BoundCardRefV1(raw_stadium.card_id, raw_stadium.serial, raw_stadium.player_index)

    def board_flag(name: str) -> bool:
        value = current.get(name)
        if type(value) is not bool:
            raise ActorVisibleV2Error(f"current.{name} must be a bool")
        return value
    selection_view = _selection_view(select, current, actor=actor, deck_count=self_player.deck_count)
    minimum = _strict_int(select.get("minCount"), field="select.minCount")
    maximum = _strict_int(select.get("maxCount"), field="select.maxCount")
    if not minimum <= maximum <= len(options):
        raise ActorVisibleV2Error("select bounds are inconsistent with legal options")
    view = ActorInformationViewV2(
        actor=actor,
        self_player=self_player,
        opponent_player=opponent_player,
        private_state=ActorPrivateStateV2(own_hand=own_hand, selection_view=selection_view),
        board_stadium=stadium_ref,
        stadium_played=board_flag("stadiumPlayed"),
        supporter_played=board_flag("supporterPlayed"),
        energy_attached=board_flag("energyAttached"),
        retreated=board_flag("retreated"),
        first_player=first_player,
        observed_result=observed_result,
        step=_strict_int(obs.get("step"), field="step"),
        turn=_strict_int(current.get("turn"), field="current.turn"),
        turn_action_count=_strict_int(current.get("turnActionCount"), field="current.turnActionCount"),
        remain_damage_counter=_strict_int(select.get("remainDamageCounter"), field="select.remainDamageCounter"),
        remain_energy_cost=_strict_int(select.get("remainEnergyCost"), field="select.remainEnergyCost"),
        selection_type=_strict_int(select.get("type"), field="select.type"),
        selection_context=_strict_int(select.get("context"), field="select.context"),
        min_count=minimum,
        max_count=maximum,
    )
    action_by_index = {action.option_index: action.action_key for action in legacy.legal_actions}
    bindings: list[ActorVisibleLegalActionV2] = []
    for index, raw_option in enumerate(options):
        option = _mapping(raw_option, field="select.option[]")
        action_key = action_by_index[index]
        binding_core = _source_from_option(option, view=view)
        public_action_id = public_action_id_v1(action_key.to_public_trace_payload())
        local_action_id = derive_local_action_id_v1(
            action_key_digest=action_key.digest,
            binding_core=binding_core,
        )
        binding = ActorVisibleActionBindingV1(
            core=binding_core,
            action_key_digest=action_key.digest,
            public_action_id=public_action_id,
            local_action_id=local_action_id,
        )
        bindings.append(
            ActorVisibleLegalActionV2(
                binding=binding,
                action_key=action_key,
            )
        )
    if len({action.local_action_id for action in bindings}) != len(bindings):
        raise ActorVisibleV2Error("C1 v2 requires globally unique local action IDs")
    public_counts: dict[str, int] = {}
    for action in bindings:
        public_counts[action.public_action_id] = public_counts.get(action.public_action_id, 0) + 1
    collision_groups = tuple(sorted(
        (public_id, count) for public_id, count in public_counts.items() if count > 1
    ))
    return ActorVisibleDecisionStateV2(
        information_view=view,
        legal_actions=tuple(bindings),
        public_collision_groups=collision_groups,
    )


__all__ = [
    "ACTOR_VISIBLE_BINDING_SCHEMA_VERSION",
    "ACTOR_VISIBLE_SELECTION_SCHEMA_VERSION",
    "ActorInformationViewV2",
    "ActorPrivateStateV2",
    "ActorVisibleActionBindingCoreV1",
    "ActorVisibleActionBindingV1",
    "ActorVisibleBindingEndpointV1",
    "ActorVisibleDecisionStateV2",
    "ActorVisibleLegalActionV2",
    "ActorVisibleSelectionViewV1",
    "ActorVisibleV2Error",
    "BoundCardRefV1",
    "C1_V2_SCHEMA_VERSION",
    "CardRefV2",
    "MAX_LEGAL_CANDIDATES_V2",
    "OPTION_RESOLVER_TABLE_V1",
    "OptionResolverRowV1",
    "PokemonRefV2",
    "PokemonStateV2",
    "build_actor_visible_decision_state_v2",
    "deserialize_actor_visible_decision_state_v2",
    "derive_local_action_id_v1",
    "project_c1v2_to_c1v1_own_private_state",
    "project_c1v2_to_c1v1_public_state",
    "rebuild_actor_visible_action_binding_core_v1",
    "serialize_actor_visible_decision_state_v2",
    "validate_actor_visible_decision_state_v2",
    "validate_actor_visible_legal_action_v2",
]
