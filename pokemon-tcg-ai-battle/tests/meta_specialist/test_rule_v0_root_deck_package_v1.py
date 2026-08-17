from __future__ import annotations


def test_package_lane_is_submission_compatible_root_policy_and_two_card_only() -> None:
    from scripts import run_rule_v0_root_deck_package_v1 as runner

    assert runner.PACKAGE_SWAP_COUNT == 2
    assert runner.DEFAULT_WORKERS == 12
    assert runner.DEFAULT_CANDIDATE_COUNT == 2
    assert runner.PARENT_POLICY.name == "main.py"
    assert runner.PARENT_DECK.name == "deck.csv"


def test_package_smoke_gate_is_fail_closed() -> None:
    from scripts import run_rule_v0_root_deck_package_v1 as runner

    assert runner.smoke_passes({"completed_games": 2, "faults": 0})
    assert not runner.smoke_passes({"completed_games": 2, "faults": 1})
    assert not runner.smoke_passes({"completed_games": 1, "faults": 0})


def test_package_rank_requires_two_added_cards() -> None:
    from scripts import run_rule_v0_root_deck_package_v1 as runner

    assert runner.package_rank({"added_cards": [3, 6]}, {3: 1.0, 6: 2.0}) == (-3.0, (3, 6))
    assert runner.package_rank({"added_cards": [3]}, {3: 1.0}) is None
