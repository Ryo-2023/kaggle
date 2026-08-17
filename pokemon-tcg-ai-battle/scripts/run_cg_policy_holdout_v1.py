#!/usr/bin/env python3
"""Run one bounded policy-only cg holdout on a frozen deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import CgAlternatingRuntimeError, CgPackageSpecV1
from mage_ptcg.meta_specialist.cg_policy_holdout_v1 import load_policy_holdout_refs, run_policy_holdout_v1


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, default=ROOT / "opponents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--stage-games", type=int, choices=(96, 384, 768, 1536), default=96)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, choices=(16, 64), default=None)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_policy_holdout_v1(
            candidate=CgPackageSpecV1.from_package(args.candidate_package),
            control=CgPackageSpecV1.from_package(args.control_package),
            reference_ids=load_policy_holdout_refs(args.config),
            pool_root=args.pool_root,
            stage_games=args.stage_games,
            base_seed=args.base_seed,
            output_root=args.output,
            execute=args.execute,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (CgAlternatingRuntimeError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
