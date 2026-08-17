"""Immutable exact knowledge about a hidden card zone."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from mage_ptcg.contracts.types import CardId

from .card_counts import CardCountInput, CardCounts, _validated_card_id
from .errors import BeliefValidationError


def _validated_non_negative_int(
    value: object,
    path: tuple[str | int, ...],
    name: str,
) -> int:
    if type(value) is not int:
        raise BeliefValidationError(
            f"invalid_{name}",
            path,
            f"{name} must be an int and must not be bool",
        )
    if value < 0:
        raise BeliefValidationError(
            f"negative_{name}",
            path,
            f"{name} must be non-negative",
        )
    return value


@dataclass(frozen=True, slots=True)
class KnownCardPosition:
    """A known card type at a zero-based position within a hidden zone."""

    position: int
    card_id: CardId

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "position",
            _validated_non_negative_int(self.position, ("position",), "position"),
        )
        object.__setattr__(
            self,
            "card_id",
            _validated_card_id(self.card_id, ("card_id",)),
        )

    def to_canonical_payload(self) -> dict[str, int]:
        return {"card_id": int(self.card_id), "position": self.position}

    @classmethod
    def from_canonical_payload(cls, payload: object) -> "KnownCardPosition":
        if type(payload) is not dict or set(payload) != {"position", "card_id"}:
            raise BeliefValidationError(
                "invalid_known_position_payload",
                (),
                "KnownCardPosition data must contain only position and card_id",
            )
        return cls(position=payload["position"], card_id=payload["card_id"])


@dataclass(frozen=True, slots=True)
class HiddenZoneKnowledge:
    """Known positions, known composition without position, and unknown mass."""

    total_count: int
    positioned_known: tuple[KnownCardPosition, ...] | Iterable[KnownCardPosition] = ()
    unpositioned_known: CardCounts | CardCountInput = field(default_factory=CardCounts)

    def __post_init__(self) -> None:
        total_count = _validated_non_negative_int(
            self.total_count,
            ("total_count",),
            "total_count",
        )
        object.__setattr__(self, "total_count", total_count)

        try:
            positioned_known = tuple(self.positioned_known)
        except TypeError as exc:
            raise BeliefValidationError(
                "invalid_positioned_known",
                ("positioned_known",),
                "positioned_known must be iterable",
            ) from exc

        for index, known_position in enumerate(positioned_known):
            if not isinstance(known_position, KnownCardPosition):
                raise BeliefValidationError(
                    "invalid_known_position",
                    ("positioned_known", index),
                    "each positioned value must be KnownCardPosition",
                )
            if known_position.position >= total_count:
                raise BeliefValidationError(
                    "position_out_of_range",
                    ("positioned_known", index, "position"),
                    "position must be less than total_count",
                )

        positions = [item.position for item in positioned_known]
        if len(positions) != len(set(positions)):
            duplicate = next(
                index
                for index, position in enumerate(positions)
                if positions.index(position) != index
            )
            raise BeliefValidationError(
                "duplicate_position",
                ("positioned_known", duplicate, "position"),
                "a position may be specified only once",
            )

        unpositioned_known = self.unpositioned_known
        if not isinstance(unpositioned_known, CardCounts):
            unpositioned_known = CardCounts(unpositioned_known)

        canonical_positions = tuple(
            sorted(positioned_known, key=lambda item: item.position)
        )
        known_total = len(canonical_positions) + unpositioned_known.total
        if known_total > total_count:
            raise BeliefValidationError(
                "known_count_exceeds_total",
                (),
                "positioned and unpositioned known cards exceed total_count",
            )

        object.__setattr__(self, "positioned_known", canonical_positions)
        object.__setattr__(self, "unpositioned_known", unpositioned_known)

    @property
    def unknown_count(self) -> int:
        return (
            self.total_count
            - len(self.positioned_known)
            - self.unpositioned_known.total
        )

    @property
    def known_counts(self) -> CardCounts:
        positioned_counts: dict[CardId, int] = {}
        for position in self.positioned_known:
            positioned_counts[position.card_id] = (
                positioned_counts.get(position.card_id, 0) + 1
            )
        return CardCounts(positioned_counts).add(self.unpositioned_known)

    def shuffle(self) -> "HiddenZoneKnowledge":
        return HiddenZoneKnowledge(
            total_count=self.total_count,
            positioned_known=(),
            unpositioned_known=self.known_counts,
        )

    def to_canonical_payload(self) -> dict[str, object]:
        return {
            "positioned_known": [
                position.to_canonical_payload() for position in self.positioned_known
            ],
            "total_count": self.total_count,
            "unpositioned_known": self.unpositioned_known.to_canonical_payload(),
        }

    @classmethod
    def from_canonical_payload(cls, payload: object) -> "HiddenZoneKnowledge":
        if type(payload) is not dict or set(payload) != {
            "total_count",
            "positioned_known",
            "unpositioned_known",
        }:
            raise BeliefValidationError(
                "invalid_hidden_zone_payload",
                (),
                "HiddenZoneKnowledge data has unexpected fields",
            )

        positioned_payload = payload["positioned_known"]
        if type(positioned_payload) is not list:
            raise BeliefValidationError(
                "invalid_positioned_known",
                ("positioned_known",),
                "positioned_known must be a list",
            )

        positioned = tuple(
            KnownCardPosition.from_canonical_payload(item)
            for item in positioned_payload
        )
        if list(position.position for position in positioned) != sorted(
            position.position for position in positioned
        ):
            raise BeliefValidationError(
                "noncanonical_position_order",
                ("positioned_known",),
                "positioned_known must be sorted by position",
            )

        return cls(
            total_count=payload["total_count"],
            positioned_known=positioned,
            unpositioned_known=CardCounts.from_canonical_payload(
                payload["unpositioned_known"]
            ),
        )


__all__ = ["HiddenZoneKnowledge", "KnownCardPosition"]
