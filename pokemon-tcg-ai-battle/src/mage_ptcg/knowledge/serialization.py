"""Strict canonical JSON serialization for Knowledge Pack v0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    ActionPrior,
    DeckEntry,
    KnowledgeConfidence,
    KnowledgeManifest,
    KnowledgePack,
    KnowledgeValidationError,
    RoleTag,
    TeamDeck,
    canonical_json_bytes,
)


def _mapping(value: object, name: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise KnowledgeValidationError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _confidence(value: object) -> KnowledgeConfidence:
    data = _mapping(value, "confidence", {"validity", "support", "freshness"})
    return KnowledgeConfidence(**data)


def _entry(value: object) -> DeckEntry:
    data = _mapping(value, "deck entry", {"card_id", "count", "role", "role_confidence", "source_ref"})
    try:
        role = RoleTag(data["role"])
    except (TypeError, ValueError) as exc:
        raise KnowledgeValidationError("deck entry role is invalid") from exc
    return DeckEntry(
        card_id=data["card_id"], count=data["count"], role=role,
        role_confidence=_confidence(data["role_confidence"]), source_ref=data["source_ref"],
    )


def pack_from_payload(value: object) -> KnowledgePack:
    """Parse, validate, and freeze an exact v0 snapshot JSON payload."""
    data = _mapping(value, "Knowledge Pack", {"manifest", "team_deck", "action_priors"})
    manifest_data = _mapping(
        data["manifest"], "manifest",
        {"schema_version", "pack_id", "source", "content_hash", "card_pool_id", "card_pool_version", "cabt_version", "action_key_schema_version", "deck_id"},
    )
    team_data = _mapping(data["team_deck"], "team_deck", {"deck_id", "entries"})
    raw_entries = team_data["entries"]
    if type(raw_entries) is not list:
        raise KnowledgeValidationError("team_deck.entries must be a list")
    raw_priors = data["action_priors"]
    if type(raw_priors) is not list:
        raise KnowledgeValidationError("action_priors must be a list")
    priors: list[ActionPrior] = []
    prior_keys = {
        "rule_id", "score", "priority", "confidence", "source_ref", "selection_type",
        "context", "option_type", "semantic_operation", "action_key_digest",
    }
    for raw_prior in raw_priors:
        prior_data = _mapping(raw_prior, "action_prior", prior_keys)
        prior_data = dict(prior_data)
        prior_data["confidence"] = _confidence(prior_data["confidence"])
        priors.append(ActionPrior(**prior_data))
    return KnowledgePack(
        manifest=KnowledgeManifest(**manifest_data),
        team_deck=TeamDeck(deck_id=team_data["deck_id"], entries=tuple(_entry(item) for item in raw_entries)),
        action_priors=tuple(priors),
    )


def serialize_pack(pack: KnowledgePack) -> bytes:
    """Serialize a previously validated snapshot as canonical JSON bytes."""
    if not isinstance(pack, KnowledgePack):
        raise TypeError("pack must be KnowledgePack")
    return canonical_json_bytes(pack.to_payload())


def load_pack(path: str | Path) -> KnowledgePack:
    """Read, parse, validate, and verify one immutable Knowledge Pack snapshot."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise KnowledgeValidationError(f"could not read Knowledge Pack {path}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeValidationError("Knowledge Pack is not valid UTF-8 JSON") from exc
    pack = pack_from_payload(payload)
    if raw != serialize_pack(pack):
        raise KnowledgeValidationError("Knowledge Pack is not in canonical JSON form")
    return pack


def write_pack(pack: KnowledgePack, path: str | Path) -> None:
    """Write canonical JSON atomically enough for the repository CLI use case."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(serialize_pack(pack))
