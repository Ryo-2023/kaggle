#!/usr/bin/env python3
"""Seed-disjoint 384 confirmation for the best v6 package only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scripts import run_rule_v0_meta_weighted_auto_confirmation384_v1 as confirmation
from scripts import run_rule_v0_root_deck_package_v6_common24 as package_common
from scripts import run_rule_v0_root_deck_package_v6 as package


COMMON24_SCHEMA = package_common.SCHEMA
CONFIRMATION_SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-v6-confirmation384"
SELECTED_CANDIDATE_ID = "9f1ea0032b53e780729034fa20eddf58a6b6701c225e4e2e25180cf40310e080"
DEFAULT_WORKERS = 12
DEFAULT_BASE_SEED = 23701000

confirmation.COMMON24_SCHEMA = COMMON24_SCHEMA
confirmation.CONFIRMATION_SCHEMA = CONFIRMATION_SCHEMA
confirmation.DEFAULT_BASE_SEED = DEFAULT_BASE_SEED
confirmation.OUTPUT_DEFAULT = package.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-package-v6-confirmation384-20260814"

_ORIGINAL_SELECT = confirmation.select_positive_common24_candidates


def select_positive_common24_candidates(manifest, summary):
    rows = _ORIGINAL_SELECT(manifest, summary)
    selected = tuple(row for row in rows if row.get("candidate_id") == SELECTED_CANDIDATE_ID)
    if len(selected) != 1:
        raise confirmation.RuleV0MetaWeightedConfirmationError(
            "v6 recovery/reset package is not uniquely common24-positive"
        )
    return selected


confirmation.select_positive_common24_candidates = select_positive_common24_candidates


def main(argv: Sequence[str] | None = None) -> int:
    global SELECTED_CANDIDATE_ID
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=confirmation.OUTPUT_DEFAULT)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=64)
    parser.add_argument("--candidate-id", default=SELECTED_CANDIDATE_ID)
    args = parser.parse_args(argv)
    if args.workers != DEFAULT_WORKERS or args.worker_recycle_games != 64:
        raise SystemExit("this confirmation lane is sealed to workers=12/recycle=64")
    SELECTED_CANDIDATE_ID = str(args.candidate_id)
    result = confirmation.execute_confirmation384(
        source_root=args.source_root.resolve(),
        output=args.output.resolve(),
        base_seed=args.base_seed,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["COMMON24_SCHEMA", "CONFIRMATION_SCHEMA", "DEFAULT_WORKERS", "SELECTED_CANDIDATE_ID"]
