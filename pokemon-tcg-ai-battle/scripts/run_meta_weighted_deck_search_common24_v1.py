#!/usr/bin/env python3
"""Common24 guardrail for positive arms from the automatic META_TRAIN search."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Mapping

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_meta_weighted_deck_search_v1 import (
    AUTHORITY_FALSE,
    GAMES_PER_OPPONENT_SEAT,
    POOL_ROOT,
    RESOURCE_CONFIG,
    ROOT,
    _file_sha,
    _write_json_no_clobber,
    _write_text_no_clobber,
    _candidate_spec,
)
from scripts.run_native_policy_candidate_pilot_v1 import build_native_candidate_games_v1


SCHEMA = "meta-specialist-meta-weighted-deck-search-common24-v1"
BROAD_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
COMMON24_GAMES_PER_ARM = 24 * 2 * GAMES_PER_OPPONENT_SEAT


class MetaWeightedCommon24Error(ValueError):
    """Raised when a positive automatic-search arm cannot be revalidated."""


def _fresh_root(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise MetaWeightedCommon24Error("output must be a final-sprint child")
    if resolved.exists() and any(resolved.iterdir()):
        raise MetaWeightedCommon24Error("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_source(source_root: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest_path = source_root / "candidate_manifest.json"
    summary_path = source_root / "weighted48_summary.json"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise MetaWeightedCommon24Error("automatic weighted source artifacts are missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if manifest.get("authority") != AUTHORITY_FALSE or summary.get("authority") != AUTHORITY_FALSE:
        raise MetaWeightedCommon24Error("source authority is open")
    if summary.get("all_faults_zero") is not True:
        raise MetaWeightedCommon24Error("source fault gate is not closed")
    positive = [row for row in summary.get("candidates", ()) if row.get("status") == "weighted_positive_candidate_only" and float(row.get("weighted_delta", 0.0)) > 0.0]
    if not positive:
        raise MetaWeightedCommon24Error("source contains no positive candidates")
    candidate_by_id = {str(row["candidate_id"]): row for row in manifest.get("candidates", ())}
    selected: list[dict[str, object]] = []
    for row in positive:
        candidate = candidate_by_id.get(str(row["candidate_id"]))
        if candidate is None:
            raise MetaWeightedCommon24Error(f"positive candidate absent from manifest: {row.get('candidate_id')}")
        deck_path = Path(str(candidate["deck_path"]))
        if _file_sha(deck_path) != candidate.get("deck_file_sha256"):
            raise MetaWeightedCommon24Error(f"candidate deck changed: {deck_path}")
        selected.append(dict(candidate))
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise MetaWeightedCommon24Error("parent identity missing")
    if _file_sha(Path(str(parent["deck_path"]))) != parent.get("deck_file_sha256"):
        raise MetaWeightedCommon24Error("parent deck changed")
    if _file_sha(Path(str(parent["policy_path"]))) != parent.get("policy_file_sha256"):
        raise MetaWeightedCommon24Error("parent policy changed")
    return manifest, {"source_manifest_sha256": _file_sha(manifest_path), "source_summary_sha256": _file_sha(summary_path), "positive": selected}


def execute(*, source_root: Path, output: Path, base_seed: int = 23610000, workers: int = 12, worker_recycle_games: int = 16) -> dict[str, object]:
    if type(workers) is not int or workers < 1 or type(worker_recycle_games) is not int or worker_recycle_games < 1:
        raise MetaWeightedCommon24Error("workers and recycle settings must be positive ints")
    output = _fresh_root(output)
    manifest, source = _load_source(source_root.resolve())
    config = json.loads(BROAD_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(value) for value in config["opponent_ids"])
    if len(references) != 24 or len(set(references)) != 24:
        raise MetaWeightedCommon24Error("broad common24 config must contain 24 unique IDs")
    pool = load_opponent_pool_v1(POOL_ROOT)
    if set(references) - set(pool):
        raise MetaWeightedCommon24Error("common24 opponent absent from pool")
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in source["positive"]:
        specs.append((f"candidate-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    games: list[object] = []
    for arm, deck_path, deck_sha in specs:
        built = build_native_candidate_games_v1(
            candidate_id=arm,
            candidate=_candidate_spec(manifest, deck_path, deck_sha),
            pool=pool,
            reference_ids=references,
            games_per_opponent_seat=GAMES_PER_OPPONENT_SEAT,
            base_seed=base_seed,
            block_id=f"{SCHEMA}-96",
        )
        games.extend(replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "common24_guardrail": True, "source_manifest_sha256": source["source_manifest_sha256"], "source_candidate_id": arm, **AUTHORITY_FALSE}) for game in built)
    expected = COMMON24_GAMES_PER_ARM * len(specs)
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise MetaWeightedCommon24Error("common24 game count/GID gate failed")
    grouped_games: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped_games[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in grouped_games["parent"]}
    for arm in sorted(set(grouped_games) - {"parent"}):
        keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in grouped_games[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise MetaWeightedCommon24Error(f"paired strata mismatch: {arm}")
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=min(workers, budget.max_workers), snapshot=before)
    admitted = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted < 1:
        raise MetaWeightedCommon24Error("resource governor did not admit workers")
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=output / "common24-96" / "evaluation", max_workers=admitted, worker_recycle_games=worker_recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    arms = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    parent_score = float(arms["parent"]["score_rate"])
    rows: list[dict[str, object]] = []
    for arm in sorted(set(arms) - {"parent"}):
        delta = (float(arms[arm]["score_rate"]) - parent_score) * 100.0
        rows.append({"arm_id": arm, "delta_score_points": delta, "fault_gate": int(arms[arm]["faults"]) == 0, "status": "common24_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0 else "candidate_only"})
    summary = {"schema_version": SCHEMA, **source, "source_broad_config_sha256": _file_sha(BROAD_CONFIG), "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(), "arms": arms, "parent_score_rate": parent_score, "candidates": rows, "faults_total": result["summary"]["faults"], "identity_gate": len({str(row["game_id"]) for row in result["rows"]}) == expected, "paired_strata_gate": True, "telemetry": {"workers_requested": workers, "workers_admitted": admitted, "worker_recycle_games": worker_recycle_games, "governor_decision": decision.to_dict(), "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes}, "authority": dict(AUTHORITY_FALSE), "next_gate": "384 only after explicit review; no automatic longrun"}
    summary_sha = _write_json_no_clobber(output / "common24_summary.json", summary)
    md_sha = _write_text_no_clobber(output / "common24_summary.md", "# META_TRAIN weighted automatic common24\n\n" + "\n".join(f"- {row['arm_id']}: {row['delta_score_points']:+.3f}pt vs parent; {row['status']}" for row in rows) + "\n")
    final_sha = _write_json_no_clobber(output / "final_summary.json", {"schema_version": SCHEMA, "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "authority": dict(AUTHORITY_FALSE), "performance_run_started": True})
    summary.update({"summary_sha256": summary_sha, "summary_md_sha256": md_sha, "final_summary_sha256": final_sha})
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=23610000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args()
    print(json.dumps(execute(**vars(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
