from __future__ import annotations

import math

import pytest

from mage_ptcg.meta_specialist.trajectory_schema_v3 import TrajectoryDecisionV3, TrajectoryEpisodeV3
from mage_ptcg.meta_specialist.representation_v3 import ActionCandidateV3, EntityTokenV3, RelationalStateV3


def _state() -> RelationalStateV3:
    return RelationalStateV3(
        (0.0,) * 41,
        (EntityTokenV3(1, 1, 1, 1, 10, None, (0.0,), (), ()),),
        (ActionCandidateV3("a" * 64, 0, 1, None, (), (), 0), ActionCandidateV3("b" * 64, 1, 1, None, (), (), 0)),
    )


def test_trajectory_stores_base_behavior_distribution_not_gumbel_logits() -> None:
    decision = TrajectoryDecisionV3(
        state=_state(), base_logits=(2.0, 0.0), base_log_probs=(-0.126928, -2.126928), chosen_index=0,
        behavior_log_prob=-0.126928, sampling_mode="gumbel-max", legal_action_count=2,
        normalized_entropy=0.527, policy_version="p0", hidden_state_hash="c" * 64,
        model_latency_ms=1.0, environment_latency_ms=2.0,
    )
    episode = TrajectoryEpisodeV3("e" * 64, (decision,), "win", "opp")
    assert episode.decisions[0].base_logits != episode.decisions[0].base_log_probs


def test_trajectory_rejects_misaligned_behavior_probability() -> None:
    with pytest.raises(ValueError, match="behavior_log_prob"):
        TrajectoryDecisionV3(
            state=_state(), base_logits=(2.0, 0.0), base_log_probs=(-0.126928, -2.126928), chosen_index=0,
            behavior_log_prob=-2.0, sampling_mode="greedy", legal_action_count=2,
            normalized_entropy=0.527, policy_version="p0", hidden_state_hash="c" * 64,
            model_latency_ms=1.0, environment_latency_ms=2.0,
        )
