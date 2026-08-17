"""Tests for HiddenZoneKnowledge."""

import pytest

from mage_ptcg.belief import (
    BeliefValidationError,
    CardCounts,
    HiddenZoneKnowledge,
    KnownCardPosition,
)
from mage_ptcg.contracts import CardId


def test_unknown_count_known_counts_and_shuffle() -> None:
    knowledge = HiddenZoneKnowledge(
        total_count=5,
        positioned_known=(
            KnownCardPosition(0, CardId(7)),
            KnownCardPosition(4, CardId(9)),
        ),
        unpositioned_known=CardCounts({CardId(7): 1}),
    )

    assert knowledge.unknown_count == 2
    assert knowledge.known_counts == CardCounts({CardId(7): 2, CardId(9): 1})

    shuffled = knowledge.shuffle()
    assert shuffled.total_count == knowledge.total_count
    assert shuffled.positioned_known == ()
    assert shuffled.unpositioned_known == knowledge.known_counts
    assert shuffled.known_counts == knowledge.known_counts
    assert shuffled.unknown_count == knowledge.unknown_count
    assert shuffled.shuffle() == shuffled


def test_positions_are_sorted_canonically() -> None:
    knowledge = HiddenZoneKnowledge(
        total_count=3,
        positioned_known=(
            KnownCardPosition(2, CardId(2)),
            KnownCardPosition(0, CardId(1)),
        ),
    )
    assert tuple(item.position for item in knowledge.positioned_known) == (0, 2)


def test_duplicate_and_out_of_range_positions_are_rejected() -> None:
    with pytest.raises(BeliefValidationError) as duplicate:
        HiddenZoneKnowledge(
            total_count=2,
            positioned_known=(
                KnownCardPosition(0, CardId(1)),
                KnownCardPosition(0, CardId(2)),
            ),
        )
    assert duplicate.value.code == "duplicate_position"

    with pytest.raises(BeliefValidationError) as out_of_range:
        HiddenZoneKnowledge(
            total_count=1,
            positioned_known=(KnownCardPosition(1, CardId(1)),),
        )
    assert out_of_range.value.code == "position_out_of_range"


def test_known_count_cannot_exceed_total() -> None:
    with pytest.raises(BeliefValidationError) as caught:
        HiddenZoneKnowledge(
            total_count=1,
            positioned_known=(KnownCardPosition(0, CardId(1)),),
            unpositioned_known=CardCounts({CardId(2): 1}),
        )
    assert caught.value.code == "known_count_exceeds_total"


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_total_count_requires_exact_int(value: object) -> None:
    with pytest.raises(BeliefValidationError):
        HiddenZoneKnowledge(total_count=value)


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_position_requires_exact_int(value: object) -> None:
    with pytest.raises(BeliefValidationError):
        KnownCardPosition(position=value, card_id=CardId(1))
