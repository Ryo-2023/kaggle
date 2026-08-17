#!/usr/bin/env python3
"""Research-only common24 guardrail for the v7 Hariyama line candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Mapping

from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts import run_rule_v0_root_deck_weighted_v1 as base
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-rule-v0-root-deck-novel-v7-common24"
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-novel-v7-weighted48-20260814"
SOURCE_MANIFEST_SHA256 = "d2908c075d91be4c2d4d9f7bb95d006d96286569cea04832df8f6788ec2a9952"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-novel-v7-common24-96-20260814"
BASE_SEED = 23_560_000
WORKERS = 12
RECYCLE = 16
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh(path: Path) -> Path:
    path = path.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in path.parents or path == allowed:
        raise ValueError("output must be below runs/final-sprint-autonomous")
    if path.exists() and any(path.iterdir()):
        raise ValueError("output must be fresh and empty")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, value: object) -> str:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    temporary.write_bytes(raw)
    temporary.replace(path)
    return _sha(path)


def _write_text(path: Path, value: str) -> str:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return _sha(path)


def _rows_integrity(rows: list[Mapping[str, object]], expected: int) -> dict[str, object]:
    return {
        "requested_games": expected,
        "rows": len(rows),
        "completed_games": sum(str(row.get("status")) == "DONE" for row in rows),
        "faults": sum(str(row.get("outcome")) == "fault" for row in rows),
        "draws": sum(str(row.get("outcome")) == "draw" for row in rows),
        "seat_counts": {str(seat): sum(int(row.get("seat", -1)) == seat for row in rows) for seat in (0, 1)},
        "opponent_count": len({str(row.get("opponent_id")) for row in rows}),
        "game_ids_unique": len({str(row.get("game_id")) for row in rows}) == len(rows),
        "seeds_unique": len({int(row.get("seed")) for row in rows}) == len(rows),
        "status_distribution": {status: sum(str(row.get("status")) == status for row in rows) for status in sorted({str(row.get("status")) for row in rows})},
    }


def execute(output: Path) -> dict[str, object]:
    output = _fresh(output)
    source_manifest_path = SOURCE_ROOT / "candidate_manifest.json"
    source_summary_path = SOURCE_ROOT / "weighted48_summary.json"
    if _sha(source_manifest_path) != SOURCE_MANIFEST_SHA256:
        raise ValueError("source weighted manifest changed")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    if source_manifest.get("schema_version") != "meta-specialist-rule-v0-root-deck-novel-v7":
        raise ValueError("source schema mismatch")
    if source_manifest.get("authority") != base.AUTHORITY_FALSE:
        raise ValueError("source authority mismatch")
    candidates = [row for row in source_manifest["candidates"] if row.get("candidate_id") == "root-hariyama-to-makuhita"]
    if len(candidates) != 1:
        raise ValueError("Hariyama candidate missing or duplicated")
    candidate = candidates[0]
    parent = source_manifest["parent"]
    config = json.loads(base.COMMON24_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(value) for value in config.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise ValueError("common24 config must contain exactly 24 unique opponents")
    subset_sha = str(source_manifest["meta_train_subset"]["subset_sha256"])
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "P0_RULE_V0_ROOT_DECK_HARIYAMA_COMMON24_GUARDRAIL",
        "source_weighted_root": str(SOURCE_ROOT.resolve()),
        "source_weighted_manifest_sha256": _sha(source_manifest_path),
        "source_weighted_summary_sha256": _sha(source_summary_path),
        "source_candidate_weighted_delta_points": next(row["weighted_delta_points"] for row in source_summary["candidates"] if row["candidate_id"] == "root-hariyama-to-makuhita"),
        "parent": parent,
        "candidate": candidate,
        "common24_config_path": str(base.COMMON24_CONFIG.resolve()),
        "common24_config_sha256": _sha(base.COMMON24_CONFIG),
        "opponent_ids": list(references),
        "protocol": {
            "games_per_arm": 96,
            "games_per_seat": 2,
            "base_seed": BASE_SEED,
            "same_seed_schedule": True,
            "workers": WORKERS,
            "worker_recycle_games": RECYCLE,
            "heldout_exposure": False,
        },
        "weighted_subset_sha256": subset_sha,
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
        "invalid_source_arm_preserved": "root-poke-pad-to-dusk-ball: all 48 AGENT_INVALID; not rerun or scored",
    }
    manifest_sha = _write_json(output / "common24_manifest.json", manifest)
    budget = ResourceBudget.from_json(base.RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=WORKERS, snapshot=before)
    if decision.recommended_workers < WORKERS:
        raise ValueError(f"resource governor admitted only {decision.recommended_workers} workers")
    specs = [
        ("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])),
        ("root-hariyama-to-makuhita", Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"])),
    ]
    summaries: dict[str, object] = {}
    integrity: dict[str, object] = {}
    roots: dict[str, str] = {}
    all_game_ids: list[str] = []
    started = time.monotonic()
    for arm, deck_path, deck_sha in specs:
        games = base._build_arm_games(
            deck_path=deck_path,
            deck_sha=deck_sha,
            deck_id=arm,
            block_id=f"{SCHEMA}-96-{arm}",
            references=references,
            base_seed=BASE_SEED,
            games_per_seat=2,
        )
        games = tuple(
            base.replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "common24_evaluation_only": True,
                    "common24_config_sha256": _sha(base.COMMON24_CONFIG),
                    "weighted_subset_sha256": subset_sha,
                },
            )
            for game in games
        )
        if len(games) != 96 or len({game.game_id for game in games}) != 96:
            raise ValueError(f"game count/identity gate failed for {arm}")
        all_game_ids.extend(game.game_id for game in games)
        destination = output / "common24-96" / arm / "evaluation"
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=WORKERS, worker_recycle_games=RECYCLE, overwrite=False)
        rows = list(result["rows"])
        summaries[arm] = aggregate_ledger_v1(rows)
        integrity[arm] = _rows_integrity(rows, 96)
        roots[arm] = str(destination.resolve())
    if len(all_game_ids) != len(set(all_game_ids)):
        raise ValueError("cross-arm game IDs are not unique")
    after = ResourceSnapshot.collect()
    elapsed = max(time.monotonic() - started, 1e-9)
    parent_score = float(summaries["parent"]["score_rate"])
    candidate_score = float(summaries["root-hariyama-to-makuhita"]["score_rate"])
    payload = {
        "schema_version": f"{SCHEMA}-summary",
        "manifest_sha256": manifest_sha,
        "common24_manifest_path": str((output / "common24_manifest.json").resolve()),
        "arms": summaries,
        "integrity": integrity,
        "cross_arm_game_ids_unique": len(all_game_ids) == len(set(all_game_ids)),
        "parent_score_rate": parent_score,
        "candidate_score_rate": candidate_score,
        "candidate_delta_points": (candidate_score - parent_score) * 100.0,
        "all_faults_zero": all(int(value["faults"]) == 0 for value in integrity.values()),
        "telemetry": {
            "before": before.to_dict(),
            "after": after.to_dict(),
            "decision": decision.to_dict(),
            "workers": WORKERS,
            "worker_recycle_games": RECYCLE,
            "requested_games": len(all_game_ids),
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": len(all_game_ids) / elapsed,
        },
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "candidate-only; no automatic 384/768/longrun",
        "arm_roots": roots,
    }
    summary_sha = _write_json(output / "common24_summary.json", payload)
    parent_wdl = summaries["parent"]
    candidate_wdl = summaries["root-hariyama-to-makuhita"]
    md = (
        "# Rule v0/root deck Hariyama common24\n\n"
        f"- parent: {parent_wdl['wins']}-{parent_wdl['draws']}-{parent_wdl['losses']}-{parent_wdl['faults']} ({parent_score:.6f})\n"
        f"- root-hariyama-to-makuhita: {candidate_wdl['wins']}-{candidate_wdl['draws']}-{candidate_wdl['losses']}-{candidate_wdl['faults']} ({candidate_score:.6f}), delta={(candidate_score-parent_score)*100:+.3f}pt\n"
        f"- all faults zero: {payload['all_faults_zero']}; seat/GID/seed gates are recorded in common24_summary.json\n"
        "- candidate-only; no automatic 384/768/longrun\n"
    )
    md_sha = _write_text(output / "common24_summary.md", md)
    final = {
        "schema_version": SCHEMA,
        "output_root": str(output),
        "common24_manifest_sha256": manifest_sha,
        "summary_sha256": summary_sha,
        "summary_md_sha256": md_sha,
        "candidate_delta_points": payload["candidate_delta_points"],
        "all_faults_zero": payload["all_faults_zero"],
        "integrity": integrity,
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": True,
        "arm_roots": roots,
    }
    _write_json(output / "final_summary.json", final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(execute(args.output.resolve()), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
