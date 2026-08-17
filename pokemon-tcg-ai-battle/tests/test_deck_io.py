"""Tests for the explicit, archive-safe deck I/O boundary."""

from __future__ import annotations

from pathlib import Path

import pytest


def _deck_io():
    from mage_ptcg.deck_io import DeckValidationError, read_deck_csv, validate_deck

    return DeckValidationError, read_deck_csv, validate_deck


def test_read_deck_csv_reads_only_the_explicit_file_and_returns_fresh_lists(
    tmp_path: Path,
) -> None:
    """Catches fallback discovery or sharing a caller-mutable deck list."""
    _DeckValidationError, read_deck_csv, _validate_deck = _deck_io()
    deck_path = tmp_path / "explicit-deck.csv"
    deck_path.write_text("\n\n".join(map(str, range(1, 61))) + "\n", encoding="utf-8")

    first = read_deck_csv(deck_path, known_card_ids=set(range(1, 61)))
    second = read_deck_csv(deck_path, known_card_ids=set(range(1, 61)))

    first.append(999)
    assert second == list(range(1, 61))


def test_validate_deck_copies_a_valid_deck() -> None:
    """Catches validation that returns the caller-owned mutable list."""
    _DeckValidationError, _read_deck_csv, validate_deck = _deck_io()
    deck = [7] * 60

    validated = validate_deck(deck, known_card_ids={7})
    deck[0] = 8

    assert validated == [7] * 60


@pytest.mark.parametrize(
    ("deck", "known_card_ids"),
    [
        (list(range(59)), None),
        ([True] + [1] * 59, None),
        (["1"] + [1] * 59, None),
        ([0] + [1] * 59, None),
        ([-1] + [1] * 59, None),
        ([1] * 59 + [2], {1}),
    ],
)
def test_validate_deck_rejects_invalid_card_contracts(
    deck: list[object], known_card_ids: set[int] | None
) -> None:
    """Catches invalid length, bool, non-int, or unknown-card acceptance."""
    DeckValidationError, _read_deck_csv, validate_deck = _deck_io()
    with pytest.raises(DeckValidationError):
        validate_deck(deck, known_card_ids=known_card_ids)


@pytest.mark.parametrize("path", [None, "", 1])
def test_read_deck_csv_requires_an_explicit_nonempty_path(path: object) -> None:
    """Catches implicit CWD or non-path deck discovery."""
    DeckValidationError, read_deck_csv, _validate_deck = _deck_io()
    with pytest.raises(DeckValidationError, match="explicit"):
        read_deck_csv(path)  # type: ignore[arg-type]


def test_read_deck_csv_rejects_non_integer_nonblank_lines(tmp_path: Path) -> None:
    """Catches a parser that silently skips nonblank malformed card entries."""
    DeckValidationError, read_deck_csv, _validate_deck = _deck_io()
    deck_path = tmp_path / "malformed.csv"
    deck_path.write_text("1\nnot-an-integer\n", encoding="utf-8")

    with pytest.raises(DeckValidationError, match="line 2"):
        read_deck_csv(deck_path)


def test_read_deck_csv_translates_an_empty_exact_snapshot_to_deck_validation(
    tmp_path: Path,
) -> None:
    """Empty exact bytes reach the deck parser rather than leaking snapshot internals."""
    DeckValidationError, read_deck_csv, _validate_deck = _deck_io()
    deck_path = tmp_path / "empty.csv"
    deck_path.write_bytes(b"")

    with pytest.raises(DeckValidationError, match="exactly 60 cards"):
        read_deck_csv(deck_path)


def test_read_deck_csv_rejects_symlink_and_bounded_oversize_input(tmp_path: Path) -> None:
    DeckValidationError, read_deck_csv, _validate_deck = _deck_io()
    target = tmp_path / "target.csv"
    target.write_text("\n".join(map(str, range(1, 61))) + "\n", encoding="utf-8")
    symlink = tmp_path / "symlink.csv"
    symlink.symlink_to(target)

    with pytest.raises(DeckValidationError, match="regular|no-follow"):
        read_deck_csv(symlink)

    oversize = tmp_path / "oversize.csv"
    oversize.write_bytes(b"1\n" + b" " * (64 * 1024))
    with pytest.raises(DeckValidationError, match="maximum|size"):
        read_deck_csv(oversize)
