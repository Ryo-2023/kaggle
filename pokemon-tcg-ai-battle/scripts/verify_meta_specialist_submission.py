"""Structurally verify one meta-specialist submission archive.

Thin wrapper over ``mage_ptcg.meta_specialist.cli``'s ``verify-submission``
subcommand.  Verification reads and structurally checks the archive's bytes
on disk only; it never imports or executes the archived ``main.py``, and it
never uploads, submits, or otherwise talks to the network or the Kaggle API.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cli import main as cli_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["verify-submission", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
