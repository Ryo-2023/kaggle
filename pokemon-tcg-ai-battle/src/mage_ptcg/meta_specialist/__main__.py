"""``python -m mage_ptcg.meta_specialist`` entrypoint for the local JSON CLI."""

from __future__ import annotations

import sys

from mage_ptcg.meta_specialist.cli import main

if __name__ == "__main__":
    sys.exit(main())
