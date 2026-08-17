#!/usr/bin/env python3
"""Screen one public-state-conditioned Rule v0 policy candidate.

The candidate is intentionally narrow: after a public energy attachment and
two or more actions in the current turn, it gives ATTACK a bounded +240 score
bonus for mandatory MAIN selections.  All other observations use exact Rule
v0 fallback.  The runner compares this policy with Rule v0 on identical
META_TRAIN strata and carries no training, promotion, or submission authority.
"""

from __future__ import annotations

import argparse
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
from mage_ptcg.meta_specialist.rule_v0_phase_conditioned_overlay_v1 import (
    ATTACK_BONUS,
    MIN_TURN_ACTION_COUNT,
    POLICY_ID,
)
from scripts.parallel_cabt_evaluator_v1 import (
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_native_policy_candidate_pilot_v1 import (
    _config_sha,
    build_native_candidate_games_v1,
)
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset
from scripts.run_rule_v0_meta_weighted_auto_search_v1 import (
    AUTHORITY_FALSE,
    META_MANIFEST,
    POOL_MANIFEST,
    POOL_ROOT,
    RESOURCE_CONFIG,
    ROOT,
    _file_sha,
    _fresh_root,
    _write_bytes_no_clobber,
    _write_json_no_clobber,
)


SCHEMA = "meta-specialist-rule-v0-phase-conditioned-policy-screen-v1"
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_BASE_SEED = 23702000
GAMES_PER_OPPONENT_SEAT = 2
CANDIDATE_ID = POLICY_ID
CONTROL_ID = "rule-v0-phase-conditioned-control-rule-v0"
CANDIDATE_MAIN = ROOT / "scripts/rule_v0_phase_conditioned_attack_candidate_v1.py"
CONTROL_MAIN = ROOT / "main.py"
DECK_PATH = ROOT / "deck.csv"
CONFIG_SHA = _config_sha({}, {}, 0.0)


def build_manifest_payload(
    *,
    candidate_policy_sha256: str,
    control_policy_sha256: str,
    deck_sha256: str,
    config_sha256: str,
    selected_ids: Sequence[str],
) -> dict[str, object]:
    """Build the closed identity/authority portion before any game starts."""

    return {
        "schema_version": SCHEMA,
        "purpose": "SUBMISSION_COMPATIBLE_RULE_V0_PUBLIC_PHASE_CONDITIONED_POLICY_SCREEN",
        "policy_id": POLICY_ID,
        "candidate_id": CANDIDATE_ID,
        "control_id": CONTROL_ID,
        "candidate_policy_sha256": candidate_policy_sha256,
        "control_policy_sha256": control_policy_sha256,
        "deck_sha256": deck_sha256,
        "candidate_config_sha256": config_sha256,
        "selected_ids": list(selected_ids),
        "phase_condition": {
            "energyAttached": True,
            "turnActionCount_min": MIN_TURN_ACTION_COUNT,
            "action_type": "ATTACK",
            "bonus": ATTACK_BONUS,
        },
        "public_only": True,
        "native_teacher_labels_used": False,
        "private_observations_used": False,
        "heldout_training_exposure": 0,
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }


def _candidate_spec(*, candidate_id: str, main_path: Path, policy_sha256: str) -> dict[str, object]:
    return {
        "main_path": str(main_path.resolve()),
        "deck_path": str(DECK_PATH.resolve()),
        "policy_sha256": policy_sha256,
        "deck_sha256": _file_sha(DECK_PATH),
        "config_sha256": CONFIG_SHA,
        "env": {},
        "biases": {},
        "min_score_gain": 0.0,
        "pool_root": str(POOL_ROOT.resolve()),
        "candidate_id": candidate_id,
    }


def _build_arm_games(
    *,
    candidate_id: str,
    main_path: Path,
    policy_sha256: str,
    selected_ids: Sequence[str],
    base_seed: int,
    block_id: str,
    repetitions: int,
    arm: str,
) -> tuple[object, ...]:
    games = build_native_candidate_games_v1(
        candidate_id=candidate_id,
        candidate=_candidate_spec(
            candidate_id=candidate_id,
            main_path=main_path,
            policy_sha256=policy_sha256,
        ),
        pool=__import__("mage_ptcg.meta_specialist.opponent_pool_v1", fromlist=["load_opponent_pool_v1"]).load_opponent_pool_v1(POOL_ROOT),
        reference_ids=tuple(selected_ids),
        games_per_opponent_seat=repetitions,
        base_seed=base_seed,
        block_id=block_id,
    )
    return tuple(
        replace(
            game,
            metadata={
                **dict(game.metadata),
                "comparison_arm": arm,
                "phase_conditioned_policy": POLICY_ID,
                "pair_key": f"{game.opponent_id}|seat{game.seat}|rep{game.metadata['repetition']}",
                **AUTHORITY_FALSE,
            },
        )
        for game in games
    )


def _pair_gate(candidate_games: Sequence[object], control_games: Sequence[object]) -> None:
    candidate_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g.seed for g in candidate_games}
    control_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g.seed for g in control_games}
    if candidate_keys != control_keys:
        raise RuntimeError("candidate/control strata or seeds differ")


def _summary_for_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    return {arm: aggregate_ledger_v1(values) for arm, values in sorted(grouped.items())}


def execute_screen(
    *,
    output: Path,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if workers != DEFAULT_WORKERS:
        raise ValueError("this lane is sealed to workers=12")
    if worker_recycle_games < 1:
        raise ValueError("worker_recycle_games must be positive")
    output = _fresh_root(output)
    candidate_policy_sha = _file_sha(CANDIDATE_MAIN)
    control_policy_sha = _file_sha(CONTROL_MAIN)
    deck_sha = _file_sha(DECK_PATH)
    subset = load_meta_train_subset(META_MANIFEST)
    selected_ids = tuple(str(item) for item in subset["selected_ids"])
    if len(selected_ids) != 12:
        raise ValueError("phase-conditioned screen requires the sealed META_TRAIN 12 subset")
    manifest = build_manifest_payload(
        candidate_policy_sha256=candidate_policy_sha,
        control_policy_sha256=control_policy_sha,
        deck_sha256=deck_sha,
        config_sha256=CONFIG_SHA,
        selected_ids=selected_ids,
    )
    manifest.update(
        {
            "root_policy_closure_sha256": root_policy_sha256(),
            "deck_path": str(DECK_PATH.resolve()),
            "candidate_main_path": str(CANDIDATE_MAIN.resolve()),
            "control_main_path": str(CONTROL_MAIN.resolve()),
            "pool_manifest_path": str(POOL_MANIFEST.resolve()),
            "pool_manifest_sha256": _file_sha(POOL_MANIFEST),
            "meta_manifest_path": str(META_MANIFEST.resolve()),
            "meta_manifest_sha256": _file_sha(META_MANIFEST),
            "resource_config_path": str(RESOURCE_CONFIG.resolve()),
            "resource_config_sha256": _file_sha(RESOURCE_CONFIG),
            "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
            "protocol": {
                "weighted_games_per_arm": len(selected_ids) * 2 * GAMES_PER_OPPONENT_SEAT,
                "same_seed_schedule": True,
                "workers_requested": workers,
                "worker_recycle_games": worker_recycle_games,
                "heldout_training_exposure": 0,
            },
        }
    )
    manifest_sha = _write_json_no_clobber(output / "candidate_manifest.json", manifest)

    # A 2-game-per-arm runtime smoke on one META_TRAIN opponent is enough to
    # reject import/deck/agent faults before spending the weighted block.
    smoke_ids = selected_ids[:1]
    smoke_candidate = _build_arm_games(
        candidate_id=CANDIDATE_ID,
        main_path=CANDIDATE_MAIN,
        policy_sha256=candidate_policy_sha,
        selected_ids=smoke_ids,
        base_seed=base_seed - 10,
        block_id=f"{SCHEMA}-smoke-candidate",
        repetitions=1,
        arm="candidate",
    )
    smoke_control = _build_arm_games(
        candidate_id=CONTROL_ID,
        main_path=CONTROL_MAIN,
        policy_sha256=control_policy_sha,
        selected_ids=smoke_ids,
        base_seed=base_seed - 10,
        block_id=f"{SCHEMA}-smoke-control",
        repetitions=1,
        arm="control",
    )
    _pair_gate(smoke_candidate, smoke_control)
    smoke = run_parallel_cabt_evaluation(
        tuple(smoke_candidate + smoke_control),
        output_dir=output / "runtime-smoke" / "evaluation",
        max_workers=2,
        worker_recycle_games=16,
        overwrite=False,
    )
    smoke_summary = _summary_for_rows(smoke["rows"])
    smoke_pass = all(
        int(value["completed_games"]) == 2 and int(value["faults"]) == 0
        for value in smoke_summary.values()
    ) and set(smoke_summary) == {"candidate", "control"}
    smoke_payload = {
        "schema_version": f"{SCHEMA}-runtime-smoke",
        "arms": smoke_summary,
        "smoke_pass": smoke_pass,
        "performance_score_allowed": False,
        "authority": dict(AUTHORITY_FALSE),
    }
    smoke_sha = _write_json_no_clobber(output / "runtime_smoke.json", smoke_payload)
    if not smoke_pass:
        raise RuntimeError("phase-conditioned runtime smoke failed; weighted block not started")

    candidate_games = _build_arm_games(
        candidate_id=CANDIDATE_ID,
        main_path=CANDIDATE_MAIN,
        policy_sha256=candidate_policy_sha,
        selected_ids=selected_ids,
        base_seed=base_seed,
        block_id=f"{SCHEMA}-weighted48-candidate",
        repetitions=GAMES_PER_OPPONENT_SEAT,
        arm="candidate",
    )
    control_games = _build_arm_games(
        candidate_id=CONTROL_ID,
        main_path=CONTROL_MAIN,
        policy_sha256=control_policy_sha,
        selected_ids=selected_ids,
        base_seed=base_seed,
        block_id=f"{SCHEMA}-weighted48-control",
        repetitions=GAMES_PER_OPPONENT_SEAT,
        arm="control",
    )
    _pair_gate(candidate_games, control_games)
    all_games = tuple(candidate_games + control_games)
    if len({game.game_id for game in all_games}) != len(all_games):
        raise RuntimeError("weighted game IDs are not globally unique")
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=workers, snapshot=before)
    admitted_workers = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted_workers < 1:
        raise RuntimeError("resource governor admitted no workers")
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        all_games,
        output_dir=output / "weighted48" / "evaluation",
        max_workers=admitted_workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    summaries = _summary_for_rows(result["rows"])
    if set(summaries) != {"candidate", "control"}:
        raise RuntimeError("weighted arm metadata was not preserved")
    candidate_score = float(summaries["candidate"]["score_rate"])
    control_score = float(summaries["control"]["score_rate"])
    payload = {
        "schema_version": f"{SCHEMA}-weighted48-summary",
        "manifest_sha256": manifest_sha,
        "runtime_smoke_sha256": smoke_sha,
        "arms": summaries,
        "candidate_score_rate": candidate_score,
        "control_score_rate": control_score,
        "delta_points": (candidate_score - control_score) * 100.0,
        "all_faults_zero": int(result["summary"]["faults"]) == 0,
        "identity_gate": True,
        "heldout_training_exposure": 0,
        "authority": dict(AUTHORITY_FALSE),
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
            "memory_available_after_bytes": ResourceSnapshot.collect().memory_available_bytes,
        },
    }
    summary_sha = _write_json_no_clobber(output / "weighted48_summary.json", payload)
    md = (
        "# Rule v0 phase-conditioned policy weighted48\n\n"
        f"- control: {summaries['control']['wins']}-{summaries['control']['draws']}-{summaries['control']['losses']}\n"
        f"- candidate: {summaries['candidate']['wins']}-{summaries['candidate']['draws']}-{summaries['candidate']['losses']}\n"
        f"- delta: {(candidate_score - control_score) * 100.0:+.3f}pt; faults={result['summary']['faults']}\n"
        "- candidate status: candidate-only; no automatic common24/384\n"
    )
    md_sha = _write_bytes_no_clobber(output / "weighted48_summary.md", md.encode("utf-8"))
    final = {
        "schema_version": f"{SCHEMA}-final",
        "output_root": str(output.resolve()),
        "manifest_sha256": manifest_sha,
        "runtime_smoke_sha256": smoke_sha,
        "summary_sha256": summary_sha,
        "summary_md_sha256": md_sha,
        "delta_points": (candidate_score - control_score) * 100.0,
        "all_faults_zero": payload["all_faults_zero"],
        "candidate_status": "candidate_only",
        "authority": dict(AUTHORITY_FALSE),
    }
    _write_json_no_clobber(output / "final_summary.json", final)
    return final


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs/final-sprint-autonomous/rule-v0-phase-conditioned-policy-screen-v1-20260814")
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args(argv)
    result = execute_screen(**vars(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_ID",
    "CONTROL_ID",
    "DEFAULT_WORKERS",
    "DEFAULT_WORKER_RECYCLE_GAMES",
    "POLICY_ID",
    "build_manifest_payload",
    "execute_screen",
]
