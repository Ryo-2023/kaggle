"""TDD contracts for the sealed signed-outcome residual materializer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import (
    CrossFittedOutcomeMaterializerError,
    materialize_signed_outcome_targets_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    Wave6ProvenanceV1,
    build_seed_known_manifest_v1,
)
from tests.meta_specialist.test_build_cross_fitted_outcome_residual_manifest import _write_screen


def _domain(source: Path) -> object:
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    provenance = Wave6ProvenanceV1(
        seed=0,
        checkpoint_path="/sealed/wave6.pt",
        checkpoint_file_sha256="a" * 64,
        checkpoint_tensor_state_sha256="b" * 64,
        screen_path="/sealed/screen.json",
        screen_file_sha256="c" * 64,
        transitions_path=str(source),
        transitions_file_sha256=source_sha,
        subject_deck_sha256="d" * 64,
    )
    return build_seed_known_manifest_v1(
        provenance, context_ids=("1" * 64,), action_keys=("2" * 64,),
        transition_count=1, prefix_count=1,
    )


def _manifest(source: Path, output: Path) -> str:
    from scripts.build_cross_fitted_outcome_residual_manifest_v1 import build_manifest_from_screen_jsonl_v1

    build_manifest_from_screen_jsonl_v1(source, output=output, fold_count=2)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def test_materializer_hash_binds_screen_manifest_and_prefix_targets(tmp_path: Path) -> None:
    source = tmp_path / "screen.transitions.jsonl"
    manifest = tmp_path / "targets.json"
    _write_screen(source)
    expected_manifest_sha = _manifest(source, manifest)

    result = materialize_signed_outcome_targets_v1(
        manifest, expected_manifest_sha256=expected_manifest_sha,
        known_domain=_domain(source),
    )

    assert result.seed == 0
    assert len(result.sequences) == 4
    assert sum(len(item.steps) for item in result.sequences) == 8
    assert len(result.prefix_targets) == 8
    assert result.context_only_rows == 8
    assert all(item.target_kind == "signed_behavior_log_probability" for item in result.prefix_targets)
    assert all(item.sequence_step_index < len(result.sequences[item.sequence_index].steps) for item in result.prefix_targets)
    assert all(step.supervision_weight == 0.0 for sequence in result.sequences for step in sequence.steps)
    assert "opponent" not in str(result.to_summary_dict())
    assert "seat" not in str(result.to_summary_dict())


def test_materializer_rejects_manifest_target_outside_sealed_prefix_domain(tmp_path: Path) -> None:
    source = tmp_path / "screen.transitions.jsonl"
    manifest = tmp_path / "targets.json"
    _write_screen(source)
    _manifest(source, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["episodes"][0]["targets"][0]["target_indices"][0] = 999
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(CrossFittedOutcomeMaterializerError, match="target|domain|manifest"):
        materialize_signed_outcome_targets_v1(
            manifest, expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
            known_domain=_domain(source),
        )


def test_materializer_rejects_screen_sha_or_source_episode_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "screen.transitions.jsonl"
    manifest = tmp_path / "targets.json"
    _write_screen(source)
    expected_manifest_sha = _manifest(source, manifest)
    domain = _domain(source)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CrossFittedOutcomeMaterializerError, match="SHA|source"):
        materialize_signed_outcome_targets_v1(manifest, expected_manifest_sha256=expected_manifest_sha, known_domain=domain)


def test_materializer_can_bound_output_only_after_full_hash_join(tmp_path: Path) -> None:
    source = tmp_path / "screen.transitions.jsonl"
    manifest = tmp_path / "targets.json"
    _write_screen(source)
    expected_manifest_sha = _manifest(source, manifest)

    result = materialize_signed_outcome_targets_v1(
        manifest, expected_manifest_sha256=expected_manifest_sha,
        known_domain=_domain(source), max_episodes=2,
    )
    assert len(result.sequences) == 2
    assert len(result.prefix_targets) == 4
    assert result.source_episode_sha256
