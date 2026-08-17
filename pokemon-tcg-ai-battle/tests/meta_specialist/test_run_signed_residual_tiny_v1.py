"""Focused CLI contracts for the bounded signed-outcome residual runner."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import (
    materialize_signed_outcome_targets_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    Wave6ProvenanceV1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import STOP_ACTION_KEY_V1, build_residual_context_v1
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from tests.meta_specialist.test_build_cross_fitted_outcome_residual_manifest import _write_screen
from tests.meta_specialist.test_cross_fitted_outcome_materializer_v1 import _domain, _manifest

from scripts import run_signed_residual_tiny_v1 as runner


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, str, Path, str]:
    source0 = tmp_path / "seed0.transitions.jsonl"
    source1 = tmp_path / "seed1.transitions.jsonl"
    target = tmp_path / "seed0-targets.json"
    checkpoint0 = tmp_path / "seed0.pt"
    checkpoint1 = tmp_path / "seed1.pt"
    _write_screen(source0)
    source1.write_bytes(source0.read_bytes() + b"\n")
    descriptor0 = save_specialist_checkpoint_v4(
        checkpoint0, SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=201),
    )
    descriptor1 = save_specialist_checkpoint_v4(
        checkpoint1, SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=16, embedding_dim=12, seed=202),
    )
    target_sha = _manifest(source0, target)
    initial_domain = _domain(source0)
    provisional = replace(
        initial_domain.provenance,
        checkpoint_path=str(checkpoint0),
        checkpoint_file_sha256=_file_sha(checkpoint0),
        checkpoint_tensor_state_sha256=str(descriptor0["tensor_state_sha256"]),
        screen_path=str(source0),
        screen_file_sha256=_file_sha(source0),
    )
    provisional_domain = build_seed_known_manifest_v1(
        provisional,
        context_ids=initial_domain.context_ids,
        action_keys=initial_domain.action_keys,
        transition_count=initial_domain.transition_count,
        prefix_count=initial_domain.prefix_count,
    )
    materialization = materialize_signed_outcome_targets_v1(
        target, expected_manifest_sha256=target_sha, known_domain=provisional_domain,
    )
    contexts: list[str] = []
    action_keys = [STOP_ACTION_KEY_V1]
    for sequence in materialization.sequences:
        for step in sequence.steps:
            context = build_residual_context_v1(step.model_input, step.step_input)
            contexts.append(context.context_id)
            action_keys.extend(context.action_keys)
    seed0 = build_seed_known_manifest_v1(
        provisional,
        context_ids=contexts,
        action_keys=action_keys,
        transition_count=initial_domain.transition_count,
        prefix_count=sum(len(sequence.steps) for sequence in materialization.sequences),
    )
    seed1_provenance = Wave6ProvenanceV1(
        seed=1,
        checkpoint_path=str(checkpoint1),
        checkpoint_file_sha256=_file_sha(checkpoint1),
        checkpoint_tensor_state_sha256=str(descriptor1["tensor_state_sha256"]),
        screen_path=str(source1),
        screen_file_sha256=_file_sha(source1),
        transitions_path=str(source1),
        transitions_file_sha256=_file_sha(source1),
        subject_deck_sha256=provisional.subject_deck_sha256,
    )
    seed1 = build_seed_known_manifest_v1(
        seed1_provenance,
        context_ids=("e" * 64,),
        action_keys=(STOP_ACTION_KEY_V1,),
        transition_count=1,
        prefix_count=1,
    )
    preflight = build_frozen_residual_preflight_manifest_v1(
        (seed0, seed1), subject_deck_sha256=provisional.subject_deck_sha256,
    )
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps(preflight.to_dict(), sort_keys=True), encoding="utf-8")
    return preflight_path, _file_sha(preflight_path), target, target_sha


def test_signed_tiny_runner_requires_execute_and_explicit_max_episodes(tmp_path: Path) -> None:
    preflight, preflight_sha, target, target_sha = _fixture(tmp_path)
    with pytest.raises(SystemExit) as no_execute:
        runner.main([
            "--preflight", str(preflight), "--preflight-sha256", preflight_sha,
            "--seed", "0", "--outcome-target-manifest", str(target),
            "--outcome-target-manifest-sha256", target_sha, "--max-episodes", "2",
            "--output-dir", str(tmp_path / "out"),
        ])
    assert no_execute.value.code == 2
    with pytest.raises(SystemExit) as no_bound:
        runner.main([
            "--execute", "--preflight", str(preflight), "--preflight-sha256", preflight_sha,
            "--seed", "0", "--outcome-target-manifest", str(target),
            "--outcome-target-manifest-sha256", target_sha,
            "--output-dir", str(tmp_path / "out"),
        ])
    assert no_bound.value.code == 2


def test_signed_tiny_runner_writes_non_promotable_hash_bound_report(tmp_path: Path) -> None:
    preflight, preflight_sha, target, target_sha = _fixture(tmp_path)
    output_dir = tmp_path / "tiny-output"
    assert runner.main([
        "--execute", "--preflight", str(preflight), "--preflight-sha256", preflight_sha,
        "--seed", "0", "--outcome-target-manifest", str(target),
        "--outcome-target-manifest-sha256", target_sha, "--max-episodes", "2",
        "--max-updates", "1", "--output-dir", str(output_dir),
    ]) == 0
    payload = json.loads((output_dir / "seed-0-signed-tiny-report.json").read_text(encoding="utf-8"))
    assert payload["evidence_class"] == "SELF_SIGNED_OUTCOME_INTEGRATION_ONLY"
    assert payload["performance_evidence"] is False
    assert payload["base_checkpoint_file_sha256_before"] == payload["base_checkpoint_file_sha256_after"]
    assert payload["base_checkpoint_tensor_state_sha256_before"] == payload["base_checkpoint_tensor_state_sha256_after"]
    assert payload["target_kind"] == "signed_behavior_log_probability"
    assert payload["target_manifest_file_sha256"] == target_sha
    assert payload["positive_effective_mass"] + payload["negative_effective_mass"] == pytest.approx(payload["loss_normalizer"])
    assert payload["training_permitted"] is False
    assert payload["promotion_authority"] is False
    assert payload["longrun_allowed"] is False
    assert "opponent" not in json.dumps(payload)
    assert "seat" not in json.dumps(payload)
    assert (output_dir / "seed-0-signed-residual-sidecar.pt").is_file()
