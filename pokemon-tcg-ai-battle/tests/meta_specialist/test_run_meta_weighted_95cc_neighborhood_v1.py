from __future__ import annotations

from pathlib import Path


def test_95cc_neighborhood_binds_the_sealed_native_parent() -> None:
    from scripts import run_meta_weighted_95cc_neighborhood_v1 as runner

    assert runner.PARENT_ID == "tomato-native-95cc-meta-neighborhood-parent"
    assert runner.PARENT_DECK.name == "deck.csv"
    assert "95cc2c77" in str(runner.PARENT_DECK)
    assert runner.PARENT_POLICY.name == "main.py"
    assert runner.DEFAULT_WORKERS == 12
    assert runner.DEFAULT_CANDIDATE_COUNT == 2


def test_95cc_neighborhood_smoke_gate_is_fail_closed() -> None:
    from scripts import run_meta_weighted_95cc_neighborhood_v1 as runner

    assert runner.smoke_passes({"completed_games": 2, "faults": 0})
    assert not runner.smoke_passes({"completed_games": 1, "faults": 0})
    assert not runner.smoke_passes({"completed_games": 2, "faults": 1})


def test_95cc_neighborhood_rejects_mismatched_smoke_identity() -> None:
    from scripts import run_meta_weighted_95cc_neighborhood_v1 as runner

    assert runner.smoke_identity_matches(
        {"candidate_id": "a", "deck_multiset_sha256": "deck-a"},
        {"candidate_id": "a", "deck_multiset_sha256": "deck-a"},
    )
    assert not runner.smoke_identity_matches(
        {"candidate_id": "a", "deck_multiset_sha256": "deck-a"},
        {"candidate_id": "a", "deck_multiset_sha256": "deck-b"},
    )
