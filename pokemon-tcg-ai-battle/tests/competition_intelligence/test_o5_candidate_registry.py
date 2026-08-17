"""Focused tests for the hash-pinned candidate artifact identity registry."""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.o5_candidate_registry import (
    CANDIDATE_ARTIFACT_REGISTRY,
    NEURAL_ACTUAL_TRAINED,
    NOT_APPLICABLE,
    CandidateArtifactIdentity,
    O5CandidateRegistryError,
    resolve_candidate_identity,
)


def test_neural_actual_trained_is_registered_with_a_64_hex_model_hash():
    identity = resolve_candidate_identity("neural_actual_trained")
    assert identity is NEURAL_ACTUAL_TRAINED
    assert len(identity.model_hash) == 64
    assert all(c in "0123456789abcdef" for c in identity.model_hash)


def test_unknown_candidate_artifact_id_is_rejected():
    with pytest.raises(O5CandidateRegistryError):
        resolve_candidate_identity("not_a_real_candidate")


def test_action_schema_version_is_explicitly_not_applicable_rather_than_invented():
    assert NEURAL_ACTUAL_TRAINED.action_schema_version == NOT_APPLICABLE


def test_registry_contains_exactly_the_identity_it_maps_to():
    for candidate_id, identity in CANDIDATE_ARTIFACT_REGISTRY.items():
        assert identity.candidate_artifact_id == candidate_id


def test_identity_rejects_blank_fields():
    with pytest.raises(O5CandidateRegistryError):
        CandidateArtifactIdentity(
            candidate_artifact_id="x", model_hash="", feature_schema_hash="h", feature_schema_version="v",
            dataset_artifact_id="d", dataset_hash="h2", training_config_hash="h3",
            action_schema_version=NOT_APPLICABLE, model_format_version="v2", source_commit="c",
        )
