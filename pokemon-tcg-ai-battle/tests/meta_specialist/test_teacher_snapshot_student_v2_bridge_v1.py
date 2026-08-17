from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_decoder_compatibility_expands_fixed_unordered_multi_positive_targets() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        classify_student_v2_decoder_compatibility_v1,
    )

    result = classify_student_v2_decoder_compatibility_v1(
        selection_type=1,
        selection_context=8,
        minimum=3,
        maximum=3,
        target_digests=("a" * 64, "b" * 64, "c" * 64),
        legal_digests=("d" * 64, "b" * 64, "a" * 64, "c" * 64),
    )

    assert result == {
        "status": "SUPPORTED_MULTI_POSITIVE",
        "reason": None,
        "decoder_count": 3,
        "replica_target_digests": ["a" * 64, "b" * 64, "c" * 64],
        "stop_semantics": "fixed_cardinality",
    }


@pytest.mark.parametrize(
    ("minimum", "maximum", "targets", "expected_reason"),
    [
        (0, 1, (), "optional_decline_not_representable"),
        (1, 2, ("a" * 64, "b" * 64), "decoder_cardinality_mismatch"),
    ],
)
def test_decoder_compatibility_fails_closed_for_unrepresentable_cardinality(
    minimum: int,
    maximum: int,
    targets: tuple[str, ...],
    expected_reason: str,
) -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        classify_student_v2_decoder_compatibility_v1,
    )

    result = classify_student_v2_decoder_compatibility_v1(
        selection_type=1,
        selection_context=7,
        minimum=minimum,
        maximum=maximum,
        target_digests=targets,
        legal_digests=("a" * 64, "b" * 64, "c" * 64),
    )

    assert result["status"] == "UNSUPPORTED"
    assert result["reason"] == expected_reason
    assert result["replica_target_digests"] == []


def test_decoder_compatibility_records_ordered_and_forced_noop_without_dropping() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        classify_student_v2_decoder_compatibility_v1,
    )

    ordered = classify_student_v2_decoder_compatibility_v1(
        selection_type=5,
        selection_context=34,
        minimum=1,
        maximum=2,
        target_digests=("a" * 64,),
        legal_digests=("a" * 64, "b" * 64),
    )
    forced = classify_student_v2_decoder_compatibility_v1(
        selection_type=0,
        selection_context=0,
        minimum=0,
        maximum=0,
        target_digests=(),
        legal_digests=(),
    )

    assert ordered["status"] == "UNSUPPORTED"
    assert ordered["reason"] == "ordered_selection_not_representable"
    assert forced == {
        "status": "NO_TRAINABLE_CHOICE",
        "reason": "forced_empty_selection",
        "decoder_count": 0,
        "replica_target_digests": [],
        "stop_semantics": "forced_stop",
    }


def test_real_sealed_teacher_bridge_audits_all_records_and_refuses_partial_dataset(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        build_teacher_snapshot_student_v2_bridge_v1,
    )

    dataset = tmp_path / "student-v2.jsonl"
    audit = tmp_path / "bridge-manifest.json"
    result = build_teacher_snapshot_student_v2_bridge_v1(
        repo_root=ROOT,
        catalog_path=CATALOG,
        output_dataset_path=dataset,
        output_manifest_path=audit,
        teacher_ids=("tomatomato_archaludon",),
    )

    assert result["schema_version"] == "meta-specialist-teacher-student-v2-bridge-v1"
    assert result["performance_training_ready"] is False
    assert result["output_dataset"] is None
    assert result["output_dataset_sha256"] is None
    assert not dataset.exists()
    assert audit.is_file()
    assert audit.read_bytes() == _canonical(result)
    assert result["sources"][0]["teacher_id"] == "tomatomato_archaludon"
    assert result["sources"][0]["source_records"] == 5110
    assert result["compatibility"]["supported_multi_positive_decisions"] > 0
    assert result["compatibility"]["unsupported_by_reason"][
        "optional_decline_not_representable"
    ] > 0
    assert result["feature_boundary"] == {
        "model_inputs": [
            "rule_bc_example.public_state",
            "rule_bc_example.own_private_state",
            "rule_bc_example.visible_history",
            "rule_bc_example.legal_actions",
        ],
        "metadata_excluded_from_features": [
            "opponent_id",
            "candidate_side",
            "teacher_identity",
        ],
    }
    assert result["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "teacher_code_submission_allowed": False,
        "teacher_deck_submission_allowed": False,
    }


def test_bridge_rejects_rehashed_catalog_binding_before_reading_teacher_data(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        build_teacher_snapshot_student_v2_bridge_v1,
    )

    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["teachers"][0]["policy"]["sha256"] = "0" * 64
    payload["catalog_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in payload.items() if key != "catalog_sha256"})
    ).hexdigest()
    tampered = tmp_path / "catalog.json"
    tampered.write_bytes(_canonical(payload))

    with pytest.raises(DerivedTeacherCatalogError, match="policy SHA-256"):
        build_teacher_snapshot_student_v2_bridge_v1(
            repo_root=ROOT,
            catalog_path=tampered,
            output_dataset_path=tmp_path / "dataset.jsonl",
            output_manifest_path=tmp_path / "manifest.json",
            teacher_ids=("tomatomato_archaludon",),
        )


def test_bridge_cli_returns_machine_readable_failure_without_creating_outputs(
    tmp_path: Path,
) -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["teachers"][0]["deck"]["sha256"] = "0" * 64
    payload["catalog_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in payload.items() if key != "catalog_sha256"})
    ).hexdigest()
    tampered = tmp_path / "catalog.json"
    tampered.write_bytes(_canonical(payload))
    dataset = tmp_path / "dataset.jsonl"
    manifest = tmp_path / "manifest.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_teacher_snapshot_student_v2_bridge_v1.py"),
            "--repo-root",
            str(ROOT),
            "--catalog",
            str(tampered),
            "--output-dataset",
            str(dataset),
            "--output-manifest",
            str(manifest),
            "--teacher-id",
            "tomatomato_archaludon",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
    )

    assert completed.returncode == 2
    failure = json.loads(completed.stdout)
    assert failure["error"] == "DerivedTeacherCatalogError"
    assert "deck SHA-256" in failure["message"]
    assert not dataset.exists()
    assert not manifest.exists()


def test_record_source_must_bind_catalog_teacher_kind_and_policy_sha() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        TeacherSnapshotStudentV2BridgeError,
        _require_record_teacher_binding,
    )

    teacher = {
        "source_kind": "team_internal_agent",
        "policy": {"sha256": "a" * 64},
    }
    _require_record_teacher_binding(
        {
            "source": {
                "kind": "team_internal_agent",
                "artifact_sha256": "a" * 64,
            }
        },
        teacher=teacher,
    )
    with pytest.raises(TeacherSnapshotStudentV2BridgeError, match="source kind"):
        _require_record_teacher_binding(
            {
                "source": {
                    "kind": "pooled_external_submission_agent",
                    "artifact_sha256": "a" * 64,
                }
            },
            teacher=teacher,
        )


def test_raw_record_must_match_snapshot_again_on_write_pass() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        TeacherSnapshotStudentV2BridgeError,
        _require_snapshot_record_match,
    )

    record_id = "a" * 64
    record = {
        "record_id": record_id,
        "content_hash": "b" * 64,
        "episode_id_hash": "c" * 64,
        "near_duplicate_id": "d" * 64,
        "teacher": {"value_target": 1.0},
    }
    snapshots = {
        record_id: {
            "record_content_hash": "b" * 64,
            "episode_id_hash": "c" * 64,
            "near_duplicate_id": "d" * 64,
            "split": "train",
            "value_target": 1.0,
        }
    }
    assert _require_snapshot_record_match(record, snapshots=snapshots) == snapshots[record_id]
    with pytest.raises(TeacherSnapshotStudentV2BridgeError, match="disagree"):
        _require_snapshot_record_match(
            {**record, "content_hash": "d" * 64}, snapshots=snapshots
        )
    with pytest.raises(TeacherSnapshotStudentV2BridgeError, match="disagree"):
        _require_snapshot_record_match(
            {**record, "near_duplicate_id": "e" * 64}, snapshots=snapshots
        )


def test_catalog_verified_explicit_omission_is_not_rejected_as_unlabelled() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        _require_teacher_manifest_counts,
    )

    teacher = {
        "collection": {
            "game_counts": {
                "requested": 96,
                "completed": 96,
                "faulted": 0,
                "unlabelled": 1,
                "other_status_count": 0,
            }
        }
    }
    manifest = {
        "games_requested": 96,
        "games_completed": 96,
        "games_faulted": 0,
        "decisions_unlabelled": 1,
        "games_other_status": [],
    }
    _require_teacher_manifest_counts(manifest, teacher=teacher)


def test_hardened_snapshot_source_artifacts_require_the_closed_binding_set() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        TeacherSnapshotStudentV2BridgeError,
        _require_snapshot_source_artifacts,
    )

    teacher = {
        "source_kind": "team_internal_agent",
        "policy": {"sha256": "a" * 64},
    }
    rows = [
        {"kind": "team_internal_agent", "artifact_sha256": "a" * 64},
        {"kind": "teacher_collection_manifest_v2", "artifact_sha256": "b" * 64},
        {"kind": "teacher_collection_contract_v2", "artifact_sha256": "c" * 64},
        {"kind": "teacher_collection_omissions_v2", "artifact_sha256": "d" * 64},
        {"kind": "teacher_collector_source_snapshot_v2", "artifact_sha256": "e" * 64},
        {"kind": "teacher_permission_trusted_bytes_v1", "artifact_sha256": "f" * 64},
        {"kind": "teacher_source_kind:team_internal_agent", "artifact_sha256": "1" * 64},
    ]
    assert _require_snapshot_source_artifacts(rows, teacher=teacher) == {
        row["kind"]: row["artifact_sha256"] for row in rows
    }
    with pytest.raises(TeacherSnapshotStudentV2BridgeError, match="closed hardened"):
        _require_snapshot_source_artifacts(rows[:-1], teacher=teacher)
    with pytest.raises(TeacherSnapshotStudentV2BridgeError, match="closed hardened"):
        _require_snapshot_source_artifacts(
            [*rows, {"kind": "unknown", "artifact_sha256": "2" * 64}],
            teacher=teacher,
        )
