#!/usr/bin/env python3
"""Build the weekend split after a factorial source batch is smoke-promoted."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1  # noqa: E402


SCHEMA = "self-owned-cg-policy-factorial-split-v1"


class SelfOwnedCgPolicyFactorialSplitError(ValueError):
    """Raised when a smoke-promoted factorial pool cannot be split safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SelfOwnedCgPolicyFactorialSplitError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgPolicyFactorialSplitError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise SelfOwnedCgPolicyFactorialSplitError(f"JSON object required: {path}")
    return value


def _write_no_clobber(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value) + b"\n"
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path.resolve())


def build_split_v1(*, output_root: str | Path, p1_package: str | Path) -> dict[str, object]:
    """Create meta_manifest and immutable META_TRAIN/DEV/FINAL bindings."""

    root = Path(output_root).resolve()
    pool_path = root / "pool_manifest.json"
    fresh_path = root / "fresh_meta.json"
    if not pool_path.is_file() or not fresh_path.is_file():
        raise SelfOwnedCgPolicyFactorialSplitError("promoted root must contain pool_manifest.json and fresh_meta.json")
    pool_raw = json.loads(pool_path.read_text(encoding="utf-8"))
    rows = pool_raw.get("opponents", pool_raw) if isinstance(pool_raw, Mapping) else pool_raw
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise SelfOwnedCgPolicyFactorialSplitError("pool manifest must contain rows")
    if any(row.get("smoke_ok") is not True for row in rows):
        raise SelfOwnedCgPolicyFactorialSplitError("all pool rows must be smoke-qualified")
    fresh = _read_object(fresh_path)
    references = fresh.get("references")
    if not isinstance(references, list):
        raise SelfOwnedCgPolicyFactorialSplitError("fresh_meta.references must be a list")
    refs = {str(item.get("id")): item for item in references if isinstance(item, Mapping)}
    if len(refs) != len(references):
        raise SelfOwnedCgPolicyFactorialSplitError("fresh_meta references must have unique ids")
    pool_rows = sorted((dict(row) for row in rows), key=lambda row: str(row.get("id")))
    ids = [str(row.get("id")) for row in pool_rows]
    if any(not value or value == "None" for value in ids) or len(ids) != len(set(ids)):
        raise SelfOwnedCgPolicyFactorialSplitError("pool rows must have unique ids")
    if len(ids) < 3:
        raise SelfOwnedCgPolicyFactorialSplitError("at least three source rows are required")
    if set(ids) != set(refs):
        raise SelfOwnedCgPolicyFactorialSplitError("pool and fresh_meta ids differ")

    meta_rows: list[dict[str, object]] = []
    for row in pool_rows:
        source_id = str(row["id"])
        ref = refs[source_id]
        source_sha = str(row.get("source_manifest_sha256") or ref.get("freshness_evidence_sha256"))
        if len(source_sha) != 64:
            raise SelfOwnedCgPolicyFactorialSplitError(f"source identity is missing: {source_id}")
        meta_rows.append(
            {
                "opponent_id": source_id,
                "archetype": f"SelfOwnedCgPolicyFactorial:{source_id}",
                "deck_sha256": str(row["canonical_deck_hash"]),
                "policy_sha256": str(row["policy_hash"]),
                "source_sha256": source_sha,
                "weight": 0.25,
                "usage_boundary": "local_eval_only",
                "training_exposure": 0,
                "source": str(row["source"]),
            }
        )
    meta_path = root / "meta_manifest.json"
    meta_payload = {
        "schema_version": "cg-self-owned-policy-factorial-meta-v1",
        "source_epoch": fresh.get("source_epoch"),
        "seed_namespace": fresh.get("seed_namespace"),
        "source_kind": "self_owned_official_card_data_deck_policy_factorial",
        "research_only": True,
        "rows": meta_rows,
    }
    meta_sha = _write_no_clobber(meta_path, meta_payload)

    train_ids = ids[:-2]
    dev_ids = [ids[-2]]
    final_ids = [ids[-1]]
    midpoint = max(1, len(train_ids) // 2)
    train_blocks = [train_ids[:midpoint], train_ids[midpoint:]]
    train_blocks = [block for block in train_blocks if block]
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(source_id: str, *, weight: float) -> dict[str, object]:
        row = dict(meta_by_id[source_id])
        row["weight"] = weight
        return {
            key: row[key]
            for key in (
                "opponent_id",
                "archetype",
                "deck_sha256",
                "policy_sha256",
                "source_sha256",
                "weight",
                "usage_boundary",
                "training_exposure",
            )
        }

    p1_root = Path(p1_package).resolve()
    p1_main = p1_root / "main.py"
    p1_deck = p1_root / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise SelfOwnedCgPolicyFactorialSplitError("P1 package must contain main.py and deck.csv")
    pool_sha = _sha256_file(pool_path)
    split_payload = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {
            "p1_policy_sha256": _sha256_file(p1_main),
            "p1_deck_sha256": _sha256_file(p1_deck),
            "meta_manifest_sha256": meta_sha,
            "pool_manifest_sha256": pool_sha,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "sources": {
            "fresh_meta_path": _relative(fresh_path),
            "meta_manifest_path": _relative(meta_path),
            "pool_manifest_path": _relative(pool_path),
        },
        "evaluation_contract": {
            "both_seats": True,
            "fault_inclusive": True,
            "training_exposure": 0,
            "teacher_labels_saved": False,
            "final_results_read_during_search": False,
            "train_games_per_opponent_seat": 2,
            "dev_games_per_opponent_seat": 2,
            "final_games_per_opponent_seat": 2,
        },
        "train_blocks": train_blocks,
        "splits": {
            "META_TRAIN": [split_row(source_id, weight=0.25) for source_id in train_ids],
            "META_DEV": [split_row(source_id, weight=1.0) for source_id in dev_ids],
            "META_FINAL": [split_row(source_id, weight=1.0) for source_id in final_ids],
        },
        "notes": [
            "Official-card-data-only self-owned deck x bounded P1 policy factorial source epoch.",
            "Only META_TRAIN is eligible for the first policy CEM; DEV and FINAL remain untouched.",
            "Runtime smoke is a package gate, not a performance promotion result.",
        ],
    }
    split_path = root / "cg_self_owned_weekend_split.json"
    split_sha = _write_no_clobber(split_path, split_payload)
    return {
        "status": "SEALED",
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": meta_sha,
        "split_path": str(split_path),
        "split_sha256": split_sha,
        "train_count": len(train_ids),
        "dev_count": len(dev_ids),
        "final_count": len(final_ids),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="enable split artifact writes")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--p1-package", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = build_split_v1(output_root=args.output_root, p1_package=args.p1_package)
    except (SelfOwnedCgPolicyFactorialSplitError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
