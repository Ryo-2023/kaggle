#!/usr/bin/env python3
"""Common24 guardrail for the explicit v6 coordinated packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scripts import run_rule_v0_meta_weighted_auto_common24_v1 as common
from scripts import run_rule_v0_root_deck_package_v6 as package


SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-v6-common24"
common.WEIGHTED_SCHEMA = package.SCHEMA
common.COMMON24_SCHEMA = SCHEMA


def execute_common24(
    *,
    source_root: Path,
    output: Path,
    base_seed: int = 23700000,
    workers: int = 12,
    worker_recycle_games: int = 16,
) -> dict[str, object]:
    if workers != 12:
        raise ValueError("this lane is sealed to workers=12")
    return common.execute_common24(
        source_root=source_root,
        output=output,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=23700000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = execute_common24(**vars(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA", "execute_common24"]
