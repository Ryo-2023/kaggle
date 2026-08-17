from mage_ptcg.optimization.alakazam_single_deviation import (
    ALAKAZAM_BASELINE_V1,
    make_alakazam_single_deviation_agent,
)


def _eligible() -> dict:
    return {"select": {"type": 0, "option": [{"type": 8}, {"type": 7}], "minCount": 1, "maxCount": 1}}


def test_exact_deck_candidate_changes_only_one_eligible_legal_action() -> None:
    policy = make_alakazam_single_deviation_agent(deck=ALAKAZAM_BASELINE_V1)
    assert policy.choose(_eligible()) == [1]
    assert policy.choose(_eligible()) == [0]
    assert policy.interventions == 1


def test_candidate_delegates_for_mismatch_or_non_eligible_selection() -> None:
    mismatch = list(ALAKAZAM_BASELINE_V1)
    mismatch[0] = 1
    policy = make_alakazam_single_deviation_agent(deck=mismatch)
    assert policy.choose(_eligible()) == [0]
    assert policy.compatibility_rejections == 1
    compatible = make_alakazam_single_deviation_agent(deck=ALAKAZAM_BASELINE_V1)
    assert compatible.choose({"select": {"type": 0, "option": [{"type": 9}, {"type": 8}], "minCount": 1, "maxCount": 1}}) == [0]
    assert compatible.interventions == 0
