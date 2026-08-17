"""Explicit, archive-safe deck parsing and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Collection, Sequence

from mage_ptcg.exact_file import ExactFileSnapshotError, read_exact_regular_file


MAX_DECK_FILE_BYTES = 64 * 1024

__all__ = [
    "DeckValidationError",
    "MAX_DECK_FILE_BYTES",
    "parse_deck_csv_bytes",
    "read_deck_csv",
    "validate_deck",
]


class DeckValidationError(ValueError):
    """Raised when a deck cannot satisfy the 60-card deck contract."""


def validate_deck(
    deck: Sequence[int],
    *,
    known_card_ids: Collection[int] | None = None,
) -> list[int]:
    """Validate and copy a 60-card deck without consulting ambient paths."""
    if len(deck) != 60:
        raise DeckValidationError(f"deck must contain exactly 60 cards, got {len(deck)}")
    if any(type(card_id) is not int or card_id <= 0 for card_id in deck):
        raise DeckValidationError("every card ID must be a positive integer and not bool")
    if known_card_ids is not None:
        unknown = sorted(set(deck).difference(known_card_ids))
        if unknown:
            raise DeckValidationError(f"deck contains unknown card IDs: {unknown}")
    return list(deck)


def parse_deck_csv_bytes(
    payload: bytes,
    *,
    known_card_ids: Collection[int] | None = None,
) -> list[int]:
    """Parse immutable one-card-per-line deck bytes without consulting a path."""
    if not isinstance(payload, bytes):
        raise DeckValidationError("deck payload must be exact bytes")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DeckValidationError("deck payload must be UTF-8") from exc

    values: list[int] = []
    for line_number, raw in enumerate(lines, start=1):
        value = raw.strip()
        if not value:
            continue
        try:
            values.append(int(value))
        except ValueError as exc:
            raise DeckValidationError(
                f"deck line {line_number} is not an integer: {value!r}"
            ) from exc
    return validate_deck(values, known_card_ids=known_card_ids)


def read_deck_csv(
    path: str | Path,
    *,
    known_card_ids: Collection[int] | None = None,
) -> list[int]:
    """Read one bounded, no-follow snapshot of an explicit deck CSV."""
    if not isinstance(path, (str, Path)) or (isinstance(path, str) and not path):
        raise DeckValidationError("deck path must be an explicit nonempty str or Path")
    deck_path = Path(path)
    try:
        snapshot = read_exact_regular_file(deck_path, max_bytes=MAX_DECK_FILE_BYTES)
    except ExactFileSnapshotError as exc:
        raise DeckValidationError(f"could not snapshot deck {deck_path}: {exc}") from exc
    return parse_deck_csv_bytes(snapshot.payload, known_card_ids=known_card_ids)
