"""Closed, serial-free features for actor-visible specialist decisions.

This module deliberately has no model dependency.  It translates the typed C1v2
boundary into immutable JSON-ready values and supplies the one semantic-class
legality primitive shared by training and runtime callers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import struct
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorInformationViewV2,
    ActorVisibleBindingEndpointV1,
    ActorVisibleDecisionStateV2,
    ActorVisibleLegalActionV2,
    BoundCardRefV1,
    CardRefV2,
    OPTION_RESOLVER_TABLE_V1,
    PokemonStateV2,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import (
    CABT_AGENT_JSON_SELECTION_CONTEXTS_V1,
    is_ordered_selection,
)


ACTOR_VISIBLE_FEATURE_DOMAIN_V1 = "actor-visible-action-v1"
CARD_VOCABULARY_SCHEMA_V1 = "specialist-card-vocabulary-v1"
MODEL_INPUT_SCHEMA_V1 = "specialist-model-input-v1"
STEP_INPUT_SCHEMA_V1 = "specialist-step-input-v1"
SEMANTIC_ACTION_SCHEMA_V1 = "specialist-semantic-action-v1"
_FEATURE_SCHEMA_PREFIX = b"mage_ptcg:specialist-feature-schema:v1\0"
_MODEL_INPUT_PREFIX = b"mage_ptcg:specialist-model-input:v1\0"
_CARD_VOCABULARY_SCHEMA_PREFIX = b"mage_ptcg:specialist-card-vocabulary-schema:v1\0"
_MAX_CARDS = 60
_MAX_CANDIDATES = 512
_MAX_ENUMERATED_SEMANTIC_ACTIONS = 65_536
_OPTION_OPERATIONS_V1 = MappingProxyType({
    0: "NUMBER", 1: "YES", 2: "NO", 3: "CARD", 4: "TOOL_CARD",
    5: "ENERGY_CARD", 6: "ENERGY", 7: "PLAY", 8: "ATTACH", 9: "EVOLVE",
    10: "ABILITY", 11: "DISCARD", 12: "RETREAT", 13: "ATTACK", 14: "END",
    15: "SKILL", 16: "SPECIAL_CONDITION",
})
_OPTION_TYPES_BY_SELECTION_TYPE_V1 = MappingProxyType({
    0: frozenset({7, 8, 9, 10, 11, 12, 13, 14}),
    1: frozenset({3}), 2: frozenset({4, 5}), 3: frozenset({3, 4, 5}),
    4: frozenset({6}), 5: frozenset({15}), 6: frozenset({13}),
    7: frozenset({9}), 8: frozenset({0}), 9: frozenset({1, 2}),
    10: frozenset({16}),
})
_PARAMETER_FIELD_BY_OPTION_TYPE_V1 = MappingProxyType({
    0: "number", 6: "energy_count", 13: "attack_id",
    15: "skill_card_id", 16: "special_condition",
})
_FLAG_STATE_SCALAR_INDICES_V1 = (
    11, 12, 13, 14, *range(23, 35), 36, 37, 38,
)

STATE_SCALAR_NAMES_V1 = (
    "first_player_role", "step", "turn", "turn_action_count", "selection_type",
    "selection_context", "min_count", "max_count", "option_count",
    "remain_damage_counter", "remain_energy_cost", "stadium_played",
    "supporter_played", "energy_attached", "retreated", "self_hand_count",
    "self_deck_count", "self_prize_count", "self_discard_count",
    "opponent_hand_count", "opponent_deck_count", "opponent_prize_count",
    "opponent_discard_count", "self_poisoned", "self_burned", "self_asleep",
    "self_paralyzed", "self_confused", "opponent_poisoned", "opponent_burned",
    "opponent_asleep", "opponent_paralyzed", "opponent_confused",
    "deck_reveal_available", "looking_available", "looking_hidden_count",
    "context_card_present", "effect_present", "stadium_present", "self_bench_max",
    "opponent_bench_max",
)
_CATEGORICAL_STATE_SCALAR_INDICES = (0, 4, 5)
_STATE_SCALAR_CAPS_V1 = MappingProxyType({
    1: 4095,  # step
    2: 255, 3: 255,  # turn / turn action count
    6: 512, 7: 512, 8: 512,  # selection bounds / candidate count
    9: 255, 10: 255,  # remaining counters
    **{index: 60 for index in range(15, 23)},
    35: 60, 39: 60, 40: 60,
})

FEATURE_SCHEMA_DESCRIPTOR_V1 = MappingProxyType({
    "schema_version": MODEL_INPUT_SCHEMA_V1,
    "feature_domain": ACTOR_VISIBLE_FEATURE_DOMAIN_V1,
    "state_scalar_names": STATE_SCALAR_NAMES_V1,
    "single_card_id_names": ("stadium", "context", "effect"),
    "card_bag_names": (
        "own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard",
    ),
    "pokemon_entity_fields": (
        "owner_role", "zone", "card_id", "hp", "max_hp", "appear_this_turn",
        "energy_type_counts", "energy_cards", "tools", "pre_evolution",
    ),
    "candidate_row_fields": (
        "selection_type", "selection_context", "option_type", "operation", "source",
        "target", "host", "number", "attack_id", "special_condition",
        "energy_count", "skill_card_id",
    ),
    "semantic_endpoint_fields": (
        "visibility", "owner_role", "semantic_zone", "card_id", "host_card_id", "pokemon",
    ),
    "card_token_rule": "PAD=0;UNK=1;official_card_id=k=>k+1",
    "max_cards": _MAX_CARDS,
    "max_candidates": _MAX_CANDIDATES,
    "categorical_state_scalar_indices": _CATEGORICAL_STATE_SCALAR_INDICES,
    "normalized_state_scalar_caps": tuple(sorted(_STATE_SCALAR_CAPS_V1.items())),
    "option_type_operations": tuple(sorted(_OPTION_OPERATIONS_V1.items())),
    "option_parameter_fields": tuple(sorted(_PARAMETER_FIELD_BY_OPTION_TYPE_V1.items())),
    "unordered_factorization": "canonical-nondecreasing-with-minimum-feasibility-v1",
    "attachment_skill_host": "transient-exact-source-to-public-host-v1",
})


class SpecialistFeatureError(ValueError):
    """Raised when a feature, vocabulary, or semantic policy contract is unsafe."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpecialistFeatureError("feature value is not canonical JSON") from exc


def _hash(prefix: bytes, core: object) -> str:
    return hashlib.sha256(prefix + _canonical_bytes(core)).hexdigest()


FEATURE_SCHEMA_HASH_V1 = _hash(_FEATURE_SCHEMA_PREFIX, dict(FEATURE_SCHEMA_DESCRIPTOR_V1))
FEATURE_SCHEMA_CANONICAL_BYTES_V1 = _canonical_bytes(dict(FEATURE_SCHEMA_DESCRIPTOR_V1))
CARD_VOCABULARY_SCHEMA_DESCRIPTOR_V1 = MappingProxyType({
    "schema_version": CARD_VOCABULARY_SCHEMA_V1,
    "fields": (
        "schema_version", "source_sha256", "recognized_card_ids", "mapping_rule",
        "environment_version", "permission_decision", "usage_decision", "test_only",
        "vocabulary_schema_hash",
    ),
    "mapping_rule": "PAD=0;UNK=1;official_card_id=k=>k+1",
})
CARD_VOCABULARY_SCHEMA_HASH_V1 = _hash(
    _CARD_VOCABULARY_SCHEMA_PREFIX, dict(CARD_VOCABULARY_SCHEMA_DESCRIPTOR_V1)
)


def _strict_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SpecialistFeatureError(f"{field} must be a non-bool int at least {minimum}")
    return value


def _strict_bool_token(value: object, *, field: str) -> int:
    if type(value) is not bool:
        raise SpecialistFeatureError(f"{field} must be a bool")
    return int(value)


@dataclass(frozen=True, slots=True)
class CollatedStateScalarsV1:
    """Pure-Python tensor preparation with explicit categorical/continuous split.

    The floats are rounded through IEEE-754 binary32 so a torch/numpy collator
    can consume these values without redefining the frozen transformation.
    """

    categorical_indices: tuple[int, int, int]
    continuous_values: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.categorical_indices) is not tuple or len(self.categorical_indices) != 3 or any(
            type(value) is not int for value in self.categorical_indices
        ):
            raise SpecialistFeatureError("collated categorical indices must be three non-bool ints")
        if not 0 <= self.categorical_indices[0] <= 2 or not 0 <= self.categorical_indices[1] <= 10 or not 0 <= self.categorical_indices[2] <= 48:
            raise SpecialistFeatureError("collated categorical index is outside its frozen domain")
        if type(self.continuous_values) is not tuple or len(self.continuous_values) != 38 or any(
            type(value) is not float or not math.isfinite(value) for value in self.continuous_values
        ):
            raise SpecialistFeatureError("collated continuous values must be 38 finite floats")


@dataclass(frozen=True, slots=True)
class CollatedCandidateRowsV1:
    """Batch-local ragged candidate padding; it never pads to a global 512."""

    rows: tuple[tuple[SemanticActionV1 | None, ...], ...]
    mask: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        if type(self.rows) is not tuple or type(self.mask) is not tuple or len(self.rows) != len(self.mask):
            raise SpecialistFeatureError("candidate batch rows/mask must be matching tuples")
        width = 0 if not self.rows else len(self.rows[0])
        if width > _MAX_CANDIDATES or any(len(row) != width for row in self.rows) or any(len(row) != width for row in self.mask):
            raise SpecialistFeatureError("candidate batch width is invalid")
        for row, mask in zip(self.rows, self.mask):
            if type(row) is not tuple or type(mask) is not tuple:
                raise SpecialistFeatureError("candidate batch rows/mask entries must be exact tuples")
            for value, present in zip(row, mask):
                if type(present) is not int or present not in (0, 1) or (present == 1) != (type(value) is SemanticActionV1):
                    raise SpecialistFeatureError("candidate batch padding/mask is invalid")
                if value is not None:
                    SemanticActionV1.__post_init__(value)


def collate_candidate_rows_v1(model_inputs: tuple[SpecialistModelInputV1, ...]) -> CollatedCandidateRowsV1:
    """Pad only to this batch's maximum legal candidate count with an exact mask."""
    if type(model_inputs) is not tuple or any(type(item) is not SpecialistModelInputV1 for item in model_inputs):
        raise SpecialistFeatureError("model_inputs must be a tuple of SpecialistModelInputV1")
    for model_input in model_inputs:
        SpecialistModelInputV1.__post_init__(model_input)
    width = max((len(item.candidate_rows) for item in model_inputs), default=0)
    return CollatedCandidateRowsV1(
        rows=tuple(item.candidate_rows + (None,) * (width - len(item.candidate_rows)) for item in model_inputs),
        mask=tuple((1,) * len(item.candidate_rows) + (0,) * (width - len(item.candidate_rows)) for item in model_inputs),
    )


def collate_state_scalars_v1(state_scalars: tuple[int, ...]) -> CollatedStateScalarsV1:
    """Emit categorical int indices and finite log-normalized binary32 counts."""
    if type(state_scalars) is not tuple or len(state_scalars) != 41:
        raise SpecialistFeatureError("state_scalars must be the exact 41-tuple")
    if any(type(value) is not int or value < 0 for value in state_scalars):
        raise SpecialistFeatureError("state_scalars must contain nonnegative non-bool ints")
    categorical = tuple(state_scalars[index] for index in _CATEGORICAL_STATE_SCALAR_INDICES)
    if categorical[0] > 2 or categorical[1] > 10 or categorical[2] > 48:
        raise SpecialistFeatureError("categorical state scalar is outside its frozen domain")
    if any(state_scalars[index] not in (0, 1) for index in _FLAG_STATE_SCALAR_INDICES_V1):
        raise SpecialistFeatureError("state scalar flag slots must be exactly 0 or 1")
    values: list[float] = []
    for index, value in enumerate(state_scalars):
        if index in _CATEGORICAL_STATE_SCALAR_INDICES:
            continue
        cap = _STATE_SCALAR_CAPS_V1.get(index)
        normalized = float(value) if cap is None else math.log1p(min(value, cap)) / math.log1p(cap)
        binary32 = struct.unpack("!f", struct.pack("!f", normalized))[0]
        if not math.isfinite(binary32):
            raise SpecialistFeatureError("collated scalar must be finite")
        values.append(binary32)
    return CollatedStateScalarsV1(categorical_indices=categorical, continuous_values=tuple(values))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CardVocabularyV1:
    """Sealed vocabulary manifest; test vocabularies cannot qualify for packaging.

    ``_issuance_seal`` is never set by this class's own constructor (it is
    ``init=False`` and defaults to ``None``); only
    ``card_vocabulary_registry_v1.load_production_card_vocabulary_v1`` sets it,
    on the *one* object it returns, as part of sealing that object to a
    process-local issuance record.  See
    :func:`require_production_card_vocabulary_v1` -- a ``dataclasses.replace``,
    ``copy``, or fresh construction never carries a live issuance record for
    its own object identity, even when every other field is byte-identical.
    """

    recognized_card_ids: frozenset[int]
    source_sha256: str
    environment_version: str
    usage_decision: str
    test_only: bool = False
    permission_decision: str = "test-only"
    _issuance_seal: object = field(default=None, init=False, repr=False, compare=False)

    @property
    def schema_version(self) -> str:
        return CARD_VOCABULARY_SCHEMA_V1

    def __post_init__(self) -> None:
        if type(self.recognized_card_ids) is not frozenset or len(self.recognized_card_ids) > 1_000_000 or any(
            type(card_id) is not int or card_id < 1 for card_id in self.recognized_card_ids
        ):
            raise SpecialistFeatureError("recognized_card_ids must be positive integer IDs")
        if type(self.source_sha256) is not str or len(self.source_sha256) != 64 or any(character not in "0123456789abcdef" for character in self.source_sha256):
            raise SpecialistFeatureError("source_sha256 must be a SHA-256 hex string")
        if type(self.environment_version) is not str or not self.environment_version or len(self.environment_version) > 256:
            raise SpecialistFeatureError("environment_version must be a nonempty string")
        if self.usage_decision not in {"test-only", "bundle_allowed", "unqualified"}:
            raise SpecialistFeatureError("usage_decision is not in the closed vocabulary domain")
        if type(self.test_only) is not bool:
            raise SpecialistFeatureError("test_only must be a bool")
        if self.test_only and self.usage_decision != "test-only":
            raise SpecialistFeatureError("test-only vocabulary must use the test-only decision")
        if self.permission_decision not in {"test-only", "bundle_allowed", "unqualified"}:
            raise SpecialistFeatureError("permission_decision is not in the closed vocabulary domain")
        if self.test_only and self.permission_decision != "test-only":
            raise SpecialistFeatureError("test-only vocabulary must use the test-only permission")

    def token_for(self, card_id: int | None) -> int:
        if card_id is None:
            return 0
        _strict_int(card_id, field="card_id", minimum=1)
        return card_id + 1 if card_id in self.recognized_card_ids else 1

    def to_manifest_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "recognized_card_ids": sorted(self.recognized_card_ids),
            "mapping_rule": "PAD=0;UNK=1;official_card_id=k=>k+1",
            "environment_version": self.environment_version,
            "permission_decision": self.permission_decision,
            "usage_decision": self.usage_decision,
            "test_only": self.test_only,
            "vocabulary_schema_hash": CARD_VOCABULARY_SCHEMA_HASH_V1,
        }


def make_test_card_vocabulary_v1(recognized_card_ids: object) -> CardVocabularyV1:
    """Make an explicitly non-promotable vocabulary for unit tests only."""
    try:
        ids = frozenset(recognized_card_ids)  # type: ignore[arg-type]
    except TypeError as exc:
        raise SpecialistFeatureError("test recognized_card_ids must be iterable") from exc
    return CardVocabularyV1(
        recognized_card_ids=ids,
        source_sha256="0" * 64,
        environment_version="test-only",
        usage_decision="test-only",
        test_only=True,
        permission_decision="test-only",
    )


def require_production_card_vocabulary_v1(vocabulary: CardVocabularyV1) -> CardVocabularyV1:
    """Fail closed unless an independently qualified vocabulary is bundle-safe.

    A trusted sealed registry now exists
    (``configs/meta_specialist/card_vocabulary_registry_v1.json``, loaded and
    verified by
    :mod:`mage_ptcg.meta_specialist.card_vocabulary_registry_v1`), so this
    delegates the actual qualification check to that module rather than
    raising unconditionally.  A caller-created ``bundle_allowed`` value is
    still not evidence of qualification: only the exact object
    ``load_production_card_vocabulary_v1`` issued -- never a
    ``dataclasses.replace``, ``copy``, or fresh construction, even with
    byte-identical fields -- can pass the delegated check.

    The import below is local to this function rather than at module scope:
    ``card_vocabulary_registry_v1`` imports ``CardVocabularyV1`` from this
    module, so importing it back at module load time would create a cycle.
    By the time this function actually runs, both modules have finished
    loading and the cycle cannot occur.
    """
    if not isinstance(vocabulary, CardVocabularyV1):
        raise SpecialistFeatureError("vocabulary must be CardVocabularyV1")
    if vocabulary.test_only:
        raise SpecialistFeatureError("test-only card vocabulary cannot be used in production")
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        require_registry_issued_card_vocabulary_v1,
    )
    return require_registry_issued_card_vocabulary_v1(vocabulary)


@dataclass(frozen=True, slots=True)
class CardBagV1:
    """Fixed-capacity multiset with a separate mask; zero is only padding."""

    tokens: tuple[int, ...]
    mask: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.tokens) is not tuple or type(self.mask) is not tuple or len(self.tokens) != _MAX_CARDS or len(self.mask) != _MAX_CARDS:
            raise SpecialistFeatureError("card bag must be padded to 60")
        if any(type(value) is not int or value < 0 for value in self.tokens):
            raise SpecialistFeatureError("card bag tokens must be nonnegative ints")
        if any(type(value) is not int or value not in (0, 1) for value in self.mask):
            raise SpecialistFeatureError("card bag mask must contain only 0 or 1")
        if any((mask == 0) != (token == 0) for token, mask in zip(self.tokens, self.mask)):
            raise SpecialistFeatureError("card bag padding and mask must agree")

    def to_dict(self) -> dict[str, object]:
        return {"tokens": list(self.tokens), "mask": list(self.mask)}


def _card_bag(cards: tuple[CardRefV2 | BoundCardRefV1, ...], vocabulary: CardVocabularyV1) -> CardBagV1:
    if len(cards) > _MAX_CARDS:
        raise SpecialistFeatureError("card multiset exceeds 60")
    tokens = sorted(vocabulary.token_for(card.card_id) for card in cards)
    return CardBagV1(
        tokens=tuple(tokens + [0] * (_MAX_CARDS - len(tokens))),
        mask=tuple([1] * len(tokens) + [0] * (_MAX_CARDS - len(tokens))),
    )


@dataclass(frozen=True, slots=True)
class PokemonEntityV1:
    owner_role: int
    zone: str
    card_id: int
    hp: int
    max_hp: int
    appear_this_turn: int
    energy_type_counts: tuple[int, ...]
    energy_cards: tuple[int, ...]
    tools: tuple[int, ...]
    pre_evolution: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.owner_role) is not int or self.owner_role not in (1, 2) or type(self.zone) is not str or self.zone not in {"active", "bench"}:
            raise SpecialistFeatureError("Pokemon entity owner_role/zone is invalid")
        for name in ("card_id", "hp", "max_hp", "appear_this_turn"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise SpecialistFeatureError(f"Pokemon entity {name} must be a nonnegative int")
        if self.appear_this_turn not in (0, 1) or self.hp > self.max_hp:
            raise SpecialistFeatureError("Pokemon entity scalar values are inconsistent")
        if type(self.energy_type_counts) is not tuple or len(self.energy_type_counts) != 12 or any(type(value) is not int or value < 0 for value in self.energy_type_counts):
            raise SpecialistFeatureError("Pokemon entity must have 12 energy type counts")
        for name in ("energy_cards", "tools", "pre_evolution"):
            values = getattr(self, name)
            if type(values) is not tuple or len(values) > _MAX_CARDS or any(type(value) is not int or value < 0 for value in values):
                raise SpecialistFeatureError(f"Pokemon entity {name} must be a bounded token multiset")
            if tuple(sorted(values)) != values:
                raise SpecialistFeatureError(f"Pokemon entity {name} must be sorted")

    def to_dict(self) -> dict[str, object]:
        return {
            "owner_role": self.owner_role, "zone": self.zone, "card_id": self.card_id,
            "hp": self.hp, "max_hp": self.max_hp, "appear_this_turn": self.appear_this_turn,
            "energy_type_counts": list(self.energy_type_counts), "energy_cards": list(self.energy_cards),
            "tools": list(self.tools), "pre_evolution": list(self.pre_evolution),
        }


@dataclass(frozen=True, slots=True)
class SemanticEndpointV1:
    """Immutable serial-free endpoint; it admits no generic nested mappings."""

    visibility: str
    owner_role: int
    semantic_zone: str
    card_id: int
    host_card_id: int
    pokemon: PokemonEntityV1 | None

    def __post_init__(self) -> None:
        if self.visibility not in {
            "not-applicable", "actor-visible", "public-visible", "hidden-unresolved",
            "owner-resolved", "special-condition",
        }:
            raise SpecialistFeatureError("semantic endpoint visibility is invalid")
        if type(self.owner_role) is not int or self.owner_role not in (0, 1, 2) or type(self.semantic_zone) is not str or not self.semantic_zone:
            raise SpecialistFeatureError("semantic endpoint owner_role/zone is invalid")
        for name in ("card_id", "host_card_id"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise SpecialistFeatureError(f"semantic endpoint {name} is invalid")
        if self.pokemon is not None and type(self.pokemon) is not PokemonEntityV1:
            raise SpecialistFeatureError("semantic endpoint pokemon must be PokemonEntityV1 or null")
        if self.pokemon is not None:
            PokemonEntityV1.__post_init__(self.pokemon)
        exact_null = (
            self.owner_role == 0
            and self.semantic_zone == "not-applicable"
            and self.card_id == 0
            and self.host_card_id == 0
            and self.pokemon is None
        )
        if self.visibility in {"not-applicable", "special-condition"}:
            if not exact_null:
                raise SpecialistFeatureError("null/special semantic endpoint must use the exact null shape")
            return
        if self.visibility == "owner-resolved":
            if self.owner_role not in (1, 2) or self.semantic_zone != "player" or self.card_id != 0 or self.host_card_id != 0 or self.pokemon is not None:
                raise SpecialistFeatureError("owner-resolved semantic endpoint must use the exact player shape")
            return
        if self.visibility == "hidden-unresolved":
            if self.card_id != 0 or self.host_card_id != 0 or self.pokemon is not None or self.semantic_zone in {"not-applicable", "player"}:
                raise SpecialistFeatureError("hidden semantic endpoint must use the exact unresolved shape")
            return
        if self.owner_role not in (1, 2) or self.card_id == 0 or self.semantic_zone in {"not-applicable", "player", "hidden"}:
            raise SpecialistFeatureError("visible semantic endpoint must bind a card and owner")
        if self.pokemon is not None:
            expected_pokemon_card = self.host_card_id if self.host_card_id != 0 else self.card_id
            if self.pokemon.card_id != expected_pokemon_card or self.pokemon.owner_role != self.owner_role:
                raise SpecialistFeatureError("semantic endpoint Pokemon snapshot does not match its card/host")

    def to_dict(self) -> dict[str, object]:
        return {
            "visibility": self.visibility, "owner_role": self.owner_role,
            "semantic_zone": self.semantic_zone, "card_id": self.card_id,
            "host_card_id": self.host_card_id,
            "pokemon": None if self.pokemon is None else self.pokemon.to_dict(),
        }


def _padded_tokens(cards: tuple[BoundCardRefV1, ...], vocabulary: CardVocabularyV1) -> tuple[int, ...]:
    if len(cards) > _MAX_CARDS:
        raise SpecialistFeatureError("Pokemon attachment multiset exceeds 60")
    return tuple(sorted(vocabulary.token_for(card.card_id) for card in cards))


def _owner_role(actor: int, owner: int | None) -> int:
    if owner is None:
        return 0
    if owner == actor:
        return 1
    if owner == 1 - actor:
        return 2
    raise SpecialistFeatureError("owner is outside the two-player domain")


def _pokemon_entity(pokemon: PokemonStateV2, zone: str, actor: int, vocabulary: CardVocabularyV1) -> PokemonEntityV1:
    counts = tuple(pokemon.energies.count(energy) for energy in range(12))
    return PokemonEntityV1(
        owner_role=_owner_role(actor, pokemon.owner), zone=zone,
        card_id=vocabulary.token_for(pokemon.ref.card_id), hp=pokemon.hp, max_hp=pokemon.max_hp,
        appear_this_turn=_strict_bool_token(pokemon.appear_this_turn, field="pokemon.appear_this_turn"),
        energy_type_counts=counts, energy_cards=_padded_tokens(pokemon.energy_cards, vocabulary),
        tools=_padded_tokens(pokemon.tools, vocabulary), pre_evolution=_padded_tokens(pokemon.pre_evolution, vocabulary),
    )


def _pokemon_snapshot(card: BoundCardRefV1 | None, view: ActorInformationViewV2, vocabulary: CardVocabularyV1) -> PokemonEntityV1 | None:
    if card is None:
        return None
    for player, owner in ((view.self_player, view.actor), (view.opponent_player, 1 - view.actor)):
        for zone, entries in (("active", player.active), ("bench", player.bench)):
            for pokemon in entries:
                if pokemon is not None and (pokemon.ref.card_id, pokemon.ref.serial, owner) == (card.card_id, card.serial, card.player_index):
                    return _pokemon_entity(pokemon, zone, view.actor, vocabulary)
    return None


def _transient_attachment_host(
    endpoint: ActorVisibleBindingEndpointV1,
    view: ActorInformationViewV2,
) -> PokemonStateV2 | None:
    """Resolve a Skill attachment's exact public host without serializing its locator."""
    zone_parts = endpoint.semantic_zone.rsplit("-", 1)
    if len(zone_parts) != 2 or zone_parts[0] not in {"active", "bench"} or zone_parts[1] not in {"tool", "energy"}:
        return None
    card = endpoint.bound_card
    if card is None:
        raise SpecialistFeatureError("actor-visible attachment endpoint has no bound source card")
    player = view.self_player if card.player_index == view.actor else view.opponent_player
    entries = player.active if zone_parts[0] == "active" else player.bench
    matches: list[PokemonStateV2] = []
    for pokemon in entries:
        if pokemon is None:
            continue
        attachments = pokemon.tools if zone_parts[1] == "tool" else pokemon.energy_cards
        if any(attachment == card for attachment in attachments):
            matches.append(pokemon)
    if len(matches) != 1:
        raise SpecialistFeatureError("actor-visible Skill attachment must resolve exactly one public host")
    return matches[0]


def _semantic_endpoint(endpoint: ActorVisibleBindingEndpointV1, *, host: ActorVisibleBindingEndpointV1, view: ActorInformationViewV2, vocabulary: CardVocabularyV1) -> SemanticEndpointV1:
    card = endpoint.bound_card
    host_card = host.bound_card
    pokemon = _pokemon_snapshot(card, view, vocabulary)
    transient_host = None if host_card is not None else _transient_attachment_host(endpoint, view)
    if transient_host is not None:
        host_card_id = transient_host.ref.card_id
        pokemon = _pokemon_entity(
            transient_host,
            endpoint.semantic_zone.rsplit("-", 1)[0],
            view.actor,
            vocabulary,
        )
    else:
        host_card_id = None if host_card is None else host_card.card_id
    return SemanticEndpointV1(
        visibility=endpoint.resolution_kind, owner_role=_owner_role(view.actor, endpoint.owner_player_index),
        semantic_zone=endpoint.semantic_zone, card_id=vocabulary.token_for(None if card is None else card.card_id),
        host_card_id=vocabulary.token_for(host_card_id), pokemon=pokemon,
    )


def _projection_require_v1(condition: bool) -> None:
    """Reject feature rows that are outside the frozen C1 resolver projection."""
    if not condition:
        raise SpecialistFeatureError(
            "semantic action endpoint is outside the frozen C1 resolver projection"
        )


def _require_null_endpoint_projection_v1(endpoint: SemanticEndpointV1) -> None:
    _projection_require_v1(endpoint.visibility == "not-applicable")


def _require_nonboard_visible_endpoint_v1(
    endpoint: SemanticEndpointV1,
    *,
    visibility: str,
    zones: frozenset[str],
    owners: frozenset[int],
) -> None:
    _projection_require_v1(
        endpoint.visibility == visibility
        and endpoint.semantic_zone in zones
        and endpoint.owner_role in owners
        and endpoint.host_card_id == 0
        and endpoint.pokemon is None
    )


def _require_hidden_endpoint_projection_v1(
    endpoint: SemanticEndpointV1,
    *,
    zones: frozenset[str],
    owners: frozenset[int],
) -> None:
    _projection_require_v1(
        endpoint.visibility == "hidden-unresolved"
        and endpoint.semantic_zone in zones
        and endpoint.owner_role in owners
    )


def _require_public_board_endpoint_v1(
    endpoint: SemanticEndpointV1,
    *,
    owners: frozenset[int],
    host_is_self: bool,
) -> None:
    pokemon = endpoint.pokemon
    _projection_require_v1(
        endpoint.visibility == "public-visible"
        and endpoint.semantic_zone in {"active", "bench"}
        and endpoint.owner_role in owners
        and endpoint.host_card_id == (endpoint.card_id if host_is_self else 0)
        and pokemon is not None
        and pokemon.zone == endpoint.semantic_zone
    )


def _source_owner_roles_v1(owner_rule: str) -> frozenset[int]:
    if owner_rule == "actor":
        return frozenset({1})
    if owner_rule == "option.playerIndex":
        return frozenset({1, 2})
    raise SpecialistFeatureError("unsupported frozen C1 source owner resolver")


def _target_owner_roles_v1(owner_rule: str) -> frozenset[int]:
    if owner_rule == "actor":
        return frozenset({1})
    if owner_rule == "option.playerIndex":
        return frozenset({1, 2})
    raise SpecialistFeatureError("unsupported frozen C1 target owner resolver")


def _validate_area_index_source_projection_v1(
    endpoint: SemanticEndpointV1,
    *,
    legal_areas: frozenset[int],
    owner_rule: str,
) -> None:
    """Validate the serial-free image of C1's exact ``_card_at_area`` resolver.

    C1 keeps missing reason and the physical locator locally, but the feature
    projection deliberately drops both.  The remaining visibility/zone/owner
    combinations below are therefore precisely the possible projected forms,
    derived from its frozen AreaType branches and the resolver row's allowed
    area set.
    """
    owners = _source_owner_roles_v1(owner_rule)
    if endpoint.visibility == "actor-visible":
        actor_visible_areas = {
            "deck-reveal": (frozenset({1}), frozenset({1})),
            "hand": (frozenset({2}), frozenset({1})),
            "looking": (frozenset({12}), frozenset({1, 2})),
        }
        allowed = actor_visible_areas.get(endpoint.semantic_zone)
        _projection_require_v1(
            allowed is not None
            and bool(legal_areas & allowed[0])
            and endpoint.owner_role in owners & allowed[1]
            and endpoint.host_card_id == 0
            and endpoint.pokemon is None
        )
        return
    if endpoint.visibility == "public-visible":
        public_areas = {
            "discard": frozenset({3}),
            "active": frozenset({4}),
            "bench": frozenset({5}),
            "stadium": frozenset({7}),
        }
        areas = public_areas.get(endpoint.semantic_zone)
        _projection_require_v1(
            areas is not None and bool(legal_areas & areas) and endpoint.owner_role in owners
        )
        if endpoint.semantic_zone in {"active", "bench"}:
            _require_public_board_endpoint_v1(
                endpoint, owners=owners, host_is_self=False
            )
        else:
            _require_nonboard_visible_endpoint_v1(
                endpoint,
                visibility="public-visible",
                zones=frozenset({endpoint.semantic_zone}),
                owners=owners,
            )
        return
    if endpoint.visibility == "hidden-unresolved":
        hidden_areas = {
            "deck": frozenset({1}), "prize": frozenset({6}),
            "energy": frozenset({8}), "tool": frozenset({9}),
            "pre-evolution": frozenset({10}), "looking": frozenset({12}),
        }
        areas = hidden_areas.get(endpoint.semantic_zone)
        _projection_require_v1(
            areas is not None and bool(legal_areas & areas) and endpoint.owner_role in owners
        )
        _require_hidden_endpoint_projection_v1(
            endpoint, zones=frozenset({endpoint.semantic_zone}), owners=owners
        )
        return
    if endpoint.visibility == "owner-resolved":
        _projection_require_v1(
            11 in legal_areas and endpoint.semantic_zone == "player" and endpoint.owner_role in owners
        )
        return
    _projection_require_v1(False)


def _validate_registry_source_projection_v1(endpoint: SemanticEndpointV1) -> None:
    """Validate the image of C1's bounded Skill registry resolver."""
    if endpoint.visibility == "special-condition":
        return
    if endpoint.visibility == "hidden-unresolved":
        _require_hidden_endpoint_projection_v1(
            endpoint, zones=frozenset({"hidden"}), owners=frozenset({0})
        )
        return
    if endpoint.visibility == "actor-visible":
        actor_visible_owners = {
            "hand": frozenset({1}), "deck-reveal": frozenset({1}),
            "looking": frozenset({1, 2}), "context-card": frozenset({1, 2}),
            "effect": frozenset({1, 2}),
        }
        owners = actor_visible_owners.get(endpoint.semantic_zone)
        _projection_require_v1(owners is not None)
        _require_nonboard_visible_endpoint_v1(
            endpoint,
            visibility="actor-visible",
            zones=frozenset({endpoint.semantic_zone}),
            owners=owners,
        )
        return
    if endpoint.visibility != "public-visible":
        _projection_require_v1(False)
        return
    if endpoint.semantic_zone in {"active", "bench"}:
        _require_public_board_endpoint_v1(
            endpoint, owners=frozenset({1, 2}), host_is_self=False
        )
        return
    if endpoint.semantic_zone in {"active-tool", "bench-tool", "active-energy", "bench-energy"}:
        pokemon = endpoint.pokemon
        base_zone = endpoint.semantic_zone.rsplit("-", 1)[0]
        _projection_require_v1(
            endpoint.owner_role in {1, 2}
            and endpoint.host_card_id != 0
            and pokemon is not None
            and pokemon.zone == base_zone
        )
        return
    _require_nonboard_visible_endpoint_v1(
        endpoint,
        visibility="public-visible",
        zones=frozenset({"discard", "stadium", "pre-evolution"}),
        owners=frozenset({1, 2}),
    )


def _validate_area_dependent_source_projection_v1(endpoint: SemanticEndpointV1) -> None:
    """Validate C1 Ability/Discard's 4/5 actor and 7 stadium-owner split."""
    if endpoint.semantic_zone in {"active", "bench"}:
        _require_public_board_endpoint_v1(
            endpoint, owners=frozenset({1}), host_is_self=False
        )
        return
    _require_nonboard_visible_endpoint_v1(
        endpoint,
        visibility="public-visible",
        zones=frozenset({"stadium"}),
        owners=frozenset({1, 2}),
    )


def _validate_semantic_resolver_projection_v1(action: SemanticActionV1) -> None:
    """Close semantic rows to the serial-free image of the frozen C1 resolver."""
    row = OPTION_RESOLVER_TABLE_V1.get(action.option_type)
    _projection_require_v1(row is not None and row.operation == action.operation)
    assert row is not None  # narrowed above; retained for static readers

    if row.target_resolver == "not-applicable":
        _require_null_endpoint_projection_v1(action.target)
    elif row.target_resolver == "in-play-pokemon":
        target_owners = _target_owner_roles_v1(row.target_owner)
        _require_public_board_endpoint_v1(
            action.target,
            owners=target_owners,
            host_is_self=row.host_resolver == "in-play-pokemon",
        )
        if row.target_owner == "option.playerIndex":
            _projection_require_v1(action.target.owner_role == action.source.owner_role)
    else:
        raise SpecialistFeatureError("unsupported frozen C1 target resolver")

    if row.host_resolver == "not-applicable":
        _require_null_endpoint_projection_v1(action.host)
    elif row.host_resolver == "in-play-pokemon":
        _projection_require_v1(action.host == action.target)
    else:
        raise SpecialistFeatureError("unsupported frozen C1 host resolver")

    source = action.source
    if row.source_resolver in {"number", "not-applicable"}:
        _require_null_endpoint_projection_v1(source)
    elif row.source_resolver == "special-condition":
        _projection_require_v1(source.visibility == "special-condition")
    elif row.source_resolver == "actor-hand":
        _require_nonboard_visible_endpoint_v1(
            source,
            visibility="actor-visible",
            zones=frozenset({"hand"}),
            owners=frozenset({1}),
        )
    elif row.source_resolver in {"attached-tool", "attached-energy"}:
        attachment_kind = "tool" if row.source_resolver == "attached-tool" else "energy"
        target = action.target
        _projection_require_v1(
            source.visibility == "public-visible"
            and source.semantic_zone in {f"active-{attachment_kind}", f"bench-{attachment_kind}"}
            and source.owner_role == target.owner_role
            and source.host_card_id == target.card_id
            and source.pokemon is None
        )
    elif row.source_resolver == "area-index":
        if row.source_owner == "area-dependent:4,5=actor;7=stadium-card.playerIndex":
            _validate_area_dependent_source_projection_v1(source)
        else:
            _validate_area_index_source_projection_v1(
                source,
                legal_areas=row.legal_source_areas,
                owner_rule=row.source_owner,
            )
    elif row.source_resolver == "actor-active":
        if source.visibility == "public-visible":
            _require_public_board_endpoint_v1(
                source, owners=frozenset({1}), host_is_self=False
            )
            _projection_require_v1(source.semantic_zone == "active")
        else:
            _require_hidden_endpoint_projection_v1(
                source, zones=frozenset({"active"}), owners=frozenset({1})
            )
    elif row.source_resolver == "bounded-card-registry":
        _validate_registry_source_projection_v1(source)
    else:
        raise SpecialistFeatureError("unsupported frozen C1 source resolver")


@dataclass(frozen=True, slots=True)
class SemanticActionV1:
    """Closed semantic candidate row, intentionally free of locators and serials."""

    selection_type: int
    selection_context: int
    option_type: int
    operation: str
    source: SemanticEndpointV1
    target: SemanticEndpointV1
    host: SemanticEndpointV1
    number: int | None
    attack_id: int | None
    special_condition: int | None
    energy_count: int | None
    skill_card_id: int | None

    def __post_init__(self) -> None:
        if type(self.selection_type) is not int or not 0 <= self.selection_type <= 10:
            raise SpecialistFeatureError("semantic action selection_type is invalid")
        if type(self.selection_context) is not int or not 0 <= self.selection_context <= 48:
            raise SpecialistFeatureError("semantic action selection_context is invalid")
        if type(self.option_type) is not int or self.option_type not in _OPTION_OPERATIONS_V1 or type(self.operation) is not str:
            raise SpecialistFeatureError("semantic action option type/operation is invalid")
        if self.operation != _OPTION_OPERATIONS_V1[self.option_type]:
            raise SpecialistFeatureError("semantic action operation does not match option_type")
        contexts = CABT_AGENT_JSON_SELECTION_CONTEXTS_V1.get(self.selection_type)
        if contexts is None or self.selection_context not in contexts or self.option_type not in _OPTION_TYPES_BY_SELECTION_TYPE_V1[self.selection_type]:
            raise SpecialistFeatureError("semantic action option_type is invalid for its selection schema")
        for name in ("source", "target", "host"):
            endpoint = getattr(self, name)
            if type(endpoint) is not SemanticEndpointV1:
                raise SpecialistFeatureError(f"semantic action {name} must be SemanticEndpointV1")
            SemanticEndpointV1.__post_init__(endpoint)
        for name in ("number", "attack_id", "special_condition", "energy_count", "skill_card_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise SpecialistFeatureError(f"semantic action {name} must be a nonnegative int or null")
        applicable = _PARAMETER_FIELD_BY_OPTION_TYPE_V1.get(self.option_type)
        for name in ("number", "attack_id", "special_condition", "energy_count", "skill_card_id"):
            if (name == applicable) != (getattr(self, name) is not None):
                raise SpecialistFeatureError("semantic action parameter applicability is invalid")
        if self.special_condition is not None and self.special_condition > 4:
            raise SpecialistFeatureError("semantic action special_condition must be in 0..4")
        if self.skill_card_id is not None and self.skill_card_id < 1:
            raise SpecialistFeatureError("semantic action Skill card token must be nonpadding")

        null_visibility = "not-applicable"
        if self.option_type in {0, 1, 2, 14}:
            if any(endpoint.visibility != null_visibility for endpoint in (self.source, self.target, self.host)):
                raise SpecialistFeatureError("semantic action endpoints are inapplicable for this option_type")
        elif self.option_type == 16:
            if self.source.visibility != "special-condition" or self.target.visibility != null_visibility or self.host.visibility != null_visibility:
                raise SpecialistFeatureError("SPECIAL_CONDITION semantic endpoints are invalid")
        elif self.option_type in {4, 5, 6}:
            expected_source_zones = {"active-tool", "bench-tool"} if self.option_type == 4 else {"active-energy", "bench-energy"}
            if self.source.semantic_zone not in expected_source_zones or self.target != self.host or self.target.visibility != "public-visible" or self.target.semantic_zone not in {"active", "bench"}:
                raise SpecialistFeatureError("attachment semantic source/target/host endpoints are invalid")
        elif self.option_type in {8, 9}:
            if self.target.visibility != "public-visible" or self.target.semantic_zone not in {"active", "bench"} or self.host.visibility != null_visibility:
                raise SpecialistFeatureError("ATTACH/EVOLVE semantic target/host endpoints are invalid")
        elif self.target.visibility != null_visibility or self.host.visibility != null_visibility:
            raise SpecialistFeatureError("semantic action target/host endpoints are inapplicable")
        if self.option_type not in {0, 1, 2, 14, 16} and self.source.visibility == null_visibility:
            raise SpecialistFeatureError("semantic action source endpoint is unexpectedly inapplicable")
        if self.option_type == 15 and self.source.visibility == "special-condition" and self.skill_card_id != 1:
            raise SpecialistFeatureError("special Skill cardId zero must map only to UNK")
        _validate_semantic_resolver_projection_v1(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "selection_type": self.selection_type, "selection_context": self.selection_context,
            "option_type": self.option_type, "operation": self.operation,
            "source": self.source.to_dict(), "target": self.target.to_dict(), "host": self.host.to_dict(),
            "number": self.number, "attack_id": self.attack_id,
            "special_condition": self.special_condition, "energy_count": self.energy_count,
            "skill_card_id": self.skill_card_id,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


def _action_fields(action: ActorVisibleLegalActionV2) -> dict[str, int]:
    payload = action.action_key.actor_identity_payload
    if payload is None:
        raise SpecialistFeatureError("v2 local action must retain a typed actor payload")
    fields = dict(payload)
    if any(type(value) is not int for value in fields.values()):
        raise SpecialistFeatureError("actor option parameters must be integers")
    return fields  # type: ignore[return-value]


def _skill_card_token(card_id: int, vocabulary: CardVocabularyV1) -> int:
    """Skill's official card-id zero is a special non-card, never padding."""
    return 1 if card_id == 0 else vocabulary.token_for(card_id)


def semantic_action_from_legal_action_v1(action: ActorVisibleLegalActionV2, view: ActorInformationViewV2, vocabulary: CardVocabularyV1) -> SemanticActionV1:
    """Derive semantic-only action information from a validated binding/action pair."""
    if not isinstance(action, ActorVisibleLegalActionV2):
        raise SpecialistFeatureError("action must be ActorVisibleLegalActionV2")
    fields = _action_fields(action)
    binding = action.binding.core
    option_type = action.action_key.option_type
    if type(option_type) is not int:
        raise SpecialistFeatureError("option_type must be an int")
    return SemanticActionV1(
        selection_type=view.selection_type, selection_context=view.selection_context,
        option_type=option_type, operation=_OPTION_OPERATIONS_V1[option_type],
        source=_semantic_endpoint(binding.source, host=binding.host, view=view, vocabulary=vocabulary),
        target=_semantic_endpoint(binding.target, host=binding.host, view=view, vocabulary=vocabulary),
        host=_semantic_endpoint(binding.host, host=binding.host, view=view, vocabulary=vocabulary),
        number=fields.get("number") if option_type == 0 else None,
        attack_id=fields.get("attackId") if option_type == 13 else None,
        special_condition=fields.get("specialConditionType") if option_type == 16 else None,
        energy_count=fields.get("count") if option_type == 6 else None,
        skill_card_id=(_skill_card_token(fields["cardId"], vocabulary) if option_type == 15 else None),
    )


def _card_bag_count_v1(bag: CardBagV1) -> int:
    """Return the validated serial-free multiplicity represented by one bag."""
    return sum(bag.mask)


def _card_bag_contains_v1(bag: CardBagV1, token: int) -> bool:
    """Test membership without inventing a physical serial/ordinal identity."""
    return any(mask == 1 and value == token for value, mask in zip(bag.tokens, bag.mask))


def _require_static_card_bag_membership_v1(
    bags: Mapping[str, CardBagV1],
    *,
    bag_name: str,
    token: int,
) -> None:
    if not _card_bag_contains_v1(bags[bag_name], token):
        raise SpecialistFeatureError(
            f"whole-input candidate source is absent from {bag_name}"
        )


def _validate_static_projection_coherence_v1(
    *,
    state_scalars: tuple[int, ...],
    single_card_ids: Mapping[str, int],
    card_bags: Mapping[str, CardBagV1],
    pokemon_entities: tuple[PokemonEntityV1, ...],
    candidate_rows: tuple[SemanticActionV1, ...],
) -> None:
    """Bind a serial-free model input's fields to the same observable decision.

    This intentionally checks only relations retained in the feature projection:
    token multiset membership, full public Pokemon snapshots, owner/zone, and
    deterministic count/presence fields.  It never attempts to restore a card
    serial, locator, or distinct physical identity after the projection dropped
    it, so equal aliases and UNK tokens remain valid.
    """
    own_hand = _card_bag_count_v1(card_bags["own_hand"])
    self_discard = _card_bag_count_v1(card_bags["self_discard"])
    opponent_discard = _card_bag_count_v1(card_bags["opponent_discard"])
    deck_reveal = _card_bag_count_v1(card_bags["deck_reveal"])
    looking_visible = _card_bag_count_v1(card_bags["looking_visible"])

    if state_scalars[15] != own_hand:
        raise SpecialistFeatureError("whole-input own_hand count does not match its card bag")
    if state_scalars[18] != self_discard:
        raise SpecialistFeatureError("whole-input self_discard count does not match its card bag")
    if state_scalars[22] != opponent_discard:
        raise SpecialistFeatureError("whole-input opponent_discard count does not match its card bag")

    deck_reveal_available = state_scalars[33]
    if deck_reveal_available == 0 and deck_reveal != 0:
        raise SpecialistFeatureError("whole-input deck_reveal bag is nonempty while unavailable")
    if deck_reveal_available == 1 and deck_reveal != state_scalars[16]:
        raise SpecialistFeatureError("whole-input deck_reveal count does not match self_deck_count")

    looking_available = state_scalars[34]
    looking_hidden = state_scalars[35]
    if looking_available == 0 and (looking_visible != 0 or looking_hidden != 0):
        raise SpecialistFeatureError("whole-input looking bag/count is nonempty while unavailable")
    if looking_available == 1 and looking_visible + looking_hidden > _MAX_CARDS:
        raise SpecialistFeatureError("whole-input looking visible/hidden count exceeds 60")

    for scalar_index, name in ((36, "context"), (37, "effect"), (38, "stadium")):
        if (state_scalars[scalar_index] == 1) != (single_card_ids[name] != 0):
            raise SpecialistFeatureError(
                f"whole-input {name} presence flag does not match its singleton token"
            )

    for owner_role, bench_cap_index in ((1, 39), (2, 40)):
        active_count = sum(
            entity.owner_role == owner_role and entity.zone == "active"
            for entity in pokemon_entities
        )
        bench_count = sum(
            entity.owner_role == owner_role and entity.zone == "bench"
            for entity in pokemon_entities
        )
        if active_count > 1:
            raise SpecialistFeatureError("whole-input public board has more than one active Pokemon")
        if bench_count > state_scalars[bench_cap_index]:
            raise SpecialistFeatureError("whole-input public board exceeds its bench capacity")

    selection_type, selection_context = state_scalars[4:6]
    static_entities = frozenset(pokemon_entities)
    for action in candidate_rows:
        if (
            action.selection_type != selection_type
            or action.selection_context != selection_context
        ):
            raise SpecialistFeatureError(
                "whole-input candidate selection schema does not match state scalars"
            )

        for endpoint in (action.source, action.target, action.host):
            if endpoint.pokemon is not None and endpoint.pokemon not in static_entities:
                raise SpecialistFeatureError(
                    "whole-input candidate board endpoint is absent from static board"
                )

        source = action.source
        if source.visibility == "actor-visible":
            bag_by_zone = {
                "hand": "own_hand",
                "deck-reveal": "deck_reveal",
                "looking": "looking_visible",
            }
            bag_name = bag_by_zone.get(source.semantic_zone)
            if bag_name is not None:
                _require_static_card_bag_membership_v1(
                    card_bags, bag_name=bag_name, token=source.card_id
                )
            elif source.semantic_zone in {"context-card", "effect"}:
                singleton = "context" if source.semantic_zone == "context-card" else "effect"
                if source.card_id != single_card_ids[singleton]:
                    raise SpecialistFeatureError(
                        f"whole-input candidate source does not match {singleton} singleton"
                    )
            else:  # SemanticActionV1 already closes normal rows, retain fail-closed input validation.
                raise SpecialistFeatureError("whole-input candidate has an unsupported actor-visible source zone")
        elif source.visibility == "public-visible":
            if source.semantic_zone == "discard":
                _require_static_card_bag_membership_v1(
                    card_bags,
                    bag_name="self_discard" if source.owner_role == 1 else "opponent_discard",
                    token=source.card_id,
                )
            elif source.semantic_zone == "stadium":
                if source.card_id != single_card_ids["stadium"]:
                    raise SpecialistFeatureError(
                        "whole-input candidate source does not match stadium singleton"
                    )
            elif source.semantic_zone == "pre-evolution":
                if not any(
                    entity.owner_role == source.owner_role
                    and source.card_id in entity.pre_evolution
                    for entity in pokemon_entities
                ):
                    raise SpecialistFeatureError(
                        "whole-input candidate source is absent from matching pre_evolution entity"
                    )
            elif source.semantic_zone in {"active-tool", "bench-tool", "active-energy", "bench-energy"}:
                base_zone, attachment_kind = source.semantic_zone.rsplit("-", 1)
                host = source.pokemon if source.pokemon is not None else action.target.pokemon
                if (
                    host is None
                    or host not in static_entities
                    or host.owner_role != source.owner_role
                    or host.zone != base_zone
                    or host.card_id != source.host_card_id
                ):
                    raise SpecialistFeatureError(
                        "whole-input candidate attachment host is absent from static board"
                    )
                attachments = host.tools if attachment_kind == "tool" else host.energy_cards
                if source.card_id not in attachments:
                    raise SpecialistFeatureError(
                        "whole-input candidate attachment source is absent from its host attachment multiset"
                    )
            elif source.semantic_zone not in {"active", "bench"}:
                # A row-level resolver check excludes unknown public zones.  This
                # branch makes a bypassed/future row fail closed at whole-input depth.
                raise SpecialistFeatureError("whole-input candidate has an unsupported public source zone")

        if (
            action.option_type == 15
            and source.visibility in {"actor-visible", "public-visible"}
            and action.skill_card_id != source.card_id
        ):
            raise SpecialistFeatureError(
                "whole-input visible Skill source does not match its skill_card_id"
            )


@dataclass(frozen=True, slots=True)
class SpecialistModelInputV1:
    schema_version: str
    feature_domain: str
    feature_schema_hash: str
    state_scalars: tuple[int, ...]
    single_card_ids: Mapping[str, int]
    card_bags: Mapping[str, CardBagV1]
    pokemon_entities: tuple[PokemonEntityV1, ...]
    candidate_rows: tuple[SemanticActionV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_INPUT_SCHEMA_V1 or self.feature_domain != ACTOR_VISIBLE_FEATURE_DOMAIN_V1 or self.feature_schema_hash != FEATURE_SCHEMA_HASH_V1:
            raise SpecialistFeatureError("model input schema/domain/hash mismatch")
        if type(self.state_scalars) is not tuple or len(self.state_scalars) != len(STATE_SCALAR_NAMES_V1) or any(type(value) is not int or value < 0 for value in self.state_scalars):
            raise SpecialistFeatureError("model input must contain exactly 41 nonnegative state scalars")
        if self.state_scalars[0] > 2 or not 0 <= self.state_scalars[4] <= 10 or not 0 <= self.state_scalars[5] <= 48:
            raise SpecialistFeatureError("model input categorical state scalars are out of domain")
        if any(self.state_scalars[index] not in (0, 1) for index in (11, 12, 13, 14, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 36, 37, 38)):
            raise SpecialistFeatureError("model input flag scalars must be 0 or 1")
        if not isinstance(self.single_card_ids, Mapping) or set(self.single_card_ids) != {"stadium", "context", "effect"}:
            raise SpecialistFeatureError("single_card_ids has the wrong closed shape")
        if not isinstance(self.card_bags, Mapping) or set(self.card_bags) != {"own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard"}:
            raise SpecialistFeatureError("card_bags has the wrong closed shape")
        if any(type(value) is not int or value < 0 for value in self.single_card_ids.values()):
            raise SpecialistFeatureError("single_card_ids must be nonnegative tokens")
        if any(type(value) is not CardBagV1 for value in self.card_bags.values()):
            raise SpecialistFeatureError("card_bags must contain CardBagV1 values")
        if type(self.pokemon_entities) is not tuple or any(type(value) is not PokemonEntityV1 for value in self.pokemon_entities):
            raise SpecialistFeatureError("pokemon_entities must be a tuple of PokemonEntityV1")
        if type(self.candidate_rows) is not tuple or any(type(value) is not SemanticActionV1 for value in self.candidate_rows):
            raise SpecialistFeatureError("candidate_rows must be a tuple of SemanticActionV1")
        if len(self.pokemon_entities) > 122 or len(self.candidate_rows) > _MAX_CANDIDATES:
            raise SpecialistFeatureError("model input exceeds entity/candidate limits")
        if self.state_scalars[8] != len(self.candidate_rows) or not self.state_scalars[6] <= self.state_scalars[7] <= len(self.candidate_rows):
            raise SpecialistFeatureError("model input selection bounds/candidate count are inconsistent")
        for bag in self.card_bags.values():
            CardBagV1.__post_init__(bag)
        for entity in self.pokemon_entities:
            PokemonEntityV1.__post_init__(entity)
        for row in self.candidate_rows:
            SemanticActionV1.__post_init__(row)
        if tuple(sorted(self.pokemon_entities, key=lambda entity: _canonical_bytes(entity.to_dict()))) != self.pokemon_entities:
            raise SpecialistFeatureError("pokemon entities must be canonically sorted")
        if tuple(sorted(self.candidate_rows, key=lambda row: row.canonical_bytes)) != self.candidate_rows:
            raise SpecialistFeatureError("candidate rows must be canonically sorted")
        _validate_static_projection_coherence_v1(
            state_scalars=self.state_scalars,
            single_card_ids=self.single_card_ids,
            card_bags=self.card_bags,
            pokemon_entities=self.pokemon_entities,
            candidate_rows=self.candidate_rows,
        )
        object.__setattr__(self, "single_card_ids", MappingProxyType({
            name: self.single_card_ids[name] for name in ("stadium", "context", "effect")
        }))
        object.__setattr__(self, "card_bags", MappingProxyType({
            name: self.card_bags[name] for name in ("own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard")
        }))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "feature_domain": self.feature_domain,
            "feature_schema_hash": self.feature_schema_hash, "state_scalars": list(self.state_scalars),
            "single_card_ids": dict(self.single_card_ids),
            "card_bags": {name: bag.to_dict() for name, bag in self.card_bags.items()},
            "pokemon_entities": [entity.to_dict() for entity in self.pokemon_entities],
            "candidate_rows": [row.to_dict() for row in self.candidate_rows],
        }


@dataclass(frozen=True, slots=True)
class ExtractedSpecialistModelInputV1:
    """In-memory model input plus deliberately nonserialized local-ID lookup."""

    model_input: SpecialistModelInputV1
    model_input_id: str
    local_action_id_to_candidate_row_index: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.local_action_id_to_candidate_row_index, Mapping):
            raise SpecialistFeatureError("local lookup must be a mapping")
        if any(type(key) is not str or len(key) != 64 or any(character not in "0123456789abcdef" for character in key) or type(value) is not int for key, value in self.local_action_id_to_candidate_row_index.items()):
            raise SpecialistFeatureError("local lookup has invalid entries")
        if len(self.local_action_id_to_candidate_row_index) != len(self.model_input.candidate_rows) or set(self.local_action_id_to_candidate_row_index.values()) != set(range(len(self.model_input.candidate_rows))):
            raise SpecialistFeatureError("local lookup must cover each candidate row exactly once")
        if self.model_input_id != derive_model_input_id_v1(self.model_input):
            raise SpecialistFeatureError("model_input_id does not verify")
        object.__setattr__(self, "local_action_id_to_candidate_row_index", MappingProxyType(dict(self.local_action_id_to_candidate_row_index)))


def _state_scalars(state: ActorVisibleDecisionStateV2) -> tuple[int, ...]:
    view = state.information_view
    selection = view.private_state.selection_view
    first_role = 0 if view.first_player == -1 else (1 if view.first_player == view.actor else 2)
    looking = selection.looking
    values = (
        first_role, view.step, view.turn, view.turn_action_count, view.selection_type,
        view.selection_context, view.min_count, view.max_count, len(state.legal_actions),
        view.remain_damage_counter, view.remain_energy_cost,
        _strict_bool_token(view.stadium_played, field="stadium_played"),
        _strict_bool_token(view.supporter_played, field="supporter_played"),
        _strict_bool_token(view.energy_attached, field="energy_attached"),
        _strict_bool_token(view.retreated, field="retreated"),
        view.self_player.hand_count, view.self_player.deck_count, view.self_player.prize_count,
        len(view.self_player.discard), view.opponent_player.hand_count,
        view.opponent_player.deck_count, view.opponent_player.prize_count,
        len(view.opponent_player.discard), _strict_bool_token(view.self_player.poisoned, field="self.poisoned"),
        _strict_bool_token(view.self_player.burned, field="self.burned"),
        _strict_bool_token(view.self_player.asleep, field="self.asleep"),
        _strict_bool_token(view.self_player.paralyzed, field="self.paralyzed"),
        _strict_bool_token(view.self_player.confused, field="self.confused"),
        _strict_bool_token(view.opponent_player.poisoned, field="opponent.poisoned"),
        _strict_bool_token(view.opponent_player.burned, field="opponent.burned"),
        _strict_bool_token(view.opponent_player.asleep, field="opponent.asleep"),
        _strict_bool_token(view.opponent_player.paralyzed, field="opponent.paralyzed"),
        _strict_bool_token(view.opponent_player.confused, field="opponent.confused"),
        int(selection.deck_reveal is not None), int(looking is not None),
        0 if looking is None else sum(card is None for card in looking),
        int(selection.context_card is not None), int(selection.effect is not None),
        int(view.board_stadium is not None), view.self_player.bench_max, view.opponent_player.bench_max,
    )
    if len(values) != 41 or any(type(value) is not int or value < 0 for value in values):
        raise SpecialistFeatureError("state scalar extraction produced an invalid 41-vector")
    return values


def derive_model_input_id_v1(model_input: SpecialistModelInputV1) -> str:
    if not isinstance(model_input, SpecialistModelInputV1):
        raise SpecialistFeatureError("model_input must be SpecialistModelInputV1")
    return _hash(_MODEL_INPUT_PREFIX, {
        "feature_domain": model_input.feature_domain,
        "feature_schema_hash": model_input.feature_schema_hash,
        "model_input": model_input.to_dict(),
    })


def canonical_model_input_bytes_v1(model_input: SpecialistModelInputV1) -> bytes:
    """Return the exact runtime/training parity bytes for one validated input."""
    if not isinstance(model_input, SpecialistModelInputV1):
        raise SpecialistFeatureError("model_input must be SpecialistModelInputV1")
    SpecialistModelInputV1.__post_init__(model_input)
    return _canonical_bytes(model_input.to_dict())


def validate_specialist_model_input_v1(model_input: SpecialistModelInputV1) -> SpecialistModelInputV1:
    """Reject legacy/public/student feature domains rather than adapting them."""
    if not isinstance(model_input, SpecialistModelInputV1):
        raise SpecialistFeatureError("model input must be SpecialistModelInputV1")
    SpecialistModelInputV1.__post_init__(model_input)
    return model_input


def extract_specialist_model_input_v1(state: ActorVisibleDecisionStateV2, vocabulary: CardVocabularyV1) -> ExtractedSpecialistModelInputV1:
    """Extract the sole shared serial/ordinal-free model input from typed C1v2 state."""
    if not isinstance(state, ActorVisibleDecisionStateV2):
        raise SpecialistFeatureError("state must be ActorVisibleDecisionStateV2")
    if not isinstance(vocabulary, CardVocabularyV1):
        raise SpecialistFeatureError("vocabulary must be CardVocabularyV1")
    view = state.information_view
    selection = view.private_state.selection_view
    entities = [
        _pokemon_entity(pokemon, zone, view.actor, vocabulary)
        for player in (view.self_player, view.opponent_player)
        for zone, values in (("active", player.active), ("bench", player.bench))
        for pokemon in values if pokemon is not None
    ]
    entities.sort(key=lambda entity: _canonical_bytes(entity.to_dict()))
    semantic_pairs = [
        (semantic_action_from_legal_action_v1(action, view, vocabulary), action.local_action_id)
        for action in state.legal_actions
    ]
    semantic_pairs.sort(key=lambda pair: (pair[0].canonical_bytes, pair[1]))
    rows = tuple(pair[0] for pair in semantic_pairs)
    lookup = MappingProxyType({local_id: index for index, (_row, local_id) in enumerate(semantic_pairs)})
    model_input = SpecialistModelInputV1(
        schema_version=MODEL_INPUT_SCHEMA_V1, feature_domain=ACTOR_VISIBLE_FEATURE_DOMAIN_V1,
        feature_schema_hash=FEATURE_SCHEMA_HASH_V1, state_scalars=_state_scalars(state),
        single_card_ids=MappingProxyType({
            "stadium": vocabulary.token_for(None if view.board_stadium is None else view.board_stadium.card_id),
            "context": vocabulary.token_for(None if selection.context_card is None else selection.context_card.card_id),
            "effect": vocabulary.token_for(None if selection.effect is None else selection.effect.card_id),
        }),
        card_bags=MappingProxyType({
            "own_hand": _card_bag(view.private_state.own_hand, vocabulary),
            "deck_reveal": _card_bag(selection.deck_reveal or (), vocabulary),
            "looking_visible": _card_bag(tuple(card for card in (selection.looking or ()) if card is not None), vocabulary),
            "self_discard": _card_bag(view.self_player.discard, vocabulary),
            "opponent_discard": _card_bag(view.opponent_player.discard, vocabulary),
        }),
        pokemon_entities=tuple(entities), candidate_rows=rows,
    )
    return ExtractedSpecialistModelInputV1(
        model_input=model_input, model_input_id=derive_model_input_id_v1(model_input),
        local_action_id_to_candidate_row_index=lookup,
    )


@dataclass(frozen=True, slots=True)
class SemanticActionClassV1:
    semantic_row: SemanticActionV1
    allowed_alias_count: int

    def __post_init__(self) -> None:
        if type(self.semantic_row) is not SemanticActionV1:
            raise SpecialistFeatureError("semantic_row must be SemanticActionV1")
        SemanticActionV1.__post_init__(self.semantic_row)
        _strict_int(self.allowed_alias_count, field="allowed_alias_count", minimum=1)

    def to_dict(self) -> dict[str, object]:
        return {"semantic_row": self.semantic_row.to_dict(), "allowed_alias_count": self.allowed_alias_count}


@dataclass(frozen=True, slots=True)
class SpecialistStepInputV1:
    schema_version: str
    order_semantics: str
    semantic_prefix: tuple[SemanticActionV1, ...]
    allowed_semantic_classes: tuple[SemanticActionClassV1, ...]
    stop_available: bool

    def __post_init__(self) -> None:
        if self.schema_version != STEP_INPUT_SCHEMA_V1 or self.order_semantics not in {"unordered_set", "ordered_sequence"}:
            raise SpecialistFeatureError("step input has invalid schema/order semantics")
        if type(self.semantic_prefix) is not tuple or any(type(row) is not SemanticActionV1 for row in self.semantic_prefix):
            raise SpecialistFeatureError("semantic_prefix must be a tuple of SemanticActionV1")
        if type(self.allowed_semantic_classes) is not tuple or any(type(item) is not SemanticActionClassV1 for item in self.allowed_semantic_classes):
            raise SpecialistFeatureError("allowed_semantic_classes must be a tuple of SemanticActionClassV1")
        if len(self.semantic_prefix) > _MAX_CANDIDATES or len(self.allowed_semantic_classes) > _MAX_CANDIDATES:
            raise SpecialistFeatureError("step input exceeds 512 rows")
        if type(self.stop_available) is not bool:
            raise SpecialistFeatureError("stop_available must be a bool")
        if self.order_semantics == "unordered_set" and tuple(sorted(self.semantic_prefix, key=lambda row: row.canonical_bytes)) != self.semantic_prefix:
            raise SpecialistFeatureError("unordered semantic_prefix must be canonically sorted")
        if tuple(sorted(self.allowed_semantic_classes, key=lambda item: item.semantic_row.canonical_bytes)) != self.allowed_semantic_classes:
            raise SpecialistFeatureError("allowed semantic classes must be canonically sorted")
        if len({item.semantic_row.canonical_bytes for item in self.allowed_semantic_classes}) != len(self.allowed_semantic_classes):
            raise SpecialistFeatureError("allowed semantic classes must be unique")
        for row in self.semantic_prefix:
            SemanticActionV1.__post_init__(row)
        for item in self.allowed_semantic_classes:
            SemanticActionClassV1.__post_init__(item)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "order_semantics": self.order_semantics,
            "semantic_prefix": [row.to_dict() for row in self.semantic_prefix],
            "allowed_semantic_classes": [item.to_dict() for item in self.allowed_semantic_classes],
            "stop_available": self.stop_available,
        }


def canonical_step_input_bytes_v1(step_input: SpecialistStepInputV1) -> bytes:
    """Return exact canonical bytes consumed identically by runtime and training."""
    if not isinstance(step_input, SpecialistStepInputV1):
        raise SpecialistFeatureError("step_input must be SpecialistStepInputV1")
    SpecialistStepInputV1.__post_init__(step_input)
    return _canonical_bytes(step_input.to_dict())


def _current_semantic_classes_v1(
    extracted: ExtractedSpecialistModelInputV1,
    selected_local_action_ids: tuple[str, ...],
    *,
    ordered: bool,
    minimum: int,
    maximum: int,
) -> tuple[SemanticActionClassV1, ...]:
    """Return the exact class domain for one already-reachable prefix."""
    if len(selected_local_action_ids) >= maximum:
        return ()
    model = extracted.model_input
    lookup = extracted.local_action_id_to_candidate_row_index
    selected = set(selected_local_action_ids)
    remaining = [
        (model.candidate_rows[index], local_id)
        for local_id, index in lookup.items()
        if local_id not in selected
    ]
    if not ordered and selected_local_action_ids:
        last_key = max(
            model.candidate_rows[lookup[local_id]].canonical_bytes
            for local_id in selected_local_action_ids
        )
        remaining = [pair for pair in remaining if pair[0].canonical_bytes >= last_key]

    if not ordered and len(selected_local_action_ids) < minimum:
        feasible: list[tuple[SemanticActionV1, str]] = []
        selected_count = len(selected_local_action_ids)
        for candidate_row, candidate_id in remaining:
            candidate_key = candidate_row.canonical_bytes
            later_count = sum(
                1
                for later_row, later_id in remaining
                if later_id != candidate_id and later_row.canonical_bytes >= candidate_key
            )
            if selected_count + 1 + later_count >= minimum:
                feasible.append((candidate_row, candidate_id))
        remaining = feasible

    grouped: dict[bytes, list[SemanticActionV1]] = {}
    for row, _local_id in remaining:
        grouped.setdefault(row.canonical_bytes, []).append(row)
    return tuple(
        SemanticActionClassV1(semantic_row=group[0], allowed_alias_count=len(group))
        for _key, group in sorted(grouped.items())
    )


def _canonical_unordered_selected_ids_v1(
    extracted: ExtractedSpecialistModelInputV1,
    selected_local_action_ids: tuple[str, ...],
) -> tuple[str, ...]:
    lookup = extracted.local_action_id_to_candidate_row_index
    return tuple(sorted(
        selected_local_action_ids,
        key=lambda local_id: (
            extracted.model_input.candidate_rows[lookup[local_id]].canonical_bytes,
            local_id,
        ),
    ))


def _require_reachable_prefix_v1(
    extracted: ExtractedSpecialistModelInputV1,
    selected_local_action_ids: tuple[str, ...],
    *,
    ordered: bool,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    replay_ids = (
        selected_local_action_ids
        if ordered
        else _canonical_unordered_selected_ids_v1(extracted, selected_local_action_ids)
    )
    reached: tuple[str, ...] = ()
    lookup = extracted.local_action_id_to_candidate_row_index
    for local_id in replay_ids:
        row = extracted.model_input.candidate_rows[lookup[local_id]]
        allowed = _current_semantic_classes_v1(
            extracted, reached, ordered=ordered, minimum=minimum, maximum=maximum
        )
        if row.canonical_bytes not in {
            item.semantic_row.canonical_bytes for item in allowed
        }:
            raise SpecialistFeatureError("semantic prefix is unreachable under canonical legality")
        reached = (*reached, local_id)
    return replay_ids


def build_specialist_step_input_v1(extracted: ExtractedSpecialistModelInputV1, selected_local_action_ids: tuple[str, ...]) -> SpecialistStepInputV1:
    """Build canonical semantic legality for both training and runtime prefixes."""
    if not isinstance(extracted, ExtractedSpecialistModelInputV1):
        raise SpecialistFeatureError("extracted input has the wrong type")
    if not isinstance(selected_local_action_ids, tuple) or any(type(value) is not str for value in selected_local_action_ids):
        raise SpecialistFeatureError("selected_local_action_ids must be a tuple of strings")
    model = extracted.model_input
    selection_type, context, minimum, maximum = model.state_scalars[4:8]
    try:
        ordered = is_ordered_selection(selection_type, context)
    except ValueError as exc:
        raise SpecialistFeatureError("model input has unrecognized selection schema") from exc
    if len(selected_local_action_ids) != len(set(selected_local_action_ids)):
        raise SpecialistFeatureError("a local candidate may be selected only once")
    lookup = extracted.local_action_id_to_candidate_row_index
    if any(local_id not in lookup for local_id in selected_local_action_ids):
        raise SpecialistFeatureError("selected local ID is not legal for this decision")
    if len(selected_local_action_ids) > maximum:
        raise SpecialistFeatureError("semantic prefix exceeds max_count")
    replay_ids = _require_reachable_prefix_v1(
        extracted,
        selected_local_action_ids,
        ordered=ordered,
        minimum=minimum,
        maximum=maximum,
    )
    selected_rows = tuple(model.candidate_rows[lookup[local_id]] for local_id in replay_ids)
    prefix = selected_rows if ordered else tuple(sorted(selected_rows, key=lambda row: row.canonical_bytes))
    classes = _current_semantic_classes_v1(
        extracted,
        replay_ids,
        ordered=ordered,
        minimum=minimum,
        maximum=maximum,
    )
    return SpecialistStepInputV1(
        schema_version=STEP_INPUT_SCHEMA_V1,
        order_semantics="ordered_sequence" if ordered else "unordered_set",
        semantic_prefix=prefix, allowed_semantic_classes=classes,
        stop_available=len(selected_local_action_ids) >= minimum,
    )


@dataclass(frozen=True, slots=True)
class SpecialistStepLogitsV1:
    semantic_logits: tuple[float, ...]
    stop_logit: float | None

    def __post_init__(self) -> None:
        if type(self.semantic_logits) is not tuple or any(type(logit) not in (int, float) or type(logit) is bool or not math.isfinite(logit) for logit in self.semantic_logits):
            raise SpecialistFeatureError("semantic logits must be a tuple of finite numbers")
        if self.stop_logit is not None and (type(self.stop_logit) not in (int, float) or type(self.stop_logit) is bool or not math.isfinite(self.stop_logit)):
            raise SpecialistFeatureError("STOP logit must be null or a finite number")


@runtime_checkable
class SpecialistStepLogitPolicyV1(Protocol):
    def logits(self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1) -> SpecialistStepLogitsV1:
        """Return exactly one finite logit per class and an optional STOP logit."""


@dataclass(frozen=True, slots=True)
class EvaluatedSpecialistStepV1:
    step_input: SpecialistStepInputV1
    semantic_logits: tuple[float, ...]
    stop_logit: float | None
    forced_stop: bool


@dataclass(frozen=True, slots=True)
class SemanticCompleteActionProbabilityV1:
    """One semantic complete action and its normalized autoregressive mass."""

    semantic_selection: tuple[SemanticActionV1, ...]
    probability: float
    log_probability: float

    def __post_init__(self) -> None:
        if type(self.semantic_selection) is not tuple or any(
            type(row) is not SemanticActionV1 for row in self.semantic_selection
        ):
            raise SpecialistFeatureError("semantic complete selection must be a row tuple")
        for row in self.semantic_selection:
            SemanticActionV1.__post_init__(row)
        if type(self.probability) is not float or not math.isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise SpecialistFeatureError("semantic complete-action probability is invalid")
        if type(self.log_probability) is not float or not math.isfinite(self.log_probability):
            raise SpecialistFeatureError("semantic complete-action log probability is invalid")


def evaluate_specialist_step_v1(policy: SpecialistStepLogitPolicyV1, extracted: ExtractedSpecialistModelInputV1, step_input: SpecialistStepInputV1) -> EvaluatedSpecialistStepV1:
    """Validate the only policy interface; sole STOP is deliberately model-free."""
    if not isinstance(step_input, SpecialistStepInputV1):
        raise SpecialistFeatureError("step_input must be SpecialistStepInputV1")
    if not step_input.allowed_semantic_classes and step_input.stop_available:
        return EvaluatedSpecialistStepV1(step_input, (), None, True)
    result = policy.logits(extracted.model_input, step_input)
    if not isinstance(result, SpecialistStepLogitsV1):
        raise SpecialistFeatureError("policy must return SpecialistStepLogitsV1")
    if len(result.semantic_logits) != len(step_input.allowed_semantic_classes):
        raise SpecialistFeatureError("policy returned the wrong semantic logit arity")
    if any(type(logit) not in (int, float) or not math.isfinite(logit) for logit in result.semantic_logits):
        raise SpecialistFeatureError("policy semantic logits must be finite")
    if step_input.stop_available:
        if type(result.stop_logit) not in (int, float) or not math.isfinite(result.stop_logit):
            raise SpecialistFeatureError("policy STOP logit must be finite when STOP is legal")
    elif result.stop_logit is not None:
        raise SpecialistFeatureError("policy returned STOP when STOP is not legal")
    return EvaluatedSpecialistStepV1(step_input, tuple(float(logit) for logit in result.semantic_logits), None if result.stop_logit is None else float(result.stop_logit), False)


def _normalized_log_probabilities_v1(logits: tuple[float, ...]) -> tuple[float, ...]:
    if not logits:
        raise SpecialistFeatureError("cannot normalize an empty logit domain")
    maximum = max(logits)
    denominator = math.fsum(math.exp(logit - maximum) for logit in logits)
    log_denominator = maximum + math.log(denominator)
    return tuple(logit - log_denominator for logit in logits)


def _representable_complete_probability_v1(log_probability: float) -> float:
    """Materialize a finite complete-action log mass without erasing support."""
    if not math.isfinite(log_probability):
        raise SpecialistFeatureError("semantic complete-action log probability is not finite")
    probability = float(math.exp(log_probability))
    if not math.isfinite(probability) or probability <= 0.0:
        raise SpecialistFeatureError(
            "semantic complete-action probability underflowed a legal finite-logit path"
        )
    return probability


def enumerate_semantic_complete_action_distribution_v1(
    extracted: ExtractedSpecialistModelInputV1,
    policy: SpecialistStepLogitPolicyV1,
) -> tuple[SemanticCompleteActionProbabilityV1, ...]:
    """Enumerate a small exact class-level distribution through the shared legality primitive.

    Each unordered semantic multiset has one nondecreasing path.  Alias choice
    happens only after the class choice and therefore never multiplies class
    probability by physical-card multiplicity.
    """
    if not isinstance(extracted, ExtractedSpecialistModelInputV1):
        raise SpecialistFeatureError("extracted input has the wrong type")
    completed: list[SemanticCompleteActionProbabilityV1] = []

    def append_completed(
        semantic_selection: tuple[SemanticActionV1, ...],
        log_probability: float,
    ) -> None:
        # Check before materializing the next row: an enumerator must never
        # briefly retain 65,537 complete actions before noticing the cap.
        if len(completed) >= _MAX_ENUMERATED_SEMANTIC_ACTIONS:
            raise SpecialistFeatureError("semantic complete-action enumeration exceeds 65536")
        completed.append(SemanticCompleteActionProbabilityV1(
            semantic_selection=semantic_selection,
            probability=_representable_complete_probability_v1(log_probability),
            log_probability=float(log_probability),
        ))

    def visit(selected_ids: tuple[str, ...], log_probability: float) -> None:
        step = build_specialist_step_input_v1(extracted, selected_ids)
        if not step.allowed_semantic_classes and step.stop_available:
            append_completed(step.semantic_prefix, log_probability)
            return
        evaluated = evaluate_specialist_step_v1(policy, extracted, step)
        logits = evaluated.semantic_logits + (
            (() if evaluated.stop_logit is None else (evaluated.stop_logit,))
        )
        log_probabilities = _normalized_log_probabilities_v1(logits)
        for item, token_log_probability in zip(
            step.allowed_semantic_classes, log_probabilities[:len(step.allowed_semantic_classes)]
        ):
            local_id = choose_lexicographic_alias_v1(
                extracted, selected_ids, item.semantic_row
            )
            visit((*selected_ids, local_id), log_probability + token_log_probability)
        if step.stop_available:
            assert evaluated.stop_logit is not None
            stop_log_probability = log_probabilities[-1]
            append_completed(
                step.semantic_prefix, log_probability + stop_log_probability
            )

    visit((), 0.0)
    if not completed or len(completed) > _MAX_ENUMERATED_SEMANTIC_ACTIONS:
        raise SpecialistFeatureError("semantic complete-action enumeration is empty or exceeds 65536")
    completed.sort(key=lambda item: _canonical_bytes([
        row.to_dict() for row in item.semantic_selection
    ]))
    total = math.fsum(item.probability for item in completed)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SpecialistFeatureError("semantic complete-action distribution is not normalized")
    return tuple(completed)


def choose_lexicographic_alias_v1(extracted: ExtractedSpecialistModelInputV1, selected_local_action_ids: tuple[str, ...], semantic_row: SemanticActionV1) -> str:
    """Choose the physical alias only after a semantic class has been selected."""
    step = build_specialist_step_input_v1(extracted, selected_local_action_ids)
    if semantic_row not in tuple(item.semantic_row for item in step.allowed_semantic_classes):
        raise SpecialistFeatureError("semantic row is not currently legal")
    selected = set(selected_local_action_ids)
    candidates = [
        local_id for local_id, index in extracted.local_action_id_to_candidate_row_index.items()
        if local_id not in selected and extracted.model_input.candidate_rows[index] == semantic_row
    ]
    if not candidates:
        raise SpecialistFeatureError("semantic class has no legal aliases")
    return min(candidates)


__all__ = [
    "ACTOR_VISIBLE_FEATURE_DOMAIN_V1", "CARD_VOCABULARY_SCHEMA_DESCRIPTOR_V1", "CARD_VOCABULARY_SCHEMA_HASH_V1", "CARD_VOCABULARY_SCHEMA_V1", "CardBagV1", "CardVocabularyV1",
    "CollatedCandidateRowsV1", "CollatedStateScalarsV1",
    "EvaluatedSpecialistStepV1", "ExtractedSpecialistModelInputV1", "FEATURE_SCHEMA_DESCRIPTOR_V1",
    "FEATURE_SCHEMA_HASH_V1", "FEATURE_SCHEMA_CANONICAL_BYTES_V1", "MODEL_INPUT_SCHEMA_V1", "SEMANTIC_ACTION_SCHEMA_V1", "STEP_INPUT_SCHEMA_V1",
    "SemanticActionClassV1", "SemanticActionV1", "SemanticCompleteActionProbabilityV1", "SemanticEndpointV1", "SpecialistFeatureError", "SpecialistModelInputV1",
    "SpecialistStepInputV1", "SpecialistStepLogitPolicyV1", "SpecialistStepLogitsV1",
    "build_specialist_step_input_v1", "canonical_model_input_bytes_v1", "canonical_step_input_bytes_v1", "choose_lexicographic_alias_v1", "collate_candidate_rows_v1", "collate_state_scalars_v1", "derive_model_input_id_v1",
    "enumerate_semantic_complete_action_distribution_v1", "evaluate_specialist_step_v1", "extract_specialist_model_input_v1", "make_test_card_vocabulary_v1",
    "require_production_card_vocabulary_v1", "semantic_action_from_legal_action_v1", "validate_specialist_model_input_v1",
]
