#!/usr/bin/env python3
"""Research-only 384 confirmation for the common24-positive Colress swap."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts import run_resource_aware_b92_confirmation_v1 as _base


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-support-weighted-v1-20260814"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-confirmation384-1182-v1-20260814"
CANDIDATE_ID = "surface-1182-to-1194"
MUTATION = {"removed_cards": [1182], "added_cards": [1194]}
CONFIRMATION_BASE_SEED = 22800000
WARMUP_BASE_SEED = 22795000
RECYCLE_GAMES = 64


def _configure() -> None:
    _base.SCHEMA = "meta-specialist-resource-aware-tomato-overlay-1194-confirmation384-v1"
    _base.SOURCE_ROOT = SOURCE_ROOT
    _base.SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
    _base.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    _base.B92_CANDIDATE_ID = CANDIDATE_ID
    _base.B92_MUTATION = dict(MUTATION)
    _base.COMMON24_BASE_SEED = CONFIRMATION_BASE_SEED
    _base.WARMUP_BASE_SEED = WARMUP_BASE_SEED
    _base.CONFIRMATION_GAMES_PER_OPPONENT_SEAT = 8
    _base.RAMP_WORKERS = (1, 2, 4, 8, 12)
    original_from_json = _base.ResourceBudget.from_json

    def from_json(path: str | Path):
        return replace(original_from_json(path), recycle_games=RECYCLE_GAMES)

    _base.ResourceBudget.from_json = staticmethod(from_json)
    original_write_text = _base._write_text_no_clobber

    def write_text(path: Path, text: str) -> str:
        if path.name == "confirmation_summary.md":
            text = text.replace("# b92a resource-aware 384 confirmation", "# Colress support resource-aware 384 confirmation")
            text = text.replace(f"`{CANDIDATE_ID}` (1185→1159)", f"`{CANDIDATE_ID}` (1182→1194)")
        return original_write_text(path, text)

    _base._write_text_no_clobber = write_text


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    _configure()
    return _base.execute(output=output)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(execute(output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CANDIDATE_ID", "MUTATION", "OUTPUT_DEFAULT", "execute"]

