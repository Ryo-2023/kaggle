#!/usr/bin/env python3
"""Unified entrypoint for the Offline Training v1 pipeline.

Usage examples::

    python scripts/run_offline_training_v1.py doctor --config configs/offline_training_v1/smoke.json
    python scripts/run_offline_training_v1.py pipeline --config configs/offline_training_v1/smoke.json
    python scripts/run_offline_training_v1.py resume --run-dir runs/offline-training-v1/<run-id>
    python scripts/run_offline_training_v1.py status --run-dir runs/offline-training-v1/<run-id>

The interpreter is resolved by the caller; the pipeline itself is
cwd-independent and interpreter-agnostic as long as the environment provides the
project dependencies (numpy and, for training, PyTorch).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mage_ptcg.offline_training.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
