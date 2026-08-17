"""Refresh O2 pool files from permitted exact deck observations only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.contracts import AllowedUse, DeckObservation, SourceKind
from mage_ptcg.o2_training_loop.core import deck_content_hash


_ACTIVE_KINDS = {SourceKind.LOCAL_SELFPLAY, SourceKind.OWN_KAGGLE, SourceKind.TEAM_SHARED}


def refresh_deck_pool(
    *, base_pool: str | Path, observations: Iterable[tuple[DeckObservation, SourceKind, frozenset[AllowedUse]]],
    output_path: str | Path, observed_at: str,
) -> dict[str, Any]:
    """Extend the existing O2 schema without admitting archive-only/public decks."""
    payload = json.loads(Path(base_pool).read_text(encoding="utf-8"))
    decks = list(payload["decks"])
    known = {item["deck_hash"] for item in decks}
    admitted = 0
    for observation, source_kind, uses in observations:
        if source_kind not in _ACTIVE_KINDS or AllowedUse.TRAINING not in uses or observation.exact_decklist is None:
            continue
        cards = [card_id for card_id, count in sorted(observation.exact_decklist.items()) for _ in range(count)]
        deck_hash = deck_content_hash(cards)
        if deck_hash in known:
            continue
        archetype = max(observation.inferred_archetypes, key=observation.inferred_archetypes.get, default="UNKNOWN")
        decks.append({
            "deck_id": f"observed-{deck_hash[:16]}", "deck_version": deck_hash[:12], "cards": cards,
            "deck_hash": deck_hash, "archetype": archetype or "UNKNOWN", "variant": deck_hash[:16],
            "roles": ["observed", "training"], "source": f"snapshot:{observation.episode_id}",
            "permission_scope": "TRAINING_AND_EVALUATION", "valid_from": observed_at, "valid_until": None,
            "confidence": "observed", "provenance": {"episode_id": observation.episode_id, "seat": observation.seat,
                                                     "source_kind": source_kind.value, "deck_observation_hash": observation.content_hash()},
        })
        known.add(deck_hash)
        admitted += 1
    payload["decks"] = sorted(decks, key=lambda item: item["deck_id"])
    atomic_write_json(output_path, payload)
    return {"schema_version": "o3-pool-refresh-v1", "deck_count": len(decks), "admitted_decks": admitted,
            "public_other_admitted": False}


def refresh_opponent_pool(*, base_pool: str | Path, output_path: str | Path, student_artifact: str | Path | None = None) -> dict[str, Any]:
    """Keep mandatory Rule/Random entries and enable a Student only when its artifact exists."""
    payload = json.loads(Path(base_pool).read_text(encoding="utf-8"))
    enabled_student = False
    if student_artifact is not None and Path(student_artifact).is_file():
        for opponent in payload["opponents"]:
            if opponent["agent_kind"] == "student_v0":
                opponent["artifact_reference"] = str(Path(student_artifact))
                opponent["enabled"] = True
                enabled_student = True
    atomic_write_json(output_path, payload)
    kinds = {item["agent_kind"] for item in payload["opponents"] if item["enabled"]}
    if not {"rule_v0", "random_legal"}.issubset(kinds):
        raise ValueError("Rule Agent v0 and Random Legal Agent must remain enabled")
    return {"schema_version": "o3-pool-refresh-v1", "opponent_count": len(payload["opponents"]),
            "student_enabled": enabled_student, "bounded_search_enabled": False}


__all__ = ["refresh_deck_pool", "refresh_opponent_pool"]
