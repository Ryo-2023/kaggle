#!/usr/bin/env python3
"""Run seed-disjoint 384 confirmation for the priority-one Dusk candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_meta_weighted_auto_confirmation384_v1 as confirmation


confirmation.COMMON24_SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-v10-common24"
confirmation.CONFIRMATION_SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-v10-confirmation384"
confirmation.DEFAULT_BASE_SEED = 23_630_000
confirmation.OUTPUT_DEFAULT = (
    confirmation.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-confirmation384-20260814"
)
CONFIRMATION_CANDIDATE_ID = "root-dusk-to-bloodmoon-ursaluna"


_ORIGINAL_SELECT_POSITIVE = confirmation.select_positive_common24_candidates


def _select_priority_one(manifest, summary):
    rows = _ORIGINAL_SELECT_POSITIVE(manifest, summary)
    selected = tuple(row for row in rows if row.get("candidate_id") == CONFIRMATION_CANDIDATE_ID)
    if len(selected) != 1:
        raise confirmation.RuleV0MetaWeightedConfirmationError(
            "priority-one Dusk candidate is not uniquely common24-positive"
        )
    return selected


confirmation.select_positive_common24_candidates = _select_priority_one


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=confirmation.OUTPUT_DEFAULT)
    args = parser.parse_args()
    result = confirmation.execute_confirmation384(
        source_root=args.source_root.resolve(),
        output=args.output.resolve(),
        base_seed=confirmation.DEFAULT_BASE_SEED,
        workers=12,
        worker_recycle_games=64,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
