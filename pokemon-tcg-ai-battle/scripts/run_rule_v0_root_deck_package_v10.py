#!/usr/bin/env python3
"""Smoke-gated coordinated Dusk/search packages after the v10 priority screen.

This is a research-only wrapper around the audited package runner.  It keeps
Rule v0 fixed and evaluates two explicit two-card hypotheses generated from
the v10 hard-negative result:

* two Dusk Ball slots become Bloodmoon Ursaluna + Hilda;
* Dusk Ball + Fighting Gong become Bloodmoon Ursaluna + Ultra Ball.

No production entrypoint or submission authority is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from scripts import run_rule_v0_root_deck_package_v6 as lane


SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-v10"
OUTPUT_DEFAULT = lane.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-package-v10-20260814"
DEFAULT_GENERATOR_SEED = 23682000
DEFAULT_BASE_SEED = 23683000
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 2

# The first package follows the two priority-one cards.  The second preserves
# a generic search role while adding the same direct Bloodmoon line; both are
# novel two-card hypotheses and are checked again by the imported builder.
lane.FIXED_PACKAGES = (
    ((1102, 1102), (135, 1225)),
    ((1102, 1142), (135, 1121)),
)
lane.SCHEMA = SCHEMA
lane.OUTPUT_DEFAULT = OUTPUT_DEFAULT
lane.base.SCHEMA = SCHEMA
lane.base.OUTPUT_DEFAULT = OUTPUT_DEFAULT
lane.package.SCHEMA = SCHEMA
lane.package.OUTPUT_DEFAULT = OUTPUT_DEFAULT


def execute_with_smoke(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if workers != DEFAULT_WORKERS:
        raise ValueError("v10 package lane is sealed to workers=12")
    return lane.execute_with_smoke(
        output=output,
        candidate_count=candidate_count,
        generator_seed=generator_seed,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--generator-seed", type=int, default=DEFAULT_GENERATOR_SEED)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args(argv)
    print(json.dumps(execute_with_smoke(**vars(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

