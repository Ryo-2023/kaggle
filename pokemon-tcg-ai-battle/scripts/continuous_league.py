#!/usr/bin/env python3
"""継続 R2D3 学習とオフラインリーグを操作する entrypoint。"""

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for candidate in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mage_ptcg.continuous_league.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
