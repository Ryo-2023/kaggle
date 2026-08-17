#!/usr/bin/env python3
"""Run a bounded CABT smoke over a sealed historical opponent pool."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import run_parallel_cabt_evaluation  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_smoke(
    *,
    pool_root: Path,
    candidate_package: Path,
    output_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    workers: int,
    timeout_seconds: float,
    reference_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    pool_root = pool_root.resolve()
    candidate_package = candidate_package.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(output_root)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    output_root.mkdir(parents=True, exist_ok=False)
    raw_pool = json.loads((pool_root / "pool_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(raw_pool, list) or not raw_pool:
        raise ValueError("historical pool manifest must be a non-empty list")
    pool_refs = tuple(str(row["id"]) for row in raw_pool if isinstance(row, dict))
    if reference_ids is None:
        refs = pool_refs
    else:
        refs = tuple(str(item) for item in reference_ids)
        if not refs or len(set(refs)) != len(refs):
            raise ValueError("reference_ids must be non-empty and unique when supplied")
        unknown = sorted(set(refs) - set(pool_refs))
        if unknown:
            raise ValueError(f"reference_ids are not present in historical pool: {unknown}")
    arm = arena.ArenaArm(
        arm_id="historical-pool-smoke-p1",
        policy_id="cg-lethal-target-v1",
        policy_sha256=arena._sha256(candidate_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=candidate_package,
    )
    games = arena._build_games(
        arm=arm,
        refs=refs,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id="historical-meta-smoke",
    )
    games = tuple(replace(game, timeout_seconds=float(timeout_seconds)) for game in games)
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=output_root / "evaluation",
        max_workers=workers,
        worker_recycle_games=16,
        overwrite=False,
    )
    faults = sum(1 for row in result["rows"] if row.get("outcome") == "fault")
    summary = {
        "schema_version": "cg-historical-meta-smoke-v1",
        "status": "COMPLETE" if faults == 0 and len(result["rows"]) == len(games) else "FAULT",
        "pool_root": str(pool_root),
        "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
        "candidate_package": str(candidate_package),
        "candidate_policy_sha256": _sha256(candidate_package / "main.py"),
        "reference_ids": list(refs),
        "requested_games": len(games),
        "completed_rows": len(result["rows"]),
        "faults": faults,
        "games_per_opponent_seat": games_per_opponent_seat,
        "base_seed": base_seed,
        "per_game_timeout_seconds": float(timeout_seconds),
        "evaluator_summary": result["summary"],
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    (output_root / "smoke_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=20260870)
    parser.add_argument("--games-per-opponent-seat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="bounded worker timeout for each smoke game; use a smaller value to quarantine slow sources",
    )
    parser.add_argument(
        "--reference-id",
        action="append",
        dest="reference_ids",
        help="smoke only the selected pool reference; repeat for a training-only subset",
    )
    args = parser.parse_args(argv)
    print(json.dumps(run_smoke(pool_root=args.pool_root, candidate_package=args.candidate_package, output_root=args.output, base_seed=args.base_seed, games_per_opponent_seat=args.games_per_opponent_seat, workers=args.workers, timeout_seconds=args.timeout_seconds, reference_ids=args.reference_ids), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
