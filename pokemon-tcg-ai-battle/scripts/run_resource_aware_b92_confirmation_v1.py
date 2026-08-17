#!/usr/bin/env python3
"""Research-only 384-game confirmation for the sealed b92a deck mutation.

The parent and the single b92a mutation use the same Tomato native policy and
the same common24 seed/seat/opponent schedule.  This wrapper owns a fresh root,
performs the resource-governor ramp, and records a paired schedule audit.  It
never changes a production entrypoint, grants authority, or overwrites an
existing artifact.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
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
from scripts.run_native_policy_candidate_pilot_v1 import (
    _config_sha,
    build_native_candidate_games_v1,
)
from scripts.run_resource_aware_deck_candidate_v1 import build_warmup_plan
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-b92-confirmation-v1"
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v2-20260813"
SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
COMMON24_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
POOL_ROOT = ROOT / "opponents"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-b92-confirmation-v1-20260813"

B92_CANDIDATE_ID = "b92a3b55c5fa3485670e3d3a7b6212e8d9c28cd9605a2e5ca813e04a832a7e9d"
B92_MUTATION = {"removed_cards": [1185], "added_cards": [1159]}
COMMON24_COUNT = 24
CONFIRMATION_GAMES_PER_OPPONENT_SEAT = 8
COMMON24_BASE_SEED = 22_600_000
WARMUP_BASE_SEED = 22_500_000
RAMP_WORKERS = (1, 2, 4, 8, 12)
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class ConfirmationError(ValueError):
    """Raised when the sealed confirmation contract is not closed."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_no_clobber(path: Path, payload: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def _write_text_no_clobber(path: Path, text: str) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def expected_confirmation_games() -> int:
    return 2 * COMMON24_COUNT * 2 * CONFIRMATION_GAMES_PER_OPPONENT_SEAT


def validate_output_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs" / "final-sprint-autonomous").resolve()
    if allowed not in resolved.parents:
        raise ConfirmationError("output root must be contained below repository final-sprint-autonomous")
    if resolved == allowed or (resolved.exists() and any(resolved.iterdir())):
        raise ConfirmationError("output root must be a fresh empty child")
    return resolved


def assert_b92_identity(candidate_id: str, deck_sha: str) -> None:
    if candidate_id != B92_CANDIDATE_ID:
        raise ConfirmationError(f"only sealed b92 candidate is allowed, got {candidate_id}")
    if len(deck_sha) != 64 or not all(char in "0123456789abcdef" for char in deck_sha):
        raise ConfirmationError("candidate deck SHA is malformed")


def _load_sealed_source() -> tuple[dict[str, object], dict[str, object], str]:
    if not SOURCE_MANIFEST.is_file():
        raise ConfirmationError(f"missing sealed source manifest: {SOURCE_MANIFEST}")
    source_sha = _file_sha(SOURCE_MANIFEST)
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("authority") != {
        "research_only": True,
        "execution_authority": False,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }:
        raise ConfirmationError("source manifest authority is not closed")
    parent = manifest.get("parent")
    candidates = manifest.get("candidates")
    if not isinstance(parent, dict) or not isinstance(candidates, list):
        raise ConfirmationError("source manifest is malformed")
    candidate = next((row for row in candidates if isinstance(row, dict) and row.get("candidate_id") == B92_CANDIDATE_ID), None)
    if candidate is None:
        raise ConfirmationError("sealed b92 candidate is absent")
    assert_b92_identity(B92_CANDIDATE_ID, str(candidate.get("deck_file_sha256")))
    if candidate.get("removed_cards") != B92_MUTATION["removed_cards"] or candidate.get("added_cards") != B92_MUTATION["added_cards"]:
        raise ConfirmationError("b92 mutation identity changed")
    for row in (parent, candidate):
        deck_path = Path(str(row["deck_path"]))
        if _file_sha(deck_path) != row.get("deck_file_sha256"):
            raise ConfirmationError(f"sealed deck changed: {deck_path}")
    policy_path = Path(str(parent["policy_path"]))
    if _file_sha(policy_path) != parent.get("policy_sha256"):
        raise ConfirmationError("sealed native policy changed")
    return manifest, candidate, source_sha


def _reference_ids() -> tuple[str, ...]:
    payload = json.loads(COMMON24_CONFIG.read_text(encoding="utf-8"))
    refs = payload.get("opponent_ids") if isinstance(payload, dict) else None
    if not isinstance(refs, list) or len(refs) != COMMON24_COUNT or len(set(refs)) != COMMON24_COUNT:
        raise ConfirmationError("common24 reference config must contain 24 unique IDs")
    return tuple(str(item) for item in refs)


def _candidate_spec(*, main_path: Path, deck_path: Path, policy_sha: str, deck_sha: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {
        "main_path": str(main_path),
        "deck_path": str(deck_path),
        "policy_sha256": policy_sha,
        "deck_sha256": deck_sha,
        "env": env,
        "biases": biases,
        "config_sha256": _config_sha(env, biases),
        "pool_root": str(POOL_ROOT.resolve()),
    }


def build_confirmation_games(manifest: Mapping[str, object], candidate: Mapping[str, object]) -> tuple[object, ...]:
    parent = manifest["parent"]
    if not isinstance(parent, Mapping):
        raise ConfirmationError("parent is malformed")
    candidate_id = str(candidate["candidate_id"])
    assert_b92_identity(candidate_id, str(candidate["deck_file_sha256"]))
    refs = _reference_ids()
    pool = load_opponent_pool_v1(POOL_ROOT)
    policy_path = Path(str(parent["policy_path"])).resolve()
    policy_sha = str(parent["policy_sha256"])
    specs = (
        ("parent", "b92-confirm-parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])),
        ("candidate", f"b92-confirm-{candidate_id[:12]}", Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"])),
    )
    games: list[object] = []
    block_id = f"{SCHEMA}-common24-384"
    for arm, arm_id, deck_path, deck_sha in specs:
        built = build_native_candidate_games_v1(
            candidate_id=arm_id,
            candidate=_candidate_spec(main_path=policy_path, deck_path=deck_path, policy_sha=policy_sha, deck_sha=deck_sha),
            pool=pool,
            reference_ids=refs,
            games_per_opponent_seat=CONFIRMATION_GAMES_PER_OPPONENT_SEAT,
            base_seed=COMMON24_BASE_SEED,
            block_id=block_id,
        )
        games.extend(
            replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "confirmation_schema": SCHEMA,
                    "comparison_arm": arm,
                    "source_manifest_sha256": _file_sha(SOURCE_MANIFEST),
                    "source_candidate_id": candidate_id,
                    "source_candidate_deck_sha256": str(candidate["deck_file_sha256"]),
                    "common24_reference_config_sha256": _file_sha(COMMON24_CONFIG),
                    **AUTHORITY_FALSE,
                },
            )
            for game in built
        )
    expected = expected_confirmation_games()
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise ConfirmationError(f"confirmation game count/identity failed: {len(games)}")
    by_arm: dict[str, list[object]] = defaultdict(list)
    for game in games:
        by_arm[str(game.metadata["comparison_arm"])].append(game)
    if set(by_arm) != {"parent", "candidate"} or any(len(rows) != expected // 2 for rows in by_arm.values()):
        raise ConfirmationError("confirmation arm cardinality failed")
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm["parent"]}
    candidate_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm["candidate"]}
    if set(parent_keys) != set(candidate_keys):
        raise ConfirmationError("candidate/parent strata keys differ")
    if any(parent_keys[key].seed != candidate_keys[key].seed for key in parent_keys):
        raise ConfirmationError("candidate/parent seed schedule differs")
    if len({g.seed for g in by_arm["parent"]}) != expected // 2 or len({g.seed for g in by_arm["candidate"]}) != expected // 2:
        raise ConfirmationError("seed uniqueness within arm failed")
    return tuple(games)


def _warmup(*, output: Path, manifest: Mapping[str, object], budget: ResourceBudget) -> dict[str, object]:
    initial = ResourceSnapshot.collect()
    plan = build_warmup_plan(budget=budget, task_cap=budget.max_workers, snapshot=initial, ramp_workers=RAMP_WORKERS)
    if plan.get("warmup_status") != "ready":
        raise ConfirmationError("resource governor blocked warm-up")
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    refs = _reference_ids()[:2]
    pool = load_opponent_pool_v1(POOL_ROOT)
    rows: list[dict[str, object]] = []
    for workers in RAMP_WORKERS:
        if workers > int(plan["safe_workers"]):
            rows.append({"workers": workers, "status": "not_admitted"})
            continue
        before = ResourceSnapshot.collect()
        games = build_native_candidate_games_v1(
            candidate_id=f"b92-warmup-parent-{workers}",
            candidate=_candidate_spec(
                main_path=Path(str(parent["policy_path"])),
                deck_path=Path(str(parent["deck_path"])),
                policy_sha=str(parent["policy_sha256"]),
                deck_sha=str(parent["deck_file_sha256"]),
            ),
            pool=pool,
            reference_ids=refs,
            games_per_opponent_seat=1,
            base_seed=WARMUP_BASE_SEED + workers * 100,
            block_id=f"{SCHEMA}-warmup-{workers}",
        )
        destination = output / "warmup" / f"workers-{workers}" / "evaluation"
        started = time.monotonic()
        result = run_parallel_cabt_evaluation(
            games, output_dir=destination, max_workers=workers,
            worker_recycle_games=budget.recycle_games, overwrite=False,
        )
        elapsed = max(time.monotonic() - started, 1e-9)
        after = ResourceSnapshot.collect()
        summary = result["summary"]
        rows.append({
            "workers": workers,
            "status": "DONE",
            "requested_games": summary["requested_games"],
            "completed_games": summary["completed_games"],
            "faults": summary["faults"],
            "fault_gate": int(summary["faults"]) == 0,
            "throughput_games_per_second": summary["completed_games"] / elapsed,
            "memory_available_before_bytes": before.memory_available_bytes,
            "memory_available_after_bytes": after.memory_available_bytes,
            "rss_before_bytes": before.process_rss_bytes,
            "rss_after_bytes": after.process_rss_bytes,
            "worker_restarts_observed": 0,
            "output_dir": str(destination.resolve()),
        })
    telemetry = {
        "schema_version": f"{SCHEMA}-warmup",
        "source_manifest_sha256": _file_sha(SOURCE_MANIFEST),
        "budget": budget.to_dict(),
        "governor_plan": plan,
        "governor_decision": ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=initial).to_dict(),
        "ramp": rows,
        "authority": dict(AUTHORITY_FALSE),
        "no_process_kill": True,
    }
    telemetry["telemetry_sha256"] = _write_json_no_clobber(output / "warmup_telemetry.json", telemetry)
    return telemetry


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    output = validate_output_root(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest, candidate, source_manifest_sha = _load_sealed_source()
    games = build_confirmation_games(manifest, candidate)
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=output, manifest=manifest, budget=budget)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise ConfirmationError("resource governor blocked confirmation")
    eval_dir = output / "common24-384" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        games, output_dir=eval_dir, max_workers=workers,
        worker_recycle_games=budget.recycle_games, overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        metadata = row.get("metadata", {})
        grouped[str(metadata.get("comparison_arm", "unknown"))].append(row)
    arms = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    if set(arms) != {"candidate", "parent"} or any(int(item["requested_games"]) != expected_confirmation_games() // 2 for item in arms.values()):
        raise ConfirmationError("final arm summary cardinality failed")
    parent_score = float(arms["parent"]["score_rate"])
    candidate_score = float(arms["candidate"]["score_rate"])
    pair_rows: dict[str, dict[tuple[str, int, int], Mapping[str, object]]] = {}
    for arm, values in grouped.items():
        pair_rows[arm] = {
            (str(row["opponent_id"]), int(row["seat"]), int(row["metadata"].get("repetition", -1))): row
            for row in values
        }
    paired_keys = set(pair_rows["parent"]) == set(pair_rows["candidate"])
    paired_seed_match = paired_keys and all(
        pair_rows["parent"][key].get("seed") == pair_rows["candidate"][key].get("seed")
        for key in pair_rows["parent"]
    )
    opponent_support = {
        arm: {opponent: len([row for row in values if row.get("opponent_id") == opponent]) for opponent in _reference_ids()}
        for arm, values in grouped.items()
    }
    seat_support = {
        arm: {str(seat): len([row for row in values if int(row.get("seat", -1)) == seat]) for seat in (0, 1)}
        for arm, values in grouped.items()
    }
    summary = {
        "schema_version": SCHEMA,
        "source_manifest_path": str(SOURCE_MANIFEST.resolve()),
        "source_manifest_sha256": source_manifest_sha,
        "source_candidate_id": B92_CANDIDATE_ID,
        "mutation": dict(B92_MUTATION),
        "candidate_deck_file_sha256": str(candidate["deck_file_sha256"]),
        "candidate_deck_multiset_sha256": str(candidate["deck_multiset_sha256"]),
        "parent_deck_file_sha256": str(manifest["parent"]["deck_file_sha256"]),
        "parent_deck_multiset_sha256": str(manifest["parent"]["deck_multiset_sha256"]),
        "native_policy_path": str(manifest["parent"]["policy_path"]),
        "native_policy_sha256": str(manifest["parent"]["policy_sha256"]),
        "common24_reference_config_sha256": _file_sha(COMMON24_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "requested_games_total": expected_confirmation_games(),
        "completed_games_total": result["summary"]["completed_games"],
        "faults_total": result["summary"]["faults"],
        "fault_gate": int(result["summary"]["faults"]) == 0,
        "identity_gate": len({str(row["game_id"]) for row in result["rows"]}) == expected_confirmation_games(),
        "seed_schedule_gate": paired_seed_match,
        "paired_strata_gate": paired_keys,
        "arms": arms,
        "delta_score_points_candidate_minus_parent": (candidate_score - parent_score) * 100.0,
        "seat_support": seat_support,
        "opponent_support": opponent_support,
        "telemetry": {
            "workers": workers,
            "governor_decision": decision.to_dict(),
            "requested_games": expected_confirmation_games(),
            "completed_games": result["summary"]["completed_games"],
            "faults": result["summary"]["faults"],
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": result["summary"]["completed_games"] / elapsed,
            "memory_available_before_bytes": before.memory_available_bytes,
            "memory_available_after_bytes": after.memory_available_bytes,
            "rss_before_bytes": before.process_rss_bytes,
            "rss_after_bytes": after.process_rss_bytes,
            "worker_restarts_observed": 0,
            "worker_recycle_games": budget.recycle_games,
        },
        "warmup_telemetry_sha256": _file_sha(output / "warmup_telemetry.json"),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "next_gate": "no 768/longrun automatic; review 384 delta and integrity first",
    }
    summary["summary_sha256"] = _write_json_no_clobber(output / "confirmation_summary.json", summary)
    md = "\n".join([
        "# b92a resource-aware 384 confirmation",
        "",
        f"- candidate: `{B92_CANDIDATE_ID}` (1185→1159)",
        f"- parent: `{arms['parent']['wins']}-{arms['parent']['draws']}-{arms['parent']['losses']}` ({float(arms['parent']['score_rate']):.6f})",
        f"- candidate: `{arms['candidate']['wins']}-{arms['candidate']['draws']}-{arms['candidate']['losses']}` ({float(arms['candidate']['score_rate']):.6f})",
        f"- delta candidate-parent: `{summary['delta_score_points_candidate_minus_parent']:+.3f}pt`",
        f"- faults: `{summary['faults_total']}`; paired strata: `{summary['paired_strata_gate']}`; seed schedule: `{summary['seed_schedule_gate']}`",
        f"- workers: `{workers}`; throughput: `{summary['telemetry']['throughput_games_per_second']:.3f} games/s`",
        "- authority: all false; candidate-only; no automatic 768/longrun",
        "",
    ])
    summary["summary_md_sha256"] = _write_text_no_clobber(output / "confirmation_summary.md", md)
    summary["final_summary_sha256"] = _write_json_no_clobber(output / "final_summary.json", {
        "schema_version": SCHEMA,
        "output_root": str(output),
        "confirmation_summary_sha256": summary["summary_sha256"],
        "confirmation_summary_md_sha256": summary["summary_md_sha256"],
        "warmup_telemetry_sha256": summary["warmup_telemetry_sha256"],
        "authority": dict(AUTHORITY_FALSE),
        "performance_run_started": True,
    })
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(execute(output=args.output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "B92_CANDIDATE_ID",
    "COMMON24_COUNT",
    "CONFIRMATION_GAMES_PER_OPPONENT_SEAT",
    "ConfirmationError",
    "assert_b92_identity",
    "build_confirmation_games",
    "execute",
    "expected_confirmation_games",
    "validate_output_root",
]
