#!/usr/bin/env python3
"""Build a TRAIN-only calibrated heterogeneous meta pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.opponent_ingest.calibrated_meta_pool_v1 import (  # noqa: E402
    CalibratedMetaPoolError,
    build_calibrated_meta_pool_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", action="append", dest="source_roots", required=True, type=Path)
    parser.add_argument("--ledger", action="append", dest="ledgers", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--p1-package", required=True, type=Path)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--target-score", type=float, default=0.15)
    parser.add_argument("--score-floor", type=float, default=0.02)
    parser.add_argument("--score-ceiling", type=float, default=0.35)
    parser.add_argument("--requested-count", type=int, default=12)
    parser.add_argument("--min-families", type=int, default=3)
    parser.add_argument("--family-cap", type=int, default=4)
    parser.add_argument("--min-games-per-candidate", type=int, default=2)
    parser.add_argument("--consumed-id", action="append", default=[])
    parser.add_argument("--consumed-policy-sha256", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        report = build_calibrated_meta_pool_v1(
            source_roots=tuple(args.source_roots),
            calibration_ledger_paths=tuple(args.ledgers),
            output_root=args.output,
            p1_package=args.p1_package,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            target_score=args.target_score,
            score_floor=args.score_floor,
            score_ceiling=args.score_ceiling,
            requested_count=args.requested_count,
            min_families=args.min_families,
            family_cap=args.family_cap,
            min_games_per_candidate=args.min_games_per_candidate,
            consumed_ids=tuple(args.consumed_id),
            consumed_policy_sha256=tuple(args.consumed_policy_sha256),
        )
    except (CalibratedMetaPoolError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
