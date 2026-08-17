#!/usr/bin/env python3
"""Research-only META_TRAIN weighted deck screen from Tomato native parent.

The parent is the sealed Tomato native deck and policy.  At most two novel
legal one-card mutations are generated, then parent and candidates are scored
on the same weighted META_TRAIN strata.  This lane deliberately stops after
weighted48: a positive result does not automatically start common24, 384, or
longrun evaluation.
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
from mage_ptcg.meta_specialist.deck_mutation_v1 import generate_deck_mutation_candidates_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1, deck_multiset_identity_v1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1
from scripts.run_resource_aware_deck_candidate_v1 import build_warmup_plan
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-tomato-weighted-deck-v1"
TOMATO_PARENT_ID = "tomatomato_archaludon-native"
TOMATO_PARENT_DECK = ROOT / "opponents/tomatomato_archaludon/deck.csv"
TOMATO_PARENT_POLICY = ROOT / "opponents/tomatomato_archaludon/main.py"
TOMATO_PARENT_DECK_SHA256 = "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"
TOMATO_PARENT_POLICY_SHA256 = "8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_ROOT = ROOT / "opponents"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-weighted-deck-v1-20260814"
COMMON24_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
MAX_CANDIDATES = 2
GENERATOR_SEED = 20260814
WARMUP_BASE_SEED = 22600000
WEIGHTED_BASE_SEED = 22610000
RAMP_WORKERS = (1, 2, 4, 8, 12)
WEIGHTED_GAMES_PER_OPPONENT_SEAT = 2
TARGET_ADDITIONS = (1142, 1123, 1086, 1141, 1252, 1102, 1192)
REPLACEMENT_POOL = (8, 1097, 1121, 1122, 1142, 1147, 1152, 1159, 1182, 1185, 1192, 1213, 1227, 1244, 1252, 1123, 1086, 1141, 1102)
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class TomatoWeightedDeckError(ValueError):
    """Raised when the Tomato weighted screen contract is open or malformed."""


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
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def validate_parent_identity(parent_id: str, deck_sha256: str) -> None:
    if parent_id != TOMATO_PARENT_ID:
        raise TomatoWeightedDeckError(f"Tomato parent identity mismatch: {parent_id}")
    if deck_sha256 != TOMATO_PARENT_DECK_SHA256:
        raise TomatoWeightedDeckError("Tomato parent deck SHA mismatch")


def _existing_multisets() -> set[str]:
    vocabulary = load_production_card_vocabulary_v1()
    identities: set[str] = set()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:
            raise TomatoWeightedDeckError(f"malformed existing deck while proving novelty: {path}") from exc
        identities.add(deck_multiset_identity_v1(cards))
    return identities


def select_tomato_candidates(*, generator_seed: int = GENERATOR_SEED) -> tuple[dict[str, object], ...]:
    vocabulary = load_production_card_vocabulary_v1()
    base = tuple(parse_deck_csv_bytes(TOMATO_PARENT_DECK.read_bytes()))
    validate_deck(base, known_card_ids=vocabulary.recognized_card_ids)
    prior = _existing_multisets()
    signature = CoreSignatureV1(archetype_id="archaludon-cinderace", required_counts={57: 1, 169: 4, 190: 4, 666: 4})
    generated = generate_deck_mutation_candidates_v1(
        base_cards=base,
        signature=signature,
        replacement_pool=REPLACEMENT_POOL,
        swap_counts=(1,),
        candidates_per_swap=128,
        seed=generator_seed,
        known_card_ids=vocabulary.recognized_card_ids,
    )
    chosen: list[object] = []
    used_targets: set[int] = set()
    for candidate in generated:
        if candidate.deck_multiset_sha256 in prior or candidate.added_cards[0] not in TARGET_ADDITIONS:
            continue
        target = int(candidate.added_cards[0])
        if target in used_targets:
            continue
        chosen.append(candidate)
        used_targets.add(target)
        if len(chosen) == MAX_CANDIDATES:
            break
    if len(chosen) != MAX_CANDIDATES:
        raise TomatoWeightedDeckError("fewer than two novel target-overlay candidates")
    return tuple({
        "candidate_id": c.candidate_id,
        "removed_cards": list(c.removed_cards),
        "added_cards": list(c.added_cards),
        "card_ids": list(c.card_ids),
        "deck_multiset_sha256": c.deck_multiset_sha256,
        "parent_deck_multiset_sha256": deck_multiset_identity_v1(base),
        "novel_against_all_scanned_decks": True,
    } for c in chosen)


def _write_deck_no_clobber(path: Path, cards: Sequence[int]) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite deck: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ("\n".join(str(card) for card in cards) + "\n").encode()
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


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise TomatoWeightedDeckError("output must be a repository-contained final-sprint child")
    if resolved.exists() and any(resolved.iterdir()):
        raise TomatoWeightedDeckError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def materialize_manifest(*, output: Path, generator_seed: int = GENERATOR_SEED) -> dict[str, object]:
    output = _fresh_root(output)
    validate_parent_identity(TOMATO_PARENT_ID, _file_sha(TOMATO_PARENT_DECK))
    if _file_sha(TOMATO_PARENT_POLICY) != TOMATO_PARENT_POLICY_SHA256:
        raise TomatoWeightedDeckError("Tomato native policy SHA changed")
    subset = load_meta_train_subset(META_MANIFEST)
    candidates = select_tomato_candidates(generator_seed=generator_seed)
    materialized: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        path = output / "candidates" / str(candidate["candidate_id"]) / "deck.csv"
        deck_sha = _write_deck_no_clobber(path, candidate["card_ids"])
        materialized.append({**candidate, "ordinal": index, "deck_path": str(path.resolve()), "deck_file_sha256": deck_sha, **AUTHORITY_FALSE})
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "TOMATO_NATIVE_PARENT_META_TRAIN_WEIGHTED_DECK_SCREEN",
        "parent": {
            "candidate_id": TOMATO_PARENT_ID,
            "deck_path": str(TOMATO_PARENT_DECK.resolve()),
            "deck_file_sha256": _file_sha(TOMATO_PARENT_DECK),
            "deck_multiset_sha256": deck_multiset_identity_v1(tuple(parse_deck_csv_bytes(TOMATO_PARENT_DECK.read_bytes()))),
            "policy_path": str(TOMATO_PARENT_POLICY.resolve()),
            "policy_sha256": _file_sha(TOMATO_PARENT_POLICY),
            "usage_boundary": "local_eval_only",
        },
        "candidate_generation": {
            "generator_module": "src/mage_ptcg/meta_specialist/deck_mutation_v1.py",
            "generator_module_sha256": _file_sha(ROOT / "src/mage_ptcg/meta_specialist/deck_mutation_v1.py"),
            "generator_seed": generator_seed,
            "target_additions": list(TARGET_ADDITIONS),
            "replacement_pool": list(REPLACEMENT_POOL),
            "novelty_proof": "all candidate multiset identities absent from opponents/** and prior final-sprint runs/** deck.csv",
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


def _candidate_spec(*, parent: Mapping[str, object], deck_path: Path, deck_sha: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {
        "main_path": str(parent["policy_path"]),
        "deck_path": str(deck_path),
        "policy_sha256": str(parent["policy_sha256"]),
        "deck_sha256": deck_sha,
        "env": env,
        "biases": biases,
        "config_sha256": _config_sha(env, biases),
        "pool_root": str(POOL_ROOT.resolve()),
    }


def _references(subset: Mapping[str, object]) -> tuple[str, ...]:
    values = tuple(str(item) for item in subset["selected_ids"])
    if len(values) != 12 or len(set(values)) != 12:
        raise TomatoWeightedDeckError("weighted subset must contain 12 unique IDs")
    return values


def _build_games(*, manifest: Mapping[str, object], arm: str, deck_path: Path, deck_sha: str, references: Sequence[str]) -> tuple[object, ...]:
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    pool = load_opponent_pool_v1(POOL_ROOT)
    built = build_native_candidate_games_v1(
        candidate_id=f"tomato-weighted-{arm}",
        candidate=_candidate_spec(parent=parent, deck_path=deck_path, deck_sha=deck_sha),
        pool=pool,
        reference_ids=references,
        games_per_opponent_seat=WEIGHTED_GAMES_PER_OPPONENT_SEAT,
        base_seed=WEIGHTED_BASE_SEED,
        block_id=f"{SCHEMA}-weighted48",
    )
    return tuple(replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "weighted_meta_train": True, "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"], **AUTHORITY_FALSE}) for game in built)


def _weighted(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    per_opponent: dict[str, object] = {}
    numerator = denominator = 0.0
    for opponent, weight in weights.items():
        values = [row for row in rows if str(row.get("opponent_id")) == opponent]
        score = sum(1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0 for row in values)
        rate = score / len(values) if values else None
        per_opponent[opponent] = {"weight": weight, "games": len(values), "rate": rate}
        if rate is not None:
            numerator += weight * rate
            denominator += weight
    aggregate = aggregate_ledger_v1(rows)
    return {**aggregate, "weighted_meta_score": numerator / denominator if denominator else None, "per_opponent": per_opponent, "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows), "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows), "seat_counts": {str(seat): len([row for row in rows if int(row.get("seat", -1)) == seat]) for seat in (0, 1)}}


def _warmup(*, output: Path, manifest: Mapping[str, object], budget: ResourceBudget) -> dict[str, object]:
    initial = ResourceSnapshot.collect()
    plan = build_warmup_plan(budget=budget, task_cap=budget.max_workers, snapshot=initial, ramp_workers=RAMP_WORKERS)
    if plan.get("warmup_status") != "ready":
        raise TomatoWeightedDeckError("resource governor blocked warmup")
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
        games = build_native_candidate_games_v1(candidate_id=f"tomato-warmup-{workers}", candidate=_candidate_spec(parent=parent, deck_path=Path(str(parent["deck_path"])), deck_sha=str(parent["deck_file_sha256"])), pool=pool, reference_ids=refs, games_per_opponent_seat=1, base_seed=WARMUP_BASE_SEED + workers * 100, block_id=f"{SCHEMA}-warmup-{workers}")
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


def execute(*, output: Path = OUTPUT_DEFAULT, generator_seed: int = GENERATOR_SEED) -> dict[str, object]:
    manifest = materialize_manifest(output=output, generator_seed=generator_seed)
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=Path(str(manifest["output_root"])), manifest=manifest, budget=budget)
    output_path = Path(str(manifest["output_root"]))
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    refs = _references(manifest["meta_train_subset"])
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in manifest["candidates"]:
        assert isinstance(row, Mapping)
        specs.append((f"candidate-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    games: list[object] = []
    for arm, deck_path, deck_sha in specs:
        games.extend(_build_games(manifest=manifest, arm=arm, deck_path=deck_path, deck_sha=deck_sha, references=refs))
    expected = len(specs) * len(refs) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise TomatoWeightedDeckError("weighted game count or identity gate failed")
    by_arm: dict[str, list[object]] = defaultdict(list)
    for game in games:
        by_arm[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm["parent"]}
    for arm in sorted(set(by_arm) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in by_arm[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise TomatoWeightedDeckError("candidate/parent paired schedule mismatch")
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise TomatoWeightedDeckError("resource governor blocked weighted evaluation")
    destination = output_path / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    weights = {str(k): float(v) for k, v in manifest["meta_train_subset"]["selected_weights"].items()}
    # Aggregate only final ledger rows; pre-run game objects are not mappings.
    final_grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        final_grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    arms = {arm: _weighted(rows, weights) for arm, rows in sorted(final_grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates = []
    for arm in sorted(set(arms) - {"parent"}):
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        row = next(item for item in manifest["candidates"] if arm.endswith(str(item["candidate_id"])[:12]))
        candidates.append({"arm_id": arm, "candidate_id": row["candidate_id"], "deck_file_sha256": row["deck_file_sha256"], "deck_multiset_sha256": row["deck_multiset_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "status": "weighted_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0.0 else "candidate_only"})
    summary = {"schema_version": f"{SCHEMA}-weighted48", "manifest_sha256": _file_sha(output_path / "candidate_manifest.json"), "warmup_telemetry_sha256": warmup["telemetry_sha256"], "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"], "arms": arms, "parent_arm_id": "parent", "parent_weighted_meta_score": parent_score, "candidates": candidates, "all_faults_zero": int(result["summary"]["faults"]) == 0, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": expected, "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0, "worker_recycle_games": budget.recycle_games}, "authority": dict(AUTHORITY_FALSE), "next_gate": "weighted48 result only; no automatic common24/384/longrun"}
    summary["summary_sha256"] = _write_json_no_clobber(output_path / "weighted48_summary.json", summary)
    summary["summary_md_sha256"] = _write_text_no_clobber(output_path / "weighted48_summary.md", "# Tomato native weighted48\n\n" + "\n".join(f"- {row['candidate_id']}: {row['weighted_delta_points']:+.3f}pt vs Tomato parent; faults={row['fault_gate']}; status={row['status']}" for row in candidates) + "\n")
    summary["final_summary_sha256"] = _write_json_no_clobber(output_path / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output_path), "weighted_summary_sha256": summary["summary_sha256"], "weighted_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup["telemetry_sha256"], "authority": dict(AUTHORITY_FALSE), "performance_run_started": True})
    return summary


def finalize_existing(*, output: Path) -> dict[str, object]:
    """Seal summaries from a completed weighted ledger without rerunning games.

    This is intentionally separate from :func:`execute`: if a wrapper crashes
    after the evaluator has atomically sealed its ledger, the result can be
    audited and finalized without duplicating performance games.
    """
    output = output.resolve()
    manifest_path = output / "candidate_manifest.json"
    ledger_path = output / "weighted48/evaluation/ledger.jsonl"
    evaluation_summary_path = output / "weighted48/evaluation/summary.json"
    warmup_path = output / "warmup_telemetry.json"
    for path in (manifest_path, ledger_path, evaluation_summary_path, warmup_path):
        if not path.is_file():
            raise TomatoWeightedDeckError(f"missing completed artifact: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise TomatoWeightedDeckError("manifest schema mismatch")
    parent = manifest.get("parent")
    candidates = manifest.get("candidates")
    if not isinstance(parent, Mapping) or not isinstance(candidates, list) or len(candidates) != MAX_CANDIDATES:
        raise TomatoWeightedDeckError("manifest parent/candidates malformed")
    validate_parent_identity(str(parent.get("candidate_id")), str(parent.get("deck_file_sha256")))
    if _file_sha(Path(str(parent["policy_path"]))) != parent.get("policy_sha256"):
        raise TomatoWeightedDeckError("parent policy changed after evaluation")
    for row in candidates:
        if not isinstance(row, Mapping):
            raise TomatoWeightedDeckError("candidate row malformed")
        deck_path = Path(str(row["deck_path"]))
        if _file_sha(deck_path) != row.get("deck_file_sha256"):
            raise TomatoWeightedDeckError(f"candidate deck changed: {deck_path}")
    subset = manifest.get("meta_train_subset")
    if not isinstance(subset, Mapping):
        raise TomatoWeightedDeckError("META_TRAIN subset missing")
    reloaded_subset = load_meta_train_subset(Path(str(subset["source_path"])))
    if reloaded_subset["subset_sha256"] != subset.get("subset_sha256"):
        raise TomatoWeightedDeckError("META_TRAIN subset changed after evaluation")
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    references = _references(subset)
    expected_per_arm = len(references) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(rows) != expected_per_arm * (1 + MAX_CANDIDATES):
        raise TomatoWeightedDeckError(f"unexpected completed ledger rows: {len(rows)}")
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TomatoWeightedDeckError("ledger metadata malformed")
        grouped[str(metadata.get("comparison_arm", ""))].append(row)
    if set(grouped) != {"parent", *(f"candidate-{str(row['candidate_id'])[:12]}" for row in candidates)}:
        raise TomatoWeightedDeckError(f"unexpected arm identities: {sorted(grouped)}")
    weights = {str(key): float(value) for key, value in subset["selected_weights"].items()}
    arms = {arm: _weighted(values, weights) for arm, values in sorted(grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    parent_keys = {(str(row["opponent_id"]), int(row["seat"]), int(row["metadata"]["repetition"])): row for row in grouped["parent"]}
    if len(parent_keys) != expected_per_arm:
        raise TomatoWeightedDeckError("parent paired-key identity is not unique")
    results: list[dict[str, object]] = []
    for row in candidates:
        arm = f"candidate-{str(row['candidate_id'])[:12]}"
        values = grouped[arm]
        keys = {(str(item["opponent_id"]), int(item["seat"]), int(item["metadata"]["repetition"])): item for item in values}
        if keys.keys() != parent_keys.keys() or any(keys[key].get("seed") != parent_keys[key].get("seed") for key in parent_keys):
            raise TomatoWeightedDeckError(f"paired schedule mismatch: {arm}")
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        results.append({"arm_id": arm, "candidate_id": row["candidate_id"], "deck_file_sha256": row["deck_file_sha256"], "deck_multiset_sha256": row["deck_multiset_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "paired_strata_gate": True, "status": "weighted_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0.0 else "candidate_only"})
    evaluation_summary = json.loads(evaluation_summary_path.read_text(encoding="utf-8"))
    warmup_sha = _file_sha(warmup_path)
    summary = {"schema_version": f"{SCHEMA}-weighted48-finalized", "manifest_sha256": _file_sha(manifest_path), "warmup_telemetry_sha256": warmup_sha, "evaluation_ledger_sha256": _file_sha(ledger_path), "evaluation_summary_sha256": _file_sha(evaluation_summary_path), "weighted_subset_sha256": subset["subset_sha256"], "arms": arms, "parent_weighted_meta_score": parent_score, "candidates": results, "all_faults_zero": all(int(value["faults"]) == 0 for value in arms.values()), "evaluation_summary": evaluation_summary, "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "next_gate": "weighted48 only; no automatic common24/384/longrun"}
    summary["summary_sha256"] = _write_json_no_clobber(output / "weighted48_summary.json", summary)
    summary["summary_md_sha256"] = _write_text_no_clobber(output / "weighted48_summary.md", "# Tomato native weighted48（finalized）\n\n" + "\n".join(f"- {item['candidate_id']}: {item['weighted_delta_points']:+.3f}pt vs Tomato parent; faults={item['fault_gate']}; status={item['status']}" for item in results) + "\n")
    summary["final_summary_sha256"] = _write_json_no_clobber(output / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output), "weighted_summary_sha256": summary["summary_sha256"], "weighted_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup_sha, "authority": dict(AUTHORITY_FALSE), "performance_run_started": True, "performance_rerun": False})
    return summary


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--generator-seed", type=int, default=GENERATOR_SEED)
    args = parser.parse_args()
    print(json.dumps(execute(output=args.output, generator_seed=args.generator_seed), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_CANDIDATES", "TOMATO_PARENT_DECK_SHA256", "TOMATO_PARENT_ID", "TomatoWeightedDeckError", "execute", "finalize_existing", "materialize_manifest", "select_tomato_candidates", "validate_parent_identity"]
