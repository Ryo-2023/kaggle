#!/usr/bin/env python3
"""Unified entrypoint for the Competition Intelligence sidecar CLI.

Usage examples::

    python scripts/run_competition_intelligence.py doctor
    python scripts/run_competition_intelligence.py ingest-local \\
        --run-dir runs/competition-intelligence/<run-id> --input <path>
    python scripts/run_competition_intelligence.py rebuild-catalog \\
        --run-dir runs/competition-intelligence/<run-id>

Only the commands implemented so far are available; see
``mage_ptcg.competition_intelligence.cli`` for the current scope and the
tracked continuation plan for the rest of the O1 design.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mage_ptcg.competition_intelligence.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
