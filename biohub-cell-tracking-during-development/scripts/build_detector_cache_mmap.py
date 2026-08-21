#!/usr/bin/env python3
"""Build low-RSS edge sidecars for a READY detector cache."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.detector_fixed_race.cache import build_edge_memory_map  # noqa: E402, I001


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} CACHE_ROOT")
    print(build_edge_memory_map(Path(sys.argv[1])))
