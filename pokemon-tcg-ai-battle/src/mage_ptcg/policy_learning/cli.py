"""Command line entry points for candidate-only policy-learning artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .dagger import aggregate_records, select_queries
from .league import PSROState, PopulationMember
from .model import ActorCriticConfig
from .training import _device, _atomic_json, evaluate, family_vocabulary, load_model, train_offline
from .data import load_examples


def _write_jsonl(path: Path, values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-policy-learning")
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train-offline")
    train.add_argument("--dataset", type=Path, required=True); train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument("--device", default="cpu"); train.add_argument("--epochs", type=int, default=20); train.add_argument("--batch-size", type=int, default=256); train.add_argument("--workers", type=int, default=0)
    train.add_argument("--hidden-size", type=int, default=128); train.add_argument("--recurrent-size", type=int, default=128); train.add_argument("--blocks", type=int, default=2); train.add_argument("--dropout", type=float, default=.05)
    train.add_argument("--learning-rate", type=float, default=3e-4); train.add_argument("--awr-beta", type=float, default=1.0); train.add_argument("--seed", type=int, default=71000); train.add_argument("--resume", action="store_true"); train.add_argument("--initialize-from", type=Path)
    train.add_argument("--objective", choices=("bc", "awr"), default="awr")
    train.add_argument("--no-recurrence", action="store_true"); train.add_argument("--rule-proposal-input", action="store_true")
    train.add_argument("--value-weight", type=float, default=None); train.add_argument("--family-weight", type=float, default=None)
    train.add_argument("--progress", action="store_true"); train.add_argument("--progress-interval-seconds", type=float, default=None)
    evaluate_p = sub.add_parser("evaluate")
    evaluate_p.add_argument("--dataset", type=Path, required=True); evaluate_p.add_argument("--model-dir", type=Path, required=True); evaluate_p.add_argument("--split", default="test", choices=("validation", "test", "opponent_holdout", "deck_holdout", "teacher_policy_holdout")); evaluate_p.add_argument("--device", default="cpu"); evaluate_p.add_argument("--batch-size", type=int, default=256); evaluate_p.add_argument("--output", type=Path, required=True)
    dagger = sub.add_parser("dagger-select")
    dagger.add_argument("--rollout-records", type=Path, required=True); dagger.add_argument("--budget", type=int, required=True); dagger.add_argument("--output", type=Path, required=True)
    merge = sub.add_parser("dagger-merge")
    merge.add_argument("--base", type=Path, required=True); merge.add_argument("--relabeled", type=Path, required=True); merge.add_argument("--output", type=Path, required=True)
    psro = sub.add_parser("psro")
    psro.add_argument("--members", type=Path, required=True); psro.add_argument("--payoffs", type=Path, required=True); psro.add_argument("--output", type=Path, required=True); psro.add_argument("--samples", type=int, default=0); psro.add_argument("--seed", type=int, default=71000)
    args = parser.parse_args(argv)
    try:
        if args.command == "train-offline":
            config = ActorCriticConfig(hidden_size=args.hidden_size, recurrent_size=args.recurrent_size, blocks=args.blocks, dropout=args.dropout,
                                       use_recurrence=not args.no_recurrence, use_rule_proposal=args.rule_proposal_input)
            # BC is intentionally a policy-only baseline unless auxiliary
            # losses are explicitly requested, so it is comparable to AWR.
            value_weight = args.value_weight if args.value_weight is not None else (0.0 if args.objective == "bc" else .5)
            family_weight = args.family_weight if args.family_weight is not None else (0.0 if args.objective == "bc" else .1)
            result = train_offline(dataset=args.dataset, output_dir=args.output_dir, device_name=args.device, epochs=args.epochs, batch_size=args.batch_size, workers=args.workers, config=config, learning_rate=args.learning_rate, objective=args.objective, awr_beta=args.awr_beta, value_weight=value_weight, family_weight=family_weight, seed=args.seed, resume=args.resume, progress=args.progress, progress_interval_seconds=args.progress_interval_seconds, initialize_from=args.initialize_from)
        elif args.command == "evaluate":
            model, summary, families = load_model(args.model_dir, device_name=args.device)
            values = load_examples(args.dataset, splits=(args.split,))
            result = {"schema": summary["schema"], "split": args.split, **evaluate(model, values, families=families, device=_device(args.device), batch_size=args.batch_size)}
            _atomic_json(args.output, result)
        elif args.command == "dagger-select":
            records = [json.loads(line) for line in args.rollout_records.read_text(encoding="utf-8").splitlines() if line.strip()]
            result = [asdict(query) for query in select_queries(records, budget=args.budget)]
            _write_jsonl(args.output, result)
        elif args.command == "dagger-merge":
            result = aggregate_records(base=args.base, relabeled=args.relabeled, output=args.output)
        else:
            members = json.loads(args.members.read_text(encoding="utf-8")); payoffs = json.loads(args.payoffs.read_text(encoding="utf-8"))
            state = PSROState()
            for index, row in enumerate(members):
                state.add_member(PopulationMember(**row), against_existing=payoffs[index][:index])
            result = {"meta_strategy": state.meta_strategy(), "opponent_samples": state.sample_opponents(count=args.samples, seed=args.seed) if args.samples else []}
            _atomic_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False)); return 2
