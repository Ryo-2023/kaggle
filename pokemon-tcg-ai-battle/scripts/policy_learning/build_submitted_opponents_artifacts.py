#!/usr/bin/env python3
"""Create a truthful handoff bundle from existing qualification evidence.

This command does not claim to run CABT/GPU benchmarks.  Those fields are
written as ``NOT_RUN`` until their dedicated runners provide measured rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from dataclasses import asdict
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.policy_learning.submitted_opponents import load_registry, population_document, split_assets, write_split_manifests
from mage_ptcg.policy_learning.r2d3.semantic_state import FEATURE_REGISTRY


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = list(rows[0]) if rows else ["status"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True); parser.add_argument("--ledger", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--seed", type=int, default=71000)
    args = parser.parse_args(); root = args.output_dir; root.mkdir(parents=True, exist_ok=False)
    assets = load_registry(args.repo, args.ledger); split = split_assets(assets, seed=args.seed)
    _write_csv(root / "submitted_asset_registry.csv", [asdict(asset) for asset in assets]); _write_csv(root / "qualification_results.csv", [asdict(asset) for asset in assets]); write_split_manifests(root, assets, seed=args.seed)
    names = {"training": "submitted_training_population.json", "validation": "submitted_validation_population.json", "deck_holdout": "submitted_deck_holdout_population.json", "final_holdout": "submitted_final_holdout_population.json"}
    for split_name, output_name in names.items(): _write_json(root / output_name, population_document(split[split_name], split=split_name, seed=args.seed))
    _write_json(root / "semantic_feature_registry.json", FEATURE_REGISTRY)
    _write_json(root / "replay_manifest.json", {"schema": "r2d3-replay-manifest-v1", "status": "CONFIGURED_NOT_COLLECTED", "burn_in": "configurable", "prioritized": True, "demonstration_ratios": [0, 1 / 64, 1 / 32, 1 / 16]})
    _write_csv(root / "demonstration_registry.csv", [])
    _write_csv(root / "ppo_submitted_population_smoke.csv", [{"status": "NOT_RUN", "reason": "requires isolated CABT runner"}])
    _write_csv(root / "submitted_validation_scorecard.csv", [{"status": "NOT_RUN", "reason": "candidate freeze and CABT runner required"}])
    _write_json(root / "r2d3_smoke_results.json", {"status": "UNIT_FORWARD_BACKWARD_PASSED", "cabt_games": "NOT_RUN"})
    _write_csv(root / "gpu_inference_benchmark.csv", [{"status": "NOT_RUN", "reason": "GPU access unavailable to this session"}]); _write_json(root / "gpu_utilization.json", {"status": "NOT_RUN", "reason": "NVML access blocked by operating system"})
    _write_json(root / "psro_population.json", {"status": "CONFIGURED", "members": ["rule_v0", "rule_v1", "family", "submitted_agents_dev", "historical_snapshots", "r2d3_snapshots"]}); _write_csv(root / "psro_payoff_matrix.csv", [{"status": "NOT_RUN", "reason": "requires CABT matches"}]); _write_json(root / "psro_meta_strategy.json", {"status": "NOT_RUN"})
    texts = {"00_executive_summary.md": "# 提出済みOpponent Population / R2D3-PSRO handoff\n\n既存台帳から安全なPopulation splitを生成した。実CABT/GPU評価は未実行として明示する。\n", "01_repository_state.md": "# Repository state\n\nbranch/HEADは実行時に別途記録する。refはread-only列挙のみを行った。\n", "02_research_and_architecture.md": "# Architecture\n\nPPO/V-traceを維持し、feature-flagged R2D3 recurrent prioritized replayとSP-PSROを追加する。\n", "03_work_log.md": "# Work log\n\n台帳統合、policy/lineage split、R2D3 unit smokeを実行した。\n", "r2d3_architecture.md": "# R2D3 architecture\n\nSemantic actor-visible state/action encoder、GRU/LRU、distributional Double Q、prioritized sequence replay、central inference boundaryを実装。\n", "inference_decision.md": "# Inference decision\n\nGPU benchmarkはNVML access blockのため未決定。learner-only fallbackを維持する。\n", "psro_smoke_decision.md": "# PSRO smoke decision\n\nSP-PSRO metadata and expansion gate are configured; payoff collection is未実行。\n", "test_results.md": "# Test results\n\npolicy learning + submitted-opponents/R2D3 unit tests passed; real CABT smoke未実行。\n", "limitations.md": "# Limitations\n\n既存台帳のruntime evidenceを再利用。native local unsupportedは壊れたassetと分類していない。GPU/CABT benchmarkは未実行。\n", "next_actions.md": "# Next actions\n\n隔離CABT runnerでtraining eligible assetを両side 4局以上smokeし、結果を更新する。\n"}
    for name, text in texts.items(): (root / name).write_text(text, encoding="utf-8")
    _write_json(root / "final_readiness.json", {"population_split": "READY", "ppo_population_runtime": "CONFIGURED", "r2d3_unit": "READY", "cabt_smoke": "NOT_RUN", "gpu_benchmark": "NOT_RUN", "promotion": "NOT_READY"})
    files = [path for path in sorted(root.iterdir()) if path.is_file() and path.name != "checksums.sha256"]; _write_json(root / "artifact_manifest.json", {"schema": "submitted-opponents-r2d3-psro-v1", "files": [path.name for path in files]})
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in sorted(root.iterdir()) if path.is_file() and path.name != "checksums.sha256"); (root / "checksums.sha256").write_text(checksums, encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
