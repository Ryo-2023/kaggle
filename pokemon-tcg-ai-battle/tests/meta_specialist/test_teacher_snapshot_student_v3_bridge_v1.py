from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

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


def test_v3_compatibility_supports_zero_variable_and_fixed_unordered_sets() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        classify_student_v3_set_compatibility_v1,
    )

    optional_decline = classify_student_v3_set_compatibility_v1(
        selection_type=1,
        selection_context=7,
        minimum=0,
        maximum=2,
        target_digests=(),
        legal_digests=("a" * 64, "b" * 64),
    )
    variable_multi = classify_student_v3_set_compatibility_v1(
        selection_type=1,
        selection_context=7,
        minimum=0,
        maximum=2,
        target_digests=("a" * 64, "b" * 64),
        legal_digests=("a" * 64, "b" * 64, "c" * 64),
    )
    fixed_multi = classify_student_v3_set_compatibility_v1(
        selection_type=1,
        selection_context=8,
        minimum=2,
        maximum=2,
        target_digests=("a" * 64, "b" * 64),
        legal_digests=("a" * 64, "b" * 64),
    )

    assert optional_decline == {
        "status": "SUPPORTED_SET",
        "reason": None,
        "target_count": 0,
        "cardinality_semantics": "optional_decline",
        "selection_schema": "1:7",
    }
    assert variable_multi["status"] == "SUPPORTED_SET"
    assert variable_multi["target_count"] == 2
    assert variable_multi["cardinality_semantics"] == "variable_cardinality"
    assert fixed_multi["status"] == "SUPPORTED_SET"
    assert fixed_multi["cardinality_semantics"] == "fixed_cardinality"


def test_v3_compatibility_counts_ordered_alias_and_forced_noop_fail_closed() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        classify_student_v3_set_compatibility_v1,
    )

    ordered = classify_student_v3_set_compatibility_v1(
        selection_type=5,
        selection_context=34,
        minimum=1,
        maximum=2,
        target_digests=("a" * 64,),
        legal_digests=("a" * 64, "b" * 64),
    )
    alias = classify_student_v3_set_compatibility_v1(
        selection_type=1,
        selection_context=7,
        minimum=1,
        maximum=1,
        target_digests=("a" * 64,),
        legal_digests=("a" * 64, "a" * 64),
    )
    forced = classify_student_v3_set_compatibility_v1(
        selection_type=0,
        selection_context=0,
        minimum=0,
        maximum=0,
        target_digests=(),
        legal_digests=(),
    )

    assert ordered["status"] == "UNSUPPORTED"
    assert ordered["reason"] == "ordered_selection_requires_pointer_head"
    assert ordered["selection_schema"] == "5:34"
    assert alias["status"] == "UNSUPPORTED"
    assert alias["reason"] == "target_action_alias_collision"
    assert forced["status"] == "NO_TRAINABLE_CHOICE"
    assert forced["reason"] == "forced_empty_selection"


def test_sealed_split_audit_preserves_canonical_mapping_and_near_duplicate_groups() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        audit_sealed_split_integrity_v1,
    )

    records = {
        "1" * 64: {
            "episode_id_hash": "a" * 64,
            "near_duplicate_id": "d" * 64,
            "split": "train",
        },
        "2" * 64: {
            "episode_id_hash": "a" * 64,
            "near_duplicate_id": "d" * 64,
            "split": "train",
        },
        "3" * 64: {
            "episode_id_hash": "b" * 64,
            "near_duplicate_id": "e" * 64,
            "split": "development",
        },
        "4" * 64: {
            "episode_id_hash": "c" * 64,
            "near_duplicate_id": "f" * 64,
            "split": "test",
        },
    }

    audit = audit_sealed_split_integrity_v1(
        records,
        declared_ubiquitous_near_duplicate_ids=(),
    )

    assert audit["mapped_record_counts"] == {
        "test": 1,
        "train": 2,
        "validation": 1,
    }
    assert audit["episode_split_intersection_count"] == 0
    assert audit["near_duplicate_split_intersection_count"] == 0
    assert audit["non_ubiquitous_near_duplicate_split_intersection_count"] == 0


def test_sealed_split_audit_exposes_cross_teacher_and_declared_ubiquitous_intersections() -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        audit_sealed_split_integrity_v1,
    )

    shared = "d" * 64
    ubiquitous = "e" * 64
    records = {
        "1" * 64: {
            "episode_id_hash": "a" * 64,
            "near_duplicate_id": shared,
            "split": "train",
        },
        "2" * 64: {
            "episode_id_hash": "b" * 64,
            "near_duplicate_id": shared,
            "split": "development",
        },
        "3" * 64: {
            "episode_id_hash": "c" * 64,
            "near_duplicate_id": ubiquitous,
            "split": "train",
        },
        "4" * 64: {
            "episode_id_hash": "f" * 64,
            "near_duplicate_id": ubiquitous,
            "split": "test",
        },
    }

    audit = audit_sealed_split_integrity_v1(
        records,
        declared_ubiquitous_near_duplicate_ids=(ubiquitous,),
    )

    assert audit["episode_split_intersection_count"] == 0
    assert audit["near_duplicate_split_intersection_count"] == 2
    assert audit["non_ubiquitous_near_duplicate_split_intersection_ids"] == [shared]
    assert audit["declared_ubiquitous_near_duplicate_split_intersection_ids"] == [
        ubiquitous
    ]


def test_bridge_cli_rejects_invalid_catalog_without_publishing_outputs() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    payload["teachers"][0]["policy"]["sha256"] = "0" * 64
    payload["catalog_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in payload.items() if key != "catalog_sha256"})
    ).hexdigest()
    # The production bridge now requires its formal catalog root-of-trust to
    # live under repo_root.  Keep this negative fixture inside that boundary so
    # the test still reaches (and does not weaken) the policy-SHA verification.
    with tempfile.TemporaryDirectory(
        prefix=".test-v3-invalid-catalog-", dir=ROOT / "runs"
    ) as temporary:
        directory = Path(temporary)
        catalog = directory / "catalog.json"
        catalog.write_bytes(_canonical(payload))
        dataset = directory / "dataset.jsonl"
        manifest = directory / "manifest.json"

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/build_teacher_snapshot_student_v3_bridge_v1.py"),
                "--repo-root",
                str(ROOT),
                "--catalog",
                str(catalog),
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
        assert "policy SHA-256" in failure["message"]
        assert not dataset.exists()
        assert not manifest.exists()


def test_formal_bridge_verifier_rejects_a_self_consistent_untrusted_catalog_path(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        BRIDGE_SCHEMA_V1,
        TeacherSnapshotStudentV3BridgeError,
        verify_teacher_snapshot_student_v3_bridge_manifest_v1,
    )

    old = json.loads(
        (
            ROOT
            / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v1-tomato/bridge-manifest.json"
        ).read_text(encoding="utf-8")
    )
    old["schema_version"] = BRIDGE_SCHEMA_V1
    old["catalog_path"] = "runs/not-a-formal-catalog/catalog.json"
    old["bridge_sha256"] = hashlib.sha256(
        BRIDGE_SCHEMA_V1.encode("ascii")
        + b"\0"
        + _canonical({key: value for key, value in old.items() if key != "bridge_sha256"})
    ).hexdigest()
    manifest = tmp_path / "bridge.json"
    manifest.write_bytes(_canonical(old))

    with pytest.raises(TeacherSnapshotStudentV3BridgeError, match="catalog"):
        verify_teacher_snapshot_student_v3_bridge_manifest_v1(manifest, ROOT)


def test_formal_bridge_verifier_rejects_semantic_hash_before_primary_artifacts(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        BRIDGE_SCHEMA_V1,
        TeacherSnapshotStudentV3BridgeError,
        verify_teacher_snapshot_student_v3_bridge_manifest_v1,
    )

    old = json.loads(
        (
            ROOT
            / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v1-tomato/bridge-manifest.json"
        ).read_text(encoding="utf-8")
    )
    old["schema_version"] = BRIDGE_SCHEMA_V1
    old["catalog_path"] = str(CATALOG.relative_to(ROOT))
    old["bridge_sha256"] = "0" * 64
    manifest = tmp_path / "bridge.json"
    manifest.write_bytes(_canonical(old))

    with pytest.raises(TeacherSnapshotStudentV3BridgeError, match="semantic SHA-256"):
        verify_teacher_snapshot_student_v3_bridge_manifest_v1(manifest, ROOT)
