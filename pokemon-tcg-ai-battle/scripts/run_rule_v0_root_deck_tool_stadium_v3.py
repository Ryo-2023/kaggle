#!/usr/bin/env python3
"""Sealed research-only Tool/Stadium surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from scripts import run_rule_v0_root_deck_weighted_v1 as base
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import (
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


base.SCHEMA = "meta-specialist-rule-v0-root-deck-tool-stadium-v3"
base.OUTPUT_DEFAULT = base.ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-tool-stadium-v3-weighted48-20260814"
base.WEIGHTED_BASE_SEED = 23_470_000
base.SURFACES = (
    ("root-hero-cape-to-maximum-belt", 1159, 1158),
    ("root-gravity-mountain-to-festival-grounds", 1252, 1245),
)
COMMON24_BASE_SEED = 23_480_000


def execute_common24(*, source_root: Path, output: Path) -> dict[str, object]:
    """Run common24 only for weighted-positive Tool/Stadium candidates.

    The source weighted root is treated as immutable: its manifest and summary
    are hashed into a fresh common24 manifest, and every arm uses the same
    absolute common24 opponent paths and seed schedule.  This is a
    research-only guardrail; it never grants promotion, training, submission,
    or long-run authority.
    """
    source_root = source_root.resolve()
    source_manifest_path = source_root / "candidate_manifest.json"
    source_summary_path = source_root / "weighted48_summary.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    weighted = json.loads(source_summary_path.read_text(encoding="utf-8"))
    if not isinstance(source_manifest, Mapping) or not isinstance(weighted, Mapping):
        raise ValueError("sealed weighted source malformed")
    if source_manifest.get("schema_version") != base.SCHEMA:
        raise ValueError("weighted source schema mismatch")
    positives = [
        row for row in weighted.get("candidates", ())
        if isinstance(row, Mapping)
        and row.get("fault_gate") is True
        and row.get("identity_gate") is True
        and float(row.get("weighted_delta", 0.0)) > 0.0
    ]
    if not positives:
        raise ValueError("no weighted-positive candidate")
    output = base._fresh_root(output)
    config = json.loads(base.COMMON24_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(item) for item in config.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise ValueError("common24 config must contain exactly 24 unique opponents")
    by_id = {str(row["candidate_id"]): row for row in source_manifest.get("candidates", ())}
    candidate_rows = []
    for row in positives:
        candidate_id = str(row["candidate_id"])
        if candidate_id not in by_id:
            raise ValueError(f"weighted candidate missing from manifest: {candidate_id}")
        candidate_rows.append(by_id[candidate_id])
    parent = source_manifest["parent"]
    manifest = {
        "schema_version": f"{base.SCHEMA}-common24",
        "purpose": "P0_RULE_V0_ROOT_DECK_TOOL_STADIUM_COMMON24_GUARDRAIL",
        "source_weighted_root": str(source_root),
        "source_weighted_manifest_sha256": base._file_sha(source_manifest_path),
        "source_weighted_summary_sha256": base._file_sha(source_summary_path),
        "parent": parent,
        "candidates": candidate_rows,
        "common24_config_path": str(base.COMMON24_CONFIG.resolve()),
        "common24_config_sha256": base._file_sha(base.COMMON24_CONFIG),
        "opponent_ids": list(references),
        "protocol": {
            "games_per_arm": 96,
            "games_per_seat": 2,
            "base_seed": COMMON24_BASE_SEED,
            "same_seed_schedule": True,
            "workers": 12,
            "worker_recycle_games": 16,
            "heldout_exposure": False,
        },
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(base.AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = base._write_no_clobber(output / "common24_manifest.json", manifest)
    budget = ResourceBudget.from_json(base.RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=12, snapshot=before)
    if decision.recommended_workers < 12:
        raise ValueError(f"resource governor admitted only {decision.recommended_workers} workers")
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    specs.extend(
        (str(row["candidate_id"]), Path(str(row["deck_path"])), str(row["deck_file_sha256"]))
        for row in candidate_rows
    )
    summaries: dict[str, object] = {}
    arm_roots: dict[str, str] = {}
    all_ids: list[str] = []
    started = time.monotonic()
    for arm, deck_path, deck_sha in specs:
        games = base._build_arm_games(
            deck_path=deck_path,
            deck_sha=deck_sha,
            deck_id=arm,
            block_id=f"{base.SCHEMA}-common24-96-{arm}",
            references=references,
            base_seed=COMMON24_BASE_SEED,
            games_per_seat=2,
        )
        games = tuple(
            replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "common24_evaluation_only": True,
                    "common24_config_sha256": base._file_sha(base.COMMON24_CONFIG),
                    "weighted_subset_sha256": source_manifest["meta_train_subset"]["subset_sha256"],
                    "heldout_exposure": False,
                },
            )
            for game in games
        )
        if len(games) != 96 or len({game.game_id for game in games}) != 96:
            raise ValueError(f"common24 game identity/count gate failed: {arm}")
        destination = output / "common24-96" / arm / "evaluation"
        result = run_parallel_cabt_evaluation(
            games,
            output_dir=destination,
            max_workers=12,
            worker_recycle_games=16,
            overwrite=False,
        )
        summaries[arm] = aggregate_ledger_v1(result["rows"])
        arm_roots[arm] = str(destination.resolve())
        all_ids.extend(game.game_id for game in games)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("common24 cross-arm game IDs are not unique")
    after = ResourceSnapshot.collect()
    elapsed = max(time.monotonic() - started, 1e-9)
    parent_score = float(summaries["parent"]["score_rate"])
    candidate_results = []
    for row in candidate_rows:
        arm = str(row["candidate_id"])
        score = float(summaries[arm]["score_rate"])
        candidate_results.append(
            {
                "candidate_id": arm,
                "deck_file_sha256": row["deck_file_sha256"],
                "deck_multiset_sha256": row["deck_multiset_sha256"],
                "score_rate": score,
                "delta_points": (score - parent_score) * 100.0,
                "fault_gate": int(summaries[arm]["faults"]) == 0,
                "status": (
                    "common24_positive_candidate_only"
                    if int(summaries[arm]["faults"]) == 0 and score > parent_score
                    else "candidate_only"
                ),
                "root": arm_roots[arm],
            }
        )
    payload = {
        "schema_version": f"{base.SCHEMA}-common24-summary",
        "manifest_sha256": manifest_sha,
        "common24_manifest_path": str((output / "common24_manifest.json").resolve()),
        "arms": summaries,
        "parent_score_rate": parent_score,
        "candidates": candidate_results,
        "all_faults_zero": all(int(summary["faults"]) == 0 for summary in summaries.values()),
        "identity_gate": len(all_ids) == len(set(all_ids)),
        "seat_counts": {arm: {"0": 48, "1": 48} for arm in summaries},
        "opponents_per_arm": 24,
        "telemetry": {
            "before": before.to_dict(),
            "after": after.to_dict(),
            "decision": decision.to_dict(),
            "workers": 12,
            "worker_recycle_games": 16,
            "requested_games": len(all_ids),
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": len(all_ids) / elapsed,
        },
        "authority": dict(base.AUTHORITY_FALSE),
        "next_gate": "candidate-only; no automatic 384/longrun",
    }
    summary_sha = base._write_no_clobber(output / "common24_summary.json", payload)
    lines = [
        "# Rule v0/root deck Tool/Stadium common24",
        "",
        f"- parent: {summaries['parent']['wins']}-{summaries['parent']['draws']}-{summaries['parent']['losses']}-{summaries['parent']['faults']} ({parent_score:.6f})",
    ]
    lines.extend(
        f"- {row['candidate_id']}: {summaries[row['candidate_id']]['wins']}-{summaries[row['candidate_id']]['draws']}-{summaries[row['candidate_id']]['losses']}-{summaries[row['candidate_id']]['faults']} ({row['score_rate']:.6f}), delta={row['delta_points']:+.3f}pt"
        for row in candidate_results
    )
    lines.append("- faults=0 gate required; no automatic 384/longrun")
    md_sha = base._write_text_no_clobber(output / "common24_summary.md", "\n".join(lines) + "\n")
    final = {
        "schema_version": f"{base.SCHEMA}-common24",
        "output_root": str(output),
        "common24_manifest_sha256": manifest_sha,
        "summary_sha256": summary_sha,
        "summary_md_sha256": md_sha,
        "candidates": candidate_results,
        "all_faults_zero": payload["all_faults_zero"],
        "authority": dict(base.AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": True,
        "arm_roots": arm_roots,
    }
    final_sha = base._write_no_clobber(output / "final_summary.json", final)
    final["final_summary_sha256"] = final_sha
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    parser.add_argument("--common24-from-weighted", action="store_true")
    parser.add_argument("--common24-output", type=Path, default=None)
    args = parser.parse_args()
    if args.common24_from_weighted:
        result = execute_common24(
            source_root=args.output.resolve(),
            output=(args.common24_output or Path(str(args.output) + "-common24")).resolve(),
        )
    else:
        result = base.execute(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
