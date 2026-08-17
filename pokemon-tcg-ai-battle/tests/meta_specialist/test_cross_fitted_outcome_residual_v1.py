"""TDD contracts for sealed cross-fitted MC residual targets."""

from __future__ import annotations

import hashlib

import pytest

from tests.meta_specialist.test_trajectory_v1 import _immediate_stop_transition

from mage_ptcg.meta_specialist.cross_fitted_outcome_residual_v1 import (
    CrossFittedOutcomeResidualError,
    OutcomeEpisodeV1,
    build_cross_fitted_outcome_manifest_v1,
    load_cross_fitted_outcome_manifest_v1,
)


def _episode_id(index: int) -> str:
    return hashlib.sha256(f"episode-{index}".encode()).hexdigest()


def _episode(index: int, terminal_reward: float) -> OutcomeEpisodeV1:
    first_transition = _immediate_stop_transition(terminal=False, reward=0.0)
    terminal_transition = _immediate_stop_transition(terminal=True, reward=terminal_reward)
    return OutcomeEpisodeV1(
        episode_id=_episode_id(index), transitions=(first_transition, terminal_transition),
    )


def test_cross_fitted_manifest_uses_only_episode_return_not_opponent_or_seat() -> None:
    manifest = build_cross_fitted_outcome_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, 1.0), _episode(3, -1.0)),
        fold_count=2,
    )

    assert manifest.objective_kind == "cross_fitted_mc_signed_behavior_residual"
    assert manifest.training_permitted is False
    assert manifest.promotion_authority is False
    assert manifest.longrun_allowed is False
    assert len(manifest.episodes) == 4
    assert all(item.return_value == pytest.approx(item.targets[0].return_value) for item in manifest.episodes)
    assert all(item.targets[0].target_indices for item in manifest.episodes)
    assert all(-1.0 <= item.targets[0].signed_weight <= 1.0 for item in manifest.episodes)
    serialized = str(manifest.to_dict())
    assert "opponent" not in serialized
    assert "seat" not in serialized
    assert "pool-member" not in serialized


def test_cross_fitted_manifest_keeps_negative_advantage_as_a_signed_behavior_target() -> None:
    manifest = build_cross_fitted_outcome_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, -1.0), _episode(3, -1.0)),
        fold_count=2,
    )
    signed = [target.signed_weight for episode in manifest.episodes for target in episode.targets]
    assert any(value > 0.0 for value in signed)
    assert any(value < 0.0 for value in signed)
    assert all(target.target_kind == "signed_behavior_log_probability" for episode in manifest.episodes for target in episode.targets)


def test_cross_fitted_manifest_rejects_nonterminal_or_reentered_episode_topology() -> None:
    episode = _episode(0, 1.0)
    with pytest.raises(CrossFittedOutcomeResidualError, match="terminal|topology"):
        OutcomeEpisodeV1(episode_id=episode.episode_id, transitions=(episode.transitions[0],))

    with pytest.raises(CrossFittedOutcomeResidualError, match="distinct|duplicate"):
        build_cross_fitted_outcome_manifest_v1((episode, episode), fold_count=2)


def test_cross_fitted_manifest_loader_rejects_open_or_teacher_reclassified_payload() -> None:
    manifest = build_cross_fitted_outcome_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, 1.0), _episode(3, -1.0)),
        fold_count=2,
    )
    payload = manifest.to_dict()
    assert load_cross_fitted_outcome_manifest_v1(payload).to_dict() == payload

    open_payload = {**payload, "opponent_id": "forbidden"}
    with pytest.raises(CrossFittedOutcomeResidualError, match="open|unknown|closed"):
        load_cross_fitted_outcome_manifest_v1(open_payload)

    reclassified = manifest.to_dict()
    reclassified["episodes"][0]["targets"][0]["target_kind"] = "teacher_hard_selection"
    with pytest.raises(CrossFittedOutcomeResidualError, match="teacher|target"):
        load_cross_fitted_outcome_manifest_v1(reclassified)
