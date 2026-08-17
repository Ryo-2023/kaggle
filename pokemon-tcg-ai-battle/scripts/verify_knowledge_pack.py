"""Verify canonical integrity and runtime compatibility of a C2a Knowledge Pack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mage_ptcg.knowledge import (  # noqa: E402
    check_compatibility,
    load_pack,
    read_deck_card_ids,
    runtime_compatibility_for_deck,
)


def main() -> int:
    """Load, integrity-check, and compatibility-check a pack, returning nonzero on failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument(
        "--pack", type=Path, default=REPOSITORY_ROOT / "artifacts/knowledge/team_deck_v0.json"
    )
    args = parser.parse_args()
    try:
        pack = load_pack(args.pack)
        report = check_compatibility(
            pack,
            runtime_compatibility_for_deck(read_deck_card_ids(args.deck)),
        )
    except ValueError as exc:
        print(f"Knowledge Pack verification failed: {exc}", file=sys.stderr)
        return 1
    if not report.compatible:
        print("Knowledge Pack is incompatible:", file=sys.stderr)
        for reason in report.reasons:
            print(f"- {reason}", file=sys.stderr)
        return 1
    print(f"verified pack_id={pack.manifest.pack_id}")
    print(f"content_hash={pack.manifest.content_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
