"""Fail-closed C5 dataset, targeted distillation, League-lite, and gate CLI.

Exit codes: 0 success; 2 invalid/unsafe input (details quarantined); 3 a
required actual-cabt capability is unavailable.  All commands require an
explicit output directory and write deterministic JSON summaries atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for path in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mage_ptcg.distillation.actionkey_adapter import adapt_records
from mage_ptcg.distillation.contracts import DecisionDatasetError, atomic_write_json, atomic_write_records, build_record_from_rule_bc, digest, load_records, validate_records
from mage_ptcg.distillation.knowledge import load_curated_knowledge
from mage_ptcg.distillation.orchestration import SplitConfig, build_split_manifest, convert_to_rule_bc, model_provenance, records_for_split, validate_split_manifest
from mage_ptcg.distillation.registry import default_teacher_registry, require_teacher
from mage_ptcg.distillation.selection import SelectionConfig, select_targeted, selected_records
from mage_ptcg.evaluation.promotion import PromotionConfig, evaluate_promotion
from mage_ptcg.league import LeagueAgent, LeagueCapabilityUnavailable, LeaguePlan, run_actual_cabt
from mage_ptcg.student.dataset import load_dataset, write_dataset
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import StudentV0Model, train_model


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(output_dir: Path, command: str, value: object) -> None:
    atomic_write_json(output_dir / f"{command}-summary.json", value)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _command_build(args: argparse.Namespace) -> dict[str, object]:
    if args.actual_cabt:
        # C4 Rule BC v1 does not attest to an actual cabt episode source.  A
        # future actual-trace adapter must provide that provenance rather than
        # allowing a caller to relabel a fixture as actual evidence.
        raise DecisionDatasetError("actual cabt trace adapter is unavailable; C4 Rule BC input is offline/synthetic only")
    examples = load_dataset(args.input)
    records = [build_record_from_rule_bc(item, source_kind="c4-rule-bc", synthetic=True, environment_version=args.environment_version, agent_config_hash=args.agent_config_hash) for item in examples]
    output = args.output_dir / "datasets" / "canonical-decision.jsonl"
    atomic_write_records(output, records)
    knowledge_summary: dict[str, object] | None = None
    if args.curated_knowledge_dir is not None:
        # The ActionKey adapter maps each teacher rule onto the persisted public
        # ActionKey candidates and fails closed on anything ambiguous,
        # unsupported, or private.  C4 examples carry no attested rule binding,
        # so applied stays honestly 0 while every skip reason is recorded.
        knowledge = load_curated_knowledge(args.curated_knowledge_dir)
        adapter_manifest = adapt_records(records, knowledge)
        knowledge_summary = {
            "mode": "offline-actionkey-adapter",
            "teacher_registry_only": True,
            "load_metrics": dict(sorted(knowledge.load_metrics.items())),
            "input_hash": digest(sorted(item["content_hash"] for item in records), domain="adapter-input"),
            **adapter_manifest,
        }
    return {"status": "OK", "dataset": str(output), **validate_records(records), "curated_knowledge": knowledge_summary}


def _command_validate(args: argparse.Namespace) -> dict[str, object]:
    records = load_records(args.input)
    selected = records
    if args.selection:
        selected = selected_records(records, _read_json(args.selection))
    split_summary: dict[str, int] | None = None
    if args.split_manifest:
        split_manifest = _read_json(args.split_manifest)
        if not args.selection and isinstance(split_manifest, dict) and isinstance(split_manifest.get("assignments"), dict):
            record_ids = {str(record["record_id"]) for record in records}
            if set(split_manifest["assignments"]) != record_ids:
                raise DecisionDatasetError("split manifest covers a selected subset; specify --selection to reconstruct that subset")
        split_summary = validate_split_manifest(selected, split_manifest)
    return {"status": "OK", **validate_records(selected), "selection_records": len(selected) if args.selection else None, "split": split_summary}


def _command_select(args: argparse.Namespace) -> dict[str, object]:
    records = load_records(args.input)
    manifest = select_targeted(records, SelectionConfig(args.limit, args.max_per_episode, args.max_per_near_duplicate))
    output = args.output_dir / "selections" / "targeted-selection.json"
    atomic_write_json(output, manifest)
    return {"status": "OK", "selection": str(output), "selected_records": len(manifest["selected"]), "synthetic_records": sum(bool(item["source"]["synthetic"]) for item in records)}


def _command_convert(args: argparse.Namespace) -> dict[str, object]:
    records = load_records(args.input)
    if args.selection:
        records = selected_records(records, _read_json(args.selection))
    split = build_split_manifest(records, SplitConfig(args.validation_percent, args.test_percent, args.seed))
    target = records_for_split(records, split, "train")
    examples = convert_to_rule_bc(target)
    output = args.output_dir / "datasets" / "targeted-rule-bc.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_dataset(output, examples)
    split_path = args.output_dir / "datasets" / "split-manifest.json"
    atomic_write_json(split_path, split)
    return {"status": "OK", "dataset": str(output), "split_manifest": str(split_path), "train_examples": len(examples), "split_counts": split["counts"]}


def _command_train(args: argparse.Namespace) -> dict[str, object]:
    examples = load_dataset(args.dataset)
    model = train_model(examples, epochs=args.epochs, learning_rate=args.learning_rate)
    output = args.output_dir / "models" / "targeted-student.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    model.export(output)
    model_hash = _hash_file(output)
    provenance = {
        "schema_version": "c5-model-artifact-provenance-v1", "model_hash": model_hash,
        "input_dataset_hash": _hash_file(args.dataset), "training_config": {"epochs": args.epochs, "learning_rate": args.learning_rate},
        "source_classification": "offline-rule-bc; actual-cabt-status-not-attested",
    }
    provenance_path = args.output_dir / "models" / "targeted-student-provenance.json"
    atomic_write_json(provenance_path, provenance)
    return {"status": "FIXTURE_OR_OFFLINE_ONLY", "model": str(output), "model_hash": model_hash, "provenance": str(provenance_path), "examples": len(examples), "epochs": args.epochs, "learning_rate": args.learning_rate}


def _command_evaluate(args: argparse.Namespace) -> dict[str, object]:
    result = evaluate_model(StudentV0Model.load(args.model), load_dataset(args.dataset), repeats=args.repeats)
    result.update({"status": "OFFLINE_FIDELITY_ONLY", "model_hash": _hash_file(args.model)})
    return result


def _command_registry(args: argparse.Namespace) -> dict[str, object]:
    capabilities = args.capability or []
    entry = require_teacher(args.teacher_id, capabilities, registry=default_teacher_registry(args.revision))
    return {"status": "AVAILABLE", "teacher": entry.to_dict()}


def _command_league(args: argparse.Namespace) -> dict[str, object]:
    raw = _read_json(args.plan)
    if not isinstance(raw, dict):
        raise ValueError("league plan must be an object")
    agents = tuple(LeagueAgent(**item) for item in raw["agents"])
    plan = LeaguePlan(raw["champion_id"], agents, tuple(raw["seeds"]), raw["deck_fingerprint"], raw["config_hash"], raw["timeout_ms"], raw["environment_version"])
    run_actual_cabt(plan, output_path=str(args.output_dir / "runs" / "league-run.json"))
    raise AssertionError("unreachable")


def _command_gate(args: argparse.Namespace) -> dict[str, object]:
    report = _read_json(args.report)
    return evaluate_promotion(report, PromotionConfig(args.minimum_games, args.latency_budget_ms))


def _command_report(args: argparse.Namespace) -> dict[str, object]:
    records = load_records(args.dataset)
    return {"schema_version": "c5-evidence-report-v1", "dataset": str(args.dataset), "dataset_hash": digest(sorted(item["content_hash"] for item in records), domain="evidence"), **validate_records(records), "actual_cabt_data_collected": any(not item["source"]["synthetic"] for item in records), "actual_league_evaluation": "NOT_DONE", "promotion_decision": "NO_DECISION"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="explicit C5 artifact directory")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--input", type=Path, required=True)
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true")
    source.add_argument("--actual-cabt", action="store_true", help="rejected until an attested trace adapter exists")
    build.add_argument("--environment-version", required=True)
    build.add_argument("--agent-config-hash", required=True)
    build.add_argument("--curated-knowledge-dir", type=Path, help="optional curated pack for offline provenance only")
    validate = commands.add_parser("validate")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--selection", type=Path, help="selection manifest used to deterministically reconstruct a subset")
    validate.add_argument("--split-manifest", type=Path)
    select = commands.add_parser("select")
    select.add_argument("--input", type=Path, required=True)
    select.add_argument("--limit", type=int, required=True)
    select.add_argument("--max-per-episode", type=int, default=2)
    select.add_argument("--max-per-near-duplicate", type=int, default=1)
    convert = commands.add_parser("convert")
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--selection", type=Path)
    convert.add_argument("--validation-percent", type=int, default=20)
    convert.add_argument("--test-percent", type=int, default=20)
    convert.add_argument("--seed", default="c5-default-seed")
    train = commands.add_parser("train")
    train.add_argument("--dataset", type=Path, required=True)
    train.add_argument("--epochs", type=int, default=120)
    train.add_argument("--learning-rate", type=float, default=0.15)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--model", type=Path, required=True)
    evaluate.add_argument("--repeats", type=int, default=20)
    registry = commands.add_parser("registry")
    registry.add_argument("--teacher-id", required=True)
    registry.add_argument("--revision", default="unknown")
    registry.add_argument("--capability", action="append")
    league = commands.add_parser("league")
    league.add_argument("--plan", type=Path, required=True)
    gate = commands.add_parser("gate")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--minimum-games", type=int, required=True)
    gate.add_argument("--latency-budget-ms", type=float, required=True)
    report = commands.add_parser("report")
    report.add_argument("--dataset", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    handlers = {"build": _command_build, "validate": _command_validate, "select": _command_select, "convert": _command_convert, "train": _command_train, "evaluate": _command_evaluate, "registry": _command_registry, "league": _command_league, "gate": _command_gate, "report": _command_report}
    try:
        value = handlers[args.command](args)
        _summary(args.output_dir, args.command, value)
    except LeagueCapabilityUnavailable as exc:
        value = {"status": "CAPABILITY_UNAVAILABLE", "reason": str(exc)}
        _summary(args.output_dir, args.command, value)
        return 3
    except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError, DecisionDatasetError) as exc:
        atomic_write_json(args.output_dir / "quarantine" / f"{args.command}-error.json", {"status": "INVALID_OR_UNSAFE_INPUT", "error": str(exc)})
        print(f"C5 {args.command} failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
