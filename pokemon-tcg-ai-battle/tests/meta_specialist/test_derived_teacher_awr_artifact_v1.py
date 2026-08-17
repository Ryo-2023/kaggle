"""Publication contracts for the derived-teacher AWR artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.meta_specialist.test_derived_teacher_actor_visible_awr_v1 import _sample


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _result():
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_cross_fitted_actor_visible_awr_v1,
    )

    samples = tuple(
        _sample(
            f"artifact-{fold}-{position}", fold=fold, fold_count=2,
            target=1.0 if position == 0 else -1.0,
        )
        for fold in range(2)
        for position in range(2)
    )
    return build_cross_fitted_actor_visible_awr_v1(samples, fold_count=2)


def test_manifest_roundtrip_recomputes_diagnostics_and_denies_authority(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_derived_teacher_awr_manifest_payload_v1,
        write_actor_visible_awr_sidecar_v1,
    )
    from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
        read_derived_teacher_awr_manifest_v1,
        write_derived_teacher_awr_manifest_v1,
    )

    result = _result()
    sidecar = write_actor_visible_awr_sidecar_v1(result.rows, tmp_path / "sidecar.jsonl")
    sidecar["path"] = "sidecar.jsonl"
    payload = build_derived_teacher_awr_manifest_payload_v1(
        result=result,
        catalog_binding={
            "path": "catalog.json", "file_sha256": _sha("catalog-file"),
            "catalog_sha256": _sha("catalog-semantic"),
        },
        decision_binding={"path": "decision.md", "sha256": _sha("decision")},
        source_bindings=[{
            "teacher_id": "teacher-a", "archetype": "test",
            "policy_sha256": _sha("policy"), "deck_sha256": _sha("deck"),
            "snapshot_source_kind": "pooled_external_submission_agent",
            "permission_manifest_id": _sha("permission"),
            "dataset_manifest_path": "dataset.json",
            "dataset_manifest_sha256": _sha("dataset"),
            "snapshot_index_path": "index.json",
            "snapshot_index_sha256": _sha("index"),
            "dataset_snapshot_sha256": _sha("snapshot"),
            "feature_domain": "actor-visible", "feature_schema_hash": _sha("feature"),
            "record_count": 4, "shards": [],
        }],
        sidecar_binding=sidecar,
    )
    manifest_path = tmp_path / "manifest.json"
    write_derived_teacher_awr_manifest_v1(payload, manifest_path)
    loaded = read_derived_teacher_awr_manifest_v1(
        manifest_path, repo_root=tmp_path, verify_sources=False,
    )
    assert loaded == payload
    assert loaded["diagnostics"]["overall"]["row_count"] == 4
    assert all(value is False for value in loaded["authority"].values())


def test_manifest_reader_fails_closed_on_reclassification(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        ActorVisibleAwrError,
        build_derived_teacher_awr_manifest_payload_v1,
        write_actor_visible_awr_sidecar_v1,
    )
    from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
        read_derived_teacher_awr_manifest_v1,
        write_derived_teacher_awr_manifest_v1,
    )

    result = _result()
    sidecar = write_actor_visible_awr_sidecar_v1(result.rows, tmp_path / "sidecar.jsonl")
    sidecar["path"] = "sidecar.jsonl"
    payload = build_derived_teacher_awr_manifest_payload_v1(
        result=result,
        catalog_binding={
            "path": "catalog.json", "file_sha256": _sha("catalog-file"),
            "catalog_sha256": _sha("catalog-semantic"),
        },
        decision_binding={"path": "decision.md", "sha256": _sha("decision")},
        source_bindings=[{
            "teacher_id": "teacher-a", "archetype": "test",
            "policy_sha256": _sha("policy"), "deck_sha256": _sha("deck"),
            "snapshot_source_kind": "pooled_external_submission_agent",
            "permission_manifest_id": _sha("permission"),
            "dataset_manifest_path": "dataset.json",
            "dataset_manifest_sha256": _sha("dataset"),
            "snapshot_index_path": "index.json",
            "snapshot_index_sha256": _sha("index"),
            "dataset_snapshot_sha256": _sha("snapshot"),
            "feature_domain": "actor-visible", "feature_schema_hash": _sha("feature"),
            "record_count": 4, "shards": [],
        }],
        sidecar_binding=sidecar,
    )
    path = tmp_path / "manifest.json"
    write_derived_teacher_awr_manifest_v1(payload, path)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["authority"]["training_authority"] = True
    path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ActorVisibleAwrError, match="SHA-256|authority|tamper"):
        read_derived_teacher_awr_manifest_v1(path, repo_root=tmp_path, verify_sources=False)


def test_snapshot_source_kind_and_policy_sha_must_both_match_catalog() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        ActorVisibleAwrError,
    )
    from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
        validate_snapshot_source_artifacts_v1,
    )

    source_kind = "team_internal_agent"
    policy = _sha("internal-policy")
    provenance = [
        {"kind": source_kind, "artifact_sha256": policy},
        {"kind": "teacher_collection_manifest_v2", "artifact_sha256": _sha("manifest")},
        {"kind": "teacher_collection_contract_v2", "artifact_sha256": _sha("contract")},
        {"kind": "teacher_collection_omissions_v2", "artifact_sha256": _sha("omissions")},
        {"kind": "teacher_collector_source_snapshot_v2", "artifact_sha256": _sha("collector")},
        {"kind": "teacher_permission_trusted_bytes_v1", "artifact_sha256": _sha("permission")},
        {
            "kind": f"teacher_source_kind:{source_kind}",
            "artifact_sha256": hashlib.sha256(source_kind.encode("utf-8")).hexdigest(),
        },
    ]
    assert validate_snapshot_source_artifacts_v1(
        provenance,
        expected_policy_sha256=policy,
        expected_source_kind=source_kind,
    ) == source_kind
    with pytest.raises(ActorVisibleAwrError, match="policy|source"):
        validate_snapshot_source_artifacts_v1(
            [{**row, "artifact_sha256": _sha("wrong")} if row["kind"] == source_kind else row for row in provenance],
            expected_policy_sha256=policy,
            expected_source_kind=source_kind,
        )
    with pytest.raises(ActorVisibleAwrError, match="kind|source"):
        validate_snapshot_source_artifacts_v1(
            [row for row in provenance if row["kind"] != "teacher_collection_omissions_v2"],
            expected_policy_sha256=policy,
            expected_source_kind=source_kind,
        )
    with pytest.raises(ActorVisibleAwrError, match="kind|source"):
        validate_snapshot_source_artifacts_v1(
            [*provenance, {"kind": "unknown", "artifact_sha256": _sha("unknown")}],
            expected_policy_sha256=policy,
            expected_source_kind=source_kind,
        )
