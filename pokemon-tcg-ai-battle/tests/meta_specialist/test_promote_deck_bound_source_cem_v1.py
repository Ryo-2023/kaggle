from __future__ import annotations

import pytest

from scripts.promote_deck_bound_source_cem_v1 import (
    DeckBoundSourcePromotionError,
    build_promotion_smoke_summary_v1,
)


def _campaign() -> dict[str, object]:
    return {
        "status": "SOURCE_POOL_STAGED",
        "selected_strict_ids": ["source-b", "source-a"],
        "staged_batch": {"status": "STAGED", "source_ids": ["source-b", "source-a"]},
    }


def _summary(*, faults: int = 0, completed: int = 4, requested: int = 4) -> dict[str, object]:
    return {
        "completed_games": completed,
        "requested_games": requested,
        "faults": faults,
        "status_distribution": {"DONE": completed},
        "score_rate": 0.5,
    }


def test_promotion_smoke_wrapper_seals_selected_ids_and_summary() -> None:
    wrapped = build_promotion_smoke_summary_v1(_campaign(), _summary())

    assert wrapped["status"] == "COMPLETE"
    assert wrapped["selected_ids"] == ["source-b", "source-a"]
    assert wrapped["evaluator_summary"]["completed_games"] == 4
    assert wrapped["faults"] == 0


@pytest.mark.parametrize(
    "campaign,summary,pattern",
    [
        ({"status": "NO_STRICT_SOURCE_POOL"}, _summary(), "staged"),
        (_campaign(), _summary(faults=1), "fault"),
        (_campaign(), _summary(completed=3), "complete"),
    ],
)
def test_promotion_smoke_wrapper_fails_closed(
    campaign: dict[str, object], summary: dict[str, object], pattern: str
) -> None:
    with pytest.raises(DeckBoundSourcePromotionError, match=pattern):
        build_promotion_smoke_summary_v1(campaign, summary)
