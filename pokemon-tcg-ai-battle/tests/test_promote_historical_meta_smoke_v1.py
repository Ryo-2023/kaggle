from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.promote_historical_meta_smoke_v1 import promote_historical_meta_smoke_v1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _make_input(root: Path, *, faults: int = 0, smoke_ids: list[str] | None = None) -> tuple[Path, Path, Path]:
    pool_root = root / "sealed"
    pool_root.mkdir(parents=True)
    rows = [
        {
            "id": "op-a",
            "policy_hash": "a" * 64,
            "canonical_deck_hash": "b" * 64,
            "smoke_ok": False,
            "usage_boundary": "local_eval_only",
        },
        {
            "id": "op-b",
            "policy_hash": "c" * 64,
            "canonical_deck_hash": "d" * 64,
            "smoke_ok": False,
            "usage_boundary": "local_eval_only",
        },
    ]
    _write_json(pool_root / "pool_manifest.json", rows)
    for row in rows:
        (pool_root / row["id"]).mkdir()
        (pool_root / row["id"] / "main.py").write_text("# policy\n", encoding="utf-8")
        (pool_root / row["id"] / "deck.csv").write_text("1 " * 60, encoding="utf-8")
    fresh = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
        "reference_ids": ["op-a", "op-b"],
        "references": [
            {"id": "op-a", "fresh": True, "unused_before_run": True},
            {"id": "op-b", "fresh": True, "unused_before_run": True},
        ],
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    fresh_path = root / "sealed" / "fresh_meta.json"
    _write_json(fresh_path, fresh)
    smoke_path = root / "smoke_summary.json"
    selected_smoke_ids = smoke_ids or ["op-a", "op-b"]
    _write_json(
        smoke_path,
        {
            "schema_version": "cg-historical-meta-smoke-v1",
            "status": "COMPLETE" if faults == 0 else "FAULT",
            "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
            "reference_ids": selected_smoke_ids,
            "requested_games": 2 * len(selected_smoke_ids),
            "completed_rows": 2 * len(selected_smoke_ids),
            "faults": faults,
            "research_only": True,
        },
    )
    return pool_root, fresh_path, smoke_path


def test_promote_smoke_creates_new_hash_bound_pool_without_mutating_input(tmp_path: Path) -> None:
    pool_root, fresh_path, smoke_path = _make_input(tmp_path)
    original_pool_sha = _sha256(pool_root / "pool_manifest.json")
    output = tmp_path / "promoted"

    report = promote_historical_meta_smoke_v1(
        pool_root=pool_root,
        fresh_meta_path=fresh_path,
        smoke_summary_path=smoke_path,
        output_root=output,
    )

    assert report["status"] == "SEALED"
    assert report["input_pool_manifest_sha256"] == original_pool_sha
    assert _sha256(pool_root / "pool_manifest.json") == original_pool_sha
    promoted_rows = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))
    assert all(row["smoke_ok"] is True for row in promoted_rows)
    promoted_fresh = json.loads((output / "fresh_meta.json").read_text(encoding="utf-8"))
    assert promoted_fresh["pool_manifest_sha256"] == report["pool_manifest_sha256"]
    promoted_smoke = json.loads((output / "smoke_summary.json").read_text(encoding="utf-8"))
    assert promoted_smoke["pool_manifest_sha256"] == report["pool_manifest_sha256"]
    assert promoted_smoke["input_pool_manifest_sha256"] == original_pool_sha
    assert promoted_fresh["smoke_summary_sha256"] == _sha256(output / "smoke_summary.json")
    assert (output / "op-a" / "main.py").is_file()
    assert (output / "op-b" / "deck.csv").is_file()


def test_promote_smoke_rejects_faulted_summary(tmp_path: Path) -> None:
    pool_root, fresh_path, smoke_path = _make_input(tmp_path, faults=1)
    with pytest.raises(ValueError, match="fault-free"):
        promote_historical_meta_smoke_v1(
            pool_root=pool_root,
            fresh_meta_path=fresh_path,
            smoke_summary_path=smoke_path,
            output_root=tmp_path / "promoted",
        )


def test_promote_smoke_can_seal_fault_free_subset_from_partial_run(tmp_path: Path) -> None:
    pool_root, fresh_path, smoke_path = _make_input(tmp_path, faults=2)
    ledger_path = smoke_path.parent / "evaluation" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True)
    rows = [
        {"opponent_id": "op-a", "outcome": "loss", "status": "DONE"},
        {"opponent_id": "op-a", "outcome": "win", "status": "DONE"},
        {"opponent_id": "op-b", "outcome": "fault", "status": "FAULT"},
        {"opponent_id": "op-b", "outcome": "fault", "status": "FAULT"},
    ]
    ledger_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    output = tmp_path / "promoted-subset"
    report = promote_historical_meta_smoke_v1(
        pool_root=pool_root,
        fresh_meta_path=fresh_path,
        smoke_summary_path=smoke_path,
        output_root=output,
        reference_ids=["op-a"],
    )

    assert report["status"] == "SEALED"
    promoted_rows = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in promoted_rows] == ["op-a"]
    assert promoted_rows[0]["smoke_ok"] is True
    promoted_fresh = json.loads((output / "fresh_meta.json").read_text(encoding="utf-8"))
    assert promoted_fresh["reference_ids"] == ["op-a"]
    promoted_smoke = json.loads((output / "smoke_summary.json").read_text(encoding="utf-8"))
    assert promoted_smoke["status"] == "COMPLETE"
    assert promoted_smoke["reference_ids"] == ["op-a"]


def test_promote_smoke_can_keep_unsmoked_holdout_out_of_subset(tmp_path: Path) -> None:
    pool_root, fresh_path, smoke_path = _make_input(tmp_path, smoke_ids=["op-a"])
    ledger_path = smoke_path.parent / "evaluation" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps({"opponent_id": "op-a", "outcome": "loss", "status": "DONE"})
        + "\n"
        + json.dumps({"opponent_id": "op-a", "outcome": "win", "status": "DONE"})
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "promoted-train-only"
    report = promote_historical_meta_smoke_v1(
        pool_root=pool_root,
        fresh_meta_path=fresh_path,
        smoke_summary_path=smoke_path,
        output_root=output,
        reference_ids=["op-a"],
    )

    assert report["status"] == "SEALED"
    assert report["partial_promotion"] is True
    promoted_rows = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in promoted_rows] == ["op-a"]
    promoted_fresh = json.loads((output / "fresh_meta.json").read_text(encoding="utf-8"))
    assert promoted_fresh["reference_ids"] == ["op-a"]
