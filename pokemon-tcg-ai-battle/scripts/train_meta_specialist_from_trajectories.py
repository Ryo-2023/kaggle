"""Train the meta-specialist policy from already-collected trajectories.

Thin wrapper over ``mage_ptcg.meta_specialist.cli``'s ``train-from-trajectories``
subcommand -- the human-runnable entry point that turns real, collected
``collect-trajectories`` output into real V-trace optimizer steps
(``mage_ptcg.meta_specialist.train_from_trajectories_v1``).  It reads
``games/*/record.json`` from a ``collect-trajectories`` run directory, admits
trajectories by pool-epoch age window, takes optimizer steps up to an
explicit ``--max-steps`` budget, and publishes a resumable, content-addressed
checkpoint.  Re-running the identical command against the same ``--run-name``
continues from the stored step rather than restarting.  It never uploads,
submits, or otherwise talks to the network or the Kaggle API.

Run with ``--help`` for the full argument list, e.g.::

    PYTHONPATH=.:src .venv/bin/python scripts/train_meta_specialist_from_trajectories.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cli import main as cli_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["train-from-trajectories", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
