"""Small, pre-registered Deck-only search helpers for Alakazam.

The helpers operate on immutable recipe data.  They never alter the default
deck and reject core/dependency mutations before CABT is invoked.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from main import DeckValidationError, validate_deck


class AlakazamDeckSearchError(ValueError):
    pass


@dataclass(frozen=True)
class DeckMutation:
    candidate_id: str
    remove: int
    add: int
    reason: str
    target_failure: str
    risk: str
    source: str


def deck_hash(deck: Sequence[int]) -> str:
    safe = validate_deck(deck)
    return hashlib.sha256(("\n".join(str(card) for card in safe) + "\n").encode()).hexdigest()


def load_mutations(path: Path) -> tuple[dict[str, object], list[DeckMutation]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema") != "alakazam-flex-candidates-v2":
        raise AlakazamDeckSearchError("unexpected candidate registry schema")
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        raise AlakazamDeckSearchError("candidates must be a list")
    rows: list[DeckMutation] = []
    for value in raw:
        if not isinstance(value, Mapping):
            raise AlakazamDeckSearchError("malformed candidate")
        try:
            row = DeckMutation(
                candidate_id=str(value["candidate_id"]), remove=int(value["remove"]), add=int(value["add"]),
                reason=str(value["reason"]), target_failure=str(value["target_failure"]),
                risk=str(value["risk"]), source=str(value["source"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AlakazamDeckSearchError("malformed candidate fields") from exc
        if row.remove == row.add or not row.candidate_id:
            raise AlakazamDeckSearchError("candidate must be a non-empty one-card exchange")
        rows.append(row)
    if len({row.candidate_id for row in rows}) != len(rows):
        raise AlakazamDeckSearchError("candidate IDs must be unique")
    return dict(payload), rows


def load_slot_catalog(path: Path) -> dict[int, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema") != "alakazam-slot-catalog-v2":
        raise AlakazamDeckSearchError("unexpected slot catalog schema")
    cards = payload.get("cards")
    if not isinstance(cards, list):
        raise AlakazamDeckSearchError("catalog cards must be a list")
    result = {int(row["card_id"]): dict(row) for row in cards if isinstance(row, Mapping)}
    if len(result) != len(cards):
        raise AlakazamDeckSearchError("catalog has duplicate or malformed card IDs")
    return result


def mutate_deck(baseline: Sequence[int], mutation: DeckMutation, catalog: Mapping[int, Mapping[str, object]]) -> list[int]:
    deck = list(validate_deck(baseline))
    source = catalog.get(mutation.remove)
    if source is None or int(source.get("changeable_copies", 0)) < 1:
        raise AlakazamDeckSearchError("removal is not an adjustable baseline card")
    if mutation.remove not in deck:
        raise AlakazamDeckSearchError("removal is absent from baseline")
    if mutation.add not in catalog and mutation.add not in {65, 1147, 1227}:
        raise AlakazamDeckSearchError("addition is not a registered candidate card")
    addition = catalog.get(mutation.add, {})
    copy_limit = int(addition.get("copy_limit", 4))
    if deck.count(mutation.add) >= copy_limit:
        raise AlakazamDeckSearchError("candidate exceeds its copy limit")
    changed = list(deck)
    changed[changed.index(mutation.remove)] = mutation.add
    return validate_deck(changed)


def validate_catalog_counts(baseline: Sequence[int], catalog: Mapping[int, Mapping[str, object]]) -> dict[str, int]:
    deck = list(validate_deck(baseline))
    counts = Counter(deck)
    if set(counts) != set(catalog):
        raise AlakazamDeckSearchError("slot catalog does not cover the exact baseline")
    for card, count in counts.items():
        if int(catalog[card].get("copies", -1)) != count:
            raise AlakazamDeckSearchError(f"slot catalog count mismatch for {card}")
    categories = Counter(str(row["category"]) for row in catalog.values())
    copy_totals: Counter[str] = Counter()
    for card, row in catalog.items():
        copy_totals[str(row["category"])] += counts[card]
    if sum(copy_totals.values()) != 60:
        raise AlakazamDeckSearchError("exclusive category copies must total 60")
    return {"card_types": len(catalog), "copies": sum(counts.values()), **{f"{category}_types": categories[category] for category in sorted(categories)}, **{f"{category}_copies": copy_totals[category] for category in sorted(copy_totals)}}
