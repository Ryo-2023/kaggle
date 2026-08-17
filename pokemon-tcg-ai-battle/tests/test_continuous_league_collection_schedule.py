from __future__ import annotations

import pytest

from mage_ptcg.continuous_league.collector import (
    CollectionRequest,
    build_collection_schedule,
)
from mage_ptcg.continuous_league.contracts import LeagueContractError, content_id
from mage_ptcg.policy_learning.r2d3.online_collection import MixtureManifest, MixtureMember


def _hash(value: str) -> str:
    return content_id("collection-schedule-test", value)


def _mixture() -> MixtureManifest:
    return MixtureManifest.build(
        [
            MixtureMember("hard", 0.9, _hash("hard-policy"), "remote", "HARD", "submitted"),
            MixtureMember("easy", 0.1, _hash("easy-policy"), "remote", "EASY", "rule_v0"),
        ]
    )


def test_quota_schedule_has_exact_member_and_seat_coverage() -> None:
    request = CollectionRequest(
        population_epoch_id=_hash("epoch"),
        candidate_runtime_policy_id=_hash("candidate"),
        episodes=12,
        base_seed=17,
        subject_deck_id="subject",
        opponent_episode_quotas=(("hard", 8), ("easy", 4)),
    )
    schedule = build_collection_schedule(request, _mixture())
    assert schedule == build_collection_schedule(request, _mixture())
    assert len(schedule) == 12
    counts = {}
    for assignment in schedule:
        key = (assignment.member.opponent_policy_id, assignment.seat)
        counts[key] = counts.get(key, 0) + 1
    assert counts == {
        ("hard", "subject_first"): 4,
        ("hard", "subject_second"): 4,
        ("easy", "subject_first"): 2,
        ("easy", "subject_second"): 2,
    }


def test_quota_schedule_rejects_unknown_or_odd_quota() -> None:
    with pytest.raises(LeagueContractError, match="positive even"):
        CollectionRequest(
            population_epoch_id=_hash("epoch"),
            candidate_runtime_policy_id=_hash("candidate"),
            episodes=3,
            base_seed=17,
            subject_deck_id="subject",
            opponent_episode_quotas=(("hard", 3),),
        )
    request = CollectionRequest(
        population_epoch_id=_hash("epoch"),
        candidate_runtime_policy_id=_hash("candidate"),
        episodes=2,
        base_seed=17,
        subject_deck_id="subject",
        opponent_episode_quotas=(("missing", 2),),
    )
    with pytest.raises(LeagueContractError, match="absent"):
        build_collection_schedule(request, _mixture())
