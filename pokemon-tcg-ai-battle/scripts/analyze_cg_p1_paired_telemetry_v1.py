#!/usr/bin/env python3
"""Build a strict, public-only P1/P0 paired telemetry diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

from mage_ptcg.meta_specialist.cg_p1_paired_telemetry_v1 import (
    PAIRED_TELEMETRY_SCHEMA,
    PairedTelemetryError,
    analyze_paired_public_telemetry_v1,
    pair_public_decisions_v1,
)


def _jsonl(path: Path) -> Iterable[dict[str, object]]:
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise PairedTelemetryError(f"JSON object required: {path}:{line_number}")
        yield value


def _rows(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.glob("*.jsonl")):
        rows.extend(row for row in _jsonl(path) if row.get("record_type") == "decision")
    if not rows:
        raise PairedTelemetryError(f"no decision telemetry: {root}")
    return rows


def _outcomes(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _jsonl(path):
        game_id = row.get("game_id")
        outcome = row.get("outcome")
        if not isinstance(game_id, str) or outcome not in {"win", "loss", "draw"}:
            raise PairedTelemetryError(f"invalid terminal row: {path}")
        if row.get("status") != "DONE" or row.get("fault_kind") not in {None, ""}:
            raise PairedTelemetryError(f"fault/non-DONE terminal row: {game_id}")
        if game_id in result and result[game_id] != outcome:
            raise PairedTelemetryError(f"conflicting terminal outcome: {game_id}")
        result[game_id] = str(outcome)
    return result


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p1-telemetry", type=Path, required=True)
    parser.add_argument("--p0-telemetry", type=Path, required=True)
    parser.add_argument("--p1-ledger", type=Path, required=True)
    parser.add_argument("--p0-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-support", type=int, default=8)
    args = parser.parse_args()
    for path in (args.p1_telemetry, args.p0_telemetry, args.p1_ledger, args.p0_ledger):
        if not path.exists():
            raise SystemExit(f"missing input: {path}")
    p1_rows = _rows(args.p1_telemetry)
    p0_rows = _rows(args.p0_telemetry)
    p1_outcomes = _outcomes(args.p1_ledger)
    p0_outcomes = _outcomes(args.p0_ledger)
    pairs = pair_public_decisions_v1(
        p1_rows,
        p0_rows,
        p1_outcomes=p1_outcomes,
        p0_outcomes=p0_outcomes,
    )
    analysis = analyze_paired_public_telemetry_v1(pairs, min_support=args.min_support)
    candidate_ids = {
        "p1": sorted({str(row.get("candidate_id")) for row in p1_rows}),
        "p0": sorted({str(row.get("candidate_id")) for row in p0_rows}),
    }
    payload = {
        "schema_version": PAIRED_TELEMETRY_SCHEMA,
        "diagnostic_only": True,
        "public_only": True,
        "authority": {"training": False, "teacher": False, "promotion": False, "submission": False, "longrun": False},
        "inputs": {
            "p1_telemetry": str(args.p1_telemetry),
            "p0_telemetry": str(args.p0_telemetry),
            "p1_ledger": str(args.p1_ledger),
            "p0_ledger": str(args.p0_ledger),
            "p1_telemetry_tree_sha256": _tree_sha256(args.p1_telemetry),
            "p0_telemetry_tree_sha256": _tree_sha256(args.p0_telemetry),
            "p1_ledger_sha256": hashlib.sha256(args.p1_ledger.read_bytes()).hexdigest(),
            "p0_ledger_sha256": hashlib.sha256(args.p0_ledger.read_bytes()).hexdigest(),
        },
        "rows": {"p1_decisions": len(p1_rows), "p0_decisions": len(p0_rows), "candidate_ids": candidate_ids},
        "analysis": analysis,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite: {args.output}")
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **analysis}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
