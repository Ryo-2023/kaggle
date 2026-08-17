#!/usr/bin/env python3
"""Common24 gate for the positive 95cc native-policy neighborhood arms."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_meta_weighted_95cc_neighborhood_v1 as parent_lane
from scripts import run_meta_weighted_deck_search_common24_v1 as common


SCHEMA = "meta-specialist-meta-weighted-95cc-neighborhood-common24-v1"
common.SCHEMA = SCHEMA


def execute(*, source_root: Path, output: Path, base_seed: int = 23672000, workers: int = 12, worker_recycle_games: int = 16) -> dict[str, object]:
    if workers != 12:
        raise ValueError("this lane is sealed to workers=12")
    return common.execute(
        source_root=source_root,
        output=output,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=23672000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args()
    print(json.dumps(execute(**vars(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA", "execute"]
