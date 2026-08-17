"""Collect privacy-safe actual-cabt Rule v0 decisions into a rule-bc-v1 dataset.

The command reuses the existing actual-cabt environment, the Stable ActionKey
adapter, the actor-visible DecisionState projection, and Rule Agent v0.  It
never changes the submission agent, deck, Champion, or Promotion.  Private
rows and candidate bindings stay under a Git-ignored run directory; only
hashes, counts, schema, and privacy results are published.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mage_ptcg.dataops import DataOpsError, collect_actual_dataset  # noqa: E402


DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / ".local_artifacts" / "c4_runs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="path-safe run identifier")
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--canonical-base", required=True)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--validation-percent", type=int, default=20)
    parser.add_argument("--split-seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        summary = collect_actual_dataset(
            run_id=args.run_id,
            games=args.games,
            base_seed=args.base_seed,
            output_root=args.output_root,
            canonical_base_sha=args.canonical_base,
            deck_path=args.deck,
            repository_root=REPOSITORY_ROOT,
            max_steps=args.max_steps,
            validation_percent=args.validation_percent,
            split_seed=args.split_seed,
        )
    except (DataOpsError, ValueError, OSError) as exc:
        print(f"c4 dataset collection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
