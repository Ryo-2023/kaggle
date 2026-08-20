#!/usr/bin/env python3
"""CLI wrapper for the detector-fixed association race."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import biohub.detector_fixed_race.cli as _cli  # noqa: E402

_build_parser = _cli._build_parser
main = _cli.main


if __name__ == "__main__":
    raise SystemExit(main())
