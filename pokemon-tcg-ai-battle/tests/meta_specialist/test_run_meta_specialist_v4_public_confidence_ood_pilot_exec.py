"""Focused tests for the dry-run public OOD pilot executor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from scripts import run_meta_specialist_v4_public_confidence_ood_pilot_exec as executor
from scripts.run_meta_specialist_v4_public_confidence_ood_pilot import Wave6SeedBindingV1
from tests.meta_specialist.test_dagger_v4 import _TeacherFactory
from tests.meta_specialist.test_run_meta_specialist_v4_public_confidence_ood_bc import (
    _manifest,
)
from tests.meta_specialist.test_trajectory_v1 import _two_choice_forced_stop_transition


def _provenance() -> dict[str, object]:
    return {
        "schema_version": executor.TEACHER_PROVENANCE_SCHEMA_V1,
        "kind": "rule_teacher",
        "scope": "research-only",
        "policy_identity": "a" * 64,
        "promotion_authority": False,
    }


def _row_payload(*, game_id: str, transition_index: int, transition: object) -> dict[str, object]:
    return {
        "schema": "meta-specialist-v4-dagger-transition-v1",
        "game_id": game_id,
        "episode_group": game_id,
        "component_id": game_id,
        "partition": "train",
        "opponent_id": "must-not-escape",
        "seat": 1,
        "env_seed": 101,
        "transition_index": transition_index,
        "transition": transition.to_dict(),
    }


def _write_rows(path: Path) -> tuple[str, str]:
    transition, _ = _two_choice_forced_stop_transition()
    game_id = "b" * 64
    rows = [_row_payload(game_id=game_id, transition_index=index, transition=transition) for index in range(2)]
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return game_id, hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(tmp_path: Path, transitions_sha: str) -> Wave6SeedBindingV1:
    return Wave6SeedBindingV1(
        seed=0,
        screen_path=tmp_path / "screen.json",
        screen_file_sha256="a" * 64,
        transitions_path=tmp_path / "transitions.jsonl",
        transitions_file_sha256=transitions_sha,
        init_checkpoint_path=tmp_path / "wave6.pt",
        init_checkpoint_file_sha256="e" * 64,
        init_checkpoint_tensor_state_sha256="1" * 64,
    )


def test_load_sealed_rows_hash_binds_and_parses_canonical_transition(tmp_path: Path) -> None:
    path = tmp_path / "transitions.jsonl"
    game_id, digest = _write_rows(path)
    binding = _binding(tmp_path, digest)
    rows = executor.load_sealed_wave6_transition_rows_v1(path, binding=binding, partition="train")
    assert len(rows) == 2
    assert rows[0]["game_id"] == game_id
    assert type(rows[0]["parsed_transition"]) is type(parse_transition_payload_v4(rows[0]["transition"]))
    assert rows[0]["transition_index"] == 0
    assert rows[1]["transition_index"] == 1


def test_load_sealed_rows_rejects_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "transitions.jsonl"
    _write_rows(path)
    binding = _binding(tmp_path, "c" * 64)
    with pytest.raises(ValueError, match="SHA|hash"):
        executor.load_sealed_wave6_transition_rows_v1(path, binding=binding, partition="train")


def test_teacher_relabel_mask_keeps_full_episode_for_control_and_candidate(tmp_path: Path) -> None:
    path = tmp_path / "transitions.jsonl"
    game_id, digest = _write_rows(path)
    binding = _binding(tmp_path, digest)
    rows = executor.load_sealed_wave6_transition_rows_v1(path, binding=binding, partition="train")
    masks = {
        (game_id, 0): (True, False, False),
        (game_id, 1): (False, True, False),
    }

    material = executor.build_masked_episode_material_v1(
        rows,
        eligible_by_transition=masks,
        teacher_factory=_TeacherFactory(),
        teacher_provenance=_provenance(),
        lane="archaludon",
    )
    assert material.transition_count == 2
    assert material.prefix_count == 6
    assert material.eligible_prefix_count == 2
    assert material.context_only_prefix_count == 4
    assert material.effective_loss_mass == 2.0
    assert len(material.control_sequences) == len(material.candidate_sequences) == 1
    control = material.control_sequences[0]
    candidate = material.candidate_sequences[0]
    assert len(control.steps) == len(candidate.steps) == 6
    assert tuple(step.episode_start for step in candidate.steps).count(True) == 1
    assert tuple(step.supervision_weight for step in control.steps) == (1.0,) * 6
    assert tuple(step.supervision_weight for step in candidate.steps) == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    assert material.control_topology_sha256 == material.candidate_topology_sha256
    assert material.control_sequence_sha256 != material.candidate_sequence_sha256


def test_teacher_relabel_drops_games_without_any_eligible_prefix(tmp_path: Path) -> None:
    """A candidate sequence must never reach the trainer with zero loss rows."""
    path = tmp_path / "transitions.jsonl"
    transition, _ = _two_choice_forced_stop_transition()
    eligible_game = "b" * 64
    context_only_game = "c" * 64
    rows = [
        _row_payload(game_id=eligible_game, transition_index=0, transition=transition),
        _row_payload(game_id=context_only_game, transition_index=0, transition=transition),
    ]
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = executor.load_sealed_wave6_transition_rows_v1(
        path, binding=_binding(tmp_path, digest), partition="train",
    )

    material = executor.build_masked_episode_material_v1(
        loaded,
        eligible_by_transition={
            (eligible_game, 0): (True, False, False),
            (context_only_game, 0): (False, False, False),
        },
        teacher_factory=_TeacherFactory(),
        teacher_provenance=_provenance(),
        lane="archaludon",
    )

    assert material.transition_count == 1
    assert material.prefix_count == 3
    assert material.eligible_prefix_count == 1
    assert len(material.candidate_sequences) == 1
    assert tuple(step.supervision_weight for step in material.candidate_sequences[0].steps) == (1.0, 0.0, 0.0)


def test_teacher_relabel_requires_explicit_research_only_provenance(tmp_path: Path) -> None:
    path = tmp_path / "transitions.jsonl"
    _game_id, digest = _write_rows(path)
    rows = executor.load_sealed_wave6_transition_rows_v1(path, binding=_binding(tmp_path, digest), partition="train")
    with pytest.raises(ValueError, match="research-only|provenance"):
        executor.build_masked_episode_material_v1(
            rows,
            eligible_by_transition={},
            teacher_factory=_TeacherFactory(),
            teacher_provenance={**_provenance(), "scope": "production"},
            lane="archaludon",
        )


def test_executor_default_is_dry_run_and_reports_mask_without_trainer_call(tmp_path: Path) -> None:
    path = tmp_path / "transitions.jsonl"
    _game_id, digest = _write_rows(path)
    rows = executor.load_sealed_wave6_transition_rows_v1(path, binding=_binding(tmp_path, digest), partition="train")
    # The executor's file path remains dry-run only; no trainer callback is
    # supplied or invoked.  The report records explicit execution state.
    report = executor.build_pilot_execution_report_v1(
        seed=0,
        binding=_binding(tmp_path, digest),
        rows=rows,
        material=None,
        common_reference_artifact="bundle.json",
        common_reference_artifact_sha256="f" * 64,
        common_reference_source_list_sha256="b" * 64,
        policy_manifest=_manifest(),
        execute=False,
    )
    assert report["execution"] == "DRY_RUN_NOT_EXECUTED"
    assert report["training_started"] is False
    assert report["cabt_eval_started"] is False
    assert report["wave6"]["transitions_file_sha256"] == digest
