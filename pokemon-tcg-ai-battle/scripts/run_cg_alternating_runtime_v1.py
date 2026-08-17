#!/usr/bin/env python3
"""Run one bounded deck→policy alternating iteration for cg packages.

The default is a sealed dry-run.  ``--execute`` is required before CABT is
started.  The runner never submits, trains, promotes, or starts an unbounded
loop; a positive stage only records the next successive-halving stage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    DEFAULT_WORKERS_V1,
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
    run_cg_alternating_iteration_v1,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
DEFAULT_POOL = ROOT / "opponents"


def _read_refs(config_path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        refs = payload["opponent_ids"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CgAlternatingRuntimeError(f"invalid opponent config: {config_path}") from exc
    if not isinstance(refs, list) or len(refs) != 24 or len(set(refs)) != 24:
        raise CgAlternatingRuntimeError("config must contain exactly 24 unique opponent_ids")
    return tuple(str(item) for item in refs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-candidate-package", type=Path, required=True)
    parser.add_argument("--deck-control-package", type=Path, required=True)
    parser.add_argument("--policy-candidate-package", type=Path, required=True)
    parser.add_argument("--policy-control-package", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--stage-games", type=int, choices=(96, 384, 768, 1536), default=96)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, choices=(16, 64), default=None)
    parser.add_argument("--execute", action="store_true", help="start the CABT evaluator")
    args = parser.parse_args(argv)
    try:
        result = run_cg_alternating_iteration_v1(
            deck_candidate=CgPackageSpecV1.from_package(args.deck_candidate_package),
            deck_control=CgPackageSpecV1.from_package(args.deck_control_package),
            policy_candidate=CgPackageSpecV1.from_package(args.policy_candidate_package),
            policy_control=CgPackageSpecV1.from_package(args.policy_control_package),
            reference_ids=_read_refs(args.config),
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
