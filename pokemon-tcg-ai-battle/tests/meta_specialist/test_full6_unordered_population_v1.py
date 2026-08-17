from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.full6_unordered_population_v1 import (
    Full6UnorderedPopulationError,
    build_full6_unordered_population_manifest_v1,
    verify_full6_unordered_population_manifest_v1,
)


ROOT = Path(__file__).resolve().parents[2]
BLOCKED = ROOT / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-full6/bridge-manifest.json"
TOMATO = ROOT / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json"
REPAIR = ROOT / "runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json"


def _build(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    output = tmp_path / "full6-unordered-manifest.json"
    built = build_full6_unordered_population_manifest_v1(
        repo_root=ROOT,
        blocked_full6_bridge_manifest_path=BLOCKED,
        tomato_clean_bridge_manifest_path=TOMATO,
        repair_manifest_path=REPAIR,
        output_manifest_path=output,
    )
    return output, built


def test_actual_full6_unordered_population_is_explicitly_quarantined_and_not_ready(
    tmp_path: Path,
) -> None:
    output, manifest = _build(tmp_path)
    assert manifest["purpose"] == "FULL6_UNORDERED_POPULATION_V1"
    assert manifest["identity"] == "FULL6_UNORDERED_POPULATION_V1"
    assert manifest["coverage"] == {
        "source_decisions": 36684,
        "unordered_set_decisions": 36680,
        "coverage_closed": True,
    }
    assert manifest["ordered_quarantine"] == {
        "status": "QUARANTINED_ORDERED_UNSUPPORTED",
        "count": 4,
        "by_schema": {"5:34": 4},
        "record_ids": None,
        "target_sequences": None,
        "identities_materialized": False,
        "silent_drop": False,
    }
    assert manifest["component_split"]["source_non_ubiquitous_cross_count"] == 1
    assert manifest["component_split"]["output_non_ubiquitous_cross_count"] is None
    assert manifest["component_split"]["closure_verified"] is False
    assert manifest["readiness"]["performance_training_ready"] is False
    assert manifest["readiness"]["raw_reproduction_complete"] is False
    assert manifest["materialization"]["published_rows"] == 0
    assert manifest["authority"] == {
        "training_authority": False,
        "behavior_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
    }
    assert verify_full6_unordered_population_manifest_v1(output, ROOT) == manifest


def test_permission_matrix_does_not_infer_behavior_from_training_local(tmp_path: Path) -> None:
    _, manifest = _build(tmp_path)
    matrix = manifest["permission_matrix"]
    assert len(matrix) == 6
    assert all(row["training_local_allowed"] is True for row in matrix)
    assert all(row["derivative_weights_allowed"] is True for row in matrix)
    assert all(row["behavior_policy_allowed"] is False for row in matrix)
    assert all(row["derivative_action_labels_allowed"] is False for row in matrix)
    assert all(row["teacher_code_submission_allowed"] is False for row in matrix)
    assert all(row["deck_submission_allowed"] is False for row in matrix)


def test_verify_rejects_manifest_tampering(tmp_path: Path) -> None:
    output, manifest = _build(tmp_path)
    tampered = dict(manifest)
    tampered["readiness"] = dict(manifest["readiness"])
    tampered["readiness"]["performance_training_ready"] = True
    output.write_bytes(json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(Full6UnorderedPopulationError, match="SHA|reproduce|readiness"):
        verify_full6_unordered_population_manifest_v1(output, ROOT)


def test_build_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output, _ = _build(tmp_path)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        build_full6_unordered_population_manifest_v1(
            repo_root=ROOT,
            blocked_full6_bridge_manifest_path=BLOCKED,
            tomato_clean_bridge_manifest_path=TOMATO,
            repair_manifest_path=REPAIR,
            output_manifest_path=output,
        )
    assert output.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == hashlib.sha256(output.read_bytes()).hexdigest()
