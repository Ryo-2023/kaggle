from __future__ import annotations

import pytest


def test_tomato_parent_identity_is_sealed_and_candidate_count_bounded() -> None:
    from scripts.run_resource_aware_tomato_weighted_deck_v1 import (
        MAX_CANDIDATES,
        TOMATO_PARENT_DECK_SHA256,
        TOMATO_PARENT_ID,
    )

    assert MAX_CANDIDATES == 2
    assert TOMATO_PARENT_ID == "tomatomato_archaludon-native"
    assert TOMATO_PARENT_DECK_SHA256 == "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"


def test_tomato_runner_rejects_non_sealed_parent() -> None:
    from scripts.run_resource_aware_tomato_weighted_deck_v1 import (
        TomatoWeightedDeckError,
        validate_parent_identity,
    )

    with pytest.raises(TomatoWeightedDeckError, match="Tomato"):
        validate_parent_identity("wrong", "0" * 64)

