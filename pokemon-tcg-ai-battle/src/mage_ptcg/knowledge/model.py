"""Immutable, validated data model for the C2a Knowledge Pack v0."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, TypeAlias


SCHEMA_VERSION = "knowledge-pack-v0"
ACTION_KEY_SCHEMA_VERSION = "decision-state-v1"
JsonScalar: TypeAlias = str | int | float | bool | None


class KnowledgeValidationError(ValueError):
    """Raised when a Knowledge Pack is malformed or internally inconsistent."""


def canonical_json_bytes(value: object) -> bytes:
    """Encode a JSON-compatible value in the one v0 canonical representation."""
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise KnowledgeValidationError("value is not canonical JSON") from exc


def content_hash(value: object) -> str:
    """Return the domain-separated SHA-256 for canonical Knowledge content."""
    return hashlib.sha256(b"mage_ptcg.knowledge:v0\0" + canonical_json_bytes(value)).hexdigest()


def _strict_probability(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KnowledgeValidationError(f"{field} must be a number and must not be bool")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise KnowledgeValidationError(f"{field} must be within [0.0, 1.0]")
    return result


def _strict_card_id(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise KnowledgeValidationError(f"{field} must be a positive int and must not be bool")
    return value


class RoleTag(str, Enum):
    """The intentionally small v0 Team Deck role vocabulary."""

    CORE = "CORE"
    ENGINE = "ENGINE"
    FLEX = "FLEX"
    TECH = "TECH"


@dataclass(frozen=True, slots=True)
class KnowledgeConfidence:
    """Independent validity, evidence support, and source freshness signals."""

    validity: float
    support: float
    freshness: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "validity", _strict_probability(self.validity, "validity"))
        object.__setattr__(self, "support", _strict_probability(self.support, "support"))
        object.__setattr__(self, "freshness", _strict_probability(self.freshness, "freshness"))

    def to_payload(self) -> dict[str, float]:
        """Return the canonical JSON payload."""
        return {"freshness": self.freshness, "support": self.support, "validity": self.validity}


@dataclass(frozen=True, slots=True)
class DeckEntry:
    """One canonical Team Deck card count and its conservative role annotation."""

    card_id: int
    count: int
    role: RoleTag
    role_confidence: KnowledgeConfidence
    source_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "card_id", _strict_card_id(self.card_id, "card_id"))
        if type(self.count) is not int or self.count <= 0:
            raise KnowledgeValidationError("count must be a positive int and must not be bool")
        if not isinstance(self.role, RoleTag):
            raise KnowledgeValidationError("role must be a RoleTag")
        if not isinstance(self.role_confidence, KnowledgeConfidence):
            raise KnowledgeValidationError("role_confidence must be KnowledgeConfidence")
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise KnowledgeValidationError("source_ref must be a non-empty string")

    def to_payload(self) -> dict[str, object]:
        """Return the canonical JSON payload."""
        return {
            "card_id": self.card_id,
            "count": self.count,
            "role": self.role.value,
            "role_confidence": self.role_confidence.to_payload(),
            "source_ref": self.source_ref,
        }


def deck_identity_from_entries(entries: tuple[DeckEntry, ...]) -> str:
    """Return the stable identity of a 60-card multiset, independent of roles."""
    return "deck-" + content_hash([[entry.card_id, entry.count] for entry in entries])[:20]


def deck_identity_from_card_ids(card_ids: object) -> str:
    """Validate 60 card IDs and return their order-independent Team Deck identity."""
    if not isinstance(card_ids, (list, tuple)) or len(card_ids) != 60:
        raise KnowledgeValidationError("deck must contain exactly 60 card IDs")
    counts: dict[int, int] = {}
    for index, card_id in enumerate(card_ids):
        valid_id = _strict_card_id(card_id, f"deck[{index}]")
        counts[valid_id] = counts.get(valid_id, 0) + 1
    entries = tuple(
        DeckEntry(
            card_id=card_id,
            count=count,
            role=RoleTag.FLEX,
            role_confidence=KnowledgeConfidence(1.0, 0.0, 0.0),
            source_ref="identity-only",
        )
        for card_id, count in sorted(counts.items())
    )
    return deck_identity_from_entries(entries)


@dataclass(frozen=True, slots=True)
class TeamDeck:
    """A complete, canonical, duplicate-free 60-card Team Deck."""

    deck_id: str
    entries: tuple[DeckEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.deck_id, str) or not self.deck_id:
            raise KnowledgeValidationError("deck_id must be a non-empty string")
        if not isinstance(self.entries, tuple) or not self.entries:
            raise KnowledgeValidationError("entries must be a non-empty tuple")
        previous = 0
        total = 0
        for entry in self.entries:
            if not isinstance(entry, DeckEntry):
                raise KnowledgeValidationError("entries must contain DeckEntry values")
            if entry.card_id <= previous:
                raise KnowledgeValidationError("deck entries must be strictly sorted by card_id")
            previous = entry.card_id
            total += entry.count
        if total != 60:
            raise KnowledgeValidationError(f"deck must contain exactly 60 cards, got {total}")
        expected_id = deck_identity_from_entries(self.entries)
        if self.deck_id != expected_id:
            raise KnowledgeValidationError("deck_id does not match canonical card counts")

    def to_payload(self) -> dict[str, object]:
        """Return the canonical JSON payload."""
        return {"deck_id": self.deck_id, "entries": [entry.to_payload() for entry in self.entries]}


@dataclass(frozen=True, slots=True)
class ActionPrior:
    """A deterministic soft prior predicate over the existing ActionKey contract."""

    rule_id: str
    score: float
    priority: int
    confidence: KnowledgeConfidence
    source_ref: str
    selection_type: JsonScalar | None = None
    context: JsonScalar | None = None
    option_type: JsonScalar | None = None
    semantic_operation: str | None = None
    action_key_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id:
            raise KnowledgeValidationError("rule_id must be a non-empty string")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise KnowledgeValidationError("score must be numeric and must not be bool")
        if not math.isfinite(float(self.score)):
            raise KnowledgeValidationError("score must be finite")
        object.__setattr__(self, "score", float(self.score))
        if type(self.priority) is not int:
            raise KnowledgeValidationError("priority must be an int and must not be bool")
        if not isinstance(self.confidence, KnowledgeConfidence):
            raise KnowledgeValidationError("confidence must be KnowledgeConfidence")
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise KnowledgeValidationError("source_ref must be a non-empty string")
        for name in ("selection_type", "context", "option_type"):
            value = getattr(self, name)
            if value is not None and type(value) not in (str, int, float, bool):
                raise KnowledgeValidationError(f"{name} must be a JSON scalar or None")
        if self.semantic_operation is not None and not isinstance(self.semantic_operation, str):
            raise KnowledgeValidationError("semantic_operation must be a string or None")
        if self.action_key_digest is not None and (
            not isinstance(self.action_key_digest, str)
            or len(self.action_key_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.action_key_digest)
        ):
            raise KnowledgeValidationError("action_key_digest must be a SHA-256 hex string or None")

    def to_payload(self) -> dict[str, object]:
        """Return the canonical JSON payload."""
        return {
            "action_key_digest": self.action_key_digest,
            "confidence": self.confidence.to_payload(),
            "context": self.context,
            "option_type": self.option_type,
            "priority": self.priority,
            "rule_id": self.rule_id,
            "score": self.score,
            "selection_type": self.selection_type,
            "semantic_operation": self.semantic_operation,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeManifest:
    """Versioned provenance and compatibility metadata for a snapshot."""

    schema_version: str
    pack_id: str
    source: str
    content_hash: str
    card_pool_id: str
    card_pool_version: str
    cabt_version: str
    action_key_schema_version: str
    deck_id: str

    def __post_init__(self) -> None:
        for name in (
            "schema_version", "pack_id", "source", "content_hash", "card_pool_id",
            "card_pool_version", "cabt_version", "action_key_schema_version", "deck_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise KnowledgeValidationError(f"{name} must be a non-empty string")
        if len(self.content_hash) != 64 or any(c not in "0123456789abcdef" for c in self.content_hash):
            raise KnowledgeValidationError("content_hash must be a lowercase SHA-256 hex string")

    def to_payload(self) -> dict[str, str]:
        """Return the canonical JSON payload."""
        return {
            "action_key_schema_version": self.action_key_schema_version,
            "cabt_version": self.cabt_version,
            "card_pool_id": self.card_pool_id,
            "card_pool_version": self.card_pool_version,
            "content_hash": self.content_hash,
            "deck_id": self.deck_id,
            "pack_id": self.pack_id,
            "schema_version": self.schema_version,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class KnowledgePack:
    """A validated immutable Knowledge Pack v0 snapshot."""

    manifest: KnowledgeManifest
    team_deck: TeamDeck
    action_priors: tuple[ActionPrior, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, KnowledgeManifest) or not isinstance(self.team_deck, TeamDeck):
            raise KnowledgeValidationError("manifest and team_deck must be validated model values")
        if not isinstance(self.action_priors, tuple) or any(
            not isinstance(item, ActionPrior) for item in self.action_priors
        ):
            raise KnowledgeValidationError("action_priors must be a tuple of ActionPrior")
        ids = [item.rule_id for item in self.action_priors]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise KnowledgeValidationError("action_priors must be uniquely sorted by rule_id")
        if self.manifest.schema_version != SCHEMA_VERSION:
            raise KnowledgeValidationError("unsupported Knowledge Pack schema_version")
        if self.manifest.deck_id != self.team_deck.deck_id:
            raise KnowledgeValidationError("manifest deck_id does not match TeamDeck")
        expected_hash = content_hash(self.content_payload())
        if self.manifest.content_hash != expected_hash:
            raise KnowledgeValidationError("Knowledge Pack content_hash does not match content")
        if self.manifest.pack_id != f"knowledge-pack-v0-{expected_hash[:20]}":
            raise KnowledgeValidationError("pack_id does not match content_hash")

    def content_payload(self) -> dict[str, object]:
        """Return exactly the hash-covered snapshot content, excluding derived identifiers."""
        return {
            "action_priors": [prior.to_payload() for prior in self.action_priors],
            "compatibility": {
                "action_key_schema_version": self.manifest.action_key_schema_version,
                "cabt_version": self.manifest.cabt_version,
                "card_pool_id": self.manifest.card_pool_id,
                "card_pool_version": self.manifest.card_pool_version,
                "schema_version": self.manifest.schema_version,
            },
            "source": self.manifest.source,
            "team_deck": self.team_deck.to_payload(),
        }

    def to_payload(self) -> dict[str, object]:
        """Return the canonical serialized snapshot payload."""
        return {
            "action_priors": [prior.to_payload() for prior in self.action_priors],
            "manifest": self.manifest.to_payload(),
            "team_deck": self.team_deck.to_payload(),
        }
