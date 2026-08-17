from __future__ import annotations

import pytest


def test_surface_candidates_are_exact_1244_swaps_and_bounded() -> None:
    from scripts.run_resource_aware_tomato_surface_weighted_v1 import (
        SURFACE_SWAPS,
        build_surface_candidates,
    )

    candidates = build_surface_candidates()
    assert len(candidates) == 2
    assert [
        (tuple(row["removed_cards"]), tuple(row["added_cards"]))
        for row in candidates
    ] == [((old,), (new,)) for old, new in SURFACE_SWAPS]
    assert all(row["novel_against_all_scanned_decks"] is True for row in candidates)


def test_surface_rejects_wrong_parent_sha() -> None:
    from scripts.run_resource_aware_tomato_surface_weighted_v1 import (
        SurfaceWeightedDeckError,
        validate_surface_parent,
    )

    with pytest.raises(SurfaceWeightedDeckError, match="parent"):
        validate_surface_parent("0" * 64)
