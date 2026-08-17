"""Build a provenance-complete C4 Student model artifact for evaluation only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.student.artifact import build_artifact
from mage_ptcg.student.dataset import build_rule_bc_example, load_dataset


def _work_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _smoke_examples() -> list:
    """The existing C4 canonical fixture contract; never assert actual-data provenance."""
    def card(card_id: int) -> dict[str, object]:
        return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    def player(card_id: int) -> dict[str, object]:
        return {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card(card_id)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    def observation() -> dict[str, object]:
        return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}], "type": 0}, "step": 7}
    return [build_rule_bc_example(observation(), deck=[1] * 60, source_id=f"canonical-c4-fixture-{index}", source_revision="fixture") for index in range(12)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--canonical-base", required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--dataset-manifest-hash")
    parser.add_argument("--split-manifest-hash")
    parser.add_argument("--source-split-hash")
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--epochs", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.dataset is None:
            if any(value is not None for value in (args.dataset_manifest_hash, args.split_manifest_hash, args.source_split_hash, args.split_manifest)):
                raise ValueError("bundle provenance requires --dataset")
            examples, source_type, purpose = _smoke_examples(), "CANONICAL_C4_FIXTURE", "SMOKE_ONLY"
            provenance = {}
            split_assignments = None
            split_method = None
        else:
            examples, source_type, purpose = load_dataset(args.dataset), "PRIVACY_SAFE_DATASET", "ACTUAL_TRAINED"
            if args.split_manifest is None:
                raise ValueError("actual dataset artifact requires --split-manifest")
            split = json.loads(args.split_manifest.read_text(encoding="utf-8"))
            if not isinstance(split, dict) or not isinstance(split.get("assignments"), dict):
                raise ValueError("split manifest assignments are required")
            split_assignments = split["assignments"]
            split_method = str(split.get("split_method", split.get("method", "external_manifest")))
            provenance = {
                "dataset_manifest_hash": args.dataset_manifest_hash or "NONE",
                "split_manifest_hash": args.split_manifest_hash or "NONE",
                "source_split_hash": args.source_split_hash or "NONE",
            }
        result = build_artifact(examples=examples, output_dir=args.output_dir, canonical_base_sha=args.canonical_base, work_commit_sha=_work_commit(), dataset_source_type=source_type, artifact_purpose=purpose, epochs=args.epochs, split_assignments=split_assignments, split_method=split_method, **provenance)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Student actual artifact build failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
