#!/usr/bin/env python3
"""Research-only common24-96 guardrail for the positive Tomato mutation."""

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

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-tomato-common24-v1"
SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-weighted-deck-v1-20260814"
SOURCE_MANIFEST = SOURCE_ROOT / "candidate_manifest.json"
SOURCE_SUMMARY = SOURCE_ROOT / "weighted48_summary.json"
COMMON24_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
POOL_ROOT = ROOT / "opponents"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-ae-common24-v1-20260814"
AE_CANDIDATE_ID = "ae3075c2e0960eb5bbbcc3b3032dfeef7e3b83ddb9f995447506d9a502243ccb"
COMMON24_COUNT = 24
COMMON24_REPETITIONS = 2
COMMON24_GAMES_PER_ARM = COMMON24_COUNT * 2 * COMMON24_REPETITIONS
COMMON24_BASE_SEED = 22620000
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class TomatoCommon24Error(ValueError):
    """Raised when the common24 guardrail identity or schedule is open."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_no_clobber(path: Path, payload: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
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
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def validate_candidate_identity(candidate_id: str) -> None:
    if candidate_id != AE_CANDIDATE_ID:
        raise TomatoCommon24Error(f"only ae3075 candidate is allowed: {candidate_id}")


def validate_reference_ids(reference_ids: Sequence[str]) -> tuple[str, ...]:
    refs = tuple(reference_ids)
    if len(refs) != COMMON24_COUNT or len(set(refs)) != COMMON24_COUNT or any(type(item) is not str or not item for item in refs):
        raise TomatoCommon24Error("common24 guardrail requires exactly 24 unique nonempty IDs")
    return refs


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise TomatoCommon24Error("output must be a repository-contained final-sprint child")
    if resolved.exists() and any(resolved.iterdir()):
        raise TomatoCommon24Error("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_source() -> tuple[dict[str, object], dict[str, object], str]:
    if not SOURCE_MANIFEST.is_file() or not SOURCE_SUMMARY.is_file():
        raise TomatoCommon24Error("sealed Tomato weighted artifacts are missing")
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    summary = json.loads(SOURCE_SUMMARY.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("authority") != AUTHORITY_FALSE:
        raise TomatoCommon24Error("source manifest authority is open")
    if not isinstance(summary, dict) or summary.get("authority") != AUTHORITY_FALSE:
        raise TomatoCommon24Error("source weighted summary authority is open")
    validate_candidate_identity(AE_CANDIDATE_ID)
    row = next((item for item in manifest.get("candidates", ()) if isinstance(item, Mapping) and item.get("candidate_id") == AE_CANDIDATE_ID), None)
    if row is None:
        raise TomatoCommon24Error("ae3075 candidate is absent from source manifest")
    result = next((item for item in summary.get("candidates", ()) if isinstance(item, Mapping) and item.get("candidate_id") == AE_CANDIDATE_ID), None)
    if result is None or result.get("status") != "weighted_positive_candidate_only" or float(result.get("weighted_delta", 0.0)) <= 0.0:
        raise TomatoCommon24Error("ae3075 is not a sealed weighted-positive candidate")
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise TomatoCommon24Error("source parent malformed")
    for item in (parent, row):
        if _file_sha(Path(str(item["deck_path"]))) != item.get("deck_file_sha256"):
            raise TomatoCommon24Error("source deck changed after weighted screen")
    if _file_sha(Path(str(parent["policy_path"]))) != parent.get("policy_sha256"):
        raise TomatoCommon24Error("source policy changed after weighted screen")
    return manifest, dict(row), _file_sha(SOURCE_MANIFEST)


def _candidate_spec(parent: Mapping[str, object], deck_path: Path, deck_sha: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {"main_path": str(parent["policy_path"]), "deck_path": str(deck_path), "policy_sha256": str(parent["policy_sha256"]), "deck_sha256": deck_sha, "env": env, "biases": biases, "config_sha256": _config_sha(env, biases), "pool_root": str(POOL_ROOT.resolve())}


def build_common24_games(manifest: Mapping[str, object], candidate: Mapping[str, object]) -> tuple[object, ...]:
    validate_candidate_identity(str(candidate["candidate_id"]))
    config = json.loads(COMMON24_CONFIG.read_text(encoding="utf-8"))
    refs = validate_reference_ids(config.get("opponent_ids", ()) if isinstance(config, Mapping) else ())
    pool = load_opponent_pool_v1(POOL_ROOT)
    if set(refs) - set(pool):
        raise TomatoCommon24Error("common24 config contains unknown opponent IDs")
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise TomatoCommon24Error("parent malformed")
    specs = (("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])), ("candidate", Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"])))
    games: list[object] = []
    block_id = f"{SCHEMA}-96"
    for arm, deck_path, deck_sha in specs:
        built = build_native_candidate_games_v1(candidate_id=f"tomato-ae-{arm}", candidate=_candidate_spec(parent, deck_path, deck_sha), pool=pool, reference_ids=refs, games_per_opponent_seat=COMMON24_REPETITIONS, base_seed=COMMON24_BASE_SEED, block_id=block_id)
        games.extend(replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "common24_guardrail": True, "source_manifest_sha256": _file_sha(SOURCE_MANIFEST), "source_candidate_id": AE_CANDIDATE_ID, "common24_config_sha256": _file_sha(COMMON24_CONFIG), **AUTHORITY_FALSE}) for game in built)
    expected = 2 * COMMON24_GAMES_PER_ARM
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise TomatoCommon24Error("common24 game count or GID gate failed")
    grouped: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped["parent"]}
    candidate_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped["candidate"]}
    if parent_keys.keys() != candidate_keys.keys() or any(parent_keys[key].seed != candidate_keys[key].seed for key in parent_keys):
        raise TomatoCommon24Error("candidate/parent common24 seed-strata mismatch")
    return tuple(games)


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    output = _fresh_root(output)
    manifest, candidate, source_sha = _load_source()
    games = build_common24_games(manifest, candidate)
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise TomatoCommon24Error("ResourceGovernor blocked common24")
    destination = output / "common24-96" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    arms = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    if set(arms) != {"parent", "candidate"} or any(int(item["requested_games"]) != COMMON24_GAMES_PER_ARM for item in arms.values()):
        raise TomatoCommon24Error("common24 arm cardinality failed")
    parent_keys = {(str(row["opponent_id"]), int(row["seat"]), int(row["metadata"]["repetition"])): row for row in grouped["parent"]}
    candidate_keys = {(str(row["opponent_id"]), int(row["seat"]), int(row["metadata"]["repetition"])): row for row in grouped["candidate"]}
    if parent_keys.keys() != candidate_keys.keys() or any(parent_keys[key].get("seed") != candidate_keys[key].get("seed") for key in parent_keys):
        raise TomatoCommon24Error("final common24 paired seed-strata gate failed")
    delta = (float(arms["candidate"]["score_rate"]) - float(arms["parent"]["score_rate"])) * 100.0
    summary = {"schema_version": SCHEMA, "source_manifest_sha256": source_sha, "source_summary_sha256": _file_sha(SOURCE_SUMMARY), "source_candidate_id": AE_CANDIDATE_ID, "candidate_deck_file_sha256": candidate["deck_file_sha256"], "candidate_deck_multiset_sha256": candidate["deck_multiset_sha256"], "parent_deck_file_sha256": manifest["parent"]["deck_file_sha256"], "parent_policy_sha256": manifest["parent"]["policy_sha256"], "common24_config_sha256": _file_sha(COMMON24_CONFIG), "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(), "arms": arms, "delta_score_points_candidate_minus_parent": delta, "faults_total": result["summary"]["faults"], "fault_gate": int(result["summary"]["faults"]) == 0, "identity_gate": len({str(row["game_id"]) for row in result["rows"]}) == 2 * COMMON24_GAMES_PER_ARM, "paired_strata_gate": True, "seat_support": {arm: {str(seat): len([row for row in rows if int(row["seat"]) == seat]) for seat in (0, 1)} for arm, rows in grouped.items()}, "opponent_support": {arm: {opp: len([row for row in rows if str(row["opponent_id"]) == opp]) for opp in validate_reference_ids(json.loads(COMMON24_CONFIG.read_text())["opponent_ids"])} for arm, rows in grouped.items()}, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": result["summary"]["requested_games"], "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0, "worker_recycle_games": budget.recycle_games}, "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "next_gate": "384 only after explicit review; no automatic longrun"}
    summary["summary_sha256"] = _write_json_no_clobber(output / "common24_summary.json", summary)
    summary["summary_md_sha256"] = _write_text_no_clobber(output / "common24_summary.md", "# Tomato ae3075 common24-96\n\n" + f"- parent: {arms['parent']['wins']}-{arms['parent']['draws']}-{arms['parent']['losses']} ({arms['parent']['score_rate']:.6f})\n" + f"- candidate ae3075: {arms['candidate']['wins']}-{arms['candidate']['draws']}-{arms['candidate']['losses']} ({arms['candidate']['score_rate']:.6f})\n" + f"- delta: {delta:+.3f}pt; faults={summary['fault_gate']}; paired={summary['paired_strata_gate']}\n")
    summary["final_summary_sha256"] = _write_json_no_clobber(output / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output), "summary_sha256": summary["summary_sha256"], "summary_md_sha256": summary["summary_md_sha256"], "authority": dict(AUTHORITY_FALSE), "performance_run_started": True})
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


__all__ = ["AE_CANDIDATE_ID", "COMMON24_GAMES_PER_ARM", "TomatoCommon24Error", "build_common24_games", "execute", "validate_candidate_identity", "validate_reference_ids"]
