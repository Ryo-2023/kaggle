#!/usr/bin/env python3
"""Screen the hash-bound v2 P1 policy variants with the paired P1 runner.

The underlying runner owns the CABT lease and evaluation contract.  This
adapter only swaps in the v2 materializer so the output remains research-only
and uses the immutable P1 package as control.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v2 import (  # noqa: E402
    VARIANT_IDS,
    materialize_p1_variant_package_v2,
)
from scripts import run_cg_p1_variant_screen_v1 as _runner


_runner.VARIANT_IDS = VARIANT_IDS
_runner.materialize_p1_variant_package_v1 = materialize_p1_variant_package_v2


def run_p1_variant_screen(*args, **kwargs):
    return _runner.run_p1_variant_screen(*args, **kwargs)


def main(argv=None) -> int:
    return _runner.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["VARIANT_IDS", "main", "run_p1_variant_screen"]
