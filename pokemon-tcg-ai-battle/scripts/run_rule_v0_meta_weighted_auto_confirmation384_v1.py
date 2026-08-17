#!/usr/bin/env python3
"""Run 384-game confirmation for positive root common24 candidates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.resource_governor_v1 import (
    ResourceBudget,
    ResourceGovernor,
    ResourceSnapshot,
)
from scripts.parallel_cabt_evaluator_v1 import (
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_rule_v0_meta_weighted_auto_common24_v1 import (
    COMMON24_SCHEMA,
    COMMON24_CONFIG,
    _common24_ids,
)
from scripts.run_rule_v0_meta_weighted_auto_search_v1 import (
    AUTHORITY_FALSE,
    DEFAULT_WORKERS,
    POOL_MANIFEST,
    RESOURCE_CONFIG,
    ROOT,
    _build_arm_games,
    _file_sha,
    _fresh_root,
    _write_bytes_no_clobber,
    _write_json_no_clobber,
)


CONFIRMATION_SCHEMA = "meta-specialist-rule-v0-root-deck-meta-weighted-auto-confirmation384-v1"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-meta-weighted-auto-confirmation384-v1-20260814"
DEFAULT_BASE_SEED = 23662000
GAMES_PER_SEAT = 8
DEFAULT_WORKER_RECYCLE_GAMES = 64


class RuleV0MetaWeightedConfirmationError(ValueError):
    """Raised when the 384 confirmation contract is not met."""


def select_positive_common24_candidates(
    manifest: Mapping[str, object],
    summary: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    if manifest.get("schema_version") != COMMON24_SCHEMA:
        raise RuleV0MetaWeightedConfirmationError("common24 manifest schema mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE:
        raise RuleV0MetaWeightedConfirmationError("common24 manifest authority mismatch")
    if summary.get("all_faults_zero") is not True:
        raise RuleV0MetaWeightedConfirmationError("common24 summary has faults")
    rows = summary.get("candidates")
    if not isinstance(rows, Sequence):
        raise RuleV0MetaWeightedConfirmationError("common24 candidates are missing")
    selected: list[Mapping[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise RuleV0MetaWeightedConfirmationError("common24 candidate row is malformed")
        if row.get("fault_gate") is True and float(row.get("delta_points", 0.0)) > 0.0:
            selected.append(row)
    return tuple(selected)


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuleV0MetaWeightedConfirmationError(f"invalid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise RuleV0MetaWeightedConfirmationError(f"JSON root must be an object: {path}")
    return value


def execute_confirmation384(
    *,
    source_root: Path,
    output: Path = OUTPUT_DEFAULT,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if type(workers) is not int or workers < 1 or type(worker_recycle_games) is not int or worker_recycle_games < 1:
        raise RuleV0MetaWeightedConfirmationError("workers and worker_recycle_games must be positive ints")
    source_root = source_root.resolve()
    source_manifest_path = source_root / "common24_manifest.json"
    source_summary_path = source_root / "common24_summary.json"
    source_manifest = _read_json(source_manifest_path)
    source_summary = _read_json(source_summary_path)
    positive_rows = select_positive_common24_candidates(source_manifest, source_summary)
    if not positive_rows:
        raise RuleV0MetaWeightedConfirmationError("no common24-positive candidates are eligible for confirmation")
    parent = source_manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise RuleV0MetaWeightedConfirmationError("common24 parent is missing")
    if _file_sha(Path(str(parent["deck_path"]))) != parent.get("deck_file_sha256"):
        raise RuleV0MetaWeightedConfirmationError("parent deck changed")
    if str(parent.get("package_policy_sha256")) != root_policy_sha256():
        raise RuleV0MetaWeightedConfirmationError("root policy changed since common24")
    source_candidates = {
        str(row["candidate_id"]): row
        for row in source_manifest.get("candidates", ())
        if isinstance(row, Mapping)
    }
    specs: list[tuple[str, Path, str]] = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for positive in positive_rows:
        candidate_id = str(positive["candidate_id"])
        row = source_candidates.get(candidate_id)
        if row is None:
            raise RuleV0MetaWeightedConfirmationError(f"candidate missing from common24 manifest: {candidate_id}")
        path = Path(str(row["deck_path"]))
        if _file_sha(path) != row.get("deck_file_sha256"):
            raise RuleV0MetaWeightedConfirmationError(f"candidate deck changed: {path}")
        specs.append((candidate_id, path, str(row["deck_file_sha256"])))
    output = _fresh_root(output)
    opponent_ids = tuple(str(value) for value in source_manifest.get("opponent_ids", ()))
    if opponent_ids != _common24_ids() or len(opponent_ids) != 24 or len(set(opponent_ids)) != 24:
        raise RuleV0MetaWeightedConfirmationError("confirmation requires exact common24 opponent strata")
    manifest = {
        "schema_version": CONFIRMATION_SCHEMA,
        "purpose": "SUBMISSION_COMPATIBLE_RULE_V0_ROOT_DECK_META_WEIGHTED_CONFIRMATION384",
        "source_common_root": str(source_root),
        "source_common_manifest_sha256": _file_sha(source_manifest_path),
        "source_common_summary_sha256": _file_sha(source_summary_path),
        "parent": parent,
        "candidates": [source_candidates[str(row["candidate_id"])] for row in positive_rows],
        "common24_config_path": str(COMMON24_CONFIG.resolve()),
        "common24_config_sha256": _file_sha(COMMON24_CONFIG),
        "opponent_ids": list(opponent_ids),
        "heldout_training_exposure": 0,
        "protocol": {
            "games_per_arm": len(opponent_ids) * 2 * GAMES_PER_SEAT,
            "games_per_seat": GAMES_PER_SEAT,
            "base_seed": base_seed,
            "same_seed_schedule": True,
            "workers_requested": workers,
            "worker_recycle_games": worker_recycle_games,
            "heldout_exposure": False,
        },
        "pool_manifest_path": str(POOL_MANIFEST.resolve()),
        "pool_manifest_sha256": _file_sha(POOL_MANIFEST),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_json_no_clobber(output / "confirmation_manifest.json", manifest)
    all_games: list[object] = []
    by_arm: dict[str, list[object]] = defaultdict(list)
    for arm_id, deck_path, deck_sha in specs:
        games = _build_arm_games(
            arm_id=arm_id,
            deck_path=deck_path,
            deck_sha=deck_sha,
            references=opponent_ids,
            base_seed=base_seed,
            games_per_seat=GAMES_PER_SEAT,
            block_id_prefix=f"{CONFIRMATION_SCHEMA}-confirmation384",
        )
        games = tuple(
            replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "confirmation384": True,
                    "common24_source_summary_sha256": _file_sha(source_summary_path),
                    "heldout_training_exposure": 0,
                    **AUTHORITY_FALSE,
                },
            )
            for game in games
        )
        if len(games) != len(opponent_ids) * 2 * GAMES_PER_SEAT:
            raise RuleV0MetaWeightedConfirmationError(f"confirmation count gate failed: {arm_id}")
        by_arm[arm_id].extend(games)
        all_games.extend(games)
    if len({game.game_id for game in all_games}) != len(all_games):
        raise RuleV0MetaWeightedConfirmationError("confirmation global game IDs are not unique")
    parent_keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in by_arm["parent"]}
    for arm_id, games in by_arm.items():
        if arm_id == "parent":
            continue
        keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in games}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise RuleV0MetaWeightedConfirmationError(f"confirmation paired schedule mismatch: {arm_id}")
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=min(workers, budget.max_workers), snapshot=before)
    admitted_workers = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted_workers < 1:
        raise RuleV0MetaWeightedConfirmationError("resource governor admitted no workers")
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        tuple(all_games),
        output_dir=output / "confirmation384" / "evaluation",
        max_workers=admitted_workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    summaries = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    expected_arms = {arm for arm, _path, _sha in specs}
    if set(summaries) != expected_arms:
        raise RuleV0MetaWeightedConfirmationError("confirmation arm metadata was not preserved")
    parent_score = float(summaries["parent"]["score_rate"])
    candidates = []
    for candidate_id, _path, _sha in specs[1:]:
        score = float(summaries[candidate_id]["score_rate"])
        candidates.append({
            "candidate_id": candidate_id,
            "score_rate": score,
            "delta_points": (score - parent_score) * 100.0,
            "fault_gate": int(summaries[candidate_id]["faults"]) == 0,
            "status": "confirmation_positive_candidate_only" if int(summaries[candidate_id]["faults"]) == 0 and score > parent_score else "candidate_only",
        })
    after = ResourceSnapshot.collect()
    payload = {
        "schema_version": f"{CONFIRMATION_SCHEMA}-summary",
        "confirmation_manifest_path": str((output / "confirmation_manifest.json").resolve()),
        "confirmation_manifest_sha256": manifest_sha,
        "source_common_summary_sha256": _file_sha(source_summary_path),
        "arms": summaries,
        "parent_score_rate": parent_score,
        "candidates": candidates,
        "all_faults_zero": int(result["summary"]["faults"]) == 0,
        "identity_gate": len({game.game_id for game in all_games}) == len(all_games),
        "heldout_training_exposure": 0,
        "telemetry": {
            "workers_requested": workers,
            "workers_admitted": admitted_workers,
            "worker_recycle_games": worker_recycle_games,
            "governor_decision": decision.to_dict(),
            "requested_games": len(all_games),
            "completed_games": result["summary"]["completed_games"],
            "faults": result["summary"]["faults"],
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": len(all_games) / elapsed,
            "memory_available_before_bytes": before.memory_available_bytes,
            "memory_available_after_bytes": after.memory_available_bytes,
        },
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "candidate-only; no automatic 768/longrun/submission",
    }
    summary_sha = _write_json_no_clobber(output / "confirmation384_summary.json", payload)
    lines = ["# Rule v0/root deck automatic confirmation384", "", f"- parent: {summaries['parent']['wins']}-{summaries['parent']['draws']}-{summaries['parent']['losses']}-{summaries['parent']['faults']} ({parent_score:.6f})"]
    lines.extend(f"- {row['candidate_id']}: {row['delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates)
    md_sha = _write_bytes_no_clobber(output / "confirmation384_summary.md", ("\n".join(lines) + "\n").encode("utf-8"))
    final = {
        "schema_version": f"{CONFIRMATION_SCHEMA}-final",
        "output_root": str(output),
        "confirmation_manifest_sha256": manifest_sha,
        "summary_sha256": summary_sha,
        "summary_md_sha256": md_sha,
        "candidates": candidates,
        "all_faults_zero": payload["all_faults_zero"],
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": True,
    }
    _write_json_no_clobber(output / "final_summary.json", final)
    return final


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args(argv)
    result = execute_confirmation384(
        source_root=args.source_root,
        output=args.output,
        base_seed=args.base_seed,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
