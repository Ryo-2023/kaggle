#!/usr/bin/env python3
"""Research-only Tomato-parent weighted screen for two novel one-card swaps.

The underlying runner remains unchanged; this wrapper binds a fresh, explicit
surface and disjoint seed domain.  No common24, confirmation, longrun,
submission, or production entrypoint is started automatically.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import run_resource_aware_tomato_surface_weighted_v1 as surface


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1086-1192-weighted-v1-20260814"
SURFACE_SWAPS = ((1244, 1086), (1244, 1192))
WARMUP_BASE_SEED = 22740000
WEIGHTED_BASE_SEED = 22750000


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    surface.SURFACE_SWAPS = SURFACE_SWAPS
    surface.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    surface.WARMUP_BASE_SEED = WARMUP_BASE_SEED
    surface.WEIGHTED_BASE_SEED = WEIGHTED_BASE_SEED
    return surface.execute(output=output)


def main() -> int:
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(execute(output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["OUTPUT_DEFAULT", "SURFACE_SWAPS", "execute"]

