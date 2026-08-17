"""Focused planner contracts for the self-owned Rule BC collector."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.collect_self_owned_rule_bc_v1 import build_game_specs_v1


def test_default_schedule_is_balanced_and_seeded() -> None:
    specs = build_game_specs_v1(
        opponent_ids=("a", "b"),
        games_per_seat=2,
        base_seed=100,
        subject_deck_path=Path("deck.csv"),
        output_root=Path("runs/out"),
        source_revision="sha",
        max_steps=2000,
    )
    assert len(specs) == 8
    assert [spec.seed for spec in specs] == list(range(100, 108))
    assert {spec.subject_seat for spec in specs} == {0, 1}
    assert len({spec.game_id for spec in specs}) == len(specs)


def test_schedule_rejects_empty_or_invalid_counts() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_game_specs_v1(opponent_ids=("a",), games_per_seat=0, base_seed=1, subject_deck_path=Path("deck.csv"), output_root=Path("runs/out"), source_revision="sha", max_steps=1)
