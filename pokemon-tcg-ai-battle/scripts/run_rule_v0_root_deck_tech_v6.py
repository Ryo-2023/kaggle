#!/usr/bin/env python3
"""Sealed research-only Basic-energy/one-prize tech surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_root_deck_weighted_v1 as base


base.SCHEMA = "meta-specialist-rule-v0-root-deck-tech-v6"
base.OUTPUT_DEFAULT = base.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-tech-v6-weighted48-20260814"
base.WEIGHTED_BASE_SEED = 23_530_000
# The two candidates intentionally alter different root slots.  Remove only
# those source IDs from the shared core assertion; every other root Pokémon,
# trainer, energy count, 60-card legality, and exactly-one ACE SPEC gate stays
# enforced by the common fail-closed builder.
base.ROOT_CORE_COUNTS = {k: v for k, v in base.ROOT_CORE_COUNTS.items() if k not in {6, 677}}
base.SURFACES = (
    ("root-basic-f-to-prism-energy", 6, 16),
    ("root-riolu-to-stonjourner", 677, 682),
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
