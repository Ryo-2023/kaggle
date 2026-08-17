"""Immutable card-count multiset."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeAlias

from mage_ptcg.contracts.types import CardId

from .errors import BeliefValidationError


CardCountInput: TypeAlias = Mapping[CardId, int] | Iterable[tuple[CardId, int]]


def _validated_card_id(card_id: object, path: tuple[str | int, ...]) -> CardId:
    if type(card_id) is not int:
        raise BeliefValidationError(
            "invalid_card_id",
            path,
            "card_id must be an int and must not be bool",
        )
    return CardId(card_id)


def _validated_count(count: object, path: tuple[str | int, ...]) -> int:
    if type(count) is not int:
        raise BeliefValidationError(
            "invalid_card_count",
            path,
            "count must be an int and must not be bool",
        )
    if count < 0:
        raise BeliefValidationError(
            "negative_card_count",
            path,
            "count must be non-negative",
        )
    return count


@dataclass(frozen=True, slots=True, init=False)
class CardCounts(Mapping[CardId, int]):
    """Canonical, immutable card counts sorted by card ID.

    Duplicate card IDs are rejected for iterable input. Zero counts are accepted
    at construction and removed from the canonical value.
    """

    _items: tuple[tuple[CardId, int], ...]

    def __init__(self, counts: CardCountInput | None = None) -> None:
        if counts is None:
            raw_items: object = ()
        elif isinstance(counts, Mapping):
            raw_items = counts.items()
        else:
            raw_items = counts

        try:
            iterator = iter(raw_items)
        except TypeError as exc:
            raise BeliefValidationError(
                "invalid_card_counts",
                (),
                "counts must be a mapping or iterable of (card_id, count) pairs",
            ) from exc

        normalized: dict[CardId, int] = {}
        seen: set[CardId] = set()

        for index, item in enumerate(iterator):
            try:
                card_id, count = item
            except (TypeError, ValueError) as exc:
                raise BeliefValidationError(
                    "invalid_card_count_entry",
                    (index,),
                    "each entry must be a (card_id, count) pair",
                ) from exc

            validated_card_id = _validated_card_id(card_id, (index, "card_id"))
            if validated_card_id in seen:
                raise BeliefValidationError(
                    "duplicate_card_id",
                    (index, "card_id"),
                    "each card_id may appear only once",
                )
            seen.add(validated_card_id)

            validated_count = _validated_count(count, (index, "count"))
            if validated_count:
                normalized[validated_card_id] = validated_count

        object.__setattr__(
            self,
            "_items",
            tuple(sorted(normalized.items(), key=lambda item: int(item[0]))),
        )

    @classmethod
    def empty(cls) -> "CardCounts":
        return cls()

    @classmethod
    def from_mapping(cls, counts: Mapping[CardId, int]) -> "CardCounts":
        return cls(counts)

    @classmethod
    def from_pairs(cls, counts: Iterable[tuple[CardId, int]]) -> "CardCounts":
        return cls(counts)

    @classmethod
    def from_canonical_payload(cls, payload: object) -> "CardCounts":
        if type(payload) is not list:
            raise BeliefValidationError(
                "invalid_card_counts_payload",
                (),
                "CardCounts data must be a list",
            )

        pairs: list[tuple[CardId, int]] = []
        previous_card_id: int | None = None
        for index, entry in enumerate(payload):
            if type(entry) is not list or len(entry) != 2:
                raise BeliefValidationError(
                    "invalid_card_count_entry",
                    (index,),
                    "each canonical entry must be [card_id, count]",
                )
            card_id = _validated_card_id(entry[0], (index, "card_id"))
            count = _validated_count(entry[1], (index, "count"))
            if count == 0:
                raise BeliefValidationError(
                    "noncanonical_zero_count",
                    (index, "count"),
                    "canonical CardCounts must omit zero counts",
                )
            if previous_card_id is not None and int(card_id) <= previous_card_id:
                raise BeliefValidationError(
                    "noncanonical_card_order",
                    (index, "card_id"),
                    "canonical CardCounts must be strictly sorted by card_id",
                )
            previous_card_id = int(card_id)
            pairs.append((card_id, count))
        return cls.from_pairs(pairs)

    @property
    def total(self) -> int:
        return sum(count for _, count in self._items)

    def count(self, card_id: CardId | int) -> int:
        validated = _validated_card_id(card_id, ("card_id",))
        for current_card_id, count in self._items:
            if current_card_id == validated:
                return count
        return 0

    def contains(self, other: "CardCounts") -> bool:
        if not isinstance(other, CardCounts):
            raise TypeError("other must be CardCounts")
        return all(self.count(card_id) >= count for card_id, count in other.items())

    def add(self, other: "CardCounts") -> "CardCounts":
        if not isinstance(other, CardCounts):
            raise TypeError("other must be CardCounts")
        result = dict(self._items)
        for card_id, count in other.items():
            result[card_id] = result.get(card_id, 0) + count
        return CardCounts(result)

    def subtract(self, other: "CardCounts") -> "CardCounts":
        if not isinstance(other, CardCounts):
            raise TypeError("other must be CardCounts")
        result = dict(self._items)
        for card_id, count in other.items():
            available = result.get(card_id, 0)
            if count > available:
                raise BeliefValidationError(
                    "subtraction_underflow",
                    ("card_counts", int(card_id)),
                    f"cannot subtract {count} from available count {available}",
                )
            remaining = available - count
            if remaining:
                result[card_id] = remaining
            else:
                result.pop(card_id, None)
        return CardCounts(result)

    def to_canonical_payload(self) -> list[list[int]]:
        return [[int(card_id), count] for card_id, count in self._items]

    def __getitem__(self, card_id: CardId) -> int:
        validated = _validated_card_id(card_id, ("card_id",))
        for current_card_id, count in self._items:
            if current_card_id == validated:
                return count
        raise KeyError(card_id)

    def __iter__(self) -> Iterator[CardId]:
        return iter(card_id for card_id, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


__all__ = ["CardCounts", "CardCountInput"]
