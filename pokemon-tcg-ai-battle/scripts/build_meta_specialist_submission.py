"""Build and structurally verify a meta-specialist submission archive.

Thin wrapper over ``mage_ptcg.meta_specialist.cli``'s ``build-submission``
subcommand.  Building only produces and structurally verifies local archive
bytes from an already-authored bundle spec (see
``mage_ptcg.meta_specialist.package.write_bundle_spec``); it never uploads,
submits, or otherwise talks to the network or the Kaggle API.  See
``docs/runbooks/meta-specialist-p0-foundation.md`` for the full local
workflow (qualify-deck -> lock-deck -> write a bundle spec -> build here ->
verify).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cli import main as cli_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    forwarded = list(sys.argv[1:] if argv is None else argv)
    return cli_main(["build-submission", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
