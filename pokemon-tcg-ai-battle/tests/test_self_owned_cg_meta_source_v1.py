from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (
    SelfOwnedCgMetaSourceError,
    materialize_self_owned_cg_meta_batch_v1,
    materialize_self_owned_cg_meta_source_v1,
    promote_self_owned_cg_meta_batch_v1,
    promote_self_owned_cg_meta_source_v1,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v1-20260816/package"


def _smoke_summary(*, faults: int = 0) -> dict[str, object]:
    return {
        "status": "COMPLETE",
        "research_only": True,
        "evaluator_summary": {
            "requested_games": 4,
            "completed_games": 4 - faults,
            "faults": faults,
            "fault_rate": faults / 4,
            "status_distribution": {"DONE": 4 - faults, "FAULT": faults},
        },
    }


def test_materialize_requires_explicit_package_and_writes_isolated_pool(tmp_path):
    staged = tmp_path / "staged"
    result = materialize_self_owned_cg_meta_source_v1(
        candidate_package=PACKAGE,
        output_root=staged,
        seed_namespace="self-owned-cg-meta-test-a",
    )

    assert result["status"] == "STAGED"
    assert result["source_id"].startswith("self-owned-cg-")
    row = json.loads((staged / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    assert row["smoke_ok"] is False
    assert row["usage_boundary"] == "local_eval_only"
    assert (staged / row["id"] / "main.py").is_file()
    assert (staged / row["id"] / "deck.csv").is_file()
    assert json.loads((staged / "source_manifest.json").read_text(encoding="utf-8"))["parent_deck"] is None


def test_promote_requires_fault_free_smoke_and_verifies_fresh_meta(tmp_path):
    staged = tmp_path / "staged"
    materialize_self_owned_cg_meta_source_v1(
        candidate_package=PACKAGE,
        output_root=staged,
        seed_namespace="self-owned-cg-meta-test-b",
    )
    smoke = tmp_path / "smoke-summary.json"
    smoke.write_text(json.dumps(_smoke_summary()) + "\n", encoding="utf-8")
    promoted = tmp_path / "promoted"
    result = promote_self_owned_cg_meta_source_v1(
        staged_root=staged,
        output_root=promoted,
        smoke_summary=smoke,
    )

    assert result["status"] == "PROMOTED"
    row = json.loads((promoted / "pool_manifest.json").read_text(encoding="utf-8"))[0]
    assert row["smoke_ok"] is True
    assert (promoted / "fresh_meta.json").is_file()
    assert (promoted / "freshness-evidence.json").is_file()
    assert result["fresh_meta_verified"] is True


def test_promote_rejects_faulted_smoke_without_writing_output(tmp_path):
    staged = tmp_path / "staged"
    materialize_self_owned_cg_meta_source_v1(
        candidate_package=PACKAGE,
        output_root=staged,
        seed_namespace="self-owned-cg-meta-test-c",
    )
    smoke = tmp_path / "fault-smoke-summary.json"
    smoke.write_text(json.dumps(_smoke_summary(faults=1)) + "\n", encoding="utf-8")
    promoted = tmp_path / "promoted"
    with pytest.raises(SelfOwnedCgMetaSourceError, match="fault"):
        promote_self_owned_cg_meta_source_v1(
            staged_root=staged,
            output_root=promoted,
            smoke_summary=smoke,
        )
    assert not promoted.exists()


def test_materialize_is_no_clobber(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        materialize_self_owned_cg_meta_source_v1(
            candidate_package=PACKAGE,
            output_root=staged,
            seed_namespace="self-owned-cg-meta-test-d",
        )
    assert (staged / "sentinel").read_text(encoding="utf-8") == "keep"


def test_batch_source_seal_and_promotion_uses_sorted_identity_rows(tmp_path):
    staged = tmp_path / "batch-staged"
    result = materialize_self_owned_cg_meta_batch_v1(
        candidate_packages=(PACKAGE,),
        output_root=staged,
        seed_namespace="self-owned-cg-meta-batch-test",
    )
    assert result["status"] == "STAGED"
    source_id = result["source_ids"][0]
    smoke = tmp_path / "batch-smoke.json"
    smoke.write_text(json.dumps(_smoke_summary()) + "\n", encoding="utf-8")
    promoted = tmp_path / "batch-promoted"
    result = promote_self_owned_cg_meta_batch_v1(
        staged_root=staged,
        output_root=promoted,
        smoke_summary=smoke,
    )
    assert result["status"] == "PROMOTED"
    fresh = json.loads((promoted / "fresh_meta.json").read_text(encoding="utf-8"))
    assert fresh["reference_ids"] == [source_id]
    assert json.loads((promoted / "pool_manifest.json").read_text(encoding="utf-8"))[0]["smoke_ok"] is True
    pool_sha = hashlib.sha256((promoted / "pool_manifest.json").read_bytes()).hexdigest()
    smoke_payload = json.loads((promoted / "smoke_summary.json").read_text(encoding="utf-8"))
    assert smoke_payload["pool_manifest_sha256"] == pool_sha


def test_batch_source_epoch_is_explicitly_bound(tmp_path):
    staged = tmp_path / "batch-staged"
    materialize_self_owned_cg_meta_batch_v1(
        candidate_packages=(PACKAGE,),
        output_root=staged,
        seed_namespace="self-owned-cg-meta-batch-test-epoch",
        source_epoch="self_owned_official_card_data_role_separated_v4_test",
    )
    batch = json.loads((staged / "batch_manifest.json").read_text(encoding="utf-8"))
    assert batch["source_epoch"] == "self_owned_official_card_data_role_separated_v4_test"
