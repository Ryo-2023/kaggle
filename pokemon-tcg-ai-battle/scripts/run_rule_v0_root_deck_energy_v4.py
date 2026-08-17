#!/usr/bin/env python3
"""Sealed research-only Basic-F-energy ratio surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_root_deck_weighted_v1 as base
from scripts import run_rule_v0_root_deck_tool_stadium_v3 as common24_runner


base.SCHEMA = "meta-specialist-rule-v0-root-deck-energy-v4"
base.OUTPUT_DEFAULT = base.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-energy-v4-weighted48-20260814"
base.WEIGHTED_BASE_SEED = 23_490_000
# Keep the Pokémon/trainer core fixed while changing exactly one of the 14
# Basic Fighting Energy cards.  Both alternatives are recognized legal cards
# and are non-ACE-SPEC.
base.ROOT_CORE_COUNTS = {**base.ROOT_CORE_COUNTS, 6: 13}
base.SURFACES = (
    ("root-basic-f-to-rock-fighting-energy", 6, 20),
    ("root-basic-f-to-mist-energy", 6, 11),
)
COMMON24_BASE_SEED = 23_500_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    parser.add_argument("--common24-from-weighted", action="store_true")
    parser.add_argument("--common24-output", type=Path, default=None)
    args = parser.parse_args()
    if args.common24_from_weighted:
        # Reuse the sealed dynamic-positive common24 implementation in the
        # Tool/Stadium research wrapper.  Both modules share the imported base
        # object, so this wrapper's schema and disjoint seed are bound before
        # execution; no production runner or prior root is touched.
        base.SCHEMA = "meta-specialist-rule-v0-root-deck-energy-v4"
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
