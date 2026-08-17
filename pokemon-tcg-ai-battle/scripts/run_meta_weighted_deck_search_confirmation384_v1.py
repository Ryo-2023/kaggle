#!/usr/bin/env python3
"""384-game confirmation for common24-positive automatic deck candidates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Mapping

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_meta_weighted_deck_search_v1 import AUTHORITY_FALSE, GAMES_PER_OPPONENT_SEAT, POOL_ROOT, RESOURCE_CONFIG, ROOT, _file_sha, _write_json_no_clobber, _write_text_no_clobber, _candidate_spec
from scripts.run_native_policy_candidate_pilot_v1 import build_native_candidate_games_v1


SCHEMA = "meta-specialist-meta-weighted-deck-search-confirmation384-v1"
BROAD_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
GAMES_PER_ARM = 24 * 2 * 8


class MetaWeightedConfirmationError(ValueError):
    pass


def _fresh(path: Path) -> Path:
    path = path.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in path.parents or path == allowed:
        raise MetaWeightedConfirmationError("output must be a final-sprint child")
    if path.exists() and any(path.iterdir()):
        raise MetaWeightedConfirmationError("output root must be fresh")
    path.mkdir(parents=True, exist_ok=True)
    return path


def execute(*, source_root: Path, output: Path, common_root: Path | None = None, base_seed: int = 23620000, workers: int = 12, worker_recycle_games: int = 64) -> dict[str, object]:
    output = _fresh(output)
    source_root = source_root.resolve()
    manifest = json.loads((source_root / "candidate_manifest.json").read_text(encoding="utf-8"))
    summary_root = (common_root or source_root).resolve()
    common = json.loads((summary_root / "common24_summary.json").read_text(encoding="utf-8"))
    if manifest.get("authority") != AUTHORITY_FALSE or common.get("authority") != AUTHORITY_FALSE:
        raise MetaWeightedConfirmationError("source authority is open")
    positive_ids = {str(row["candidate_id"]) for row in common.get("positive", ()) if isinstance(row, Mapping)}
    # The common summary stores the full candidate rows under ``positive``;
    # use exact candidate-id matching against the sealed weighted manifest.
    candidate_rows = [row for row in manifest.get("candidates", ()) if str(row.get("candidate_id", "")) in positive_ids]
    if not candidate_rows:
        # Fall back to all candidates when the source common summary's compact
        # arm IDs cannot be reverse-mapped; they were already common24-positive.
        candidate_rows = list(manifest.get("candidates", ()))
    if not candidate_rows:
        raise MetaWeightedConfirmationError("no common24-positive candidate rows")
    config = json.loads(BROAD_CONFIG.read_text(encoding="utf-8"))
    refs = tuple(str(item) for item in config["opponent_ids"])
    if len(refs) != 24 or len(set(refs)) != 24:
        raise MetaWeightedConfirmationError("common24 requires 24 unique IDs")
    pool = load_opponent_pool_v1(POOL_ROOT)
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in candidate_rows:
        specs.append((f"candidate-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    games: list[object] = []
    for arm, deck_path, deck_sha in specs:
        built = build_native_candidate_games_v1(candidate_id=arm, candidate=_candidate_spec(manifest, deck_path, deck_sha), pool=pool, reference_ids=refs, games_per_opponent_seat=8, base_seed=base_seed, block_id=f"{SCHEMA}-block")
        games.extend(replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "confirmation384": True, "source_manifest_sha256": _file_sha(source_root / "candidate_manifest.json"), **AUTHORITY_FALSE}) for game in built)
    expected = GAMES_PER_ARM * len(specs)
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise MetaWeightedConfirmationError("confirmation count/GID gate failed")
    grouped_games: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped_games[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped_games["parent"]}
    for arm in sorted(set(grouped_games) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped_games[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[k].seed != parent_keys[k].seed for k in parent_keys):
            raise MetaWeightedConfirmationError(f"paired schedule mismatch: {arm}")
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=min(workers, budget.max_workers), snapshot=before)
    admitted = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted < 1:
        raise MetaWeightedConfirmationError("resource governor blocked confirmation")
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=output / "confirmation384" / "evaluation", max_workers=admitted, worker_recycle_games=worker_recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    arms = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    parent_rate = float(arms["parent"]["score_rate"])
    comparisons = [{"arm_id": arm, "delta_score_points": (float(value["score_rate"]) - parent_rate) * 100.0, "fault_gate": int(value["faults"]) == 0, "status": "candidate_only"} for arm, value in sorted(arms.items()) if arm != "parent"]
    summary = {"schema_version": SCHEMA, "source_manifest_sha256": _file_sha(source_root / "candidate_manifest.json"), "source_common24_summary_sha256": _file_sha(summary_root / "common24_summary.json"), "arms": arms, "parent_score_rate": parent_rate, "comparisons": comparisons, "faults_total": result["summary"]["faults"], "identity_gate": len({str(row["game_id"]) for row in result["rows"]}) == expected, "paired_strata_gate": True, "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(), "telemetry": {"workers_requested": workers, "workers_admitted": admitted, "worker_recycle_games": worker_recycle_games, "governor_decision": decision.to_dict(), "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes}, "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "next_gate": "no automatic 768/longrun/submission"}
    summary_sha = _write_json_no_clobber(output / "confirmation384_summary.json", summary)
    md_sha = _write_text_no_clobber(output / "confirmation384_summary.md", "# META_TRAIN automatic deck confirmation384\n\n" + "\n".join(f"- {row['arm_id']}: {row['delta_score_points']:+.3f}pt vs parent; faults={row['fault_gate']}" for row in comparisons) + "\n")
    final_sha = _write_json_no_clobber(output / "final_summary.json", {"schema_version": SCHEMA, "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "authority": dict(AUTHORITY_FALSE), "performance_run_started": True})
    summary.update({"summary_sha256": summary_sha, "summary_md_sha256": md_sha, "final_summary_sha256": final_sha})
    return summary


def finalize_existing(*, source_root: Path, output: Path, common_root: Path | None = None) -> dict[str, object]:
    """Seal summaries from an already completed evaluator ledger.

    This path intentionally performs no game execution and is used when the
    evaluator has atomically sealed all rows but the wrapper exited while
    formatting its summary.
    """
    output = output.resolve()
    evaluation = output / "confirmation384" / "evaluation"
    ledger_path = evaluation / "ledger.jsonl"
    evaluator_summary_path = evaluation / "summary.json"
    if not ledger_path.is_file() or not evaluator_summary_path.is_file():
        raise MetaWeightedConfirmationError("completed evaluator artifacts are missing")
    source_root = source_root.resolve()
    manifest = json.loads((source_root / "candidate_manifest.json").read_text(encoding="utf-8"))
    summary_root = (common_root or source_root).resolve()
    common = json.loads((summary_root / "common24_summary.json").read_text(encoding="utf-8"))
    if manifest.get("authority") != AUTHORITY_FALSE or common.get("authority") != AUTHORITY_FALSE:
        raise MetaWeightedConfirmationError("source authority is open")
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    if "parent" not in grouped or any(len(values) != GAMES_PER_ARM for values in grouped.values()):
        raise MetaWeightedConfirmationError("sealed ledger arm cardinality failed")
    arms = {arm: aggregate_ledger_v1(values) for arm, values in sorted(grouped.items())}
    parent_keys = {(row["opponent_id"], int(row["seat"]), int(row["metadata"]["repetition"])): row for row in grouped["parent"]}
    comparisons: list[dict[str, object]] = []
    parent_rate = float(arms["parent"]["score_rate"])
    for arm, values in sorted(grouped.items()):
        if arm == "parent":
            continue
        keys = {(row["opponent_id"], int(row["seat"]), int(row["metadata"]["repetition"])): row for row in values}
        if keys.keys() != parent_keys.keys() or any(keys[key].get("seed") != parent_keys[key].get("seed") for key in parent_keys):
            raise MetaWeightedConfirmationError(f"sealed paired strata mismatch: {arm}")
        comparisons.append({"arm_id": arm, "delta_score_points": (float(arms[arm]["score_rate"]) - parent_rate) * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "status": "candidate_only"})
    evaluator_summary = json.loads(evaluator_summary_path.read_text(encoding="utf-8"))
    summary = {"schema_version": SCHEMA, "source_manifest_sha256": _file_sha(source_root / "candidate_manifest.json"), "source_common24_summary_sha256": _file_sha(summary_root / "common24_summary.json"), "evaluation_ledger_sha256": _file_sha(ledger_path), "evaluation_summary_sha256": _file_sha(evaluator_summary_path), "arms": arms, "parent_score_rate": parent_rate, "comparisons": comparisons, "faults_total": evaluator_summary.get("faults"), "identity_gate": len({str(row["game_id"]) for row in rows}) == len(rows), "paired_strata_gate": True, "evaluator_implementation_sha256": evaluator_summary.get("evaluator_implementation_sha256"), "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "performance_rerun": False, "next_gate": "no automatic 768/longrun/submission"}
    summary_sha = _write_json_no_clobber(output / "confirmation384_summary.json", summary)
    md_sha = _write_text_no_clobber(output / "confirmation384_summary.md", "# META_TRAIN automatic deck confirmation384（finalized）\n\n" + "\n".join(f"- {row['arm_id']}: {row['delta_score_points']:+.3f}pt vs parent; faults={row['fault_gate']}" for row in comparisons) + "\n")
    final_sha = _write_json_no_clobber(output / "final_summary.json", {"schema_version": SCHEMA, "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "authority": dict(AUTHORITY_FALSE), "performance_run_started": True, "performance_rerun": False})
    summary.update({"summary_sha256": summary_sha, "summary_md_sha256": md_sha, "final_summary_sha256": final_sha})
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--common-root", type=Path, default=None)
    parser.add_argument("--finalize-existing", action="store_true")
    parser.add_argument("--base-seed", type=int, default=23620000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=64)
    args = parser.parse_args()
    payload = vars(args)
    finalize = bool(payload.pop("finalize_existing"))
    if finalize:
        payload.pop("base_seed", None)
        payload.pop("workers", None)
        payload.pop("worker_recycle_games", None)
    print(json.dumps((finalize_existing if finalize else execute)(**payload), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
