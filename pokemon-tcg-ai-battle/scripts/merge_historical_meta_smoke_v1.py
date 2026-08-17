#!/usr/bin/env python3
"""Merge immutable, fault-free historical source batches for CABT research."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence


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


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc


def _copy_tree_contents(source: Path, destination: Path, *, excluded: set[str]) -> None:
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        if child.name in excluded:
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        elif child.is_file():
            if target.exists() and target.read_bytes() != child.read_bytes():
                raise ValueError(f"conflicting artifact while merging: {target}")
            if not target.exists():
                shutil.copy2(child, target)
        else:
            raise ValueError(f"unsupported source artifact: {child}")


def merge_historical_meta_smoke_v1(
    *,
    input_roots: Sequence[Path | str],
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
) -> dict[str, object]:
    roots = [Path(value).resolve() for value in input_roots]
    output = Path(output_root).resolve()
    if not roots or len(roots) != len(set(roots)):
        raise ValueError("at least two distinct input roots are required")
    if not source_epoch or not seed_namespace:
        raise ValueError("source_epoch and seed_namespace are required")
    if output.exists():
        raise FileExistsError(output)

    all_rows: list[dict[str, Any]] = []
    all_refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    source_batches: list[dict[str, str]] = []
    for root in roots:
        pool_path = root / "pool_manifest.json"
        fresh_path = root / "fresh_meta.json"
        smoke_path = root / "smoke_summary.json"
        if not pool_path.is_file() or not fresh_path.is_file() or not smoke_path.is_file():
            raise ValueError(f"smoke-promoted pool is incomplete: {root}")
        raw_pool = _read_json(pool_path, "pool_manifest.json")
        fresh = _read_json(fresh_path, "fresh_meta.json")
        smoke = _read_json(smoke_path, "smoke_summary.json")
        if not isinstance(raw_pool, list) or not raw_pool or not all(isinstance(row, Mapping) for row in raw_pool):
            raise ValueError(f"{root}: pool manifest must be a non-empty list")
        if not isinstance(fresh, Mapping) or not isinstance(smoke, Mapping):
            raise ValueError(f"{root}: fresh_meta and smoke_summary must be objects")
        if smoke.get("schema_version") != "cg-historical-meta-smoke-v1" or smoke.get("status") != "COMPLETE" or smoke.get("faults") != 0:
            raise ValueError(f"{root}: smoke summary is not COMPLETE and fault-free")
        if smoke.get("pool_manifest_sha256") != _sha256_file(pool_path):
            raise ValueError(f"{root}: smoke summary pool SHA mismatch")
        rows = [dict(row) for row in raw_pool]
        ids = [str(row.get("id", "")) for row in rows]
        if any(not oid for oid in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{root}: pool ids must be non-empty and unique")
        if any(row.get("smoke_ok") is not True for row in rows):
            raise ValueError(f"{root}: every pool row must be smoke_ok")
        fresh_ids = fresh.get("reference_ids")
        refs = fresh.get("references")
        if not isinstance(fresh_ids, list) or set(fresh_ids) != set(ids) or len(fresh_ids) != len(ids):
            raise ValueError(f"{root}: fresh_meta references do not cover the pool")
        if not isinstance(refs, list):
            raise ValueError(f"{root}: fresh_meta.references must be a list")
        overlap = seen_ids.intersection(ids)
        if overlap:
            raise ValueError(f"duplicate historical source id(s): {sorted(overlap)}")
        seen_ids.update(ids)
        all_rows.extend(rows)
        all_refs.extend(dict(row) for row in refs if isinstance(row, Mapping))
        source_batches.append(
            {
                "root": str(root),
                "pool_manifest_sha256": _sha256_file(pool_path),
                "fresh_meta_sha256": _sha256_file(fresh_path),
                "smoke_summary_sha256": _sha256_file(smoke_path),
            }
        )

    output.mkdir(parents=True)
    excluded = {"pool_manifest.json", "fresh_meta.json", "smoke_summary.json", "smoke_promotion_report.json", "intake_report.json", "merge_report.json", "meta_manifest.json", "cg_historical_split.json", "split_report.json"}
    for root in roots:
        _copy_tree_contents(root, output, excluded=excluded)
    all_rows.sort(key=lambda row: str(row["id"]))
    pool_out = output / "pool_manifest.json"
    _write_new(pool_out, all_rows)
    pool_sha = _sha256_file(pool_out)
    reference_ids = [str(row["id"]) for row in all_rows]
    all_refs.sort(key=lambda row: str(row.get("id", "")))
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
    fresh_payload = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"kaggle-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "pool_manifest_sha256": pool_sha,
        "reference_ids": reference_ids,
        "references": all_refs,
        "freshness_basis": "union of independently sealed, fault-free local smoke batches",
        "source_batches": source_batches,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    fresh_out = output / "fresh_meta.json"
    _write_new(fresh_out, fresh_payload)
    report = {
        "schema_version": "cg-historical-meta-smoke-merge-v1",
        "status": "SEALED",
        "input_roots": [str(root) for root in roots],
        "source_batches": source_batches,
        "output_root": str(output),
        "pool_manifest_path": str(pool_out),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_out),
        "fresh_meta_sha256": _sha256_file(fresh_out),
        "reference_count": len(reference_ids),
        "reference_ids": reference_ids,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(output / "merge_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", dest="input_roots", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    args = parser.parse_args(argv)
    report = merge_historical_meta_smoke_v1(input_roots=args.input_roots, output_root=args.output, source_epoch=args.source_epoch, seed_namespace=args.seed_namespace)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
