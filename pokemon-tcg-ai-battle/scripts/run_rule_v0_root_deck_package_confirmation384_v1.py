#!/usr/bin/env python3
"""Run seed-disjoint 384 confirmation for the positive two-card package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Import the package producer first so the generic Rule v0 builders are bound
# to the coordinated-package schema before importing the confirmation module.
from scripts import run_rule_v0_root_deck_package_v1 as package
from scripts import run_rule_v0_root_deck_package_common24_v1 as package_common
from scripts import run_rule_v0_meta_weighted_auto_confirmation384_v1 as confirmation


COMMON24_SCHEMA = package_common.SCHEMA
CONFIRMATION_SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-confirmation384-v1"
CONFIRMATION_CANDIDATE_ID = "8de3e32b1ed3f3c229c418412a722d99384b3986b28797a0a8d7d6eb15f5a057"
DEFAULT_WORKERS = 12
SELECTED_CANDIDATE_ID = CONFIRMATION_CANDIDATE_ID

confirmation.COMMON24_SCHEMA = COMMON24_SCHEMA
confirmation.CONFIRMATION_SCHEMA = CONFIRMATION_SCHEMA
confirmation.DEFAULT_BASE_SEED = 23_683_000
confirmation.OUTPUT_DEFAULT = (
    confirmation.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-package-confirmation384-v1-20260814"
)

_ORIGINAL_SELECT_POSITIVE = confirmation.select_positive_common24_candidates


def select_positive_common24_candidates(manifest, summary):
    rows = _ORIGINAL_SELECT_POSITIVE(manifest, summary)
    selected = tuple(row for row in rows if row.get("candidate_id") == SELECTED_CANDIDATE_ID)
    if len(selected) != 1:
        raise confirmation.RuleV0MetaWeightedConfirmationError(
            "the sealed two-card package is not uniquely common24-positive"
        )
    return selected


confirmation.select_positive_common24_candidates = select_positive_common24_candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=confirmation.OUTPUT_DEFAULT)
    parser.add_argument("--base-seed", type=int, default=confirmation.DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=confirmation.DEFAULT_WORKER_RECYCLE_GAMES)
    parser.add_argument("--candidate-id", default=CONFIRMATION_CANDIDATE_ID)
    args = parser.parse_args(argv)
    if args.workers != DEFAULT_WORKERS:
        raise SystemExit("this confirmation lane is sealed to workers=12")
    global SELECTED_CANDIDATE_ID
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


__all__ = [
    "COMMON24_SCHEMA",
    "CONFIRMATION_CANDIDATE_ID",
    "CONFIRMATION_SCHEMA",
    "DEFAULT_WORKERS",
    "confirmation",
    "select_positive_common24_candidates",
]
