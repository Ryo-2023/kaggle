"""Run full-BC v3 on a bounded teacher-record slice."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.bc_trainer_v3 import (  # noqa: E402
    load_bc_examples_from_teacher_records_v3,
    split_episode_groups_v3,
    train_bc_v3,
)
from mage_ptcg.meta_specialist.neural_model_v3 import SpecialistModelV3  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teacher-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    examples = load_bc_examples_from_teacher_records_v3(args.teacher_root, limit=args.limit)
    train, valid = split_episode_groups_v3(examples, validation_fraction=0.2)
    model = SpecialistModelV3(card_vocabulary_size=4096, hidden_dim=32, embedding_dim=16, seed=args.seed)
    result = train_bc_v3(model, train, valid, epochs=args.epochs, learning_rate=1e-3)
    checkpoint_hash = hashlib.sha256(b"".join(value.numpy().tobytes() for key, value in sorted(result.checkpoint_state.items()))).hexdigest()
    report = {
        "schema": "meta-specialist-bc-v3", "seed": args.seed, "source": str(args.teacher_root),
        "examples": len(examples), "train_examples": len(train), "validation_examples": len(valid),
        "best_epoch": result.best_epoch, "best_validation_nll": result.best_validation_nll,
        "history": list(result.train_history), "checkpoint_sha256": checkpoint_hash,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
