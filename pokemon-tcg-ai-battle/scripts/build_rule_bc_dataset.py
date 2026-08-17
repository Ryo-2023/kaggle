"""Convert local cabt observation JSONL into privacy-bounded Rule v0 BC JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from main import read_deck_csv
from mage_ptcg.student.dataset import DatasetValidationError, build_rule_bc_example, write_dataset


def _revision() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL records containing observation and source_id")
    parser.add_argument("--output", type=Path, required=True, help="new output JSONL path")
    parser.add_argument("--deck", type=Path, default=Path("deck.csv"))
    args = parser.parse_args(argv)
    try:
        deck = read_deck_csv(args.deck)
        examples = []
        with args.input.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("source_id"), str):
                    raise DatasetValidationError(f"line {line_number} needs object fields observation and source_id")
                history = tuple(record.get("visible_history", ()))
                examples.append(build_rule_bc_example(record.get("observation"), deck=deck, source_id=record["source_id"], source_revision=_revision(), visible_history=history))
        print(json.dumps({"examples": write_dataset(args.output, examples), "output": str(args.output)}, ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, DatasetValidationError) as exc:
        print(f"dataset build failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
