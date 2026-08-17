from __future__ import annotations

from mage_ptcg.meta_specialist.teacher_revalidation_v3 import build_teacher_manifest_v3


def test_teacher_manifest_counts_statuses_and_hashes() -> None:
    records = [
        {"episode_id_hash": "a" * 64, "near_duplicate_id": "b" * 64, "teacher": {"status": "available", "quality_weight": 1.0, "teacher_id": "t"}, "selection": ["x"], "legal_actions": []},
        {"episode_id_hash": "c" * 64, "near_duplicate_id": "d" * 64, "teacher": {"status": "unavailable", "quality_weight": 0.2, "teacher_id": "t"}, "selection": [], "legal_actions": []},
    ]
    manifest = build_teacher_manifest_v3(records, lane="test")
    assert manifest["record_count"] == 2
    assert manifest["status_counts"] == {"available": 1, "unavailable": 1}
    assert len(manifest["manifest_sha256"]) == 64
