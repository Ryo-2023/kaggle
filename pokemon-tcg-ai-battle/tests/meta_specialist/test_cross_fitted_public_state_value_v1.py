"""Contracts for the research-only public-state value baseline."""

from __future__ import annotations

import hashlib

import pytest

from tests.meta_specialist.test_trajectory_v1 import _immediate_stop_transition
from mage_ptcg.meta_specialist.cross_fitted_outcome_residual_v1 import OutcomeEpisodeV1


def _episode(index: int, terminal_reward: float) -> OutcomeEpisodeV1:
    return OutcomeEpisodeV1(
        episode_id=hashlib.sha256(f"public-value-episode-{index}".encode()).hexdigest(),
        transitions=(
            _immediate_stop_transition(terminal=False, reward=0.0),
            _immediate_stop_transition(terminal=True, reward=terminal_reward),
        ),
    )


def test_public_value_manifest_is_cross_fitted_and_uses_bucket_baseline() -> None:
    from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (
        PUBLIC_STATE_VALUE_OBJECTIVE_V1,
        build_cross_fitted_public_state_value_manifest_v1,
    )

    manifest = build_cross_fitted_public_state_value_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, 1.0), _episode(3, -1.0)),
        fold_count=2,
    )
    assert manifest.objective_kind == PUBLIC_STATE_VALUE_OBJECTIVE_V1
    assert len(manifest.episodes) == 4
    assert all(target.baseline_source == "public_bucket" for episode in manifest.episodes for target in episode.targets)
    assert any(target.advantage > 0.0 for episode in manifest.episodes for target in episode.targets)
    assert any(target.advantage < 0.0 for episode in manifest.episodes for target in episode.targets)
    assert manifest.training_permitted is False
    assert manifest.promotion_authority is False
    assert manifest.longrun_allowed is False
    serialized = str(manifest.to_dict())
    assert "opponent_id" not in serialized
    assert "seat" not in serialized


def test_public_value_manifest_records_external_fallback_contract() -> None:
    from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (
        build_cross_fitted_public_state_value_manifest_v1,
    )

    # The fixture exposes one public bucket in every external fold, so no
    # fallback is needed.  The manifest nevertheless carries a closed
    # fallback counter/source contract for heterogeneous real data.
    manifest = build_cross_fitted_public_state_value_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, 1.0), _episode(3, -1.0)),
        fold_count=2,
    )
    assert manifest.fallback_target_count == 0
    assert all(target.baseline_source == "public_bucket" for episode in manifest.episodes for target in episode.targets)


def test_public_value_loader_rejects_open_schema_and_teacher_reclassification() -> None:
    from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (
        PublicStateValueError,
        build_cross_fitted_public_state_value_manifest_v1,
        load_cross_fitted_public_state_value_manifest_v1,
    )

    manifest = build_cross_fitted_public_state_value_manifest_v1(
        (_episode(0, 1.0), _episode(1, -1.0), _episode(2, 1.0), _episode(3, -1.0)),
        fold_count=2,
    )
    payload = manifest.to_dict()
    assert load_cross_fitted_public_state_value_manifest_v1(payload).to_dict() == payload
    with pytest.raises(PublicStateValueError, match="open|unknown|closed"):
        load_cross_fitted_public_state_value_manifest_v1({**payload, "opponent_id": "forbidden"})
    bad = manifest.to_dict()
    bad["episodes"][0]["targets"][0]["target_kind"] = "teacher_hard_selection"
    with pytest.raises(PublicStateValueError, match="target|teacher"):
        load_cross_fitted_public_state_value_manifest_v1(bad)


def test_public_state_model_value_manifest_is_fold_external_and_closed() -> None:
    from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (
        PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1,
        build_cross_fitted_public_state_model_value_manifest_v1,
        load_cross_fitted_public_state_value_manifest_v1,
    )

    manifest = build_cross_fitted_public_state_model_value_manifest_v1(
        tuple(_episode(index, 1.0 if index % 2 == 0 else -1.0) for index in range(6)),
        fold_count=2,
        ridge_lambda=1.0,
    )
    assert manifest.objective_kind == PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1
    assert manifest.value_feature_schema
    assert len(manifest.value_model_sha256) == 64
    assert all(
        target.baseline_source == "public_state_model"
        for episode in manifest.episodes
        for target in episode.targets
    )
    assert load_cross_fitted_public_state_value_manifest_v1(manifest.to_dict()).to_dict() == manifest.to_dict()
    serialized = str(manifest.to_dict())
    assert "opponent_id" not in serialized
    assert "seat" not in serialized
