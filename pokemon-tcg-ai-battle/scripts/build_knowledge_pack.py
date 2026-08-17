"""Build the reproducible C2a Team Deck Knowledge Pack v0 sample."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mage_ptcg.knowledge import build_team_deck_pack, load_pack, write_pack  # noqa: E402


def main() -> int:
    """Build, reload, and report one verified canonical snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "artifacts/knowledge/team_deck_v0.json"
    )
    args = parser.parse_args()
    pack = build_team_deck_pack(args.deck)
    write_pack(pack, args.output)
    verified = load_pack(args.output)
    print(f"pack_id={verified.manifest.pack_id}")
    print(f"content_hash={verified.manifest.content_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
