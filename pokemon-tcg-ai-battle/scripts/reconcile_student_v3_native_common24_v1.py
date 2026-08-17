#!/usr/bin/env python3
"""Verify and summarize one Student v3 versus native Tomato common24 stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (  # noqa: E402
    Common24ReconciliationError,
    write_student_v3_native_common24_reconciliation_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing reconciliation artifact after full re-verification",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        raise SystemExit(f"output already exists; pass --overwrite explicitly: {output}")
    try:
        result = write_student_v3_native_common24_reconciliation_v1(
            args.request.resolve(), output
        )
    except Common24ReconciliationError as exc:
        raise SystemExit(f"common24 reconciliation blocked: {exc}") from exc
    print(
        json.dumps(
            {
                "artifact_path": result["artifact_path"],
                "artifact_sha256": result["artifact_sha256"],
                "reconciliation_sha256": result["reconciliation_sha256"],
                "status": result["gate"]["status"],
                "promotion_gate_eligible": result["gate"][
                    "promotion_gate_eligible"
                ],
                "target_games_per_arm": result["target_games_per_arm"],
                "candidate_minus_native_score_rate": result["comparison"][
                    "candidate_minus_native_score_rate"
                ],
                "authority": result["authority"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
