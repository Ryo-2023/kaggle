"""Collect real meta-specialist trajectories through the actor worker pool.

Thin wrapper over ``mage_ptcg.meta_specialist.cli``'s ``collect-trajectories``
subcommand -- the human-runnable entry point for Slice L5 real trajectory
collection (``mage_ptcg.meta_specialist.actor_pool_v1``).  It plays real
CABT games against the committed rule-agent opponent, one archetype lane at
a time, only ever against a deck the seed qualification report
(``runs/meta-specialist-seed-qualification/seed_qualification_report_v1.json``)
already marks ``qualified``.  It is resumable: re-running the identical
command skips every game already collected under the same ``--run-name``.
It never uploads, submits, or otherwise talks to the network or the Kaggle
API.

Run with ``--help`` for the full argument list, e.g.::

    PYTHONPATH=.:src .venv/bin/python scripts/collect_meta_specialist_trajectories.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cli import main as cli_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["collect-trajectories", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
