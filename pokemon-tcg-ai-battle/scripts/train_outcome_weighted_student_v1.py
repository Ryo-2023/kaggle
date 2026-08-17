"""Train a submission-compatible Student v0 from self-owned WDL weights."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mage_ptcg.student.dataset import load_dataset, split_examples  # noqa: E402
from mage_ptcg.student.evaluation import evaluate_model  # noqa: E402
from mage_ptcg.student.model import ModelValidationError, train_model  # noqa: E402
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection  # noqa: E402


SCHEMA_V1 = "student-self-owned-outcome-weighted-training-v1"


def _partition_student_examples(examples: list[object]) -> tuple[list[object], dict[str, str]]:
    """Separate labels this candidate-wise Student can represent.

    Ordered Skill selections require pointer-head semantics that Student v0
    intentionally does not claim.  They may be excluded only through the
    explicit CLI flag; unknown schemas remain hard errors.
    """
    kept: list[object] = []
    excluded: dict[str, str] = {}
    for example in examples:
        try:
            ordered = is_ordered_selection(
                getattr(example, "selection_type"),
                getattr(example, "selection_context"),
            )
        except (AttributeError, ValueError) as exc:
            raise ModelValidationError("dataset has an unknown CABT selection schema") from exc
        if ordered:
            excluded[str(getattr(example, "example_id"))] = "ordered_selection_not_representable"
        else:
            kept.append(example)
    return kept, excluded


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train_bundle_v1(
    *,
    collection_root: Path,
    output_root: Path,
    epochs: int = 80,
    learning_rate: float = 0.08,
    win_weight: float = 1.5,
    draw_weight: float = 1.0,
    loss_weight: float = 0.5,
    exclude_unsupported: bool = False,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    manifest_path = collection_root / "manifest.json"
    dataset_path = collection_root / "rule_bc_outcome_weighted.jsonl"
    weights_path = collection_root / "outcome_weights.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "READY_FOR_WEIGHTED_TRAINING" or manifest.get("research_only") is not True:
        raise ValueError("collection manifest is not ready for self-owned weighted training")
    examples = load_dataset(dataset_path)
    trainable_examples, excluded_examples = _partition_student_examples(examples)
    if excluded_examples and not exclude_unsupported:
        raise ModelValidationError(
            "ordered Skill labels are present; pass --exclude-unsupported to train the non-ordered subset"
        )
    if not trainable_examples:
        raise ModelValidationError("dataset has no non-ordered Student examples")
    raw_weights = json.loads(weights_path.read_text(encoding="utf-8"))
    if not isinstance(raw_weights, dict):
        raise ValueError("outcome_weights.json must be an object")
    base_weights = {str(key): float(value) for key, value in raw_weights.items()}
    if set(base_weights) != {example.example_id for example in examples}:
        raise ValueError("outcome weights do not cover the dataset")
    if any(type(value) not in (int, float) or value <= 0.0 for value in (win_weight, draw_weight, loss_weight)):
        raise ValueError("override weights must be positive")
    episodes = json.loads((collection_root / "episodes.json").read_text(encoding="utf-8"))
    if not isinstance(episodes, list):
        raise ValueError("episodes.json must be a list")
    weights: dict[str, float] = {}
    for episode in episodes:
        winner = episode.get("winner")
        seat = episode.get("subject_seat")
        if winner == 2:
            episode_weight = float(draw_weight)
        elif winner == seat:
            episode_weight = float(win_weight)
        else:
            episode_weight = float(loss_weight)
        for example_id in episode.get("example_ids", []):
            if example_id in weights:
                raise ValueError("episodes assign an example more than once")
            weights[str(example_id)] = episode_weight
    if set(weights) != set(base_weights):
        raise ValueError("episodes and outcome weights do not cover the same examples")
    train, validation = split_examples(trainable_examples, validation_percent=20)
    train_weights = {example.example_id: weights[example.example_id] for example in train}
    model = train_model(
        train,
        epochs=epochs,
        learning_rate=learning_rate,
        example_weights=train_weights,
    )
    output_root.mkdir(parents=True)
    model_path = output_root / "student-v0-outcome-weighted.json"
    model.export(model_path)
    validation_result = evaluate_model(model, validation, repeats=1)
    training_manifest = {
        "schema_version": SCHEMA_V1,
        "research_only": True,
        "authority": {"training": False, "behavior": False, "submission": False, "promotion": False},
        "collection_manifest_path": str(manifest_path.resolve()),
        "collection_manifest_sha256": _sha256(manifest_path),
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": _sha256(dataset_path),
        "weights_path": str(weights_path.resolve()),
        "weights_sha256": _sha256(weights_path),
        "model_path": str(model_path.resolve()),
        "model_sha256": _sha256(model_path),
        "examples": len(examples),
        "trainable_examples": len(trainable_examples),
        "excluded_examples": len(excluded_examples),
        "excluded_by_reason": {
            reason: sum(value == reason for value in excluded_examples.values())
            for reason in sorted(set(excluded_examples.values()))
        },
        "unsupported_examples_policy": "exclude_ordered_skill" if exclude_unsupported else "reject",
        "train_examples": len(train),
        "validation_examples": len(validation),
        "epochs": epochs,
        "learning_rate": learning_rate,
        "weight_rule": {"win": win_weight, "draw": draw_weight, "loss": loss_weight},
        "validation": validation_result,
        "status": "READY_FOR_LOCAL_EVALUATION",
    }
    (output_root / "training_manifest.json").write_text(
        json.dumps(training_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return training_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--learning-rate", type=float, default=0.08)
    parser.add_argument("--win-weight", type=float, default=1.5)
    parser.add_argument("--draw-weight", type=float, default=1.0)
    parser.add_argument("--loss-weight", type=float, default=0.5)
    parser.add_argument(
        "--exclude-unsupported",
        action="store_true",
        help="explicitly exclude ordered Skill labels that Student v0 cannot represent",
    )
    args = parser.parse_args(argv)
    try:
        result = train_bundle_v1(
            collection_root=args.collection_root,
            output_root=args.output,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            win_weight=args.win_weight,
            draw_weight=args.draw_weight,
            loss_weight=args.loss_weight,
            exclude_unsupported=args.exclude_unsupported,
        )
    except (OSError, ValueError, ModelValidationError) as exc:
        print(f"weighted Student training failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["train_bundle_v1"]
