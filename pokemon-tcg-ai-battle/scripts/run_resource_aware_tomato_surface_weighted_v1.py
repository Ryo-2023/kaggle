#!/usr/bin/env python3
"""Research-only exact Tomato-parent 1244 overlay weighted screen.

This lane evaluates only the two sealed one-card surfaces 1244→1123 and
1244→1252.  It excludes every previously materialized deck multiset, runs a
ResourceGovernor ramp, then compares both candidates with the Tomato native
parent on the same META_TRAIN weighted strata.  It never auto-starts common24,
384, longrun, submission, or any production entrypoint.
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

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1, deck_multiset_identity_v1, validate_mutation_v1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1
from scripts.run_resource_aware_deck_candidate_v1 import build_warmup_plan
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-tomato-surface-weighted-v1"
TOMATO_PARENT_ID = "tomatomato_archaludon-native"
TOMATO_PARENT_DECK = ROOT / "opponents/tomatomato_archaludon/deck.csv"
TOMATO_PARENT_POLICY = ROOT / "opponents/tomatomato_archaludon/main.py"
TOMATO_PARENT_DECK_SHA256 = "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"
TOMATO_PARENT_POLICY_SHA256 = "8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_ROOT = ROOT / "opponents"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-surface-weighted-v1-20260814"
SURFACE_SWAPS = ((1244, 1123), (1244, 1252))
MAX_CANDIDATES = 2
RAMP_WORKERS = (1, 2, 4, 8, 12)
WARMUP_BASE_SEED = 22700000
WEIGHTED_BASE_SEED = 22710000
WEIGHTED_GAMES_PER_OPPONENT_SEAT = 2
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class SurfaceWeightedDeckError(ValueError):
    """Raised when the exact overlay screen contract is not closed."""


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


def validate_surface_parent(deck_sha256: str) -> None:
    if deck_sha256 != TOMATO_PARENT_DECK_SHA256:
        raise SurfaceWeightedDeckError("Tomato parent deck SHA mismatch")
    if _file_sha(TOMATO_PARENT_DECK) != TOMATO_PARENT_DECK_SHA256:
        raise SurfaceWeightedDeckError("Tomato parent deck changed")
    if _file_sha(TOMATO_PARENT_POLICY) != TOMATO_PARENT_POLICY_SHA256:
        raise SurfaceWeightedDeckError("Tomato parent policy changed")


def _existing_multisets() -> set[str]:
    vocabulary = load_production_card_vocabulary_v1()
    identities: set[str] = set()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    # The current lane's own sealed candidate decks are verification artifacts,
    # not prior population members.  Ignore them so a read-only post-run
    # novelty probe remains deterministic; materialize_manifest still refuses
    # to reuse a non-empty root via _fresh_root().
    self_lane_root = OUTPUT_DEFAULT.resolve()
    paths = [
        path
        for path in paths
        if self_lane_root not in path.resolve().parents
    ]
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:
            raise SurfaceWeightedDeckError(f"malformed existing deck during novelty scan: {path}") from exc
        identities.add(deck_multiset_identity_v1(cards))
    return identities


def build_surface_candidates() -> tuple[dict[str, object], ...]:
    vocabulary = load_production_card_vocabulary_v1()
    base = tuple(parse_deck_csv_bytes(TOMATO_PARENT_DECK.read_bytes()))
    validate_deck(base, known_card_ids=vocabulary.recognized_card_ids)
    parent_identity = deck_multiset_identity_v1(base)
    prior = _existing_multisets()
    signature = CoreSignatureV1(archetype_id="archaludon-cinderace", required_counts={57: 1, 169: 4, 190: 4, 666: 4})
    results: list[dict[str, object]] = []
    for old_card, new_card in SURFACE_SWAPS:
        if old_card not in base:
            raise SurfaceWeightedDeckError(f"parent deck lacks requested card {old_card}")
        if new_card not in vocabulary.recognized_card_ids:
            raise SurfaceWeightedDeckError(f"unknown replacement card {new_card}")
        cards = list(base)
        cards.remove(old_card)
        cards.append(new_card)
        mutated = tuple(sorted(cards))
        validate_deck(mutated, known_card_ids=vocabulary.recognized_card_ids)
        try:
            validate_mutation_v1(card_ids=mutated, signature=signature)
        except Exception as exc:
            raise SurfaceWeightedDeckError(f"engine/core legality rejected {old_card}->{new_card}") from exc
        identity = deck_multiset_identity_v1(mutated)
        if identity in prior or identity == parent_identity:
            raise SurfaceWeightedDeckError(f"surface multiset already evaluated: {old_card}->{new_card}")
        results.append({
            "candidate_id": f"surface-{old_card}-to-{new_card}",
            "removed_cards": [old_card],
            "added_cards": [new_card],
            "card_ids": list(mutated),
            "parent_deck_multiset_sha256": parent_identity,
            "deck_multiset_sha256": identity,
            "novel_against_all_scanned_decks": True,
            "legality_gate": True,
        })
    if len(results) != MAX_CANDIDATES or len({row["deck_multiset_sha256"] for row in results}) != MAX_CANDIDATES:
        raise SurfaceWeightedDeckError("surface candidate count/identity gate failed")
    return tuple(results)


def _write_deck_no_clobber(path: Path, cards: Sequence[int]) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite deck: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ("\n".join(str(card) for card in cards) + "\n").encode()
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


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise SurfaceWeightedDeckError("output must be below repository final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise SurfaceWeightedDeckError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _candidate_spec(parent: Mapping[str, object], deck_path: Path, deck_sha: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {"main_path": str(parent["policy_path"]), "deck_path": str(deck_path), "policy_sha256": str(parent["policy_sha256"]), "deck_sha256": deck_sha, "env": env, "biases": biases, "config_sha256": _config_sha(env, biases), "pool_root": str(POOL_ROOT.resolve())}


def _references(subset: Mapping[str, object]) -> tuple[str, ...]:
    refs = tuple(str(item) for item in subset["selected_ids"])
    if len(refs) != 12 or len(set(refs)) != 12:
        raise SurfaceWeightedDeckError("META_TRAIN weighted subset must contain 12 unique IDs")
    return refs


def materialize_manifest(*, output: Path) -> dict[str, object]:
    output = _fresh_root(output)
    validate_surface_parent(TOMATO_PARENT_DECK_SHA256)
    subset = load_meta_train_subset(META_MANIFEST)
    candidates = build_surface_candidates()
    parent_cards = tuple(parse_deck_csv_bytes(TOMATO_PARENT_DECK.read_bytes()))
    materialized: list[dict[str, object]] = []
    for row in candidates:
        path = output / "candidates" / str(row["candidate_id"]) / "deck.csv"
        deck_sha = _write_deck_no_clobber(path, row["card_ids"])
        materialized.append({**row, "deck_path": str(path.resolve()), "deck_file_sha256": deck_sha, **AUTHORITY_FALSE})
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "TOMATO_NATIVE_PARENT_EXACT_1244_OVERLAY_WEIGHTED48",
        "parent": {
            "candidate_id": TOMATO_PARENT_ID,
            "deck_path": str(TOMATO_PARENT_DECK.resolve()),
            "deck_file_sha256": _file_sha(TOMATO_PARENT_DECK),
            "deck_multiset_sha256": deck_multiset_identity_v1(parent_cards),
            "policy_path": str(TOMATO_PARENT_POLICY.resolve()),
            "policy_sha256": _file_sha(TOMATO_PARENT_POLICY),
            "usage_boundary": "local_eval_only",
        },
        "surface": {
            "swaps": [list(pair) for pair in SURFACE_SWAPS],
            "candidate_count": MAX_CANDIDATES,
            "novelty_scan": "opponents/** + prior final-sprint runs/** deck.csv multiset identities",
            "engine_legality": "validate_deck + validate_mutation_v1",
        },
        "meta_train_subset": subset,
        "protocol": {
            "weighted_games_per_arm": len(subset["selected_ids"]) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "warmup_ramp_workers": list(RAMP_WORKERS),
            "weighted_base_seed": WEIGHTED_BASE_SEED,
            "common24_auto_start": False,
            "confirmation_auto_start": False,
        },
        "candidates": materialized,
        "pool_manifest_path": str((POOL_ROOT / "pool_manifest.json").resolve()),
        "pool_manifest_sha256": _file_sha(POOL_ROOT / "pool_manifest.json"),
        "resource_budget_path": str(RESOURCE_CONFIG.resolve()),
        "resource_budget_sha256": _file_sha(RESOURCE_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_json_no_clobber(output / "candidate_manifest.json", manifest)
    return {**manifest, "manifest_sha256": manifest_sha, "output_root": str(output)}


def _build_games(*, manifest: Mapping[str, object], arm: str, deck_path: Path, deck_sha: str, references: Sequence[str]) -> tuple[object, ...]:
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    pool = load_opponent_pool_v1(POOL_ROOT)
    built = build_native_candidate_games_v1(
        candidate_id=f"tomato-surface-{arm}",
        candidate=_candidate_spec(parent, deck_path, deck_sha),
        pool=pool,
        reference_ids=references,
        games_per_opponent_seat=WEIGHTED_GAMES_PER_OPPONENT_SEAT,
        base_seed=WEIGHTED_BASE_SEED,
        block_id=f"{SCHEMA}-weighted48",
    )
    return tuple(replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "weighted_meta_train": True, "surface_swaps": [list(pair) for pair in SURFACE_SWAPS], "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"], **AUTHORITY_FALSE}) for game in built)


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


def _warmup(*, output: Path, manifest: Mapping[str, object], budget: ResourceBudget) -> dict[str, object]:
    initial = ResourceSnapshot.collect()
    plan = build_warmup_plan(budget=budget, task_cap=budget.max_workers, snapshot=initial, ramp_workers=RAMP_WORKERS)
    if plan.get("warmup_status") != "ready":
        raise SurfaceWeightedDeckError("ResourceGovernor blocked warmup")
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    pool = load_opponent_pool_v1(POOL_ROOT)
    refs = _references(manifest["meta_train_subset"])[-2:]
    records: list[dict[str, object]] = []
    for workers in RAMP_WORKERS:
        if workers > int(plan["safe_workers"]):
            records.append({"workers": workers, "status": "not_admitted"})
            continue
        before = ResourceSnapshot.collect()
        games = build_native_candidate_games_v1(candidate_id=f"surface-warmup-{workers}", candidate=_candidate_spec(parent, Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])), pool=pool, reference_ids=refs, games_per_opponent_seat=1, base_seed=WARMUP_BASE_SEED + workers * 100, block_id=f"{SCHEMA}-warmup-{workers}")
        destination = output / "warmup" / f"workers-{workers}" / "evaluation"
        started = time.monotonic()
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
        elapsed = max(time.monotonic() - started, 1e-9)
        after = ResourceSnapshot.collect()
        summary = result["summary"]
        records.append({"workers": workers, "status": "DONE", "requested_games": summary["requested_games"], "completed_games": summary["completed_games"], "faults": summary["faults"], "fault_gate": int(summary["faults"]) == 0, "throughput_games_per_second": summary["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0, "output_dir": str(destination.resolve())})
    telemetry = {"schema_version": f"{SCHEMA}-warmup", "manifest_sha256": _file_sha(output / "candidate_manifest.json"), "budget": budget.to_dict(), "governor_plan": plan, "governor_decision": ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=initial).to_dict(), "ramp": records, "authority": dict(AUTHORITY_FALSE), "no_process_kill": True}
    telemetry["telemetry_sha256"] = _write_json_no_clobber(output / "warmup_telemetry.json", telemetry)
    return telemetry


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    manifest = materialize_manifest(output=output)
    output_path = Path(str(manifest["output_root"]))
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=output_path, manifest=manifest, budget=budget)
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    references = _references(manifest["meta_train_subset"])
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in manifest["candidates"]:
        assert isinstance(row, Mapping)
        specs.append((str(row["candidate_id"]), Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    games: list[object] = []
    for arm, deck_path, deck_sha in specs:
        games.extend(_build_games(manifest=manifest, arm=arm, deck_path=deck_path, deck_sha=deck_sha, references=references))
    expected = len(specs) * len(references) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise SurfaceWeightedDeckError("weighted game count/GID gate failed")
    by_arm: dict[str, list[object]] = defaultdict(list)
    for game in games:
        by_arm[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm["parent"]}
    for arm in sorted(set(by_arm) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise SurfaceWeightedDeckError(f"paired schedule mismatch: {arm}")
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise SurfaceWeightedDeckError("ResourceGovernor blocked weighted48")
    destination = output_path / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    final_grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        final_grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    weights = {str(key): float(value) for key, value in manifest["meta_train_subset"]["selected_weights"].items()}
    arms = {arm: _weighted(rows, weights) for arm, rows in sorted(final_grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates: list[dict[str, object]] = []
    for arm in sorted(set(arms) - {"parent"}):
        row = next(item for item in manifest["candidates"] if item["candidate_id"] == arm)
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        candidates.append({"arm_id": arm, "candidate_id": row["candidate_id"], "removed_cards": row["removed_cards"], "added_cards": row["added_cards"], "deck_file_sha256": row["deck_file_sha256"], "deck_multiset_sha256": row["deck_multiset_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "paired_strata_gate": True, "status": "weighted_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0.0 else "candidate_only"})
    summary = {"schema_version": f"{SCHEMA}-weighted48", "manifest_sha256": _file_sha(output_path / "candidate_manifest.json"), "warmup_telemetry_sha256": warmup["telemetry_sha256"], "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"], "arms": arms, "parent_weighted_meta_score": parent_score, "candidates": candidates, "all_faults_zero": int(result["summary"]["faults"]) == 0, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": expected, "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0, "worker_recycle_games": budget.recycle_games}, "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "next_gate": "positive weighted candidates only; no automatic common24/384/longrun"}
    summary["summary_sha256"] = _write_json_no_clobber(output_path / "weighted48_summary.json", summary)
    summary["summary_md_sha256"] = _write_text_no_clobber(output_path / "weighted48_summary.md", "# Tomato exact surface weighted48\n\n" + "\n".join(f"- {row['candidate_id']} ({row['removed_cards'][0]}→{row['added_cards'][0]}): {row['weighted_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates) + "\n")
    summary["final_summary_sha256"] = _write_json_no_clobber(output_path / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output_path), "weighted_summary_sha256": summary["summary_sha256"], "weighted_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup["telemetry_sha256"], "authority": dict(AUTHORITY_FALSE), "performance_run_started": True})
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


__all__ = ["SURFACE_SWAPS", "SurfaceWeightedDeckError", "build_surface_candidates", "execute", "materialize_manifest", "validate_surface_parent"]
