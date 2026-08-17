#!/usr/bin/env python3
"""Sealed research-only Solrock/Lunatone line-ratio surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_root_deck_weighted_v1 as base
from scripts import run_rule_v0_root_deck_tool_stadium_v3 as common24_runner


base.SCHEMA = "meta-specialist-rule-v0-root-deck-line-v5"
base.OUTPUT_DEFAULT = base.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-line-v5-weighted48-20260814"
base.WEIGHTED_BASE_SEED = 23_510_000
# The two arms are a symmetric one-card Solrock/Lunatone ratio test:
# root has Lunatone x2 / Solrock x3.  Exclude those two changing counts from
# the shared core assertion while retaining every other Pokémon/trainer/energy
# core count and the exactly-one ACE SPEC gate.
base.ROOT_CORE_COUNTS = {k: v for k, v in base.ROOT_CORE_COUNTS.items() if k not in {675, 676}}
base.SURFACES = (
    ("root-solrock-to-lunatone", 676, 675),
    ("root-lunatone-to-solrock", 675, 676),
)
COMMON24_BASE_SEED = 23_520_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    parser.add_argument("--common24-from-weighted", action="store_true")
    parser.add_argument("--common24-output", type=Path, default=None)
    args = parser.parse_args()
    if args.common24_from_weighted:
        base.SCHEMA = "meta-specialist-rule-v0-root-deck-line-v5"
        common24_runner.COMMON24_BASE_SEED = COMMON24_BASE_SEED
        result = common24_runner.execute_common24(
            source_root=args.output.resolve(),
            output=(args.common24_output or Path(str(args.output) + "-common24")).resolve(),
        )
    else:
        result = base.execute(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
