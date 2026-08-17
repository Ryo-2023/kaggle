#!/usr/bin/env python3
"""Research-only 384-game confirmation for the sealed Night Stretcher deck.

This wrapper reuses the fail-closed b92 confirmation harness with a different,
explicitly sealed source manifest.  It is intentionally separate from the
production evaluator and keeps the Tomato native policy, common24 opponent
set, paired seed/seat schedule, and authority-false contract unchanged.  The
fresh output root is the only write target.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts import run_resource_aware_b92_confirmation_v1 as _base


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-overlay-1097-1159-weighted-v1-20260814"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-night-confirmation384-v1-20260814"
NIGHT_CANDIDATE_ID = "surface-1152-to-1097"
NIGHT_MUTATION = {"removed_cards": [1152], "added_cards": [1097]}
CONFIRMATION_BASE_SEED = 22_730_000
WARMUP_BASE_SEED = 22_720_000
RECYCLE_GAMES = 64


def _configure() -> None:
    """Bind every mutable harness identity to this sealed Night lane."""

    _base.SCHEMA = "meta-specialist-resource-aware-tomato-night-confirmation384-v1"
    _base.SOURCE_ROOT = SOURCE_ROOT
    _base.SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
    _base.OUTPUT_DEFAULT = OUTPUT_DEFAULT
    _base.B92_CANDIDATE_ID = NIGHT_CANDIDATE_ID
    _base.B92_MUTATION = dict(NIGHT_MUTATION)
    _base.COMMON24_BASE_SEED = CONFIRMATION_BASE_SEED
    _base.WARMUP_BASE_SEED = WARMUP_BASE_SEED
    _base.CONFIRMATION_GAMES_PER_OPPONENT_SEAT = 8
    _base.RAMP_WORKERS = (1, 2, 4, 8, 12)

    # Keep the checked-in budget immutable while making this confirmation's
    # worker recycling explicit and reproducible.
    original_from_json = _base.ResourceBudget.from_json

    def from_json(path: str | Path):
        return replace(original_from_json(path), recycle_games=RECYCLE_GAMES)

    _base.ResourceBudget.from_json = staticmethod(from_json)

    # The shared harness summary body contains b92-specific prose.  Replace
    # only that presentation text before its atomic no-clobber write; all
    # machine-readable identity fields come from the bound constants above.
    original_write_text = _base._write_text_no_clobber

    def write_text(path: Path, text: str) -> str:
        if path.name == "confirmation_summary.md":
            text = text.replace("# b92a resource-aware 384 confirmation", "# Night Stretcher resource-aware 384 confirmation")
            text = text.replace(
                f"`{NIGHT_CANDIDATE_ID}` (1185→1159)",
                f"`{NIGHT_CANDIDATE_ID}` (1152→1097)",
            )
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

