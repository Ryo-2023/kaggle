"""Reproducible Team Deck Knowledge Pack v0 construction."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .model import (
    ACTION_KEY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ActionPrior,
    DeckEntry,
    KnowledgeConfidence,
    KnowledgeManifest,
    KnowledgePack,
    KnowledgeValidationError,
    RoleTag,
    TeamDeck,
    content_hash,
    deck_identity_from_entries,
)
from .compatibility import (
    DEFAULT_CABT_VERSION,
    DEFAULT_CARD_POOL_ID,
    DEFAULT_CARD_POOL_VERSION,
)


def read_deck_card_ids(path: str | Path) -> tuple[int, ...]:
    """Read exactly sixty positive integer card IDs from a simple deck CSV."""
    deck_path = Path(path)
    try:
        lines = deck_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise KnowledgeValidationError(f"could not read deck {deck_path}: {exc}") from exc
    values: list[int] = []
    for number, line in enumerate(lines, start=1):
        token = line.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise KnowledgeValidationError(f"deck line {number} is not an integer") from exc
        if type(value) is not int or value <= 0:
            raise KnowledgeValidationError(f"deck line {number} must be a positive card ID")
        values.append(value)
    if len(values) != 60:
        raise KnowledgeValidationError(f"deck must contain exactly 60 cards, got {len(values)}")
    return tuple(values)


def build_team_deck_pack(
    deck_path: str | Path,
    *,
    source: str | None = None,
    card_pool_id: str = DEFAULT_CARD_POOL_ID,
    card_pool_version: str = DEFAULT_CARD_POOL_VERSION,
    cabt_version: str = DEFAULT_CABT_VERSION,
) -> KnowledgePack:
    """Build the conservative, reproducible v0 snapshot from the supplied deck CSV.

    No card-effect data is tracked in this repository.  Accordingly every card
    receives the least-assertive ``FLEX`` role with zero evidence support; this
    is explicit data rather than a fabricated semantic classification.
    """
    card_ids = read_deck_card_ids(deck_path)
    source_ref = "Team Deck CSV; role classification unverified"
    role_confidence = KnowledgeConfidence(validity=1.0, support=0.0, freshness=0.0)
    entries = tuple(
        DeckEntry(card_id=card_id, count=count, role=RoleTag.FLEX, role_confidence=role_confidence, source_ref=source_ref)
        for card_id, count in sorted(Counter(card_ids).items())
    )
    deck = TeamDeck(deck_id=deck_identity_from_entries(entries), entries=entries)
    priors = (
        ActionPrior(
            rule_id="rule-v0-main-play-tie-break",
            score=1.0,
            priority=1,
            confidence=KnowledgeConfidence(validity=1.0, support=1.0, freshness=1.0),
            source_ref="agents/rule_agent.py:_MAIN_ACTION_SCORES",
            selection_type=0,
            option_type=7,
            semantic_operation="PLAY",
        ),
    )
    pack_source = source or "deck.csv + agents/rule_agent.py"
    content = {
        "action_priors": [prior.to_payload() for prior in priors],
        "compatibility": {
            "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
            "cabt_version": cabt_version,
            "card_pool_id": card_pool_id,
            "card_pool_version": card_pool_version,
            "schema_version": SCHEMA_VERSION,
        },
        "source": pack_source,
        "team_deck": deck.to_payload(),
    }
    digest = content_hash(content)
    manifest = KnowledgeManifest(
        schema_version=SCHEMA_VERSION,
        pack_id=f"knowledge-pack-v0-{digest[:20]}",
        source=pack_source,
        content_hash=digest,
        card_pool_id=card_pool_id,
        card_pool_version=card_pool_version,
        cabt_version=cabt_version,
        action_key_schema_version=ACTION_KEY_SCHEMA_VERSION,
        deck_id=deck.deck_id,
    )
    return KnowledgePack(manifest=manifest, team_deck=deck, action_priors=priors)
