"""Focused tests for the fail-closed neural Candidate factory.

These tests never touch the real 630KB+ production export file; they build
small synthetic export documents with the same self-consistent schema (the
``model_hash`` field really is the sha256 digest of the rest of the
document, matching ``offline_training.export.validate_export``'s own
recomputation) so hash-mismatch and load-failure paths can be exercised
quickly and deterministically -- including distinguishing the export
format's *own* self-consistency check from this factory's *expected
identity* check.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from mage_ptcg.competition_intelligence.o5_candidate_factory import (
    NeuralCandidateAgent,
    O5CandidateError,
    build_neural_candidate,
)
from mage_ptcg.competition_intelligence.o5_candidate_registry import CandidateArtifactIdentity, NOT_APPLICABLE

_DECK = list(range(1, 61))
_NO_SELECT_OBS = {"select": None}
_OBS = {"select": {"type": 0, "minCount": 1, "maxCount": 1, "option": [{"type": 14}]}}


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fixture_document(*, weight: float = 1.0, feature_schema_hash: str = "feature-hash-x") -> dict:
    document = {
        "schema_version": "offline-training-v1-neural-export-v1",
        "model_purpose": "TEST_FIXTURE",
        "architecture": {"input_dim": 1, "hidden": [], "output_dim": 1},
        "normalization": {"mean": [0.0], "std": [1.0]},
        "layers": [{"weight": [[weight]], "bias": [0.0]}],
        "feature_dimension": 1,
        "feature_schema_hash": feature_schema_hash,
        "feature_schema_version": "fixture-v1",
        "dataset_hash": "dataset-hash-x",
        "config_hash": "config-hash-x",
        "teacher_id": "rule-agent-v0",
        "fallback_policy": "rule-agent-v0",
    }
    document["model_hash"] = _digest(document)
    return document


_CANONICAL_DOCUMENT = _fixture_document(weight=1.0)
_DIFFERENT_DOCUMENT = _fixture_document(weight=2.0)  # self-consistent, but a genuinely different model


def _write(path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _identity(**overrides) -> CandidateArtifactIdentity:
    base = dict(
        candidate_artifact_id="fixture_candidate",
        model_hash=_CANONICAL_DOCUMENT["model_hash"],
        feature_schema_hash=_CANONICAL_DOCUMENT["feature_schema_hash"],
        feature_schema_version="fixture-v1",
        dataset_artifact_id="fixture-dataset",
        dataset_hash="dataset-hash-x",
        training_config_hash="config-hash-x",
        action_schema_version=NOT_APPLICABLE,
        model_format_version="offline-training-v1-neural-export-v1",
        source_commit="0" * 40,
    )
    base.update(overrides)
    return CandidateArtifactIdentity(**base)


def test_build_neural_candidate_requires_a_model_path():
    with pytest.raises(O5CandidateError, match="model_path"):
        build_neural_candidate(_identity(), model_path=None, deck=_DECK)


def test_build_neural_candidate_fails_closed_when_a_different_self_consistent_model_is_supplied(tmp_path):
    # _DIFFERENT_DOCUMENT is internally self-consistent (its own model_hash
    # matches its own content) but is a genuinely different model than what
    # the identity expects -- this must fail on the *expected* hash check,
    # not the export format's own internal self-consistency check.
    export_path = tmp_path / "export.json"
    _write(export_path, _DIFFERENT_DOCUMENT)
    with pytest.raises(O5CandidateError, match="verification"):
        build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)


def test_build_neural_candidate_fails_closed_on_feature_schema_hash_mismatch(tmp_path):
    export_path = tmp_path / "export.json"
    _write(export_path, _CANONICAL_DOCUMENT)
    identity = _identity(feature_schema_hash="an-expected-hash-that-does-not-match")
    with pytest.raises(O5CandidateError, match="verification"):
        build_neural_candidate(identity, model_path=export_path, deck=_DECK)


def test_build_neural_candidate_fails_closed_on_corrupt_export_self_consistency(tmp_path):
    export_path = tmp_path / "export.json"
    corrupt = dict(_CANONICAL_DOCUMENT)
    corrupt["model_hash"] = "not-the-real-digest"
    _write(export_path, corrupt)
    with pytest.raises(O5CandidateError, match="verification"):
        build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)


def test_build_neural_candidate_fails_closed_on_missing_file(tmp_path):
    with pytest.raises(O5CandidateError):
        build_neural_candidate(_identity(), model_path=tmp_path / "does-not-exist.json", deck=_DECK)


def test_build_neural_candidate_succeeds_and_submits_deck_on_select_none(tmp_path):
    export_path = tmp_path / "export.json"
    _write(export_path, _CANONICAL_DOCUMENT)
    agent = build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)
    assert isinstance(agent, NeuralCandidateAgent)
    assert agent(_NO_SELECT_OBS) == _DECK
    assert agent.fallback_count == 0
    assert agent.inference_count == 0


def test_neural_candidate_agent_falls_back_and_counts_it_when_policy_returns_none(tmp_path):
    export_path = tmp_path / "export.json"
    _write(export_path, _CANONICAL_DOCUMENT)
    agent = build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)
    # The fixture declares feature_dimension=1 but the real state/action
    # feature extractors produce a much larger vector, so applying the 1x1
    # weight matrix raises inside NeuralRuntimePolicy.choose()'s try block
    # and it returns None -- exercising the real fallback path, not a mock.
    selection = agent(_OBS)
    assert selection is not None
    assert agent.inference_count == 1
    assert agent.fallback_count == 1
    assert agent.last_fallback_reason is not None


def test_two_independently_built_candidates_from_the_same_artifact_behave_identically(tmp_path):
    export_path = tmp_path / "export.json"
    _write(export_path, _CANONICAL_DOCUMENT)
    first = build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)
    second = build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)
    assert first(_NO_SELECT_OBS) == second(_NO_SELECT_OBS)
    assert first(_OBS) == second(_OBS)
    assert first.fallback_count == second.fallback_count == 1


def test_candidate_agent_matches_kaggle_environments_call_convention_for_non_function_callables(tmp_path):
    # kaggle_environments.agent.Agent.act() only trims its call args down to
    # 1 (observation-only) when ``hasattr(agent, "__code__")`` is True --
    # true for a plain function, false for a class instance's bound
    # __call__. Every other agent factory in this repo returns a plain
    # function for exactly this reason; NeuralCandidateAgent is the one
    # object-based agent, so it must tolerate being called with both
    # (observation, configuration) exactly as kaggle_environments does.
    export_path = tmp_path / "export.json"
    _write(export_path, _CANONICAL_DOCUMENT)
    agent = build_neural_candidate(_identity(), model_path=export_path, deck=_DECK)
    assert not hasattr(agent, "__code__")
    assert agent(_NO_SELECT_OBS, {"episodeSteps": 10000}) == _DECK
    assert agent(_OBS, {"episodeSteps": 10000}) is not None
