"""Fail-closed research-only connection contract for frozen V4 ensembles."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.research_ensemble_evaluator_v1 import (
    ResearchCheckpointMemberV1,
    ResearchEnsembleBindingError,
    ResearchEnsembleEvaluationSpecV1,
    build_research_ensemble_binding_v1,
)


def _checkpoint(tmp_path: Path, *, seed: int, hidden_dim: int = 8) -> tuple[Path, str, str]:
    path = tmp_path / f"wave6-seed{seed}.pt"
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=hidden_dim, embedding_dim=6, seed=seed).eval()
    descriptor = save_specialist_checkpoint_v4(path, model)
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), descriptor["tensor_state_sha256"]


def _spec(tmp_path: Path, *, hidden_dim: int = 8) -> ResearchEnsembleEvaluationSpecV1:
    members = []
    for seed in (0, 1):
        path, file_sha, tensor_sha = _checkpoint(tmp_path, seed=seed, hidden_dim=hidden_dim)
        members.append(ResearchCheckpointMemberV1(
            seed=seed,
            checkpoint_path=path,
            file_sha256=file_sha,
            tensor_state_sha256=tensor_sha,
            lineage_id=("a" if seed == 0 else "b") * 64,
        ))
    return ResearchEnsembleEvaluationSpecV1(
        members=tuple(members),
        deck_file_sha256="d" * 64,
        protocol_sha256="e" * 64,
        timeout_seconds=600.0,
        fault_policy="fail_closed",
        reset_mode="normal",
    )


def test_builder_loads_two_hash_bound_wave6_members_and_returns_fresh_factory(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    binding = build_research_ensemble_binding_v1(spec)

    first = binding.new_policy()
    second = binding.new_policy()
    assert first is not second
    assert first.member_count == second.member_count == 2
    assert first.reset_mode == "normal"
    assert binding.evaluation_payload()["fault_policy"] == "fail_closed"
    assert binding.evaluation_payload()["timeout_seconds"] == 600.0
    assert all(
        not parameter.requires_grad
        for member in binding.frozen_member_policies
        for parameter in member._model.parameters()
    )


def test_builder_rejects_checkpoint_hash_lineage_or_seed_contract_mismatch(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    bad_file = ResearchEnsembleEvaluationSpecV1(
        members=(ResearchCheckpointMemberV1(
            seed=0,
            checkpoint_path=spec.members[0].checkpoint_path,
            file_sha256="0" * 64,
            tensor_state_sha256=spec.members[0].tensor_state_sha256,
            lineage_id=spec.members[0].lineage_id,
        ), spec.members[1]),
        deck_file_sha256=spec.deck_file_sha256,
        protocol_sha256=spec.protocol_sha256,
        timeout_seconds=spec.timeout_seconds,
        fault_policy=spec.fault_policy,
        reset_mode=spec.reset_mode,
    )
    with pytest.raises(ResearchEnsembleBindingError, match="file SHA"):
        build_research_ensemble_binding_v1(bad_file)

    duplicate_seed = ResearchEnsembleEvaluationSpecV1(
        members=(spec.members[0], ResearchCheckpointMemberV1(
            seed=0,
            checkpoint_path=spec.members[1].checkpoint_path,
            file_sha256=spec.members[1].file_sha256,
            tensor_state_sha256=spec.members[1].tensor_state_sha256,
            lineage_id=spec.members[1].lineage_id,
        )),
        deck_file_sha256=spec.deck_file_sha256,
        protocol_sha256=spec.protocol_sha256,
        timeout_seconds=spec.timeout_seconds,
        fault_policy=spec.fault_policy,
        reset_mode=spec.reset_mode,
    )
    with pytest.raises(ResearchEnsembleBindingError, match="seed set"):
        build_research_ensemble_binding_v1(duplicate_seed)


def test_builder_rejects_open_timeout_fault_and_model_config(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    with pytest.raises(ValueError, match="timeout_seconds"):
        ResearchEnsembleEvaluationSpecV1(
            members=spec.members, deck_file_sha256=spec.deck_file_sha256,
            protocol_sha256=spec.protocol_sha256, timeout_seconds=0.0,
            fault_policy="fail_closed", reset_mode="normal",
        )
    with pytest.raises(ValueError, match="fault_policy"):
        ResearchEnsembleEvaluationSpecV1(
            members=spec.members, deck_file_sha256=spec.deck_file_sha256,
            protocol_sha256=spec.protocol_sha256, timeout_seconds=600.0,
            fault_policy="continue", reset_mode="normal",
        )

    mismatch = _spec(tmp_path / "mismatch", hidden_dim=9)
    with pytest.raises(ResearchEnsembleBindingError, match="model_config"):
        build_research_ensemble_binding_v1(mismatch)


def test_evaluation_payload_is_closed_and_contains_no_submit_or_training_action(tmp_path: Path) -> None:
    payload = build_research_ensemble_binding_v1(_spec(tmp_path)).evaluation_payload()
    assert set(payload) == {
        "schema_version", "members", "deck_file_sha256", "protocol_sha256",
        "timeout_seconds", "fault_policy", "reset_mode",
    }
    assert payload["schema_version"] == "meta-specialist-research-ensemble-eval-v1"
    assert all(set(member) == {
        "seed", "checkpoint_path", "file_sha256", "tensor_state_sha256", "lineage_id",
    } for member in payload["members"])
    assert "submit" not in payload and "train" not in payload
