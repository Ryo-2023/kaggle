#!/usr/bin/env python3
"""Bind a sealed historical source pool to a hash-covered cg META split.

The historical intake deliberately emits only source/deck snapshots.  This
small bridge adds the meta distribution and split bindings needed by the
research-only P1 CEM runner.  It never changes the repository pool, package,
Champion, or submission artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1


ROOT = Path(__file__).resolve().parents[1]
LOCAL_EVAL_ONLY = "local_eval_only"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(value))


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _require_ids(label: str, values: Sequence[str], pool: Mapping[str, Mapping[str, object]]) -> list[str]:
    result = [str(value) for value in values]
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{label} ids must be non-empty and unique")
    missing = sorted(set(result) - set(pool))
    if missing:
        raise ValueError(f"{label} ids are absent from historical pool: {missing}")
    return result


def build_historical_meta_split_v1(
    *,
    pool_root: Path | str,
    fresh_meta_path: Path | str,
    p1_package: Path | str,
    train_ids: Sequence[str],
    dev_ids: Sequence[str],
    final_ids: Sequence[str],
) -> dict[str, object]:
    root = Path(pool_root).resolve()
    pool_path = root / "pool_manifest.json"
    fresh_path = Path(fresh_meta_path).resolve()
    p1 = Path(p1_package).resolve()
    if not pool_path.is_file() or not fresh_path.is_file():
        raise ValueError("historical pool and fresh_meta.json are required")
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise ValueError("P1 package must contain main.py and deck.csv")
    raw_pool = json.loads(pool_path.read_text(encoding="utf-8"))
    if not isinstance(raw_pool, list) or not raw_pool or not all(isinstance(row, Mapping) for row in raw_pool):
        raise ValueError("historical pool manifest must be a non-empty list")
    pool = {str(row["id"]): dict(row) for row in raw_pool}
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fresh_refs = fresh.get("references") if isinstance(fresh, Mapping) else None
    if not isinstance(fresh_refs, list):
        raise ValueError("fresh_meta.json references must be a list")
    fresh_ids = {str(row.get("id")) for row in fresh_refs if isinstance(row, Mapping)}
    all_ids = _require_ids("all", [*train_ids, *dev_ids, *final_ids], pool)
    if set(all_ids) != fresh_ids:
        raise ValueError("split ids must cover exactly all fresh historical references")
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("historical split ids overlap")
    train = _require_ids("META_TRAIN", train_ids, pool)
    dev = _require_ids("META_DEV", dev_ids, pool)
    final = _require_ids("META_FINAL", final_ids, pool)
    if set(train) & (set(dev) | set(final)) or set(dev) & set(final):
        raise ValueError("META split ids overlap")

    meta_rows: list[dict[str, object]] = []
    for opponent_id in all_ids:
        row = pool[opponent_id]
        policy_sha = str(row.get("policy_hash"))
        deck_sha = str(row.get("canonical_deck_hash"))
        source_sha = _sha256_bytes(
            _canonical_json(
                {
                    "candidate_id": opponent_id,
                    "source_branch": row.get("source_branch"),
                    "source_commit": row.get("source_commit"),
                    "source_policy_sha256": row.get("source_policy_sha256"),
                    "policy_sha256": policy_sha,
                    "deck_sha256": deck_sha,
                }
            )
        )
        meta_rows.append(
            {
                "opponent_id": opponent_id,
                "archetype": f"historical:{row.get('source_branch', 'unknown')}:{str(row.get('source_commit', ''))[:12]}",
                "deck_sha256": deck_sha,
                "policy_sha256": policy_sha,
                "source_sha256": source_sha,
                "weight": 1.0,
                "usage_boundary": LOCAL_EVAL_ONLY,
                "training_exposure": 0,
                "source": "internal_agents_historical",
                "source_commit": row.get("source_commit"),
                "source_branch": row.get("source_branch"),
                "historical_snapshot": True,
            }
        )

    meta_path = root / "meta_manifest.json"
    split_path = root / "cg_historical_split.json"
    report_path = root / "split_report.json"
    _write_new(meta_path, {"schema_version": "cg-historical-meta-distribution-v1", "research_only": True, "rows": meta_rows})
    meta_sha = _sha256_file(meta_path)
    pool_sha = _sha256_file(pool_path)
    p1_policy_sha = _sha256_file(p1 / "main.py")
    p1_deck_sha = _sha256_file(p1 / "deck.csv")

    by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(opponent_id: str) -> dict[str, object]:
        source = by_id[opponent_id]
        return {
            "opponent_id": opponent_id,
            "archetype": source["archetype"],
            "deck_sha256": source["deck_sha256"],
            "policy_sha256": source["policy_sha256"],
            "source_sha256": source["source_sha256"],
            "weight": 1.0,
            "usage_boundary": LOCAL_EVAL_ONLY,
            "training_exposure": 0,
        }

    split_payload = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {
            "p1_policy_sha256": p1_policy_sha,
            "p1_deck_sha256": p1_deck_sha,
            "meta_manifest_sha256": meta_sha,
            "pool_manifest_sha256": pool_sha,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)},
        "evaluation_contract": {
            "both_seats": True,
            "fault_inclusive": True,
            "training_exposure": 0,
            "teacher_labels_saved": False,
            "final_results_read_during_search": False,
        },
        "train_blocks": [train],
        "splits": {
            "META_TRAIN": [split_row(opponent_id) for opponent_id in train],
            "META_DEV": [split_row(opponent_id) for opponent_id in dev],
            "META_FINAL": [split_row(opponent_id) for opponent_id in final],
        },
        "notes": [
            "First-parent historical internal snapshots are local-evaluation-only source candidates.",
            "They are not public/native evidence and must not be used for submission or promotion.",
            f"fresh_meta_sha256={_sha256_file(fresh_path)}",
        ],
    }
    _write_new(split_path, split_payload)
    loaded = load_weekend_split(split_path, verify_sources=True)
    report = {
        "schema_version": "cg-historical-meta-split-v1",
        "status": "SEALED",
        "pool_root": str(root),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": meta_sha,
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "split_ids": {"META_TRAIN": list(loaded.ids("META_TRAIN")), "META_DEV": list(loaded.ids("META_DEV")), "META_FINAL": list(loaded.ids("META_FINAL"))},
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--fresh-meta", type=Path, required=True)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--train-id", action="append", required=True)
    parser.add_argument("--dev-id", action="append", required=True)
    parser.add_argument("--final-id", action="append", required=True)
    args = parser.parse_args(argv)
    result = build_historical_meta_split_v1(
        pool_root=args.pool_root,
        fresh_meta_path=args.fresh_meta,
        p1_package=args.p1_package,
        train_ids=args.train_id,
        dev_ids=args.dev_id,
        final_ids=args.final_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

