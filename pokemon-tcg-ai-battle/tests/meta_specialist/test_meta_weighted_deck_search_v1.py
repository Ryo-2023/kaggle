from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.meta_weighted_deck_search_v1 import (
    MetaWeightedDeckSearchError,
    build_weighted_card_frequency_v1,
    generate_meta_weighted_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_weighted_card_frequency_is_deterministic_and_weighted(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    # The parser enforces real 60-card decks.  Use deliberately simple decks
    # so weighted copy frequency is easy to verify: card 1 appears 60 times
    # in the 0.75-weight deck and card 2 appears 60 times in the 0.25 deck.
    first.write_text("1\n" * 60, encoding="utf-8")
    second.write_text("2\n" * 60, encoding="utf-8")

    result = build_weighted_card_frequency_v1(
        deck_paths={"first": first, "second": second},
        selected_ids=("first", "second"),
        selected_weights={"first": 0.75, "second": 0.25},
    )

    assert result[0][0] == 1
    assert result[0][1] == pytest.approx(45.0)
    assert result[1][0] == 2
    assert result[1][1] == pytest.approx(15.0)
    assert result == build_weighted_card_frequency_v1(
        deck_paths={"second": second, "first": first},
        selected_ids=("first", "second"),
        selected_weights={"first": 0.75, "second": 0.25},
    )


def test_generate_candidates_is_novel_deterministic_and_diversified() -> None:
    parent = tuple(int(token) for token in (REPO_ROOT / "deck.csv").read_text().split())
    replacement_pool = (1, 2, 3, 5, 6, 8, 1097, 1121, 1122, 1182, 1194, 1213)
    # The vocabulary is intentionally supplied by the caller in production;
    # the test only needs a small known set containing the parent and pool IDs.
    known = set(parent) | set(replacement_pool)
    core = CoreSignatureV1(
        archetype_id="root-archaludon",
        required_counts={673: 2, 674: 2, 675: 2, 676: 3, 677: 4, 678: 4},
    )
    candidates = generate_meta_weighted_candidates_v1(
        parent_cards=parent,
        replacement_pool=replacement_pool,
        card_frequency={1: 0.1, 2: 0.2, 3: 0.3, 5: 0.4, 6: 1.0, 8: 0.9, 1097: 0.8, 1121: 0.7, 1122: 0.6, 1182: 0.5, 1194: 0.4, 1213: 0.3},
        prior_multisets=set(),
        known_card_ids=known,
        core_signature=core,
        candidate_count=4,
        seed=20260820,
    )
    assert len(candidates) == 4
    assert [item.to_dict() for item in candidates] == [
        item.to_dict()
        for item in generate_meta_weighted_candidates_v1(
            parent_cards=parent,
            replacement_pool=replacement_pool,
            card_frequency={1: 0.1, 2: 0.2, 3: 0.3, 5: 0.4, 6: 1.0, 8: 0.9, 1097: 0.8, 1121: 0.7, 1122: 0.6, 1182: 0.5, 1194: 0.4, 1213: 0.3},
            prior_multisets=set(),
            known_card_ids=known,
            core_signature=core,
            candidate_count=4,
            seed=20260820,
        )
    ]
    assert len({item.added_cards[0] for item in candidates}) == 4
    assert all(item.authority.to_dict() == {"promotion_allowed": False, "training_allowed": False, "submission_allowed": False} for item in candidates)


def test_candidate_count_and_weight_contracts_fail_closed() -> None:
    parent = tuple(int(token) for token in (REPO_ROOT / "deck.csv").read_text().split())
    with pytest.raises(MetaWeightedDeckSearchError, match="candidate_count"):
        generate_meta_weighted_candidates_v1(
            parent_cards=parent,
            replacement_pool=(1, 2),
            card_frequency={1: 1.0, 2: 0.5},
            prior_multisets=set(),
            known_card_ids=None,
            candidate_count=0,
            seed=1,
            core_signature=CoreSignatureV1(
                archetype_id="root-archaludon",
                required_counts={673: 2, 674: 2, 675: 2, 676: 3, 677: 4, 678: 4},
            ),
        )
    with pytest.raises(MetaWeightedDeckSearchError, match="weights"):
        build_weighted_card_frequency_v1(
            deck_paths={"first": Path("/missing")},
            selected_ids=("first",),
            selected_weights={"other": 1.0},
        )
