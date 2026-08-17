"""CLI for the read-only submitted-opponent registry."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path

from .submitted_opponents import load_registry, materialize_runtime_population, registry_document, write_split_manifests


def _write_csv(path: Path, assets: object) -> None:
    rows = [asdict(asset) for asset in assets]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["asset_id"])
        writer.writeheader(); writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.policy_learning submitted-opponents")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "qualify", "split", "materialize"):
        command = sub.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path.cwd())
        command.add_argument("--ledger", type=Path, required=True)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--seed", type=int, default=71000)
        if name == "materialize":
            command.add_argument("--runtime-population", type=Path, required=True)
            command.add_argument("--split", choices=("training", "validation", "deck_holdout", "final_holdout"), required=True)
    args = parser.parse_args(argv)
    try:
        assets = load_registry(args.repo, args.ledger)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(args.output_dir / "submitted_asset_registry.csv", assets)
        (args.output_dir / "submitted_asset_registry.json").write_text(json.dumps(registry_document(assets), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.command == "split":
            manifests = write_split_manifests(args.output_dir, assets, seed=args.seed)
            print(json.dumps({name: str(path) for name, path in manifests.items()}, ensure_ascii=False, sort_keys=True))
        elif args.command == "materialize":
            runtime = materialize_runtime_population(source_population=args.runtime_population, assets=assets, split=args.split, seed=args.seed)
            path = args.output_dir / f"runtime-population-submitted-{args.split.replace('_', '-')}-v1.json"
            path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"population": str(path), "population_hash": runtime["semantic_population_digest"]}, ensure_ascii=False, sort_keys=True))
        else:
            # Qualification is ledger-backed here.  It must not mislabel a
            # previously official-valid native asset as broken merely because
            # this host lacks its runtime.
            _write_csv(args.output_dir / "qualification_results.csv", assets)
            print(json.dumps({"assets": len(assets), "registry_hash": registry_document(assets)["registry_hash"]}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False)); return 2
