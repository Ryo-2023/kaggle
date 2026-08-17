#!/usr/bin/env python3
"""Seal a fault-free smoke result as a new, hash-bound local pool.

The Kaggle-kernel intake is immutable: its pool manifest records
``smoke_ok=false`` until a separate bounded smoke run has completed.  This
bridge creates a new artifact root with the same source snapshots and
``smoke_ok=true`` rows only when the smoke summary is complete, fault-free,
and covers the entire sealed pool.  It never changes the input intake, the
repository opponent pool, Champion state, or submission artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


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


def _load_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _copy_tree_contents(source: Path, destination: Path, *, excluded: set[str]) -> None:
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        if child.name in excluded:
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        elif child.is_file():
            shutil.copy2(child, target)
        else:
            raise ValueError(f"unsupported intake artifact: {child}")


def promote_historical_meta_smoke_v1(
    *,
    pool_root: Path | str,
    fresh_meta_path: Path | str,
    smoke_summary_path: Path | str,
    output_root: Path | str,
    reference_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    source_root = Path(pool_root).resolve()
    fresh_path = Path(fresh_meta_path).resolve()
    smoke_path = Path(smoke_summary_path).resolve()
    output = Path(output_root).resolve()
    pool_path = source_root / "pool_manifest.json"
    if not pool_path.is_file() or not fresh_path.is_file() or not smoke_path.is_file():
        raise ValueError("pool_manifest.json, fresh_meta.json, and smoke_summary.json are required")
    if output.exists():
        raise FileExistsError(output)

    raw_pool = json.loads(pool_path.read_text(encoding="utf-8"))
    if not isinstance(raw_pool, list) or not raw_pool or not all(isinstance(row, Mapping) for row in raw_pool):
        raise ValueError("historical pool manifest must be a non-empty list")
    pool_rows = [dict(row) for row in raw_pool]
    ids = [str(row.get("id", "")) for row in pool_rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("historical pool ids must be non-empty and unique")

    fresh = _load_object(fresh_path, "fresh_meta.json")
    fresh_ids_raw = fresh.get("reference_ids")
    fresh_refs = fresh.get("references")
    if not isinstance(fresh_ids_raw, list) or not all(isinstance(value, str) for value in fresh_ids_raw):
        raise ValueError("fresh_meta.reference_ids must be a string list")
    if not isinstance(fresh_refs, list):
        raise ValueError("fresh_meta.references must be a list")
    if set(fresh_ids_raw) != set(ids) or len(fresh_ids_raw) != len(ids):
        raise ValueError("fresh_meta references must cover the entire sealed pool")

    smoke = _load_object(smoke_path, "smoke_summary.json")
    if smoke.get("schema_version") != "cg-historical-meta-smoke-v1":
        raise ValueError("unexpected smoke summary schema")
    if smoke.get("research_only") is not True:
        raise ValueError("smoke summary must be research_only")
    if smoke.get("pool_manifest_sha256") != _sha256_file(pool_path):
        raise ValueError("smoke summary pool manifest SHA mismatch")
    smoke_ids = smoke.get("reference_ids")
    if not isinstance(smoke_ids, list) or not smoke_ids or any(not isinstance(value, str) for value in smoke_ids):
        raise ValueError("smoke summary reference_ids must be a non-empty string list")
    if len(smoke_ids) != len(set(smoke_ids)) or set(smoke_ids) - set(ids):
        raise ValueError("smoke summary references are absent from the sealed pool")
    requested = smoke.get("requested_games")
    completed = smoke.get("completed_rows")
    if not isinstance(requested, int) or not isinstance(completed, int) or requested <= 0 or completed != requested:
        raise ValueError("smoke summary must report all requested games completed")

    if reference_ids is None:
        if set(smoke_ids) != set(ids) or len(smoke_ids) != len(ids):
            raise ValueError("smoke summary must cover the entire sealed pool")
        selected_ids = tuple(ids)
        if smoke.get("status") != "COMPLETE" or smoke.get("faults") != 0:
            raise ValueError("smoke summary must be COMPLETE and fault-free")
        selected_ledger_rows: list[Mapping[str, object]] = []
        partial = False
    else:
        selected_ids = tuple(str(value) for value in reference_ids)
        if not selected_ids or len(selected_ids) != len(set(selected_ids)):
            raise ValueError("reference_ids must be non-empty and unique")
        unknown = sorted(set(selected_ids) - set(ids))
        if unknown:
            raise ValueError(f"reference_ids are absent from sealed pool: {unknown}")
        if not set(selected_ids).issubset(set(smoke_ids)):
            raise ValueError("partial smoke summary must cover the selected reference_ids")
        ledger_path = smoke_path.parent / "evaluation" / "ledger.jsonl"
        if not ledger_path.is_file():
            raise ValueError("partial promotion requires evaluation/ledger.jsonl")
        try:
            raw_ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("partial promotion ledger is unreadable") from exc
        selected_ledger_rows = [
            row for row in raw_ledger
            if isinstance(row, Mapping) and str(row.get("opponent_id", "")) in set(selected_ids)
        ]
        if not selected_ledger_rows:
            raise ValueError("partial promotion ledger has no rows for reference_ids")
        games_per_seat = smoke.get("games_per_opponent_seat", 1)
        if type(games_per_seat) is not int or games_per_seat <= 0:
            raise ValueError("smoke summary games_per_opponent_seat must be positive")
        expected_per_reference = 2 * games_per_seat
        for opponent_id in selected_ids:
            rows_for_id = [row for row in selected_ledger_rows if str(row.get("opponent_id")) == opponent_id]
            if len(rows_for_id) != expected_per_reference:
                raise ValueError(
                    f"partial promotion requires complete smoke rows for {opponent_id}: "
                    f"{len(rows_for_id)} != {expected_per_reference}"
                )
            if any(row.get("status") != "DONE" or row.get("outcome") not in {"win", "draw", "loss"} for row in rows_for_id):
                raise ValueError(f"partial promotion reference is not fault-free: {opponent_id}")
        partial = True

    output.mkdir(parents=True)
    _copy_tree_contents(source_root, output, excluded={"pool_manifest.json", "fresh_meta.json", "cg_historical_split.json", "meta_manifest.json", "split_report.json"})
    selected_set = set(selected_ids)
    promoted_rows = []
    for row in pool_rows:
        if str(row["id"]) not in selected_set:
            continue
        row["smoke_ok"] = True
        row["smoke_summary_sha256"] = _sha256_file(smoke_path)
        promoted_rows.append(row)
    pool_out = output / "pool_manifest.json"
    _write_new(pool_out, promoted_rows)
    pool_sha = _sha256_file(pool_out)

    promoted_fresh = dict(fresh)
    promoted_fresh["reference_ids"] = list(selected_ids)
    promoted_fresh["references"] = [
        dict(ref) for ref in fresh_refs
        if isinstance(ref, Mapping) and str(ref.get("id")) in selected_set
    ]
    promoted_fresh["partial_promotion"] = partial
    if len(promoted_fresh["references"]) != len(selected_ids):
        raise ValueError("fresh_meta references do not cover selected reference_ids")
    promoted_fresh["pool_manifest_sha256"] = pool_sha
    promoted_fresh["smoke_summary_sha256"] = _sha256_file(smoke_path)
    promoted_fresh["smoke_status"] = "COMPLETE_FAULT_FREE"
    promoted_fresh["smoke_reference_ids"] = list(smoke_ids)
    fresh_out = output / "fresh_meta.json"
    input_pool_sha = _sha256_file(pool_path)
    input_smoke_sha = _sha256_file(smoke_path)
    smoke_out = output / "smoke_summary.json"
    promoted_smoke = dict(smoke)
    promoted_smoke["input_pool_manifest_sha256"] = input_pool_sha
    promoted_smoke["input_smoke_summary_sha256"] = input_smoke_sha
    promoted_smoke["pool_manifest_sha256"] = pool_sha
    promoted_smoke["promotion_schema_version"] = "cg-historical-meta-smoke-promotion-v1"
    promoted_smoke["status"] = "COMPLETE"
    promoted_smoke["faults"] = 0
    promoted_smoke["reference_ids"] = list(selected_ids)
    promoted_smoke["requested_games"] = len(selected_ledger_rows) if partial else requested
    promoted_smoke["completed_rows"] = len(selected_ledger_rows) if partial else completed
    promoted_smoke["partial_promotion"] = partial
    _write_new(smoke_out, promoted_smoke)
    output_smoke_sha = _sha256_file(smoke_out)
    promoted_fresh["smoke_summary_sha256"] = output_smoke_sha
    _write_new(fresh_out, promoted_fresh)
    report = {
        "schema_version": "cg-historical-meta-smoke-promotion-v1",
        "status": "SEALED",
        "input_pool_root": str(source_root),
        "input_pool_manifest_sha256": _sha256_file(pool_path),
        "input_fresh_meta_sha256": _sha256_file(fresh_path),
        "input_smoke_summary_sha256": input_smoke_sha,
        "smoke_summary_sha256": output_smoke_sha,
        "output_root": str(output),
        "pool_manifest_path": str(pool_out),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_out),
        "fresh_meta_sha256": _sha256_file(fresh_out),
        "reference_ids": list(selected_ids),
        "partial_promotion": partial,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(output / "smoke_promotion_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--fresh-meta", type=Path, required=True)
    parser.add_argument("--smoke-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-id", action="append", dest="reference_ids", help="promote only a fault-free subset proven by evaluation/ledger.jsonl")
    args = parser.parse_args(argv)
    report = promote_historical_meta_smoke_v1(
        pool_root=args.pool_root,
        fresh_meta_path=args.fresh_meta,
        smoke_summary_path=args.smoke_summary,
        output_root=args.output,
        reference_ids=args.reference_ids,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
