"""Typed relational state and action schema for Meta Specialist v3.

The v2 model input is intentionally kept as the compatibility boundary.  This
module is the lossless, relation-preserving projection used by the v3 model:
entity identity is only a local public key for expressing host/source/target
edges, while hidden entities are represented without hidden card identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Sequence


REPRESENTATION_V3_SCHEMA = "meta-specialist-relational-representation-v3"


class RepresentationV3Error(ValueError):
    """Raised when a relational feature is outside the closed v3 contract."""


@dataclass(frozen=True, slots=True)
class PublicEntityLocatorV3:
    """State-local public alignment key, deliberately excluded from embeddings.

    ``zone_ordinal`` distinguishes public objects only while adapting a legacy
    record or resolving an action relation in *this* state.  It is neither a
    card serial nor a model feature: neural code may use it only to find the
    state token whose public features it consumes.
    """

    owner_role: int
    semantic_zone: str
    zone_ordinal: int

    def __post_init__(self) -> None:
        _check_int(self.owner_role, name="owner_role", minimum=1, maximum=2)
        if type(self.semantic_zone) is not str or not self.semantic_zone or len(self.semantic_zone) > 64:
            raise RepresentationV3Error("semantic_zone must be a bounded nonempty string")
        _check_int(self.zone_ordinal, name="zone_ordinal")

    def canonical_key(self) -> tuple[int, str, int]:
        return (self.owner_role, self.semantic_zone, self.zone_ordinal)


def _check_int(value: object, *, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f"..{maximum}" if maximum is not None else f">={minimum}"
        raise RepresentationV3Error(f"{name} must be an int in {bound}")
    return value


def _check_float_tuple(values: object, *, name: str) -> tuple[float, ...]:
    if type(values) is not tuple or any(type(value) is not float or not math.isfinite(value) for value in values):
        raise RepresentationV3Error(f"{name} must be a tuple of finite floats")
    return values


@dataclass(frozen=True, slots=True)
class EntityTokenV3:
    """One public entity and its typed relation references.

    ``entity_id`` is a state-local public key.  It is never fed to the model as
    a scalar feature; it is used only to resolve ``host_entity_id`` and action
    source/target edges.  ``zone=5`` is the hidden/unresolved zone and requires
    ``card_id=0`` so hidden card identity cannot leak into the model.
    """

    entity_id: int
    entity_type: int
    owner: int
    zone: int
    card_id: int
    host_entity_id: int | None
    scalar_features: tuple[float, ...]
    categorical_features: tuple[int, ...]
    binary_flags: tuple[int, ...]
    public_locator: PublicEntityLocatorV3 | None = None

    def __post_init__(self) -> None:
        _check_int(self.entity_id, name="entity_id", minimum=1)
        _check_int(self.entity_type, name="entity_type", maximum=31)
        _check_int(self.owner, name="owner", maximum=2)
        _check_int(self.zone, name="zone", maximum=31)
        _check_int(self.card_id, name="card_id")
        if self.zone == 5 and self.card_id != 0:
            raise RepresentationV3Error("hidden entity must not carry card identity")
        if self.host_entity_id is not None:
            _check_int(self.host_entity_id, name="host_entity_id", minimum=1)
        _check_float_tuple(self.scalar_features, name="scalar_features")
        if type(self.categorical_features) is not tuple or any(
            type(value) is not int or value < 0 for value in self.categorical_features
        ):
            raise RepresentationV3Error("categorical_features must be nonnegative ints")
        if type(self.binary_flags) is not tuple or any(value not in (0, 1) for value in self.binary_flags):
            raise RepresentationV3Error("binary_flags must contain only 0/1")
        if self.public_locator is not None and type(self.public_locator) is not PublicEntityLocatorV3:
            raise RepresentationV3Error("public_locator must be PublicEntityLocatorV3 or null")


@dataclass(frozen=True, slots=True)
class ActionCandidateV3:
    """A legal action candidate with explicit source/target relation keys."""

    stable_action_id: str
    action_type: int
    source_entity_id: int | None
    target_entity_id: int | None
    categorical_args: tuple[int, ...]
    numeric_args: tuple[float, ...]
    selection_step: int
    source_locator: PublicEntityLocatorV3 | None = None
    target_locator: PublicEntityLocatorV3 | None = None
    selected_locators: tuple[PublicEntityLocatorV3, ...] = ()
    selection_order_sensitive: bool = False
    selectable_locator: PublicEntityLocatorV3 | None = None

    def __post_init__(self) -> None:
        if type(self.stable_action_id) is not str or not self.stable_action_id:
            raise RepresentationV3Error("stable_action_id must be a nonempty string")
        _check_int(self.action_type, name="action_type", maximum=63)
        for name, value in (("source_entity_id", self.source_entity_id), ("target_entity_id", self.target_entity_id)):
            if value is not None:
                _check_int(value, name=name, minimum=1)
        if type(self.categorical_args) is not tuple or any(type(value) is not int or value < 0 for value in self.categorical_args):
            raise RepresentationV3Error("categorical_args must be nonnegative ints")
        _check_float_tuple(self.numeric_args, name="numeric_args")
        _check_int(self.selection_step, name="selection_step", maximum=512)
        for name in ("source_locator", "target_locator", "selectable_locator"):
            locator = getattr(self, name)
            if locator is not None and type(locator) is not PublicEntityLocatorV3:
                raise RepresentationV3Error(f"{name} must be PublicEntityLocatorV3 or null")
        if type(self.selected_locators) is not tuple or any(
            type(locator) is not PublicEntityLocatorV3 for locator in self.selected_locators
        ):
            raise RepresentationV3Error("selected_locators must be a tuple of PublicEntityLocatorV3")
        if len(self.selected_locators) > 512:
            raise RepresentationV3Error("selected_locators exceeds the v3 maximum of 512")
        if type(self.selection_order_sensitive) is not bool:
            raise RepresentationV3Error("selection_order_sensitive must be a bool")
        if len({locator.canonical_key() for locator in self.selected_locators}) != len(self.selected_locators):
            raise RepresentationV3Error("selected_locators may not contain duplicate public entities")
        if self.selection_order_sensitive:
            return
        canonical = tuple(sorted(self.selected_locators, key=PublicEntityLocatorV3.canonical_key))
        if canonical != self.selected_locators:
            object.__setattr__(self, "selected_locators", canonical)
        # A set has no meaningful selection position.  Do not leak arbitrary
        # caller ordering through a step embedding.
        if self.selection_step != 0:
            object.__setattr__(self, "selection_step", 0)

    @property
    def excludes_selected_duplicate(self) -> bool:
        """Whether the policy-selected public entity is already selected."""
        locator = self.selectable_locator if self.selectable_locator is not None else self.source_locator
        return locator is not None and locator in self.selected_locators


@dataclass(frozen=True, slots=True)
class SemanticPrefixTokenV3:
    """Typed semantic prefix row; deliberately carries no stable/local ID."""
    action_type: int
    categorical_args: tuple[int, ...]
    numeric_args: tuple[float, ...]
    source_locator: PublicEntityLocatorV3 | None = None
    target_locator: PublicEntityLocatorV3 | None = None

    def __post_init__(self) -> None:
        _check_int(self.action_type, name="action_type", maximum=63)
        if type(self.categorical_args) is not tuple or any(type(value) is not int or value < 0 for value in self.categorical_args):
            raise RepresentationV3Error("prefix categorical_args must be nonnegative ints")
        _check_float_tuple(self.numeric_args, name="prefix numeric_args")
        for locator in (self.source_locator, self.target_locator):
            if locator is not None and type(locator) is not PublicEntityLocatorV3:
                raise RepresentationV3Error("prefix locator is invalid")


@dataclass(frozen=True, slots=True)
class RelationalStateV3:
    """Immutable relational state consumed by the v3 neural model."""

    state_scalars: tuple[float, ...]
    entities: tuple[EntityTokenV3, ...]
    candidates: tuple[ActionCandidateV3, ...]
    semantic_prefix: tuple[SemanticPrefixTokenV3, ...] = ()
    prefix_order_sensitive: bool = False

    def __post_init__(self) -> None:
        _check_float_tuple(self.state_scalars, name="state_scalars")
        if type(self.entities) is not tuple or any(type(entity) is not EntityTokenV3 for entity in self.entities):
            raise RepresentationV3Error("entities must be a tuple of EntityTokenV3")
        if type(self.candidates) is not tuple or any(type(candidate) is not ActionCandidateV3 for candidate in self.candidates):
            raise RepresentationV3Error("candidates must be a tuple of ActionCandidateV3")
        if type(self.semantic_prefix) is not tuple or any(type(token) is not SemanticPrefixTokenV3 for token in self.semantic_prefix):
            raise RepresentationV3Error("semantic_prefix must be typed prefix tokens")
        if type(self.prefix_order_sensitive) is not bool:
            raise RepresentationV3Error("prefix_order_sensitive must be bool")
        ids = {entity.entity_id for entity in self.entities}
        if len(ids) != len(self.entities):
            raise RepresentationV3Error("entity_id must be unique within one state")
        for entity in self.entities:
            if entity.host_entity_id is not None and entity.host_entity_id not in ids:
                raise RepresentationV3Error("host_entity_id must point to an entity in the same state")
        locators = tuple(entity.public_locator for entity in self.entities if entity.public_locator is not None)
        if len(set(locators)) != len(locators):
            raise RepresentationV3Error("public_locator must be unique within one state")
        known_locators = frozenset(locators)
        for candidate in self.candidates:
            for reference in (candidate.source_entity_id, candidate.target_entity_id):
                if reference is not None and reference not in ids:
                    raise RepresentationV3Error("action relation key must point to an entity in the same state")
            for locator in (
                candidate.source_locator, candidate.target_locator, candidate.selectable_locator,
                *candidate.selected_locators,
            ):
                if locator is not None and locator not in known_locators:
                    raise RepresentationV3Error("action public locator must point to an entity in the same state")
        for token in self.semantic_prefix:
            for locator in (token.source_locator, token.target_locator):
                if locator is not None and locator not in known_locators:
                    raise RepresentationV3Error("prefix public locator must point to an entity in the same state")

    def canonical_entity_order(self) -> tuple[EntityTokenV3, ...]:
        """Return a deterministic order without changing exchangeable semantics."""
        return tuple(sorted(self.entities, key=lambda item: (item.zone, item.owner, item.entity_type, item.card_id, item.entity_id)))


def stable_action_id_v3(action_payload: object) -> str:
    """Content-address one public action payload without using local indices."""
    try:
        encoded = json.dumps(action_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RepresentationV3Error("action payload must be canonical JSON") from exc
    return hashlib.sha256(b"mage_ptcg:stable-action:v3\0" + encoded).hexdigest()


def _pokemon_snapshot_key(pokemon: object) -> tuple[object, ...]:
    """Return all public Pokemon fields retained by v1 (never a serial)."""
    return (
        getattr(pokemon, "owner_role", None), getattr(pokemon, "zone", None),
        getattr(pokemon, "card_id", None), getattr(pokemon, "hp", None),
        getattr(pokemon, "max_hp", None), getattr(pokemon, "appear_this_turn", None),
        tuple(getattr(pokemon, "energy_type_counts", ())), tuple(getattr(pokemon, "energy_cards", ())),
        tuple(getattr(pokemon, "tools", ())), tuple(getattr(pokemon, "pre_evolution", ())),
    )


def _entity_pokemon_snapshot_key(entity: EntityTokenV3, *, entities: tuple[EntityTokenV3, ...]) -> tuple[object, ...]:
    """Recreate the v1 public Pokemon snapshot from its relational tokens."""
    assert entity.public_locator is not None
    hosted = [item for item in entities if item.host_entity_id == entity.entity_id]
    return (
        entity.public_locator.owner_role, entity.public_locator.semantic_zone, entity.card_id,
        int(entity.scalar_features[0]), int(entity.scalar_features[1]), int(entity.scalar_features[2]),
        tuple(entity.categorical_features),
        tuple(sorted(item.card_id for item in hosted if item.entity_type == 3)),
        tuple(sorted(item.card_id for item in hosted if item.entity_type == 2)),
        tuple(sorted(item.card_id for item in hosted if item.entity_type == 4)),
    )


def _semantic_zone_for_entity(entity: EntityTokenV3, *, entity_by_id: Mapping[int, EntityTokenV3]) -> str:
    if entity.entity_type == 1:
        return "active" if entity.zone == 1 else "bench"
    if entity.entity_type in {2, 3}:
        host = entity_by_id[entity.host_entity_id] if entity.host_entity_id is not None else None
        host_zone = "active" if host is not None and host.zone == 1 else "bench"
        return f"{host_zone}-{'tool' if entity.entity_type == 2 else 'energy'}"
    if entity.entity_type == 4:
        return "pre-evolution"
    if entity.zone == 6:
        return "stadium"
    if entity.zone == 7:
        return "context-card"
    if entity.zone == 8:
        return "effect"
    return {9: "hand", 10: "deck-reveal", 11: "looking", 12: "discard"}.get(entity.zone, "other-public")


def _with_public_locators(entities: Sequence[EntityTokenV3]) -> tuple[EntityTokenV3, ...]:
    """Assign deterministic public alignment ordinals after all hosts exist."""
    by_id = {entity.entity_id: entity for entity in entities}
    grouped: dict[tuple[int, str], list[EntityTokenV3]] = {}
    for entity in entities:
        grouped.setdefault((entity.owner, _semantic_zone_for_entity(entity, entity_by_id=by_id)), []).append(entity)
    locators: dict[int, PublicEntityLocatorV3] = {}
    for (owner, semantic_zone), values in grouped.items():
        for ordinal, entity in enumerate(sorted(values, key=lambda item: (
            item.entity_type, item.card_id, item.scalar_features, item.categorical_features,
            item.binary_flags, item.entity_id,
        ))):
            locators[entity.entity_id] = PublicEntityLocatorV3(owner, semantic_zone, ordinal)
    return tuple(EntityTokenV3(
        entity_id=entity.entity_id, entity_type=entity.entity_type, owner=entity.owner,
        zone=entity.zone, card_id=entity.card_id, host_entity_id=entity.host_entity_id,
        scalar_features=entity.scalar_features, categorical_features=entity.categorical_features,
        binary_flags=entity.binary_flags, public_locator=locators[entity.entity_id],
    ) for entity in entities)


def _endpoint_locator(endpoint: object, *, entities: tuple[EntityTokenV3, ...]) -> PublicEntityLocatorV3 | None:
    """Reconstruct a locator only when v1's public projection identifies one entity.

    v1 intentionally removed serials.  A card-id first match would manufacture
    a physical identity for duplicate public cards, so visible ambiguity is a
    hard error.  Hidden/unresolved endpoints remain unbound.
    """
    if endpoint is None:
        return None
    pokemon = getattr(endpoint, "pokemon", None)
    if pokemon is not None:
        matches = [
            entity for entity in entities
            if entity.entity_type == 1
            and entity.public_locator is not None
            and entity.public_locator.owner_role == getattr(pokemon, "owner_role", getattr(endpoint, "owner_role", 0))
            and entity.public_locator.semantic_zone == getattr(pokemon, "zone", "")
            and _entity_pokemon_snapshot_key(entity, entities=entities) == _pokemon_snapshot_key(pokemon)
        ]
        if len(matches) > 1:
            raise RepresentationV3Error("ambiguous_public_locator")
        return None if not matches else matches[0].public_locator
    visibility = getattr(endpoint, "visibility", "")
    card_id = getattr(endpoint, "card_id", 0)
    zone = getattr(endpoint, "semantic_zone", "")
    owner = getattr(endpoint, "owner_role", 0)
    if visibility in {"actor-visible", "public-visible"} and card_id:
        matches = [
            entity for entity in entities
            if entity.card_id == card_id and entity.owner == owner
            and entity.public_locator is not None and entity.public_locator.semantic_zone == zone
        ]
        if len(matches) > 1:
            raise RepresentationV3Error("ambiguous_public_locator")
        if matches:
            return matches[0].public_locator
    return None


def _endpoint_entity_id(endpoint: object, *, entities: tuple[EntityTokenV3, ...]) -> int | None:
    """Legacy relation-key projection; semantic candidate encoding uses locators."""
    locator = _endpoint_locator(endpoint, entities=entities)
    if locator is None:
        return None
    return next(entity.entity_id for entity in entities if entity.public_locator == locator)


def _candidate_from_semantic_action_v1(
    action: object,
    *,
    entities: tuple[EntityTokenV3, ...],
    selected_locators: tuple[PublicEntityLocatorV3, ...] = (),
    selection_order_sensitive: bool = False,
) -> ActionCandidateV3:
    """Convert one v1 semantic row without using its provenance ID as input."""
    source_locator = _endpoint_locator(action.source, entities=entities)
    target_locator = _endpoint_locator(action.target, entities=entities)
    categorical = tuple(int(value) for value in (
        action.selection_type, action.selection_context,
        action.attack_id or 0, action.special_condition or 0, action.skill_card_id or 0,
    ))
    numeric = tuple(float(value or 0) for value in (action.number, action.energy_count))
    return ActionCandidateV3(
        stable_action_id=stable_action_id_v3(action.to_dict()), action_type=int(action.option_type),
        source_entity_id=_endpoint_entity_id(action.source, entities=entities),
        target_entity_id=_endpoint_entity_id(action.target, entities=entities),
        categorical_args=categorical, numeric_args=numeric,
        selection_step=len(selected_locators) if selection_order_sensitive else 0,
        source_locator=source_locator, target_locator=target_locator,
        selected_locators=selected_locators, selection_order_sensitive=selection_order_sensitive,
        selectable_locator=_selectable_locator_from_semantic_action_v1(
            action, entities=entities, required=False,
        ),
    )


_SELECTABLE_ENDPOINT_FIELD_BY_OPTION_TYPE_V1 = {
    # These rows select the object named by the C1 source resolver.  In
    # particular 4/5/6 also expose target=host, but that host is context for
    # the selected attachment rather than the selected object.
    3: "source", 4: "source", 5: "source", 6: "source", 7: "source",
    10: "source", 11: "source", 12: "source", 13: "source", 15: "source",
    # ATTACH/EVOLVE choose an in-play Pokemon; their source is the card to be
    # attached/evolved and must not become a selected-mask entity.
    8: "target", 9: "target",
}


def _selectable_locator_from_semantic_action_v1(
    action: object, *, entities: tuple[EntityTokenV3, ...], required: bool,
) -> PublicEntityLocatorV3 | None:
    """Bind by the closed C1 option policy, never endpoint probing order."""
    option_type = getattr(action, "option_type", None)
    field = _SELECTABLE_ENDPOINT_FIELD_BY_OPTION_TYPE_V1.get(option_type)
    if field is None:
        if required:
            raise RepresentationV3Error("unclassified selectable endpoint")
        return None
    locator = _endpoint_locator(getattr(action, field, None), entities=entities)
    if locator is None:
        if required:
            raise RepresentationV3Error("selectable endpoint is not uniquely public")
        return None
    return locator


def _selected_locator_from_semantic_action_v1(
    action: object, *, entities: tuple[EntityTokenV3, ...],
) -> PublicEntityLocatorV3:
    locator = _selectable_locator_from_semantic_action_v1(action, entities=entities, required=True)
    assert locator is not None
    return locator


def representation_v3_from_model_input_v1(model_input: object, *, include_candidates: bool = True) -> RelationalStateV3:
    """Project a validated ``SpecialistModelInputV1`` into the typed v3 graph.

    This adapter is deliberately kept at the compatibility boundary.  It does
    not recover dropped serials and never emits a card identity for a hidden
    source.  Card-bag multiplicity is represented by exchangeable public card
    tokens, while Pokemon attachments/evolution cards retain host edges.
    """
    if model_input is None or not hasattr(model_input, "state_scalars"):
        raise RepresentationV3Error("model_input must expose the v1 model-input fields")

    entities: list[EntityTokenV3] = []
    next_id = 1

    def add(entity: EntityTokenV3) -> int:
        entities.append(entity)
        return entity.entity_id

    # Public Pokemon are the anchors for all relation edges.
    for pokemon in tuple(getattr(model_input, "pokemon_entities", ())):
        zone = 1 if pokemon.zone == "active" else 2
        entity = EntityTokenV3(
            entity_id=next_id, entity_type=1, owner=pokemon.owner_role, zone=zone,
            card_id=pokemon.card_id, host_entity_id=None,
            scalar_features=(float(pokemon.hp), float(pokemon.max_hp), float(pokemon.appear_this_turn)),
            categorical_features=tuple(int(value) for value in pokemon.energy_type_counts),
            binary_flags=(),
        )
        next_id += 1
        add(entity)

        for kind, cards, entity_type in (
            ("tool", pokemon.tools, 2), ("energy", pokemon.energy_cards, 3),
            ("pre-evolution", pokemon.pre_evolution, 4),
        ):
            for card_id in cards:
                add(EntityTokenV3(
                    entity_id=next_id, entity_type=entity_type, owner=pokemon.owner_role,
                    zone=3 if kind != "pre-evolution" else 4, card_id=int(card_id),
                    host_entity_id=entity.entity_id, scalar_features=(), categorical_features=(), binary_flags=(),
                ))
                next_id += 1

    def add_card(card_id: int, *, owner: int, zone: int, entity_type: int) -> int:
        nonlocal next_id
        entity = EntityTokenV3(
            entity_id=next_id, entity_type=entity_type, owner=owner, zone=zone,
            card_id=int(card_id), host_entity_id=None,
            scalar_features=(), categorical_features=(), binary_flags=(),
        )
        next_id += 1
        add(entity)
        return entity.entity_id

    # Singleton public cards and actor-visible card bags.  The bag mask is the
    # sole source of multiplicity; padding tokens are never emitted.
    for name, card_id in sorted(dict(getattr(model_input, "single_card_ids", {})).items()):
        if int(card_id):
            zone = {"stadium": 6, "context": 7, "effect": 8}[name]
            add_card(int(card_id), owner=1, zone=zone, entity_type=5)
    for bag_name, bag in dict(getattr(model_input, "card_bags", {})).items():
        owner = 1 if bag_name in {"own_hand", "deck_reveal", "looking_visible", "self_discard"} else 2
        zone = {
            "own_hand": 9, "deck_reveal": 10, "looking_visible": 11,
            "self_discard": 12, "opponent_discard": 12,
        }.get(bag_name, 13)
        for card_id, mask in zip(getattr(bag, "tokens", ()), getattr(bag, "mask", ())):
            if int(mask):
                add_card(int(card_id), owner=owner, zone=zone, entity_type=6)

    frozen_entities = _with_public_locators(entities)

    candidates: list[ActionCandidateV3] = []
    if include_candidates:
        for action in tuple(getattr(model_input, "candidate_rows", ())):
            candidates.append(_candidate_from_semantic_action_v1(action, entities=frozen_entities))

    return RelationalStateV3(
        state_scalars=tuple(float(value) for value in tuple(model_input.state_scalars)),
        entities=frozen_entities, candidates=tuple(candidates),
    )


def representation_v3_from_step_input_v1(model_input: object, step_input: object, *, allow_unbound_selected: bool = False) -> RelationalStateV3:
    """Project one real v1 autoregressive step into v3's candidate domain.

    The model input supplies the public state graph, while ``step_input`` is
    the only authority for the reachable candidate domain and selection
    prefix.  The v1 objects remain serial-free; any locator ambiguity fails
    closed through the same endpoint resolver as the compatibility adapter.
    """
    # The whole model-input candidate set can contain legal but currently
    # unreachable ambiguous aliases.  Step projection must only bind its exact
    # reachable semantic domain, not reject it for an unrelated base candidate.
    base = representation_v3_from_model_input_v1(model_input, include_candidates=False)
    order_semantics = getattr(step_input, "order_semantics", None)
    if order_semantics not in {"unordered_set", "ordered_sequence"}:
        raise RepresentationV3Error("step_input must expose a valid v1 order_semantics")
    prefix = getattr(step_input, "semantic_prefix", None)
    allowed = getattr(step_input, "allowed_semantic_classes", None)
    if type(prefix) is not tuple or type(allowed) is not tuple:
        raise RepresentationV3Error("step_input must expose tuple prefix and allowed classes")
    ordered = order_semantics == "ordered_sequence"
    selected_rows: list[PublicEntityLocatorV3] = []
    for action in prefix:
        try:
            selected_rows.append(_selected_locator_from_semantic_action_v1(action, entities=base.entities))
        except RepresentationV3Error as exc:
            if not allow_unbound_selected or str(exc) != "unclassified selectable endpoint":
                raise
            # Some legal complete-action tokens (YES/NO/NUMBER etc.) select no
            # public entity.  They still constrain the canonical next-token
            # domain; they simply have no locator duplicate to mask.
    selected = tuple(selected_rows)
    if not ordered:
        selected = tuple(sorted(selected, key=PublicEntityLocatorV3.canonical_key))
    if len(set(selected)) != len(selected):
        raise RepresentationV3Error("selected_locators may not contain duplicate public entities")
    prefix_tokens = tuple(
        SemanticPrefixTokenV3(
            action_type=int(action.option_type),
            categorical_args=tuple(int(value) for value in (action.selection_type, action.selection_context, action.attack_id or 0, action.special_condition or 0, action.skill_card_id or 0)),
            numeric_args=tuple(float(value or 0) for value in (action.number, action.energy_count)),
            source_locator=_endpoint_locator(action.source, entities=base.entities),
            target_locator=_endpoint_locator(action.target, entities=base.entities),
        ) for action in prefix
    )
    candidates = tuple(
        _candidate_from_semantic_action_v1(
            item.semantic_row, entities=base.entities, selected_locators=selected,
            selection_order_sensitive=ordered,
        )
        for item in allowed
    )
    return RelationalStateV3(
        state_scalars=base.state_scalars, entities=base.entities, candidates=candidates,
        semantic_prefix=prefix_tokens, prefix_order_sensitive=ordered,
    )


__all__ = [
    "ActionCandidateV3",
    "EntityTokenV3",
    "PublicEntityLocatorV3",
    "REPRESENTATION_V3_SCHEMA",
    "RelationalStateV3",
    "SemanticPrefixTokenV3",
    "RepresentationV3Error",
    "representation_v3_from_model_input_v1",
    "representation_v3_from_step_input_v1",
    "stable_action_id_v3",
]
