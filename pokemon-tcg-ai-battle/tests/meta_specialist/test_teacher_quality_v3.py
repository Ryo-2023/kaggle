from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.teacher_quality_v3 import (
    read_teacher_quality_manifest_v3,
    require_theta0_teacher_quality_v3,
    seal_teacher_quality_v3,
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    path.write_bytes(raw)
    return _sha256(raw)


def _seal_manifest_payload(path: Path, payload: dict[str, object], *, canonical: bool = True) -> tuple[str, str]:
    body = dict(payload)
    body.pop("manifest_sha256", None)
    manifest_sha256 = _sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    body["manifest_sha256"] = manifest_sha256
    raw = (
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if canonical
        else (json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )
    path.write_bytes(raw)
    return _sha256(raw), manifest_sha256


def _record(record_id: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_id": record_id,
        "content_hash": "1" * 64,
        "source": {"synthetic": False, "training_eligible": True, "usage_class": "qualified_training"},
        "teacher": {"status": "available", "teacher_id": "teacher-a", "teacher_revision": "rev-a"},
        "policy": {
            "implementation_path": "teachers/teacher_a.py",
            "implementation_sha256": "2" * 64,
            "version": "v1",
            "usage_boundary": "local_eval_only",
        },
        "deck": {"fingerprint_sha256": "3" * 64},
        "current_pool": {
            "evaluation_id": "current-pool-20260809",
            "result_sha256": "4" * 64,
            "games": 100,
            "wins": 60,
            "draws": 5,
            "losses": 35,
        },
        "fault": {"result_sha256": "5" * 64, "games": 100, "faults": 0},
        "strength": {"confidence": 0.9, "agreement": 0.8, "search_strength": 0.7},
        # Deliberately retained as adversarial legacy input: production must not use it.
        "quality_weight": 1.0,
    }
    record.update(overrides)
    return record


def _evidence(*records: dict[str, object]) -> dict[str, object]:
    return {"schema": "meta-specialist-teacher-quality-evidence-v1", "lane": "alakazam", "records": list(records)}


def _ready_manifest() -> dict[str, object]:
    evidence = {
        "record_id": "a" * 64,
        "content_hash": "1" * 64,
        "source": {
            "synthetic": False,
            "attested": True,
            "training_eligible": True,
            "usage_class": "qualified_training",
        },
        "teacher": {"teacher_id": "teacher-a", "teacher_revision": "rev-a"},
        "policy": {
            "implementation_sha256": "2" * 64,
            "version": "v1",
            "usage_boundary": "local_eval_only",
        },
        "deck": {"fingerprint_sha256": "3" * 64},
        "current_pool": {
            "evaluation_id": "current-pool-20260809",
            "result_sha256": "4" * 64,
            "games": 100,
            "wins": 60,
            "draws": 5,
            "losses": 35,
        },
        "fault": {"result_sha256": "5" * 64, "games": 100, "faults": 0},
        "strength": {"confidence": 0.9, "agreement": 0.8, "search_strength": 0.7},
    }
    evidence_sha256 = _sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return {
        "schema": "meta-specialist-teacher-quality-manifest-v1",
        "lane": "alakazam",
        "status": "READY",
        "theta0_allowed": True,
        "authority_gap": None,
        "records_total": 1,
        "eligible_record_count": 1,
        "exclusion_counts": {},
        "exclusions": [],
        "source_hashes": {"approved-rule.json": "6" * 64, "evidence.json": "7" * 64},
        "approved_rule": {
            "schema": "meta-specialist-teacher-quality-rule-v1",
            "approval_status": "APPROVED",
            "rule_id": "quality-rule-20260809",
            "rule_version": "v1",
            "rule_file_sha256": "6" * 64,
            "assignments": [{"evidence_sha256": evidence_sha256, "quality_weight": 0.7}],
        },
        "quality_records": [{**evidence, "evidence_sha256": evidence_sha256, "quality_weight": 0.7}],
    }


def _write_ready_authorities(
    tmp_path: Path,
) -> tuple[Path, str, str, Path, str, dict[str, Path], dict[str, str]]:
    """Create independently pinned, canonical first-party READY fixtures."""
    manifest = _ready_manifest()
    record = manifest["quality_records"][0]
    assert isinstance(record, dict)
    rule = dict(manifest["approved_rule"])
    rule.pop("rule_file_sha256")
    rule_path = tmp_path / "approved-rule.json"
    rule_sha256 = _write_json(rule_path, rule)

    artifact_paths: dict[str, Path] = {}
    artifact_sha256s: dict[str, str] = {}
    refs: list[dict[str, object]] = []
    for kind in ("source", "teacher", "policy", "deck", "current_pool", "fault", "strength"):
        source_name = f"{kind}.json"
        path = tmp_path / source_name
        payload = {
            "schema": "meta-specialist-teacher-quality-primary-evidence-v1",
            "kind": kind,
            "record_id": record["record_id"],
            "content_hash": record["content_hash"],
            "value": record[kind],
        }
        artifact_sha256 = _write_json(path, payload)
        artifact_paths[source_name] = path
        artifact_sha256s[source_name] = artifact_sha256
        refs.append({
            "record_id": record["record_id"], "kind": kind,
            "source_name": source_name, "file_sha256": artifact_sha256,
        })

    approved_rule = manifest["approved_rule"]
    assert isinstance(approved_rule, dict)
    approved_rule["rule_file_sha256"] = rule_sha256
    manifest["source_hashes"] = {"approved-rule.json": rule_sha256, **artifact_sha256s}
    manifest["primary_artifacts"] = refs
    manifest_path = tmp_path / "ready.json"
    manifest_file_sha256, manifest_sha256 = _seal_manifest_payload(manifest_path, manifest)
    return (
        manifest_path, manifest_file_sha256, manifest_sha256, rule_path, rule_sha256,
        artifact_paths, artifact_sha256s,
    )


def test_legacy_quality_weight_cannot_bypass_missing_weight_rule_authority(tmp_path: Path) -> None:
    """Fails if production starts trusting stored quality_weight or permits θ0 on an authority gap."""
    evidence_path = tmp_path / "evidence.json"
    anchor = _write_json(evidence_path, _evidence(_record("a" * 64, quality_weight=1.0)))

    manifest = seal_teacher_quality_v3(
        evidence_path, expected_evidence_file_sha256=anchor, output_path=tmp_path / "quality.json",
    )

    assert manifest["status"] == "AUTHORITY_GAP"
    assert manifest["theta0_allowed"] is False
    assert manifest["eligible_record_count"] == 0
    assert manifest["exclusion_counts"] == {"weight_rule_authority_missing": 1}
    with pytest.raises(ValueError, match="authority gap"):
        require_theta0_teacher_quality_v3(manifest)


def test_manifest_records_exclusion_reasons_and_source_hashes_without_synthetic_or_bad_fault_rows(tmp_path: Path) -> None:
    """Fails if production admits synthetic/unattested rows, ignores fault provenance, or omits sealed accounting."""
    synthetic = _record("b" * 64, source={"synthetic": True, "training_eligible": True, "usage_class": "qualified_training"})
    bad_fault = _record("c" * 64, fault={"games": 100, "faults": 0})
    evidence_path = tmp_path / "evidence.json"
    anchor = _write_json(evidence_path, _evidence(_record("a" * 64), synthetic, bad_fault))

    manifest = seal_teacher_quality_v3(
        evidence_path, expected_evidence_file_sha256=anchor, output_path=tmp_path / "quality.json",
    )

    assert manifest["records_total"] == 3
    assert manifest["eligible_record_count"] == 0
    assert manifest["exclusion_counts"] == {
        "fault_provenance_missing": 1,
        "synthetic_or_unattested": 1,
        "weight_rule_authority_missing": 1,
    }
    assert manifest["source_hashes"] == {"evidence.json": anchor}
    assert manifest["exclusions"] == [
        {"record_id": "a" * 64, "reasons": ["weight_rule_authority_missing"]},
        {"record_id": "b" * 64, "reasons": ["synthetic_or_unattested"]},
        {"record_id": "c" * 64, "reasons": ["fault_provenance_missing"]},
    ]


def test_external_evidence_sha_is_checked_before_json_parsing(tmp_path: Path) -> None:
    """Fails if production parses an unanchored or mismatched evidence file before checking its external SHA."""
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="external evidence file SHA-256"):
        seal_teacher_quality_v3(
            evidence_path, expected_evidence_file_sha256="0" * 64, output_path=tmp_path / "quality.json",
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"schema":"meta-specialist-teacher-quality-evidence-v1","schema":"duplicate","lane":"alakazam","records":[]}', "duplicate JSON key"),
        (b'{"schema":"meta-specialist-teacher-quality-evidence-v1","lane":"alakazam","records":[{"strength":{"confidence":NaN}}]}', "non-finite JSON value"),
    ],
)
def test_evidence_reader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path, raw: bytes, message: str) -> None:
    """Fails if production accepts ambiguous or non-finite first-party evidence before quality screening."""
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(raw)

    with pytest.raises(ValueError, match=message):
        seal_teacher_quality_v3(
            evidence_path, expected_evidence_file_sha256=_sha256(raw), output_path=tmp_path / "quality.json",
        )


def test_evidence_rejects_policy_path_traversal(tmp_path: Path) -> None:
    """Fails if production permits policy provenance to escape its declared artifact namespace."""
    evidence_path = tmp_path / "evidence.json"
    anchor = _write_json(evidence_path, _evidence(_record("a" * 64, policy={
        "implementation_path": "../teacher.py", "implementation_sha256": "2" * 64,
        "version": "v1", "usage_boundary": "local_eval_only",
    })))

    with pytest.raises(ValueError, match="policy implementation path"):
        seal_teacher_quality_v3(
            evidence_path, expected_evidence_file_sha256=anchor, output_path=tmp_path / "quality.json",
        )


def test_strict_reader_rejects_self_rehashed_manifest_without_original_external_anchor(tmp_path: Path) -> None:
    """Fails if production accepts a manifest whose attacker recomputed only its self hash after changing exclusions."""
    evidence_path = tmp_path / "evidence.json"
    evidence_anchor = _write_json(evidence_path, _evidence(_record("a" * 64)))
    manifest_path = tmp_path / "quality.json"
    seal_teacher_quality_v3(evidence_path, expected_evidence_file_sha256=evidence_anchor, output_path=manifest_path)
    original_anchor = _sha256(manifest_path.read_bytes())

    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["theta0_allowed"] = True
    body = dict(tampered)
    body.pop("manifest_sha256")
    tampered["manifest_sha256"] = _sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    manifest_path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ValueError, match="external manifest file SHA-256"):
        read_teacher_quality_manifest_v3(manifest_path, expected_manifest_file_sha256=original_anchor)


def test_atomic_publish_strictly_reloads_the_same_canonical_manifest(tmp_path: Path) -> None:
    """Fails if production publishes bytes its strict reader cannot reload or leaves an incomplete replacement."""
    evidence_path = tmp_path / "evidence.json"
    evidence_anchor = _write_json(evidence_path, _evidence(_record("a" * 64)))
    manifest_path = tmp_path / "quality.json"

    sealed = seal_teacher_quality_v3(
        evidence_path, expected_evidence_file_sha256=evidence_anchor, output_path=manifest_path,
    )
    published_anchor = _sha256(manifest_path.read_bytes())

    assert read_teacher_quality_manifest_v3(
        manifest_path, expected_manifest_file_sha256=published_anchor,
    ) == sealed
    assert manifest_path.read_bytes() == json.dumps(
        sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    assert list(tmp_path.glob(".quality.json.*.tmp")) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("exclusions", "not-an-array"),
        lambda payload: payload["exclusions"][0].pop("reasons"),
        lambda payload: payload["exclusions"][0].__setitem__("record_id", "not-a-digest"),
        lambda payload: payload["exclusion_counts"].__setitem__("weight_rule_authority_missing", 2),
        lambda payload: payload["source_hashes"].__setitem__("evidence.json", "not-a-digest"),
    ],
)
def test_strict_reader_rejects_nested_schema_tamper_after_all_hashes_are_recomputed(tmp_path: Path, mutate) -> None:
    """Fails if production trusts a self-consistent top-level hash without validating every nested member."""
    evidence_path = tmp_path / "evidence.json"
    evidence_anchor = _write_json(evidence_path, _evidence(_record("a" * 64)))
    manifest_path = tmp_path / "quality.json"
    manifest = seal_teacher_quality_v3(
        evidence_path, expected_evidence_file_sha256=evidence_anchor, output_path=manifest_path,
    )
    mutate(manifest)
    external_anchor, _manifest_sha256 = _seal_manifest_payload(manifest_path, manifest)

    with pytest.raises(ValueError, match="manifest|exclusion|source hash"):
        read_teacher_quality_manifest_v3(
            manifest_path, expected_manifest_file_sha256=external_anchor,
        )


def test_strict_reader_rejects_noncanonical_bytes_after_all_hashes_are_recomputed(tmp_path: Path) -> None:
    """Fails if production accepts alternate raw encodings of the same self-hashed manifest object."""
    evidence_path = tmp_path / "evidence.json"
    evidence_anchor = _write_json(evidence_path, _evidence(_record("a" * 64)))
    manifest_path = tmp_path / "quality.json"
    manifest = seal_teacher_quality_v3(
        evidence_path, expected_evidence_file_sha256=evidence_anchor, output_path=manifest_path,
    )
    external_anchor, _manifest_sha256 = _seal_manifest_payload(manifest_path, manifest, canonical=False)

    with pytest.raises(ValueError, match="canonical"):
        read_teacher_quality_manifest_v3(
            manifest_path, expected_manifest_file_sha256=external_anchor,
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"schema":"meta-specialist-teacher-quality-manifest-v1","schema":"duplicate"}', "duplicate JSON key"),
        (b'{"schema":"meta-specialist-teacher-quality-manifest-v1","quality":NaN}', "non-finite JSON value"),
    ],
)
def test_manifest_reader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path, raw: bytes, message: str) -> None:
    """Fails if production accepts ambiguous or non-finite sealed manifest bytes."""
    manifest_path = tmp_path / "quality.json"
    manifest_path.write_bytes(raw)

    with pytest.raises(ValueError, match=message):
        read_teacher_quality_manifest_v3(
            manifest_path, expected_manifest_file_sha256=_sha256(raw),
        )


def test_ready_reader_reloads_caller_anchored_rule_and_primary_artifacts(tmp_path: Path) -> None:
    """Fails if READY can succeed without independently anchored, physical primary authority."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    (
        manifest_path, manifest_file_sha256, manifest_sha256, rule_path, rule_sha256,
        artifact_paths, artifact_sha256s,
    ) = _write_ready_authorities(tmp_path)

    ready = read_ready_teacher_quality_manifest_v3(
        manifest_path,
        expected_manifest_file_sha256=manifest_file_sha256,
        expected_manifest_sha256=manifest_sha256,
        approved_rule_path=rule_path,
        expected_approved_rule_file_sha256=rule_sha256,
        primary_evidence_paths=artifact_paths,
        expected_primary_evidence_file_sha256=artifact_sha256s,
    )
    assert ready["status"] == "READY"
    assert ready["theta0_allowed"] is True
    assert ready["quality_records"][0]["quality_weight"] == 0.7


def test_ready_reader_rejects_self_declared_hashes_without_primary_artifact_references(tmp_path: Path) -> None:
    """Fails if a self-hashed manifest can invent READY while omitting the physical evidence closure."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    manifest_path = tmp_path / "ready.json"
    manifest_file_sha256, manifest_sha256 = _seal_manifest_payload(manifest_path, _ready_manifest())
    rule_path = tmp_path / "approved-rule.json"
    rule_sha256 = _write_json(rule_path, {
        "schema": "meta-specialist-teacher-quality-rule-v1", "approval_status": "APPROVED",
        "rule_id": "quality-rule-20260809", "rule_version": "v1",
        "assignments": [{"evidence_sha256": _ready_manifest()["approved_rule"]["assignments"][0]["evidence_sha256"], "quality_weight": 0.7}],
    })

    with pytest.raises(ValueError, match="primary|READY|key set"):
        read_ready_teacher_quality_manifest_v3(
            manifest_path,
            expected_manifest_file_sha256=manifest_file_sha256,
            expected_manifest_sha256=manifest_sha256,
            approved_rule_path=rule_path,
            expected_approved_rule_file_sha256=rule_sha256,
            primary_evidence_paths={},
            expected_primary_evidence_file_sha256={},
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["approved_rule"].__setitem__("approval_status", "DRAFT"),
        lambda payload: payload["quality_records"][0]["strength"].pop("agreement"),
        lambda payload: payload["quality_records"][0].__setitem__("quality_weight", 1.0),
        lambda payload: payload["quality_records"][0]["fault"].__setitem__("faults", 101),
    ],
)
def test_ready_reader_rejects_unapproved_incomplete_or_nonderived_quality(tmp_path: Path, mutate) -> None:
    """Fails if READY can be asserted without approved assignment and complete finite primary evidence."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    (
        manifest_path, _manifest_file_sha256, _manifest_sha256, rule_path, rule_sha256,
        artifact_paths, artifact_sha256s,
    ) = _write_ready_authorities(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_file_sha256, manifest_sha256 = _seal_manifest_payload(manifest_path, manifest)

    with pytest.raises(ValueError, match="READY|approved|evidence|quality|fault|manifest"):
        read_ready_teacher_quality_manifest_v3(
            manifest_path,
            expected_manifest_file_sha256=manifest_file_sha256,
            expected_manifest_sha256=manifest_sha256,
            approved_rule_path=rule_path,
            expected_approved_rule_file_sha256=rule_sha256,
            primary_evidence_paths=artifact_paths,
            expected_primary_evidence_file_sha256=artifact_sha256s,
        )


def test_ready_reader_rejects_primary_artifact_that_disagrees_with_sealed_record(tmp_path: Path) -> None:
    """Fails if a physically pinned current-pool/fault/policy/deck/search artifact is not re-derived."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    (
        manifest_path, manifest_file_sha256, manifest_sha256, rule_path, rule_sha256,
        artifact_paths, artifact_sha256s,
    ) = _write_ready_authorities(tmp_path)
    policy_path = artifact_paths["policy.json"]
    altered = json.loads(policy_path.read_text(encoding="utf-8"))
    altered["value"]["version"] = "unreviewed-v2"
    artifact_sha256s["policy.json"] = _write_json(policy_path, altered)

    with pytest.raises(ValueError, match="primary|policy|artifact"):
        read_ready_teacher_quality_manifest_v3(
            manifest_path,
            expected_manifest_file_sha256=manifest_file_sha256,
            expected_manifest_sha256=manifest_sha256,
            approved_rule_path=rule_path,
            expected_approved_rule_file_sha256=rule_sha256,
            primary_evidence_paths=artifact_paths,
            expected_primary_evidence_file_sha256=artifact_sha256s,
        )


def test_ready_reader_rejects_rule_artifact_that_disagrees_with_embedded_assignments(tmp_path: Path) -> None:
    """Fails if manifest weights are not re-derived from the independently anchored approved rule bytes."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    (
        manifest_path, manifest_file_sha256, manifest_sha256, rule_path, _rule_sha256,
        artifact_paths, artifact_sha256s,
    ) = _write_ready_authorities(tmp_path)
    altered = json.loads(rule_path.read_text(encoding="utf-8"))
    altered["assignments"][0]["quality_weight"] = 1.0
    altered_rule_sha256 = _write_json(rule_path, altered)

    with pytest.raises(ValueError, match="rule|assignment|quality"):
        read_ready_teacher_quality_manifest_v3(
            manifest_path,
            expected_manifest_file_sha256=manifest_file_sha256,
            expected_manifest_sha256=manifest_sha256,
            approved_rule_path=rule_path,
            expected_approved_rule_file_sha256=altered_rule_sha256,
            primary_evidence_paths=artifact_paths,
            expected_primary_evidence_file_sha256=artifact_sha256s,
        )
