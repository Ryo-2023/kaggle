#!/usr/bin/env python3
"""Run the v10 Dusk Ball candidates through the broad common24 guardrail."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_meta_weighted_auto_common24_v1 as common


common.WEIGHTED_SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-v10"
common.COMMON24_SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-v10-common24"
common.DEFAULT_BASE_SEED = 23_620_000
common.OUTPUT_DEFAULT = (
    common.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-common24-20260814"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=common.OUTPUT_DEFAULT)
    args = parser.parse_args()
    result = common.execute_common24(
        source_root=args.source_root.resolve(),
        output=args.output.resolve(),
        base_seed=common.DEFAULT_BASE_SEED,
        workers=12,
        worker_recycle_games=16,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
