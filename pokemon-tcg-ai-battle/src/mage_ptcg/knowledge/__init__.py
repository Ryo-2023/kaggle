"""C2a Knowledge Pack v0 public API."""

from .adapter import KnowledgeRuleAdapter
from .compatibility import (
    CompatibilityReport,
    RuntimeCompatibility,
    check_compatibility,
    runtime_cabt_version,
    runtime_compatibility_for_deck,
)
from .loader import build_team_deck_pack, read_deck_card_ids
from .model import (
    ACTION_KEY_SCHEMA_VERSION, SCHEMA_VERSION, ActionPrior, DeckEntry, KnowledgeConfidence,
    KnowledgeManifest, KnowledgePack, KnowledgeValidationError, RoleTag, TeamDeck,
    canonical_json_bytes, content_hash, deck_identity_from_card_ids,
)
from .serialization import load_pack, pack_from_payload, serialize_pack, write_pack

__all__ = [
    "ACTION_KEY_SCHEMA_VERSION", "SCHEMA_VERSION", "ActionPrior", "CompatibilityReport",
    "DeckEntry", "KnowledgeConfidence", "KnowledgeManifest", "KnowledgePack",
    "KnowledgeRuleAdapter", "KnowledgeValidationError", "RoleTag", "RuntimeCompatibility",
    "TeamDeck", "build_team_deck_pack", "canonical_json_bytes", "check_compatibility",
    "content_hash", "deck_identity_from_card_ids", "load_pack", "runtime_cabt_version",
    "runtime_compatibility_for_deck",
    "pack_from_payload", "read_deck_card_ids", "serialize_pack", "write_pack",
]
