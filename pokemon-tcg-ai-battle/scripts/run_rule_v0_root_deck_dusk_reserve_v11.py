#!/usr/bin/env python3
"""Smoke-gated reserve Dusk Ball candidates after the v10 priority screen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scripts import run_rule_v0_root_deck_novel_v10 as lane


SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-reserve-v11"
OUTPUT_DEFAULT = lane.base.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-dusk-reserve-v11-20260814"
WEIGHTED_BASE_SEED = 23640000
SMOKE_BASE_SEED = 23630000
SURFACES = (
    ("root-dusk-to-explorer-guidance", 1102, 1185),
    ("root-dusk-to-xerosic-machinations", 1102, 1197),
)

lane.SCHEMA = SCHEMA
lane.SURFACES = SURFACES
lane.EXPECTED_CANDIDATE_IDS = frozenset(row[0] for row in SURFACES)
lane.SMOKE_BASE_SEED = SMOKE_BASE_SEED
lane.base.SCHEMA = SCHEMA
lane.base.OUTPUT_DEFAULT = OUTPUT_DEFAULT
lane.base.WEIGHTED_BASE_SEED = WEIGHTED_BASE_SEED
lane.base.SURFACES = SURFACES


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args(argv)
    smoke_rows = lane.run_smoke()
    payload = {"schema_version": f"{SCHEMA}-runtime-smoke", "smoke": list(smoke_rows), "performance_score_allowed": False}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    if len(smoke_rows) != len(SURFACES) or not all(bool(row["smoke_pass"]) for row in smoke_rows):
        raise SystemExit("reserve smoke gate failed; weighted48 not started")
    result = lane.base.execute(args.output.resolve())
    print(json.dumps({"weighted48": result}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

