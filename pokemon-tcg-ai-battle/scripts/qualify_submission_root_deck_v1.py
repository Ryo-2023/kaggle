#!/usr/bin/env python3
"""Run one real CABT game and seal the repository root submission deck."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import (
    build_submission_deck_qualification_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--deck", default="deck.csv")
    parser.add_argument(
        "--output",
        default=(
            "runs/final-sprint-autonomous/submission-root-deck-qualification-v1/"
            "qualification.json"
        ),
    )
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--max-steps", type=int, default=4000)
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = build_submission_deck_qualification_v1(
        repo_root=root,
        deck_path=args.deck,
        output_path=args.output,
        source_commit=source_commit,
        seed=args.seed,
        max_steps=args.max_steps,
    )
    output = (root / args.output).resolve()
    print(
        json.dumps(
            {
                "output": str(output),
                "file_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "qualification_sha256": payload["qualification_sha256"],
                "deck_sha256": payload["qualified_deck_asset"]["deck_file_sha256"],
                "cabt_evidence_sha256": payload["cabt_evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
