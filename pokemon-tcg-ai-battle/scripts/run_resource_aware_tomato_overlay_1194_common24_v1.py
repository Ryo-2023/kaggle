#!/usr/bin/env python3
"""Research-only common24 guardrails for the two positive Colress swaps."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import run_resource_aware_tomato_common24_v1 as common24


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-support-weighted-v1-20260814"
CANDIDATES = {
    "surface-1182-to-1194": ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-common24-1182-v1-20260814",
    "surface-1227-to-1194": ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-common24-1227-v1-20260814",
}
BASE_SEEDS = {
    "surface-1182-to-1194": 22780000,
    "surface-1227-to-1194": 22790000,
}


def execute(*, candidate_id: str, output: Path | None = None) -> dict[str, object]:
    if candidate_id not in CANDIDATES:
        raise ValueError(f"unsupported positive candidate: {candidate_id}")
    destination = CANDIDATES[candidate_id] if output is None else output
    common24.SCHEMA = f"meta-specialist-resource-aware-tomato-overlay-1194-{candidate_id}-common24-v1"
    common24.SOURCE_ROOT = SOURCE_ROOT
    common24.SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
    common24.SOURCE_SUMMARY = SOURCE_ROOT / "weighted48_summary.json"
    common24.OUTPUT_DEFAULT = destination
    common24.AE_CANDIDATE_ID = candidate_id
    common24.COMMON24_BASE_SEED = BASE_SEEDS[candidate_id]
    return common24.execute(output=destination)


def main() -> int:
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, choices=sorted(CANDIDATES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(execute(candidate_id=args.candidate, output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CANDIDATES", "execute"]

