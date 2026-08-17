#!/usr/bin/env python3
"""Sealed research-only novel line/search surface for the P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_rule_v0_root_deck_weighted_v1 as base


base.SCHEMA = "meta-specialist-rule-v0-root-deck-novel-v7"
base.OUTPUT_DEFAULT = (
    base.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-novel-v7-weighted48-20260814"
)
base.WEIGHTED_BASE_SEED = 23_550_000

# Exclude only the slots touched by the two intentionally distinct mutations
# from the shared core assertion.  Every other Pokémon, trainer, energy,
# legality, and exactly-one ACE SPEC gate remains enforced by the base runner.
base.ROOT_CORE_COUNTS = {
    card_id: count
    for card_id, count in base.ROOT_CORE_COUNTS.items()
    if card_id not in {673, 674, 1102, 1152}
}
base.SURFACES = (
    ("root-hariyama-to-makuhita", 674, 673),
    ("root-poke-pad-to-dusk-ball", 1152, 1102),
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
