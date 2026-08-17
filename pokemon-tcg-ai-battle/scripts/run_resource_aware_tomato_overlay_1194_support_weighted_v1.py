#!/usr/bin/env python3
"""Research-only Tomato-parent screen for Colress's Tenacity line swaps.

Both candidates preserve the Supporter role while testing whether searching
the Full Metal Lab and Metal Energy (Colress's Tenacity) improves the
Archaludon line when replacing one copy of Boss's Orders or Lillie's
Determination.  This wrapper binds a fresh seed domain and output root to the
existing fail-closed weighted runner; it never starts a guardrail or longrun.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import run_resource_aware_tomato_surface_weighted_v1 as surface


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-support-weighted-v1-20260814"
SURFACE_SWAPS = ((1182, 1194), (1227, 1194))
WARMUP_BASE_SEED = 22760000
WEIGHTED_BASE_SEED = 22770000


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

