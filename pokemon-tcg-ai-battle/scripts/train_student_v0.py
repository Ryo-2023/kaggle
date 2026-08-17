"""Train and export the deterministic, lightweight Student v0 candidate scorer."""

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

from mage_ptcg.student.dataset import DatasetValidationError, load_dataset, split_examples, split_examples_from_assignments
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import ModelValidationError, train_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new JSON model path")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--learning-rate", type=float, default=0.15)
    parser.add_argument("--validation-percent", type=int, default=20)
    parser.add_argument("--split-manifest", type=Path, help="attested episode-group split; disables internal splitting")
    args = parser.parse_args(argv)
    try:
        examples = load_dataset(args.dataset)
        if args.split_manifest is None:
            train, validation = split_examples(examples, validation_percent=args.validation_percent)
            split_method = "internal_source_id_sha256_modulo_percent"
        else:
            split = json.loads(args.split_manifest.read_text(encoding="utf-8"))
            if not isinstance(split, dict):
                raise DatasetValidationError("split manifest must be an object")
            train, validation = split_examples_from_assignments(examples, split.get("assignments"))
            split_method = str(split.get("split_method", split.get("method", "external_manifest")))
        model = train_model(train, epochs=args.epochs, learning_rate=args.learning_rate)
        model.export(args.output)
        print(json.dumps({"train_examples": len(train), "validation_examples": len(validation), "split_method": split_method, "validation": evaluate_model(model, validation), "model_bytes": args.output.stat().st_size}, ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, DatasetValidationError, ModelValidationError) as exc:
        print(f"Student training failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
