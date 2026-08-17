"""Evaluate a Student v0 model on a held-out Rule BC dataset."""

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

from mage_ptcg.student.dataset import DatasetValidationError, load_dataset, split_examples_from_assignments
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import ModelValidationError, StudentV0Model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--partition", choices=("train", "validation"))
    args = parser.parse_args(argv)
    try:
        examples = load_dataset(args.dataset)
        if (args.split_manifest is None) != (args.partition is None):
            raise DatasetValidationError("--split-manifest and --partition must be used together")
        if args.split_manifest is not None:
            split = json.loads(args.split_manifest.read_text(encoding="utf-8"))
            if not isinstance(split, dict):
                raise DatasetValidationError("split manifest must be an object")
            train, validation = split_examples_from_assignments(examples, split.get("assignments"))
            examples = train if args.partition == "train" else validation
        result = evaluate_model(StudentV0Model.load(args.model), examples, repeats=args.repeats)
        result["model_bytes"] = args.model.stat().st_size
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, DatasetValidationError, ModelValidationError) as exc:
        print(f"Student evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
