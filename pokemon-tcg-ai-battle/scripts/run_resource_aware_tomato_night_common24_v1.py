#!/usr/bin/env python3
"""Research-only common24 guardrail for the Night Stretcher Tomato child."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_resource_aware_tomato_common24_v1 as common24


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1097-1159-weighted-v1-20260814"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-night-common24-v1-20260814"
CANDIDATE_ID = "surface-1152-to-1097"
BASE_SEED = 22720000


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    # Bind the already sealed Night weighted root to the generic guardrail
    # without changing that runner or any production/evaluator entrypoint.
    common24.SOURCE_ROOT = SOURCE_ROOT
    common24.SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
    common24.SOURCE_SUMMARY = SOURCE_ROOT / "weighted48_summary.json"
    common24.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    common24.AE_CANDIDATE_ID = CANDIDATE_ID
    common24.COMMON24_BASE_SEED = BASE_SEED
    return common24.execute(output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(execute(output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CANDIDATE_ID", "OUTPUT_DEFAULT", "SOURCE_ROOT", "execute"]
