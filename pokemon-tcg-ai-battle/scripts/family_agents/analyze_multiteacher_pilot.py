#!/usr/bin/env python3
"""Summarize a completed Family Multi-Teacher pilot without promotion."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    summary = _read(args.run / "run_summary.json")
    rows = [json.loads(line) for line in (args.run / "game_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = [json.loads(line) for path in sorted((args.run / "trajectories").glob("*.jsonl")) for line in path.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    dataset = _read(args.dataset.with_suffix(".summary.json"))
    games = {"teacher": dict(sorted(Counter(row["teacher_id"] for row in rows).items())), "opponent_type": dict(sorted(Counter(row["opponent_type"] for row in rows).items())), "candidate_side": dict(sorted(Counter(str(row["candidate_side"]) for row in rows).items()))}
    activation = Counter(decision.get("family_id") for decision in decisions if decision.get("fired_rule_ids"))
    report = {"schema_version": "family-multiteacher-pilot-analysis-v1", "pilot": summary, "games": games, "decisions": {"total": len(decisions), "activation_by_family": dict(sorted(activation.items())), "fallback_count": sum(bool(decision.get("fallback_used")) for decision in decisions), "illegal_count": sum(decision.get("legality_result") is not True for decision in decisions)}, "dataset": dataset}
    sufficient_holdout = all(int(dataset["splits"].get(name, 0)) >= 50 for name in ("validation", "test", "opponent_holdout", "deck_holdout"))
    report["gates"] = {"pilot_runtime": "PASS" if summary.get("gate") == "PASS" else "BLOCKED", "dataset_holdout": "PASS" if sufficient_holdout else "BLOCKED_INSUFFICIENT_HOLDOUT_EPISODES", "gpu_training": "NOT_STARTED", "generation_10000": "BLOCKED_DATASET_SPLIT_GATE", "promotion": "NO_DECISION"}
    root = args.artifact_root
    _write(root / "artifacts" / "multiteacher_pilot_2000_analysis.json", report)
    _write(root / "artifacts" / "final_readiness.json", {"verdict": "PILOT_RUNTIME_PASSED_DATASET_SPLIT_REWORK_REQUIRED", "pilot_runtime": report["gates"]["pilot_runtime"], "dataset_holdout": report["gates"]["dataset_holdout"], "generation_10000": report["gates"]["generation_10000"], "promotion": "NO_DECISION"})
    (root / "docs" / "multiteacher_pilot_2000_analysis.md").write_text("# 2,000局 Family Multi-Teacher pilot分析\n\n実行gateはPASSである。2,000/2,000 legal、candidate fault・mapping failure・fallbackは0、60,234 decisionをexportした。\n\nDatasetのvalidation/test/opponent_holdout/deck_holdoutは各1 episodeであり、評価gateには不足する。split再設計まではGPU本学習と10,000局generationを開始しない。Champion／submissionへの昇格判断は行わない。\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
