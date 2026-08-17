"""Official-card-data-only generator for research self-owned CG decks.

The generator intentionally has no parent-deck input.  It consumes an exact
card-data CSV and a small, versioned role specification, then emits a
deterministic 60-card multiset with an auditable identity.  It is a research
candidate boundary: no function here grants training, promotion, or
submission authority and no function touches the repository root deck.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import random
from types import MappingProxyType
from typing import Collection, Mapping, Sequence


SCHEMA_VERSION_V1 = "self-owned-cg-deck-v1"
SPEC_SCHEMA_VERSION_V1 = "self-owned-cg-deck-spec-v1"
_CARD_FIELDS = {
    "Card ID",
    "Card Name",
    "Stage (Pokémon)/Type (Energy and Trainer)",
    "Rule",
    "Previous stage",
}
_SPEC_FIELDS = {
    "schema_version",
    "archetype_id",
    "max_non_energy_copies",
    "require_ace_spec_count",
    "required_basic_pokemon_min",
    "required_basic_energy_min",
    "roles",
}
_ROLE_FIELDS = {"role", "card_ids", "count"}
_AUTHORITY_FALSE_V1 = MappingProxyType(
    {
        "training_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
    }
)


class SelfOwnedDeckV1Error(ValueError):
    """Raised when a scratch deck cannot satisfy a hard contract."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SelfOwnedDeckV1Error("value cannot be represented as canonical JSON") from exc


def canonical_deck_sha256_v1(card_ids: Sequence[int]) -> str:
    """Return the order-independent SHA-256 for a card-ID multiset."""
    try:
        values = list(card_ids)
    except TypeError as exc:
        raise SelfOwnedDeckV1Error("card IDs must be an iterable of integers") from exc
    if any(type(card_id) is not int or isinstance(card_id, bool) or card_id <= 0 for card_id in values):
        raise SelfOwnedDeckV1Error("card IDs must be positive integers")
    return _sha256_bytes(_canonical_json_bytes(sorted(values)))


def deck_file_bytes_v1(card_ids: Sequence[int]) -> bytes:
    """Serialize one-card-per-line deck bytes without consulting a path."""
    values = tuple(card_ids)
    if any(type(card_id) is not int or card_id <= 0 for card_id in values):
        raise SelfOwnedDeckV1Error("card IDs must be positive integers")
    return ("\n".join(str(card_id) for card_id in values) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class CardRecordV1:
    card_id: int
    name: str
    stage_type: str
    rule: str
    previous_stage: str | None
    is_basic_pokemon: bool
    is_basic_energy: bool
    is_ace_spec: bool


@dataclass(frozen=True, slots=True)
class CardCatalogV1:
    cards_by_id: Mapping[int, CardRecordV1]
    source_path: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class RoleSlotV1:
    role: str
    card_ids: tuple[int, ...]
    count: int


@dataclass(frozen=True, slots=True)
class SelfOwnedDeckSpecV1:
    schema_version: str
    archetype_id: str
    max_non_energy_copies: int
    require_ace_spec_count: int
    required_basic_pokemon_min: int
    required_basic_energy_min: int
    roles: tuple[RoleSlotV1, ...]
    source_path: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class SelfOwnedDeckCandidateV1:
    schema_version: str
    candidate_id: str
    archetype_id: str
    card_ids: tuple[int, ...]
    deck_file_sha256: str
    canonical_deck_sha256: str
    parent_deck: None
    seed: int
    candidate_ordinal: int
    research_only: bool
    authority: Mapping[str, bool]

    def to_manifest_dict(
        self,
        *,
        card_database_sha256: str,
        role_spec_sha256: str,
        generator_source_sha256: str,
    ) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "archetype_id": self.archetype_id,
            "card_ids": list(self.card_ids),
            "deck_file_sha256": self.deck_file_sha256,
            "canonical_deck_sha256": self.canonical_deck_sha256,
            "parent_deck": None,
            "public_parent_read": False,
            "seed": self.seed,
            "candidate_ordinal": self.candidate_ordinal,
            "generator_source_sha256": generator_source_sha256,
            "role_spec_sha256": role_spec_sha256,
            "card_database_sha256": card_database_sha256,
            "research_only": True,
            "authority": dict(self.authority),
        }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SelfOwnedDeckV1Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedDeckV1Error(f"cannot read JSON file: {path}") from exc


def _positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int or (value < 0 if allow_zero else value <= 0):
        raise SelfOwnedDeckV1Error(f"{field} must be a positive integer")
    return value


def load_card_catalog_v1(card_database_path: str | Path) -> CardCatalogV1:
    """Load and hash the official CSV without consulting any deck snapshot."""
    path = Path(card_database_path).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SelfOwnedDeckV1Error(f"cannot read card database: {path}") from exc
    try:
        # ``Effect Explanation`` contains quoted newlines.  Feeding a StringIO
        # stream (rather than ``splitlines``) preserves CSV record boundaries.
        rows = list(csv.DictReader(StringIO(raw.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SelfOwnedDeckV1Error(f"card database is not valid UTF-8 CSV: {path}") from exc
    if not rows or not _CARD_FIELDS.issubset(rows[0]):
        raise SelfOwnedDeckV1Error("card database is missing required fields")
    records: dict[int, CardRecordV1] = {}
    for row_index, row in enumerate(rows, start=2):
        try:
            card_id = int(str(row["Card ID"]).strip())
        except (TypeError, ValueError) as exc:
            raise SelfOwnedDeckV1Error(f"invalid Card ID at CSV row {row_index}") from exc
        name = str(row["Card Name"]).strip()
        stage_type = str(row["Stage (Pokémon)/Type (Energy and Trainer)"]).strip()
        rule = str(row["Rule"]).strip()
        previous_raw = str(row["Previous stage"]).strip()
        previous_stage = None if previous_raw.lower() in {"", "n/a", "na"} else previous_raw
        if card_id <= 0:
            raise SelfOwnedDeckV1Error(f"non-positive Card ID at row {row_index}")
        # The official CSV is one row per attack/ability, so a card ID can
        # legitimately occur multiple times.  Identity columns must agree;
        # the first row supplies the single catalog record.
        if card_id in records:
            previous = records[card_id]
            if (
                previous.name,
                previous.stage_type,
                previous.rule,
                previous.previous_stage,
            ) != (name, stage_type, rule, previous_stage):
                raise SelfOwnedDeckV1Error(f"conflicting card identity at row {row_index}")
            continue
        records[card_id] = CardRecordV1(
            card_id=card_id,
            name=name,
            stage_type=stage_type,
            rule=rule,
            previous_stage=previous_stage,
            is_basic_pokemon=stage_type == "Basic Pokémon",
            is_basic_energy=stage_type == "Basic Energy",
            is_ace_spec=rule.upper() == "ACE SPEC",
        )
    return CardCatalogV1(
        cards_by_id=MappingProxyType(records),
        source_path=str(path),
        source_sha256=_sha256_bytes(raw),
    )


def load_self_owned_deck_spec_v1(spec_path: str | Path) -> SelfOwnedDeckSpecV1:
    """Load a strict, content-addressed role specification."""
    path = Path(spec_path).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SelfOwnedDeckV1Error(f"cannot read deck specification: {path}") from exc
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise SelfOwnedDeckV1Error("deck specification must be an object")
    if set(payload) != _SPEC_FIELDS:
        raise SelfOwnedDeckV1Error("deck specification has unexpected or missing fields")
    if payload["schema_version"] != SPEC_SCHEMA_VERSION_V1:
        raise SelfOwnedDeckV1Error("unsupported deck specification schema")
    archetype_id = payload["archetype_id"]
    if not isinstance(archetype_id, str) or not archetype_id.strip():
        raise SelfOwnedDeckV1Error("archetype_id must be non-empty")
    max_copies = _positive_int(payload["max_non_energy_copies"], "max_non_energy_copies")
    ace_count = _positive_int(payload["require_ace_spec_count"], "require_ace_spec_count", allow_zero=True)
    basic_min = _positive_int(payload["required_basic_pokemon_min"], "required_basic_pokemon_min", allow_zero=True)
    energy_min = _positive_int(payload["required_basic_energy_min"], "required_basic_energy_min", allow_zero=True)
    raw_roles = payload["roles"]
    if not isinstance(raw_roles, list) or not raw_roles:
        raise SelfOwnedDeckV1Error("roles must be a non-empty list")
    roles: list[RoleSlotV1] = []
    for index, raw_role in enumerate(raw_roles):
        if not isinstance(raw_role, Mapping) or set(raw_role) != _ROLE_FIELDS:
            raise SelfOwnedDeckV1Error(f"role {index} has unexpected or missing fields")
        role = raw_role["role"]
        if not isinstance(role, str) or not role.strip():
            raise SelfOwnedDeckV1Error(f"role {index} must have a non-empty name")
        count = _positive_int(raw_role["count"], f"roles[{index}].count")
        raw_ids = raw_role["card_ids"]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise SelfOwnedDeckV1Error(f"roles[{index}].card_ids must be non-empty")
        card_ids = tuple(_positive_int(value, f"roles[{index}].card_ids") for value in raw_ids)
        if len(card_ids) != len(set(card_ids)):
            raise SelfOwnedDeckV1Error(f"roles[{index}].card_ids must be unique")
        roles.append(RoleSlotV1(role=role, card_ids=card_ids, count=count))
    if sum(role.count for role in roles) != 60:
        raise SelfOwnedDeckV1Error("role counts must sum to exactly 60")
    return SelfOwnedDeckSpecV1(
        schema_version=SPEC_SCHEMA_VERSION_V1,
        archetype_id=archetype_id,
        max_non_energy_copies=max_copies,
        require_ace_spec_count=ace_count,
        required_basic_pokemon_min=basic_min,
        required_basic_energy_min=energy_min,
        roles=tuple(roles),
        source_path=str(path),
        source_sha256=_sha256_bytes(raw),
    )


def validate_self_owned_deck_v1(
    card_ids: Sequence[int],
    catalog: CardCatalogV1,
    spec: SelfOwnedDeckSpecV1,
) -> None:
    """Apply the static self-owned deck contract and fail closed."""
    values = tuple(card_ids)
    if len(values) != 60:
        raise SelfOwnedDeckV1Error(f"deck must contain exactly 60 cards, got {len(values)}")
    if any(type(card_id) is not int or card_id <= 0 for card_id in values):
        raise SelfOwnedDeckV1Error("deck card IDs must be positive integers")
    unknown = sorted(set(values).difference(catalog.cards_by_id))
    if unknown:
        raise SelfOwnedDeckV1Error(f"unknown card IDs: {unknown}")
    records = [catalog.cards_by_id[card_id] for card_id in values]
    counts_by_name = Counter(record.name for record in records if not record.is_basic_energy)
    over_copy = sorted(
        (name, count) for name, count in counts_by_name.items() if count > spec.max_non_energy_copies
    )
    if over_copy:
        raise SelfOwnedDeckV1Error(f"same-name copy cap exceeded: {over_copy}")
    ace_count = sum(record.is_ace_spec for record in records)
    if ace_count != spec.require_ace_spec_count:
        raise SelfOwnedDeckV1Error(
            f"ACE SPEC count must be {spec.require_ace_spec_count}, got {ace_count}"
        )
    if sum(record.is_basic_pokemon for record in records) < spec.required_basic_pokemon_min:
        raise SelfOwnedDeckV1Error("deck has too few Basic Pokémon")
    if sum(record.is_basic_energy for record in records) < spec.required_basic_energy_min:
        raise SelfOwnedDeckV1Error("deck has too little Basic Energy")
    names = {record.name for record in records}
    for record in records:
        if record.previous_stage is not None and record.previous_stage not in names:
            raise SelfOwnedDeckV1Error(
                f"evolution parent missing for {record.name}: {record.previous_stage}"
            )


def _candidate_id(spec: SelfOwnedDeckSpecV1, card_ids: Sequence[int], seed: int, ordinal: int) -> str:
    digest = canonical_deck_sha256_v1(card_ids)
    return f"{spec.archetype_id}-s{seed}-o{ordinal:04d}-{digest[:12]}"


def generate_self_owned_deck_v1(
    *,
    catalog: CardCatalogV1,
    spec: SelfOwnedDeckSpecV1,
    seed: int,
    ordinal: int,
    forbidden_canonical_hashes: Collection[str] = (),
) -> SelfOwnedDeckCandidateV1:
    """Generate one deterministic scratch candidate without a deck parent."""
    if type(seed) is not int or isinstance(seed, bool):
        raise SelfOwnedDeckV1Error("seed must be an integer")
    if type(ordinal) is not int or isinstance(ordinal, bool) or ordinal < 0:
        raise SelfOwnedDeckV1Error("ordinal must be a non-negative integer")
    forbidden = {str(value) for value in forbidden_canonical_hashes}
    collisions = 0
    for attempt in range(128):
        rng = random.Random((seed * 1_000_003) ^ (ordinal * 97) ^ attempt)
        cards: list[int] = []
        for role in spec.roles:
            for _ in range(role.count):
                cards.append(role.card_ids[rng.randrange(len(role.card_ids))])
        rng.shuffle(cards)
        try:
            validate_self_owned_deck_v1(cards, catalog, spec)
        except SelfOwnedDeckV1Error:
            continue
        canonical = canonical_deck_sha256_v1(cards)
        if canonical in forbidden:
            collisions += 1
            continue
        raw = deck_file_bytes_v1(cards)
        return SelfOwnedDeckCandidateV1(
            schema_version=SCHEMA_VERSION_V1,
            candidate_id=_candidate_id(spec, cards, seed, ordinal),
            archetype_id=spec.archetype_id,
            card_ids=tuple(cards),
            deck_file_sha256=_sha256_bytes(raw),
            canonical_deck_sha256=canonical,
            parent_deck=None,
            seed=seed,
            candidate_ordinal=ordinal,
            research_only=True,
            authority=_AUTHORITY_FALSE_V1,
        )
    if collisions:
        raise SelfOwnedDeckV1Error("canonical deck collision prevented candidate generation")
    raise SelfOwnedDeckV1Error("could not generate a legal deck within retry bound")


__all__ = [
    "SCHEMA_VERSION_V1",
    "SPEC_SCHEMA_VERSION_V1",
    "CardCatalogV1",
    "CardRecordV1",
    "RoleSlotV1",
    "SelfOwnedDeckCandidateV1",
    "SelfOwnedDeckSpecV1",
    "SelfOwnedDeckV1Error",
    "canonical_deck_sha256_v1",
    "deck_file_bytes_v1",
    "generate_self_owned_deck_v1",
    "load_card_catalog_v1",
    "load_self_owned_deck_spec_v1",
    "validate_self_owned_deck_v1",
]
