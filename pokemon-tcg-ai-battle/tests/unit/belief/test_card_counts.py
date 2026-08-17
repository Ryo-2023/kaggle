"""Tests for CardCounts."""

from dataclasses import FrozenInstanceError

import pytest

from mage_ptcg.belief import BeliefValidationError, CardCounts
from mage_ptcg.contracts import CardId


class IntSubclass(int):
    pass


def test_order_independent_equality_hash_and_payload() -> None:
    left = CardCounts({CardId(2): 1, CardId(1): 3})
    right = CardCounts({CardId(1): 3, CardId(2): 1})

    assert left == right
    assert hash(left) == hash(right)
    assert left.to_canonical_payload() == [[1, 3], [2, 1]]


def test_zero_count_is_removed() -> None:
    assert CardCounts({CardId(1): 0, CardId(2): 2}).to_canonical_payload() == [[2, 2]]


@pytest.mark.parametrize("value", [True, 1.0, "1", IntSubclass(1)])
def test_non_exact_int_card_ids_are_rejected(value: object) -> None:
    with pytest.raises(BeliefValidationError) as caught:
        CardCounts.from_pairs([(value, 1)])
    assert caught.value.code == "invalid_card_id"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, "invalid_card_count"),
        (1.0, "invalid_card_count"),
        (-1, "negative_card_count"),
        (IntSubclass(1), "invalid_card_count"),
    ],
)
def test_invalid_counts_are_rejected(value: object, code: str) -> None:
    with pytest.raises(BeliefValidationError) as caught:
        CardCounts.from_pairs([(CardId(1), value)])
    assert caught.value.code == code


def test_duplicate_card_id_is_rejected_even_when_one_count_is_zero() -> None:
    with pytest.raises(BeliefValidationError) as caught:
        CardCounts.from_pairs([(CardId(1), 1), (CardId(1), 0)])
    assert caught.value.code == "duplicate_card_id"


def test_contains_add_subtract_and_underflow() -> None:
    counts = CardCounts({CardId(1): 2, CardId(2): 1})
    subset = CardCounts({CardId(1): 1})

    assert counts.contains(subset)
    assert counts.add(subset) == CardCounts({CardId(1): 3, CardId(2): 1})
    assert counts.subtract(subset) == CardCounts({CardId(1): 1, CardId(2): 1})

    with pytest.raises(BeliefValidationError) as caught:
        subset.subtract(counts)
    assert caught.value.code == "subtraction_underflow"


def test_mapping_lookup_rejects_bool_key() -> None:
    counts = CardCounts({CardId(1): 2})
    with pytest.raises(BeliefValidationError):
        _ = counts[True]


def test_value_is_frozen() -> None:
    counts = CardCounts({CardId(1): 1})
    with pytest.raises(FrozenInstanceError):
        counts._items = ()
