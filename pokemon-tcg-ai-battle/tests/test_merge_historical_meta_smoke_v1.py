from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.merge_historical_meta_smoke_v1 import merge_historical_meta_smoke_v1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _make_root(root: Path, prefix: str) -> Path:
    pool = root / prefix
    pool.mkdir(parents=True)
    row = {"id": f"{prefix}-op", "policy_hash": "a" * 64, "canonical_deck_hash": "b" * 64, "smoke_ok": True, "usage_boundary": "local_eval_only"}
    _write_json(pool / "pool_manifest.json", [row])
    (pool / row["id"]).mkdir()
    (pool / row["id"] / "main.py").write_text("# policy\n", encoding="utf-8")
    (pool / row["id"] / "deck.csv").write_text("1 " * 60, encoding="utf-8")
    _write_json(pool / "fresh_meta.json", {"schema_version": "meta-specialist-cg-fresh-meta-batch-v1", "reference_ids": [row["id"]], "references": [{"id": row["id"], "fresh": True, "unused_before_run": True}], "pool_manifest_sha256": _sha256(pool / "pool_manifest.json"), "research_only": True})
    _write_json(pool / "smoke_summary.json", {"schema_version": "cg-historical-meta-smoke-v1", "status": "COMPLETE", "faults": 0, "reference_ids": [row["id"]], "research_only": True, "pool_manifest_sha256": _sha256(pool / "pool_manifest.json")})
    return pool


def test_merge_smoked_pools_creates_union_and_preserves_inputs(tmp_path: Path) -> None:
    first = _make_root(tmp_path, "first")
    second = _make_root(tmp_path, "second")
    first_sha = _sha256(first / "pool_manifest.json")
    report = merge_historical_meta_smoke_v1(input_roots=[first, second], output_root=tmp_path / "merged", source_epoch="test-epoch", seed_namespace="test-seed")
    assert report["status"] == "SEALED"
    assert report["reference_count"] == 2
    assert _sha256(first / "pool_manifest.json") == first_sha
    merged = json.loads((tmp_path / "merged" / "pool_manifest.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in merged} == {"first-op", "second-op"}
    fresh = json.loads((tmp_path / "merged" / "fresh_meta.json").read_text(encoding="utf-8"))
    assert fresh["pool_manifest_sha256"] == report["pool_manifest_sha256"]
    assert set(fresh["reference_ids"]) == {"first-op", "second-op"}


def test_merge_rejects_duplicate_ids(tmp_path: Path) -> None:
    first = _make_root(tmp_path, "same")
    second = _make_root(tmp_path, "same2")
    second_pool = json.loads((second / "pool_manifest.json").read_text(encoding="utf-8"))
    second_pool[0]["id"] = "same-op"
    _write_json(second / "pool_manifest.json", second_pool)
    with pytest.raises(ValueError, match="duplicate"):
        merge_historical_meta_smoke_v1(input_roots=[first, second], output_root=tmp_path / "merged", source_epoch="test-epoch", seed_namespace="test-seed")
