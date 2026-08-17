"""Build a hash-bound, outcome-only META_TRAIN hard-negative schedule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from mage_ptcg.meta_specialist.outcome_only_hard_negative_v1 import (
    build_outcome_only_hard_negative_schedule_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--meta-manifest", type=Path, required=True)
    parser.add_argument("--pool-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quota", type=int, default=96)
    parser.add_argument("--seed", default="outcome-only-v1")
    parser.add_argument("--opponent-cap", type=float, default=0.35)
    parser.add_argument("--family-cap", type=float, default=0.55)
    parser.add_argument("--family-floor", type=int, default=1)
    parser.add_argument("--repetitions-per-seat", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_outcome_only_hard_negative_schedule_v1(
        repo_root=args.repo_root,
        ledger_path=args.ledger,
        summary_path=args.summary,
        meta_manifest_path=args.meta_manifest,
        pool_manifest_path=args.pool_manifest,
        output_manifest_path=args.output,
        quota=args.quota,
        seed=args.seed,
        max_opponent_weight=args.opponent_cap,
        max_family_weight=args.family_cap,
        min_family_quota=args.family_floor,
        repetitions_per_seat=args.repetitions_per_seat,
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "schedule_sha256": payload["schedule_sha256"],
                "included_opponents": payload["summary"]["included_opponents"],
                "included_games": payload["summary"]["included_games"],
                "excluded_heldout": payload["summary"]["excluded_opponents"],
                "quota": payload["summary"]["quota_sum"],
                "authority": payload["authority"],
                "research_only": payload["research_only"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
