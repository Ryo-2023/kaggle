#!/usr/bin/env python3
"""Research-only wrapper for the next Tomato-parent overlay screen.

This wrapper reuses the sealed research runner but binds a fresh, explicit
surface (1244→1141 and 1244→1102).  It never changes production entrypoints or
starts common24/384/longrun/submission.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import run_resource_aware_tomato_surface_weighted_v1 as surface


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1141-1102-weighted-v1-20260814"
SURFACE_SWAPS = ((1244, 1141), (1244, 1102))


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    # Bind all mutable runner inputs before materialization.  The imported
    # runner's self-novelty scan excludes this fresh root, while its no-clobber
    # gate rejects an already populated destination.
    surface.SURFACE_SWAPS = SURFACE_SWAPS
    surface.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    return surface.execute(output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    import json
    print(json.dumps(execute(output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["OUTPUT_DEFAULT", "SURFACE_SWAPS", "execute"]
