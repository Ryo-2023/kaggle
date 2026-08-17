#!/usr/bin/env python3
"""Research-only Tomato policy threshold screen on a fixed native deck.

The upstream Tomato policy is never edited.  Each candidate is an isolated
copy with only the sealed ``_ICE_CREAM_HP_THRESHOLD`` mapping changed; the
candidate and native parent use the same META_TRAIN weighted schedule.  This
lane is deliberately bounded to weighted48 and never starts common24, 384,
longrun, training, promotion, or submission.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts import run_resource_aware_tomato_surface_weighted_v1 as surface
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-tomato-policy-threshold-weighted-v1"
TOMATO_PARENT_POLICY = surface.TOMATO_PARENT_POLICY
TOMATO_PARENT_DECK = surface.TOMATO_PARENT_DECK
TOMATO_PARENT_POLICY_SHA256 = surface.TOMATO_PARENT_POLICY_SHA256
TOMATO_PARENT_DECK_SHA256 = surface.TOMATO_PARENT_DECK_SHA256
META_MANIFEST = surface.META_MANIFEST
POOL_ROOT = surface.POOL_ROOT
RESOURCE_CONFIG = surface.RESOURCE_CONFIG
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-policy-threshold-weighted-v1-20260814"
WEIGHTED_BASE_SEED = 22810000
WARMUP_BASE_SEED = 22800000
WEIGHTED_GAMES_PER_OPPONENT_SEAT = 2
RAMP_WORKERS = (1, 2, 4, 8, 12)
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}
_THRESHOLD_KEYS = ("lucario", "starmie", "crustle", "hop", "generic")
_THRESHOLD_BLOCK = re.compile(
    r"_ICE_CREAM_HP_THRESHOLD\s*=\s*\{\n"
    r"\s*\"lucario\":\s*270,\n"
    r"\s*\"starmie\":\s*210,\n"
    r"\s*\"crustle\":\s*120,\n"
    r"\s*\"hop\":\s*220,\n"
    r"\s*\"generic\":\s*230,\n\}",
)


class PolicyThresholdError(ValueError):
    """Raised when a research policy copy is not exactly bounded."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write_bytes_exclusive(path: Path, raw: bytes) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
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
    return _sha(path)


def _write_json(path: Path, payload: object) -> str:
    return _write_bytes_exclusive(path, (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def materialize_threshold_policy_copy(*, source: Path, destination: Path, thresholds: Mapping[str, int]) -> str:
    """Copy one policy and replace only its exact threshold mapping."""

    if _sha(source) != TOMATO_PARENT_POLICY_SHA256:
        raise PolicyThresholdError("Tomato source policy bytes changed")
    if set(thresholds) != set(_THRESHOLD_KEYS):
        raise PolicyThresholdError("threshold keys must exactly match the native mapping")
    checked: dict[str, int] = {}
    for key in _THRESHOLD_KEYS:
        value = thresholds[key]
        if type(value) is not int or not 0 <= value <= 500:
            raise PolicyThresholdError(f"threshold {key} must be an integer in [0,500]")
        checked[key] = value
    source_text = source.read_text(encoding="utf-8")
    replacement = (
        "_ICE_CREAM_HP_THRESHOLD = {\n"
        + "".join(f'    "{key}": {checked[key]},\n' for key in _THRESHOLD_KEYS)
        + "}"
    )
    updated, count = _THRESHOLD_BLOCK.subn(replacement, source_text, count=1)
    if count != 1 or updated == source_text:
        raise PolicyThresholdError("sealed threshold mapping was not replaced exactly once")
    return _write_bytes_exclusive(destination, updated.encode("utf-8"))


def build_threshold_variants() -> tuple[dict[str, object], ...]:
    base = {"lucario": 270, "starmie": 210, "crustle": 120, "hop": 220, "generic": 230}
    variants = (
        ("ice-threshold-lower-v1", {key: value - 20 for key, value in base.items()}),
        ("ice-threshold-higher-v1", {key: value + 20 for key, value in base.items()}),
    )
    return tuple(
        {
            "candidate_id": candidate_id,
            "parameter_name": "_ICE_CREAM_HP_THRESHOLD",
            "thresholds": thresholds,
            "threshold_config_sha256": hashlib.sha256(_canonical(thresholds)).hexdigest(),
            "policy_sha256": hashlib.sha256(_canonical({"source": TOMATO_PARENT_POLICY_SHA256, "thresholds": thresholds})).hexdigest(),
            **AUTHORITY_FALSE,
        }
        for candidate_id, thresholds in variants
    )


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise PolicyThresholdError("output must be below final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise PolicyThresholdError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _references(subset: Mapping[str, object]) -> tuple[str, ...]:
    refs = tuple(str(item) for item in subset["selected_ids"])
    if len(refs) != 12 or len(set(refs)) != 12:
        raise PolicyThresholdError("META_TRAIN subset must contain 12 unique IDs")
    return refs


def _policy_spec(*, policy_path: Path, policy_sha: str, deck_path: Path, deck_sha: str, arm: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {
        "main_path": str(policy_path),
        "deck_path": str(deck_path),
        "policy_sha256": policy_sha,
        "deck_sha256": deck_sha,
        "env": env,
        "biases": biases,
        "config_sha256": _config_sha(env, biases),
        "pool_root": str(POOL_ROOT.resolve()),
        "policy_threshold_arm": arm,
    }


def _write_deck_copy(source: Path, destination: Path) -> str:
    return _write_bytes_exclusive(destination, source.read_bytes())


def materialize_manifest(*, output: Path) -> dict[str, object]:
    output = _fresh_root(output)
    if _sha(TOMATO_PARENT_POLICY) != TOMATO_PARENT_POLICY_SHA256 or _sha(TOMATO_PARENT_DECK) != TOMATO_PARENT_DECK_SHA256:
        raise PolicyThresholdError("Tomato parent identity changed")
    subset = surface.load_meta_train_subset(META_MANIFEST)
    variants = build_threshold_variants()
    materialized: list[dict[str, object]] = []
    for variant in variants:
        candidate_id = str(variant["candidate_id"])
        policy_path = output / "policies" / candidate_id / "main.py"
        deck_sidecar = policy_path.parent / "deck.csv"
        policy_sha = materialize_threshold_policy_copy(source=TOMATO_PARENT_POLICY, destination=policy_path, thresholds=variant["thresholds"])
        _write_deck_copy(TOMATO_PARENT_DECK, deck_sidecar)
        materialized.append({**variant, "policy_path": str(policy_path.resolve()), "policy_sha256": policy_sha, "deck_sidecar_path": str(deck_sidecar.resolve())})
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "TOMATO_NATIVE_PARENT_ICE_CREAM_THRESHOLD_WEIGHTED48",
        "parent": {
            "candidate_id": "tomatomato_archaludon-native",
            "policy_path": str(TOMATO_PARENT_POLICY.resolve()),
            "policy_sha256": TOMATO_PARENT_POLICY_SHA256,
            "deck_path": str(TOMATO_PARENT_DECK.resolve()),
            "deck_sha256": TOMATO_PARENT_DECK_SHA256,
            **AUTHORITY_FALSE,
        },
        "variants": materialized,
        "meta_train_subset": subset,
        "protocol": {
            "weighted_games_per_arm": len(subset["selected_ids"]) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "warmup_ramp_workers": list(RAMP_WORKERS),
            "weighted_base_seed": WEIGHTED_BASE_SEED,
            "common24_auto_start": False,
            "confirmation_auto_start": False,
        },
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "pool_manifest_sha256": _sha(POOL_ROOT / "pool_manifest.json"),
        "resource_budget_sha256": _sha(RESOURCE_CONFIG),
        **AUTHORITY_FALSE,
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    return {**manifest, "manifest_sha256": _write_json(output / "candidate_manifest.json", manifest), "output_root": str(output)}


def _build_games(*, manifest: Mapping[str, object], arm: str, policy_path: Path, policy_sha: str, deck_path: Path, deck_sha: str, refs: Sequence[str]) -> tuple[object, ...]:
    pool = load_opponent_pool_v1(POOL_ROOT)
    spec = _policy_spec(policy_path=policy_path, policy_sha=policy_sha, deck_path=deck_path, deck_sha=deck_sha, arm=arm)
    built = build_native_candidate_games_v1(
        candidate_id=f"tomato-threshold-{arm}",
        candidate=spec,
        pool=pool,
        reference_ids=refs,
        games_per_opponent_seat=WEIGHTED_GAMES_PER_OPPONENT_SEAT,
        base_seed=WEIGHTED_BASE_SEED,
        block_id=f"{SCHEMA}-weighted48",
    )
    return tuple(replace_game(game, arm=arm) for game in built)


def replace_game(game: object, *, arm: str) -> object:
    # Avoid importing a concrete evaluator dataclass solely for a metadata update.
    from dataclasses import replace
    return replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "weighted_meta_train": True, **AUTHORITY_FALSE})


def _weighted(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    numerator = denominator = 0.0
    per_opponent: dict[str, object] = {}
    for opponent, weight in weights.items():
        values = [row for row in rows if str(row.get("opponent_id")) == opponent]
        score = sum(1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0 for row in values)
        rate = score / len(values) if values else None
        per_opponent[opponent] = {"weight": weight, "games": len(values), "rate": rate}
        if rate is not None:
            numerator += float(weight) * rate
            denominator += float(weight)
    aggregate = aggregate_ledger_v1(rows)
    return {**aggregate, "weighted_meta_score": numerator / denominator if denominator else None, "per_opponent": per_opponent, "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows), "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows), "seat_counts": {str(seat): len([row for row in rows if int(row.get("seat", -1)) == seat]) for seat in (0, 1)}}


def _warmup(*, output: Path, budget: ResourceBudget) -> dict[str, object]:
    snapshot = ResourceSnapshot.collect()
    plan = surface.build_warmup_plan(budget=budget, task_cap=budget.max_workers, snapshot=snapshot, ramp_workers=RAMP_WORKERS)
    if plan.get("warmup_status") != "ready":
        raise PolicyThresholdError("ResourceGovernor blocked warmup")
    pool = load_opponent_pool_v1(POOL_ROOT)
    subset = surface.load_meta_train_subset(META_MANIFEST)
    refs = _references(subset)[-2:]
    records: list[dict[str, object]] = []
    for workers in RAMP_WORKERS:
        if workers > int(plan["safe_workers"]):
            records.append({"workers": workers, "status": "not_admitted"})
            continue
        before = ResourceSnapshot.collect()
        spec = _policy_spec(policy_path=TOMATO_PARENT_POLICY, policy_sha=TOMATO_PARENT_POLICY_SHA256, deck_path=TOMATO_PARENT_DECK, deck_sha=TOMATO_PARENT_DECK_SHA256, arm=f"warmup-{workers}")
        games = build_native_candidate_games_v1(candidate_id=f"tomato-threshold-warmup-{workers}", candidate=spec, pool=pool, reference_ids=refs, games_per_opponent_seat=1, base_seed=WARMUP_BASE_SEED + workers * 100, block_id=f"{SCHEMA}-warmup-{workers}")
        destination = output / "warmup" / f"workers-{workers}" / "evaluation"
        started = time.monotonic()
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
        elapsed = max(time.monotonic() - started, 1e-9)
        after = ResourceSnapshot.collect()
        summary = result["summary"]
        records.append({"workers": workers, "status": "DONE", "requested_games": summary["requested_games"], "completed_games": summary["completed_games"], "faults": summary["faults"], "fault_gate": int(summary["faults"]) == 0, "throughput_games_per_second": summary["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0})
    telemetry = {"schema_version": f"{SCHEMA}-warmup", "budget": budget.to_dict(), "governor_plan": plan, "governor_decision": ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=snapshot).to_dict(), "ramp": records, "authority": dict(AUTHORITY_FALSE), "no_process_kill": True}
    telemetry["telemetry_sha256"] = _write_json(output / "warmup_telemetry.json", telemetry)
    return telemetry


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    manifest = materialize_manifest(output=output)
    output_path = Path(str(manifest["output_root"]))
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=output_path, budget=budget)
    subset = manifest["meta_train_subset"]
    refs = _references(subset)
    parent = manifest["parent"]
    specs: list[tuple[str, Path, str, Path, str]] = [("parent", Path(str(parent["policy_path"])), str(parent["policy_sha256"]), Path(str(parent["deck_path"])), str(parent["deck_sha256"]))]
    for row in manifest["variants"]:
        specs.append((str(row["candidate_id"]), Path(str(row["policy_path"])), str(row["policy_sha256"]), Path(str(row["deck_sidecar_path"])), TOMATO_PARENT_DECK_SHA256))
    games: list[object] = []
    for arm, policy_path, policy_sha, deck_path, deck_sha in specs:
        games.extend(_build_games(manifest=manifest, arm=arm, policy_path=policy_path, policy_sha=policy_sha, deck_path=deck_path, deck_sha=deck_sha, refs=refs))
    expected = len(specs) * len(refs) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise PolicyThresholdError("weighted game count/GID gate failed")
    grouped: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped["parent"]}
    for arm in sorted(set(grouped) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise PolicyThresholdError(f"paired schedule mismatch: {arm}")
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise PolicyThresholdError("ResourceGovernor blocked weighted48")
    destination = output_path / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    final_grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        final_grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    weights = {str(key): float(value) for key, value in subset["selected_weights"].items()}
    arms = {arm: _weighted(rows, weights) for arm, rows in sorted(final_grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates = []
    for arm in sorted(set(arms) - {"parent"}):
        row = next(item for item in manifest["variants"] if item["candidate_id"] == arm)
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        candidates.append({"candidate_id": arm, "policy_sha256": row["policy_sha256"], "threshold_config_sha256": row["threshold_config_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "paired_strata_gate": True, "status": "weighted_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0 else "candidate_only"})
    summary = {"schema_version": f"{SCHEMA}-weighted48", "manifest_sha256": _sha(output_path / "candidate_manifest.json"), "warmup_telemetry_sha256": warmup["telemetry_sha256"], "weighted_subset_sha256": subset["subset_sha256"], "arms": arms, "parent_weighted_meta_score": parent_score, "candidates": candidates, "all_faults_zero": int(result["summary"]["faults"]) == 0, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": expected, "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_recycle_games": budget.recycle_games}, **AUTHORITY_FALSE, "candidate_status": "candidate_only", "next_gate": "positive weighted candidates only; no automatic common24/384/longrun"}
    summary["summary_sha256"] = _write_json(output_path / "weighted48_summary.json", summary)
    summary["summary_md_sha256"] = surface._write_text_no_clobber(output_path / "weighted48_summary.md", "# Tomato policy threshold weighted48\n\n" + "\n".join(f"- {row['candidate_id']}: {row['weighted_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates) + "\n")
    summary["final_summary_sha256"] = _write_json(output_path / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output_path), "weighted_summary_sha256": summary["summary_sha256"], "weighted_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup["telemetry_sha256"], **AUTHORITY_FALSE, "performance_run_started": True})
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


__all__ = ["OUTPUT_DEFAULT", "PolicyThresholdError", "TOMATO_PARENT_POLICY", "TOMATO_PARENT_POLICY_SHA256", "build_threshold_variants", "execute", "materialize_manifest", "materialize_threshold_policy_copy"]
