from __future__ import annotations

from pathlib import Path

import pytest


def test_root_meta_weighted_runner_defaults_to_parallel_workers() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_search_v1 import (
        DEFAULT_WORKER_RECYCLE_GAMES,
        DEFAULT_WORKERS,
    )

    assert DEFAULT_WORKERS == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES == 16


def test_root_meta_weighted_candidate_generation_preserves_root_core() -> None:
    from scripts.run_rule_v0_meta_weighted_auto_search_v1 import (
        ROOT_CORE_COUNTS,
        generate_root_meta_candidates,
    )

    parent = tuple([673] * 2 + [674] * 2 + [675] * 2 + [676] * 3 + [677] * 4 + [678] * 4 + [6] * 14 + list(range(1000, 1029)))
    frequency_rows = tuple((card, float(1000 - index), 1.0) for index, card in enumerate((1086, 1087, 1088, 1089)))
    candidates = generate_root_meta_candidates(
        parent_cards=parent,
        frequency_rows=frequency_rows,
        prior_multisets=set(),
        known_card_ids=tuple(set(parent) | {1086, 1087, 1088, 1089}),
        candidate_count=2,
        seed=123,
    )

    assert len(candidates) == 2
    for candidate in candidates:
        for card_id, minimum in ROOT_CORE_COUNTS.items():
            assert candidate.card_ids.count(card_id) >= minimum
        assert len(candidate.card_ids) == 60


def test_root_meta_weighted_output_is_confined_to_final_sprint(tmp_path: Path) -> None:
    from scripts.run_rule_v0_meta_weighted_auto_search_v1 import _fresh_root

    with pytest.raises(ValueError):
        _fresh_root(tmp_path / "outside")
