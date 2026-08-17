"""Serial-free equivalence-class representation for duplicate public cards.

V1 deliberately collapses physical aliases of one public semantic action.
This adapter keeps that quotient: an action endpoint names a public class and
the class resolves to one or more exchangeable entity tokens.  It never
reconstructs a hand index, card serial, or local action id.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Mapping, Sequence


REPRESENTATION_V4_SCHEMA = "meta-specialist-equivalence-class-representation-v4"
PUBLIC_INTEGER_MAX_V4 = (1 << 16) - 1


class RepresentationV4Error(ValueError):
    """Raised when a v4 public-equivalence representation is invalid."""


def _check_int(value: object, *, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise RepresentationV4Error(f"{name} is outside its closed v4 range")
    return value


def _finite_tuple(values: object, *, name: str) -> tuple[float, ...]:
    if type(values) is not tuple or any(type(value) is not float or not math.isfinite(value) for value in values):
        raise RepresentationV4Error(f"{name} must be a tuple of finite floats")
    return values


def _public_integer_tuple(values: object, *, name: str) -> tuple[int, ...]:
    if type(values) is not tuple or len(values) > 512 or any(
        type(value) is not int or not 0 <= value <= PUBLIC_INTEGER_MAX_V4 for value in values
    ):
        raise RepresentationV4Error(f"{name} is outside its closed v4 range")
    return values


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RepresentationV4Error("v4 public value must be canonical JSON") from exc


@dataclass(frozen=True, slots=True)
class PublicEntityClassRefV4:
    """A public endpoint equivalence class, without any physical locator."""

    visibility: str
    owner_role: int
    semantic_zone: str
    card_id: int
    host_card_id: int = 0
    pokemon_snapshot: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.visibility != "actor-visible":
            raise RepresentationV4Error("entity class ref must be actor-visible")
        _check_int(self.owner_role, name="owner_role", minimum=1, maximum=2)
        if type(self.semantic_zone) is not str or not self.semantic_zone or len(self.semantic_zone) > 64:
            raise RepresentationV4Error("semantic_zone must be a bounded nonempty string")
        _check_int(self.card_id, name="card_id", minimum=1)
        _check_int(self.host_card_id, name="host_card_id")
        if type(self.pokemon_snapshot) is not tuple:
            raise RepresentationV4Error("pokemon_snapshot must be a tuple")
        _canonical_json(self.to_dict())

    @classmethod
    def actor_visible(
        cls, owner_role: int, semantic_zone: str, card_id: int, *, host_card_id: int = 0,
        pokemon_snapshot: tuple[object, ...] = (),
    ) -> "PublicEntityClassRefV4":
        return cls("actor-visible", owner_role, semantic_zone, card_id, host_card_id, pokemon_snapshot)

    def to_dict(self) -> dict[str, object]:
        return {
            "visibility": self.visibility, "owner_role": self.owner_role,
            "semantic_zone": self.semantic_zone, "card_id": self.card_id,
            "host_card_id": self.host_card_id, "pokemon_snapshot": list(self.pokemon_snapshot),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class EntityTokenV4:
    """State-local entity; ``entity_id`` is relation-only, never a feature."""

    entity_id: int
    entity_type: int
    owner: int
    zone: int
    card_id: int
    host_entity_id: int | None
    scalar_features: tuple[float, ...]
    categorical_features: tuple[int, ...]
    binary_flags: tuple[int, ...]
    entity_class_ref: PublicEntityClassRefV4 | None = None

    def __post_init__(self) -> None:
        _check_int(self.entity_id, name="entity_id", minimum=1)
        _check_int(self.entity_type, name="entity_type", maximum=31)
        _check_int(self.owner, name="owner", maximum=2)
        _check_int(self.zone, name="zone", maximum=31)
        _check_int(self.card_id, name="card_id")
        if self.host_entity_id is not None:
            _check_int(self.host_entity_id, name="host_entity_id", minimum=1)
        _finite_tuple(self.scalar_features, name="scalar_features")
        _public_integer_tuple(self.categorical_features, name="categorical_features")
        if type(self.binary_flags) is not tuple or any(value not in (0, 1) for value in self.binary_flags):
            raise RepresentationV4Error("binary_flags must be 0/1")
        if self.entity_class_ref is not None and type(self.entity_class_ref) is not PublicEntityClassRefV4:
            raise RepresentationV4Error("entity_class_ref must be a PublicEntityClassRefV4 or null")

    def public_feature_dict(self) -> dict[str, object]:
        """Serializable feature payload deliberately excluding local IDs."""
        return {
            "entity_type": self.entity_type, "owner": self.owner, "zone": self.zone, "card_id": self.card_id,
            "scalar_features": list(self.scalar_features), "categorical_features": list(self.categorical_features),
            "binary_flags": list(self.binary_flags),
            "entity_class_ref": None if self.entity_class_ref is None else self.entity_class_ref.to_dict(),
        }


def _normalized_counts(
    counts: object, *, ordered: bool,
) -> tuple[tuple[PublicEntityClassRefV4, int], ...]:
    if type(counts) is not tuple:
        raise RepresentationV4Error("selected_class_counts must be a tuple")
    pairs: list[tuple[PublicEntityClassRefV4, int]] = []
    for item in counts:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not PublicEntityClassRefV4:
            raise RepresentationV4Error("selected_class_counts must contain typed class/count pairs")
        _check_int(item[1], name="selected class count", minimum=1, maximum=512)
        pairs.append((item[0], item[1]))
    if len({item[0] for item in pairs}) != len(pairs):
        raise RepresentationV4Error("selected_class_counts may contain each class once")
    canonical = tuple(sorted(pairs, key=lambda item: item[0].canonical_bytes()))
    if canonical != tuple(pairs):
        return canonical
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class ActionCandidateV4:
    """A legal v1 semantic class with its remaining alias multiplicity."""

    stable_action_id: str
    action_type: int
    source_class_ref: PublicEntityClassRefV4 | None
    target_class_ref: PublicEntityClassRefV4 | None
    host_class_ref: PublicEntityClassRefV4 | None
    categorical_args: tuple[int, ...]
    numeric_args: tuple[float, ...]
    allowed_alias_count: int
    selected_class_counts: tuple[tuple[PublicEntityClassRefV4, int], ...] = ()
    selection_order_sensitive: bool = False
    selection_step: int = 0
    selectable_class_ref: PublicEntityClassRefV4 | None = None

    def __post_init__(self) -> None:
        # Production adapters derive a SHA-256 from serial-free action bytes.
        # Keep the typed state useful for hand-built test/evaluation fixtures;
        # it still never accepts a local action ID as an input feature.
        if type(self.stable_action_id) is not str or not self.stable_action_id:
            raise RepresentationV4Error("stable_action_id must be nonempty")
        _check_int(self.action_type, name="action_type", maximum=63)
        for ref in (self.source_class_ref, self.target_class_ref, self.host_class_ref, self.selectable_class_ref):
            if ref is not None and type(ref) is not PublicEntityClassRefV4:
                raise RepresentationV4Error("candidate relation must be a public class ref or null")
        _public_integer_tuple(self.categorical_args, name="candidate categorical args")
        _finite_tuple(self.numeric_args, name="candidate numeric args")
        # Zero is permitted only for a stale-domain defensive mask.  Adapters
        # from v1 always provide a positive current-domain alias count.
        _check_int(self.allowed_alias_count, name="allowed_alias_count", maximum=512)
        normalized = _normalized_counts(self.selected_class_counts, ordered=self.selection_order_sensitive)
        if normalized != self.selected_class_counts:
            object.__setattr__(self, "selected_class_counts", normalized)
        if type(self.selection_order_sensitive) is not bool:
            raise RepresentationV4Error("selection_order_sensitive must be bool")
        _check_int(self.selection_step, name="selection_step", maximum=512)
        if not self.selection_order_sensitive and self.selection_step != 0:
            object.__setattr__(self, "selection_step", 0)

    @property
    def remaining_alias_count(self) -> int:
        """The current v1 domain's remaining aliases; never subtract prefix twice."""
        return self.allowed_alias_count

    @property
    def excludes_selected_duplicate(self) -> bool:
        return self.selectable_class_ref is not None and self.remaining_alias_count == 0

    def public_feature_dict(self) -> dict[str, object]:
        return {
            "stable_action_id": self.stable_action_id, "action_type": self.action_type,
            "source_class_ref": _ref_payload(self.source_class_ref), "target_class_ref": _ref_payload(self.target_class_ref),
            "host_class_ref": _ref_payload(self.host_class_ref), "categorical_args": list(self.categorical_args),
            "numeric_args": list(self.numeric_args), "allowed_alias_count": self.allowed_alias_count,
            "selected_class_counts": [[ref.to_dict(), count] for ref, count in self.selected_class_counts],
            "selection_order_sensitive": self.selection_order_sensitive, "selection_step": self.selection_step,
            "selectable_class_ref": _ref_payload(self.selectable_class_ref),
        }


def _ref_payload(ref: PublicEntityClassRefV4 | None) -> dict[str, object] | None:
    return None if ref is None else ref.to_dict()


@dataclass(frozen=True, slots=True)
class SemanticPrefixTokenV4:
    """Serial-free semantic prefix token with class relations and multiplicity."""

    action_type: int
    categorical_args: tuple[int, ...]
    numeric_args: tuple[float, ...]
    source_class_ref: PublicEntityClassRefV4 | None
    target_class_ref: PublicEntityClassRefV4 | None
    host_class_ref: PublicEntityClassRefV4 | None
    selectable_class_ref: PublicEntityClassRefV4 | None

    def __post_init__(self) -> None:
        _check_int(self.action_type, name="prefix action_type", maximum=63)
        _public_integer_tuple(self.categorical_args, name="prefix categorical args")
        _finite_tuple(self.numeric_args, name="prefix numeric args")
        for ref in (self.source_class_ref, self.target_class_ref, self.host_class_ref, self.selectable_class_ref):
            if ref is not None and type(ref) is not PublicEntityClassRefV4:
                raise RepresentationV4Error("prefix relation must be a public class ref or null")

    def canonical_bytes(self) -> bytes:
        return _canonical_json({
            "action_type": self.action_type, "categorical_args": list(self.categorical_args),
            "numeric_args": list(self.numeric_args), "source_class_ref": _ref_payload(self.source_class_ref),
            "target_class_ref": _ref_payload(self.target_class_ref), "host_class_ref": _ref_payload(self.host_class_ref),
            "selectable_class_ref": _ref_payload(self.selectable_class_ref),
        })


@dataclass(frozen=True, slots=True)
class RelationalStateV4:
    state_scalars: tuple[float, ...]
    entities: tuple[EntityTokenV4, ...]
    candidates: tuple[ActionCandidateV4, ...]
    semantic_prefix: tuple[SemanticPrefixTokenV4, ...] = ()
    prefix_order_sensitive: bool = False

    def __post_init__(self) -> None:
        _finite_tuple(self.state_scalars, name="state_scalars")
        if type(self.entities) is not tuple or any(type(item) is not EntityTokenV4 for item in self.entities):
            raise RepresentationV4Error("entities must be EntityTokenV4 values")
        if type(self.candidates) is not tuple or any(type(item) is not ActionCandidateV4 for item in self.candidates):
            raise RepresentationV4Error("candidates must be ActionCandidateV4 values")
        if type(self.semantic_prefix) is not tuple or any(type(item) is not SemanticPrefixTokenV4 for item in self.semantic_prefix):
            raise RepresentationV4Error("semantic_prefix must be SemanticPrefixTokenV4 values")
        if type(self.prefix_order_sensitive) is not bool:
            raise RepresentationV4Error("prefix_order_sensitive must be bool")
        ids = {entity.entity_id for entity in self.entities}
        if len(ids) != len(self.entities):
            raise RepresentationV4Error("entity_id must be unique inside a state")
        if any(entity.host_entity_id is not None and entity.host_entity_id not in ids for entity in self.entities):
            raise RepresentationV4Error("host_entity_id must be state-local")
        known = {entity.entity_class_ref for entity in self.entities if entity.entity_class_ref is not None}
        for candidate in self.candidates:
            for ref in (candidate.source_class_ref, candidate.target_class_ref, candidate.host_class_ref, candidate.selectable_class_ref):
                if ref is not None and ref not in known:
                    raise RepresentationV4Error("candidate class ref has no state members")
            if any(ref not in known for ref, _count in candidate.selected_class_counts):
                raise RepresentationV4Error("selected class ref has no state members")
        for token in self.semantic_prefix:
            for ref in (token.source_class_ref, token.target_class_ref, token.host_class_ref, token.selectable_class_ref):
                if ref is not None and ref not in known:
                    raise RepresentationV4Error("prefix class ref has no state members")
        if not self.prefix_order_sensitive:
            canonical = tuple(sorted(self.semantic_prefix, key=SemanticPrefixTokenV4.canonical_bytes))
            if canonical != self.semantic_prefix:
                object.__setattr__(self, "semantic_prefix", canonical)

    def member_count(self, ref: PublicEntityClassRefV4) -> int:
        if type(ref) is not PublicEntityClassRefV4:
            raise RepresentationV4Error("ref must be PublicEntityClassRefV4")
        return sum(entity.entity_class_ref == ref for entity in self.entities)

    def canonical_entity_order(self) -> tuple[EntityTokenV4, ...]:
        # entity_id is excluded: duplicate aliases are truly exchangeable.
        return tuple(sorted(self.entities, key=lambda entity: _canonical_json(entity.public_feature_dict())))

    def public_feature_dict(self) -> dict[str, object]:
        return {
            "schema": REPRESENTATION_V4_SCHEMA, "state_scalars": list(self.state_scalars),
            "entities": [item.public_feature_dict() for item in self.canonical_entity_order()],
            "candidates": [item.public_feature_dict() for item in self.candidates],
            "semantic_prefix": [json.loads(item.canonical_bytes()) for item in self.semantic_prefix],
            "prefix_order_sensitive": self.prefix_order_sensitive,
        }


def stable_action_id_v4(action_payload: object) -> str:
    return hashlib.sha256(b"mage_ptcg:stable-action:v4\0" + _canonical_json(action_payload)).hexdigest()


def _pokemon_snapshot_key(pokemon: object) -> tuple[object, ...]:
    return (
        getattr(pokemon, "owner_role", None), getattr(pokemon, "zone", None), getattr(pokemon, "card_id", None),
        getattr(pokemon, "hp", None), getattr(pokemon, "max_hp", None), getattr(pokemon, "appear_this_turn", None),
        tuple(getattr(pokemon, "energy_type_counts", ())), tuple(getattr(pokemon, "energy_cards", ())),
        tuple(getattr(pokemon, "tools", ())), tuple(getattr(pokemon, "pre_evolution", ())),
    )


def _semantic_zone_for_entity(entity: EntityTokenV4, *, by_id: Mapping[int, EntityTokenV4]) -> str:
    if entity.entity_type == 1:
        return "active" if entity.zone == 1 else "bench"
    if entity.entity_type in {2, 3}:
        host = by_id.get(entity.host_entity_id)
        host_zone = "active" if host is not None and host.zone == 1 else "bench"
        return f"{host_zone}-{'tool' if entity.entity_type == 2 else 'energy'}"
    if entity.entity_type == 4:
        return "pre-evolution"
    return {6: "stadium", 7: "context-card", 8: "effect", 9: "hand", 10: "deck-reveal", 11: "looking", 12: "discard"}.get(entity.zone, "other-public")


def _entity_pokemon_snapshot_key(entity: EntityTokenV4, *, entities: Sequence[EntityTokenV4]) -> tuple[object, ...]:
    hosted = [item for item in entities if item.host_entity_id == entity.entity_id]
    return (
        entity.owner, "active" if entity.zone == 1 else "bench", entity.card_id,
        int(entity.scalar_features[0]), int(entity.scalar_features[1]), int(entity.scalar_features[2]),
        tuple(entity.categorical_features), tuple(sorted(item.card_id for item in hosted if item.entity_type == 3)),
        tuple(sorted(item.card_id for item in hosted if item.entity_type == 2)),
        tuple(sorted(item.card_id for item in hosted if item.entity_type == 4)),
    )


def _with_class_refs(entities: Sequence[EntityTokenV4]) -> tuple[EntityTokenV4, ...]:
    frozen = tuple(entities)
    by_id = {item.entity_id: item for item in frozen}
    result: list[EntityTokenV4] = []
    for entity in frozen:
        zone = _semantic_zone_for_entity(entity, by_id=by_id)
        if entity.entity_type == 1:
            ref = PublicEntityClassRefV4.actor_visible(entity.owner, zone, entity.card_id, pokemon_snapshot=_entity_pokemon_snapshot_key(entity, entities=frozen))
        elif entity.entity_type in {2, 3} and entity.host_entity_id is not None:
            host = by_id[entity.host_entity_id]
            ref = PublicEntityClassRefV4.actor_visible(entity.owner, zone, entity.card_id, host_card_id=host.card_id, pokemon_snapshot=_entity_pokemon_snapshot_key(host, entities=frozen))
        else:
            ref = PublicEntityClassRefV4.actor_visible(entity.owner, zone, entity.card_id)
        result.append(replace(entity, entity_class_ref=ref))
    return tuple(result)


def _endpoint_class_ref(
    endpoint: object, *, entities: tuple[EntityTokenV4, ...],
    attachment_hosts: Sequence[object] = (), required: bool = False,
) -> PublicEntityClassRefV4 | None:
    if endpoint is None:
        return None
    visibility = getattr(endpoint, "visibility", "")
    if visibility not in {"actor-visible", "public-visible"}:
        return None
    owner = getattr(endpoint, "owner_role", 0)
    zone = getattr(endpoint, "semantic_zone", "")
    card_id = getattr(endpoint, "card_id", 0)
    host_card_id = getattr(endpoint, "host_card_id", 0)
    if type(owner) is not int or type(zone) is not str or type(card_id) is not int or card_id <= 0:
        raise RepresentationV4Error("visible endpoint has invalid public fields")
    pokemon = getattr(endpoint, "pokemon", None)
    class_owner = owner
    if zone in {"stadium", "context-card", "effect"}:
        # V1 exposes these as global singletons.  Its endpoint owner may be
        # either player, while the serial-free state has one shared class.
        class_owner = 1
    class_host_card_id = host_card_id
    if pokemon is not None and zone in {"active", "bench"}:
        # Endpoint adapters may repeat the Pokémon's own card ID as a host ID;
        # state Pokémon classes are direct public entities, never attachments.
        class_host_card_id = 0
    snapshot = _pokemon_snapshot_key(pokemon) if pokemon is not None else ()
    if pokemon is None and type(host_card_id) is int and host_card_id > 0:
        for host in attachment_hosts:
            if (
                getattr(host, "visibility", "") in {"actor-visible", "public-visible"}
                and getattr(host, "card_id", None) == host_card_id
                and getattr(host, "pokemon", None) is not None
            ):
                snapshot = _pokemon_snapshot_key(host.pokemon)
                break
    ref = PublicEntityClassRefV4.actor_visible(
        class_owner, zone, card_id, host_card_id=class_host_card_id, pokemon_snapshot=snapshot,
    )
    if not any(entity.entity_class_ref == ref for entity in entities):
        raise RepresentationV4Error("visible endpoint has no matching public state class")
    return ref


_SELECTABLE_ENDPOINT_FIELD_BY_OPTION_TYPE_V1 = {
    3: "source", 4: "source", 5: "source", 6: "source", 7: "source", 10: "source", 11: "source",
    12: "source", 13: "source", 15: "source", 8: "target", 9: "target",
}


def _selectable_class_ref(action: object, *, entities: tuple[EntityTokenV4, ...], required: bool) -> PublicEntityClassRefV4 | None:
    field = _SELECTABLE_ENDPOINT_FIELD_BY_OPTION_TYPE_V1.get(getattr(action, "option_type", None))
    if field is None:
        if required:
            raise RepresentationV4Error("unclassified selectable endpoint")
        return None
    ref = _endpoint_class_ref(
        getattr(action, field, None), entities=entities,
        attachment_hosts=(getattr(action, "host", None), getattr(action, "target", None)),
    )
    if ref is None and required:
        raise RepresentationV4Error("selectable endpoint is not actor-visible")
    return ref


def _prefix_token(action: object, *, entities: tuple[EntityTokenV4, ...], required_selectable: bool) -> SemanticPrefixTokenV4:
    attachment_hosts = (getattr(action, "host", None), getattr(action, "target", None))
    return SemanticPrefixTokenV4(
        action_type=int(action.option_type),
        categorical_args=tuple(int(value) for value in (action.selection_type, action.selection_context, action.attack_id or 0, action.special_condition or 0, action.skill_card_id or 0)),
        numeric_args=tuple(float(value or 0) for value in (action.number, action.energy_count)),
        source_class_ref=_endpoint_class_ref(action.source, entities=entities, attachment_hosts=attachment_hosts),
        target_class_ref=_endpoint_class_ref(action.target, entities=entities, attachment_hosts=attachment_hosts),
        host_class_ref=_endpoint_class_ref(action.host, entities=entities, attachment_hosts=attachment_hosts),
        selectable_class_ref=_selectable_class_ref(action, entities=entities, required=required_selectable),
    )


def _candidate(action: object, *, entities: tuple[EntityTokenV4, ...], allowed_alias_count: int, selected_counts: tuple[tuple[PublicEntityClassRefV4, int], ...], ordered: bool, selection_step: int) -> ActionCandidateV4:
    attachment_hosts = (getattr(action, "host", None), getattr(action, "target", None))
    return ActionCandidateV4(
        stable_action_id=stable_action_id_v4(action.to_dict()), action_type=int(action.option_type),
        source_class_ref=_endpoint_class_ref(action.source, entities=entities, attachment_hosts=attachment_hosts),
        target_class_ref=_endpoint_class_ref(action.target, entities=entities, attachment_hosts=attachment_hosts),
        host_class_ref=_endpoint_class_ref(action.host, entities=entities, attachment_hosts=attachment_hosts),
        categorical_args=tuple(int(value) for value in (action.selection_type, action.selection_context, action.attack_id or 0, action.special_condition or 0, action.skill_card_id or 0)),
        numeric_args=tuple(float(value or 0) for value in (action.number, action.energy_count)),
        allowed_alias_count=allowed_alias_count, selected_class_counts=selected_counts,
        selection_order_sensitive=ordered, selection_step=selection_step,
        selectable_class_ref=_selectable_class_ref(action, entities=entities, required=False),
    )


def _base_entities_from_model_input_v1(model_input: object) -> tuple[EntityTokenV4, ...]:
    if model_input is None or not hasattr(model_input, "state_scalars"):
        raise RepresentationV4Error("model_input must expose v1 model input fields")
    entities: list[EntityTokenV4] = []
    next_id = 1
    for pokemon in tuple(getattr(model_input, "pokemon_entities", ())):
        entity = EntityTokenV4(next_id, 1, pokemon.owner_role, 1 if pokemon.zone == "active" else 2, pokemon.card_id, None,
                               (float(pokemon.hp), float(pokemon.max_hp), float(pokemon.appear_this_turn)), tuple(int(value) for value in pokemon.energy_type_counts), (), None)
        entities.append(entity)
        next_id += 1
        for cards, entity_type, zone in ((pokemon.tools, 2, 3), (pokemon.energy_cards, 3, 3), (pokemon.pre_evolution, 4, 4)):
            for card_id in cards:
                entities.append(EntityTokenV4(next_id, entity_type, pokemon.owner_role, zone, int(card_id), entity.entity_id, (), (), (), None))
                next_id += 1
    def add_card(card_id: int, owner: int, zone: int, entity_type: int) -> None:
        nonlocal next_id
        entities.append(EntityTokenV4(next_id, entity_type, owner, zone, int(card_id), None, (), (), (), None))
        next_id += 1
    for name, card_id in sorted(dict(getattr(model_input, "single_card_ids", {})).items()):
        if int(card_id):
            add_card(int(card_id), 1, {"stadium": 6, "context": 7, "effect": 8}[name], 5)
    for bag_name, bag in dict(getattr(model_input, "card_bags", {})).items():
        owner = 1 if bag_name in {"own_hand", "deck_reveal", "looking_visible", "self_discard"} else 2
        zone = {"own_hand": 9, "deck_reveal": 10, "looking_visible": 11, "self_discard": 12, "opponent_discard": 12}.get(bag_name, 13)
        for card_id, mask in zip(getattr(bag, "tokens", ()), getattr(bag, "mask", ())):
            if int(mask):
                add_card(int(card_id), owner, zone, 6)
    return _with_class_refs(entities)


def representation_v4_from_model_input_v1(
    model_input: object, *, include_candidates: bool = True,
    allowed_alias_counts: Mapping[str, int] | None = None,
) -> RelationalStateV4:
    """Project a non-autoregressive v1 state only with caller-supplied counts.

    V1's model input alone has physical candidate rows, not the current
    semantic-class domain.  Assigning ``1`` here would manufacture a false
    multiplicity; callers that need candidates must explicitly bind the
    serial-free stable action IDs to their authoritative alias counts.
    """
    entities = _base_entities_from_model_input_v1(model_input)
    if include_candidates and allowed_alias_counts is None:
        raise RepresentationV4Error("allowed alias counts are required when projecting candidates")
    candidates_list: list[ActionCandidateV4] = []
    if include_candidates:
        assert allowed_alias_counts is not None
        for action in tuple(getattr(model_input, "candidate_rows", ())):
            action_id = stable_action_id_v4(action.to_dict())
            count = allowed_alias_counts.get(action_id)
            if type(count) is not int or count < 1:
                raise RepresentationV4Error("allowed alias counts must cover every projected semantic action")
            candidates_list.append(_candidate(
                action, entities=entities, allowed_alias_count=count, selected_counts=(), ordered=False, selection_step=0,
            ))
    return RelationalStateV4(tuple(float(value) for value in tuple(model_input.state_scalars)), entities, tuple(candidates_list))


def representation_v4_from_step_input_v1(model_input: object, step_input: object, *, allow_unbound_selected: bool = False) -> RelationalStateV4:
    """Project an exact v1 reachable step without recovering physical aliases."""
    base = representation_v4_from_model_input_v1(model_input, include_candidates=False)
    order = getattr(step_input, "order_semantics", None)
    if order not in {"unordered_set", "ordered_sequence"}:
        raise RepresentationV4Error("step_input must expose a valid v1 order_semantics")
    prefix, allowed = getattr(step_input, "semantic_prefix", None), getattr(step_input, "allowed_semantic_classes", None)
    if type(prefix) is not tuple or type(allowed) is not tuple:
        raise RepresentationV4Error("step_input must expose tuple prefix and allowed classes")
    ordered = order == "ordered_sequence"
    prefix_tokens: list[SemanticPrefixTokenV4] = []
    selected: list[PublicEntityClassRefV4] = []
    for action in prefix:
        token = _prefix_token(action, entities=base.entities, required_selectable=not allow_unbound_selected)
        prefix_tokens.append(token)
        if token.selectable_class_ref is not None:
            selected.append(token.selectable_class_ref)
    counts = Counter(selected)
    selected_counts = tuple(sorted(counts.items(), key=lambda item: item[0].canonical_bytes()))
    candidates = tuple(
        _candidate(item.semantic_row, entities=base.entities, allowed_alias_count=item.allowed_alias_count,
                   selected_counts=selected_counts, ordered=ordered, selection_step=len(prefix))
        for item in allowed
    )
    return RelationalStateV4(base.state_scalars, base.entities, candidates, tuple(prefix_tokens), ordered)


__all__ = [
    "ActionCandidateV4", "EntityTokenV4", "PUBLIC_INTEGER_MAX_V4", "PublicEntityClassRefV4", "REPRESENTATION_V4_SCHEMA",
    "RelationalStateV4", "RepresentationV4Error", "SemanticPrefixTokenV4", "representation_v4_from_model_input_v1",
    "representation_v4_from_step_input_v1", "stable_action_id_v4",
]
