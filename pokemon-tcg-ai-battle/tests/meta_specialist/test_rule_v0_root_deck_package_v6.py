from __future__ import annotations


def test_package_v6_has_two_distinct_coordinated_hypotheses() -> None:
    from scripts import run_rule_v0_root_deck_package_v6 as runner

    assert runner.PACKAGE_SWAP_COUNT == 2
    assert runner.DEFAULT_WORKERS == 12
    assert runner.FIXED_PACKAGES == (
        ((1102, 1142), (1225, 1121)),
        ((1152, 1182), (1097, 1213)),
    )


def test_package_v6_candidate_builder_preserves_core_and_novelty() -> None:
    from scripts import run_rule_v0_root_deck_package_v6 as runner

    candidates = runner.build_fixed_candidates(
        parent_cards=runner.parent_cards(),
        known_card_ids=runner.load_production_card_vocabulary_v1().recognized_card_ids,
        prior_multisets=set(),
        seed=23698000,
    )
    assert len(candidates) == 2
    assert all(candidate.swap_count == 2 for candidate in candidates)
    assert tuple(candidate.removed_cards for candidate in candidates) == (
        (1102, 1142),
        (1152, 1182),
    )
    assert tuple(candidate.added_cards for candidate in candidates) == (
        (1225, 1121),
        (1097, 1213),
    )
    assert len({candidate.deck_multiset_sha256 for candidate in candidates}) == 2


def test_package_v6_smoke_gate_is_fail_closed() -> None:
    from scripts import run_rule_v0_root_deck_package_v6 as runner

    assert runner.smoke_passes({"completed_games": 2, "faults": 0})
    assert not runner.smoke_passes({"completed_games": 2, "faults": 1})
