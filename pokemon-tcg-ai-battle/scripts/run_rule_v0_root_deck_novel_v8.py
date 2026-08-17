#!/usr/bin/env python3
"""Sealed research-only Fighting-search/mobility surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_root_deck_weighted_v1 as base


base.SCHEMA = "meta-specialist-rule-v0-root-deck-novel-v8"
base.OUTPUT_DEFAULT = (
    base.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-novel-v8-weighted48-20260814"
)
base.WEIGHTED_BASE_SEED = 23_570_000

# Both mutations replace one Fighting-search card with an already supported
# utility card.  Exclude only the touched source ID from the shared core;
# legality, 60-card count, and exactly-one ACE SPEC remain fail-closed.
base.ROOT_CORE_COUNTS = {k: v for k, v in base.ROOT_CORE_COUNTS.items() if k not in {1142}}
base.SURFACES = (
    ("root-fighting-gong-to-switch", 1142, 1123),
    ("root-fighting-gong-to-premium-power-pro", 1142, 1141),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    args = parser.parse_args()
    result = base.execute(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
