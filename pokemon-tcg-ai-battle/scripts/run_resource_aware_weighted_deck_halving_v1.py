#!/usr/bin/env python3
"""Resource-aware META_TRAIN weighted deck-halving (research-only).

The runner starts from the fixed role-8c8c69dc792c913f parent deck and Tomato
native policy, materializes at most two novel legal mutations, runs a bounded
1/2/4 worker warm-up, and then evaluates each arm on a sealed 12-opponent
META_TRAIN subset (48 games/arm).  Held-out rows, including Lucifer/Plamen
when marked META_FINAL, are never used in the weighted objective; common24 is
an evaluation-only guardrail for weighted-positive candidates.

This is a new research wrapper.  It does not edit or import a production
entrypoint, does not grant training/submission authority, and refuses to
overwrite a non-empty output root.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    DeckMutationCandidateV1,
    generate_deck_mutation_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    deck_multiset_identity_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
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
    _sha256,
    build_native_candidate_games_v1,
)
from scripts.run_resource_aware_deck_candidate_v1 import build_warmup_plan


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-weighted-deck-halving-v1"
PARENT_DECK = ROOT / (
    "runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/"
    "candidate-manifest/role-8c8c69dc792c913f/deck.csv"
)
POLICY_PATH = ROOT / "opponents/tomatomato_archaludon/main.py"
POOL_ROOT = ROOT / "opponents"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
COMMON24_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
KNOWN_CARD_DATABASE = ROOT / "data/raw/EN_Card_Data.csv"
OUTPUT_DEFAULT = ROOT / (
    "runs/final-sprint-autonomous/"
    "resource-aware-weighted-deck-halving-v1-20260813"
)
GENERATOR_SEED_DEFAULT = 20_260_814
WARMUP_BASE_SEED = 20_800_000
WEIGHTED_BASE_SEED = 20_810_000
COMMON24_BASE_SEED = 20_820_000
WEIGHTED_TOP_K = 12
GAMES_PER_OPPONENT_SEAT = 2
MAX_CANDIDATES = 2
TARGET_HARD_NEGATIVES = (
    "lucifer19_battlecore",
    "plamen06_steel",
    "aristophanivan_probabilistic",
    "harukiharada_crustle",
)
META_TRAIN_REQUIRED = (
    "aristophanivan_probabilistic",
    "harukiharada_crustle",
)
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class ResourceAwareWeightedDeckError(ValueError):
    """Raised when a manifest, candidate, or resource gate is open."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResourceAwareWeightedDeckError("value is not canonical JSON") from exc


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_no_clobber(path: Path, payload: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    raw_payload = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2,
    ) + "\n"
    raw = raw_payload.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    raw = text.encode("utf-8")
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


def _fresh_root(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"refusing non-empty output root: {path}")
    path.mkdir(parents=True, exist_ok=True)


def load_meta_train_subset(
    path: str | Path,
    *,
    top_k: int = WEIGHTED_TOP_K,
    required_ids: Sequence[str] = META_TRAIN_REQUIRED,
) -> dict[str, object]:
    """Select weighted META_TRAIN rows and prove held-out exclusion."""

    source = Path(path).resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResourceAwareWeightedDeckError(f"cannot read meta manifest: {source}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("rows"), list):
        raise ResourceAwareWeightedDeckError("meta manifest must contain rows")
    if document.get("research_only") is not True:
        raise ResourceAwareWeightedDeckError("meta manifest is not research-only")
    for key in ("promotion_authority", "training_authority", "submission_authority"):
        if document.get(key) is not False:
            raise ResourceAwareWeightedDeckError(f"meta manifest authority is open: {key}")
    rows = [row for row in document["rows"] if isinstance(row, dict)]
    if len(rows) != len(document["rows"]):
        raise ResourceAwareWeightedDeckError("meta manifest contains malformed row")
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        opponent_id = row.get("opponent_id")
        if type(opponent_id) is not str or not opponent_id:
            raise ResourceAwareWeightedDeckError("meta row opponent_id is malformed")
        if opponent_id in by_id:
            raise ResourceAwareWeightedDeckError(f"duplicate meta row: {opponent_id}")
        by_id[opponent_id] = row
    heldout_ids = sorted(
        str(row["opponent_id"])
        for row in rows
        if row.get("split") != "META_TRAIN"
    )
    eligible: list[dict[str, object]] = []
    for row in rows:
        if row.get("split") != "META_TRAIN":
            continue
        weight = row.get("weight")
        if type(weight) not in (int, float) or isinstance(weight, bool) or not math.isfinite(float(weight)):
            raise ResourceAwareWeightedDeckError(f"malformed META_TRAIN weight: {row['opponent_id']}")
        if float(weight) <= 0.0 or row.get("evaluation_allowed") is not True:
            continue
        eligible.append(row)
    eligible.sort(key=lambda row: (-float(row["weight"]), str(row["opponent_id"])))
    required = tuple(dict.fromkeys(str(value) for value in required_ids))
    if len(required) > top_k:
        raise ResourceAwareWeightedDeckError("required target count exceeds top_k")
    selected: list[dict[str, object]] = []
    for opponent_id in required:
        row = by_id.get(opponent_id)
        if row is None or row.get("split") != "META_TRAIN" or float(row.get("weight", 0.0)) <= 0.0:
            raise ResourceAwareWeightedDeckError(
                f"required target is not positive META_TRAIN: {opponent_id}"
            )
        selected.append(row)
    for row in eligible:
        if row["opponent_id"] not in {item["opponent_id"] for item in selected}:
            selected.append(row)
        if len(selected) == top_k:
            break
    if len(selected) != top_k:
        raise ResourceAwareWeightedDeckError("META_TRAIN has fewer than top_k positive rows")
    selected.sort(key=lambda row: (-float(row["weight"]), str(row["opponent_id"])))
    selected_ids = [str(row["opponent_id"]) for row in selected]
    if set(selected_ids) & set(heldout_ids):
        raise ResourceAwareWeightedDeckError("held-out row leaked into weighted subset")
    return {
        "source_path": str(source),
        "source_file_sha256": _file_sha(source),
        "selected_rows": [dict(row) for row in selected],
        "selected_ids": selected_ids,
        "selected_weights": {str(row["opponent_id"]): float(row["weight"]) for row in selected},
        "subset_sha256": _sha([dict(row) for row in selected]),
        "heldout_ids": heldout_ids,
        "heldout_target_ids": [item for item in TARGET_HARD_NEGATIVES if item in heldout_ids],
        "weight_update_excluded_heldout": True,
    }


def _existing_deck_identities() -> dict[str, list[str]]:
    vocabulary = load_production_card_vocabulary_v1()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    identities: dict[str, list[str]] = {}
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:  # fail closed: do not silently skip old assets
            raise ResourceAwareWeightedDeckError(f"malformed existing deck: {path}") from exc
        identities.setdefault(deck_multiset_identity_v1(cards), []).append(str(path.resolve()))
    return identities


def select_novel_candidates(
    *,
    parent_deck: Path = PARENT_DECK,
    generator_seed: int = GENERATOR_SEED_DEFAULT,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[dict[str, object], ...]:
    """Materialize deterministic, legal, previously unseen one-card mutations."""

    if max_candidates != 2:
        raise ResourceAwareWeightedDeckError("this lane is sealed to at most two candidates")
    vocabulary = load_production_card_vocabulary_v1()
    parent_cards = tuple(parse_deck_csv_bytes(parent_deck.read_bytes()))
    validate_deck(parent_cards, known_card_ids=vocabulary.recognized_card_ids)
    prior = _existing_deck_identities()
    signature = CoreSignatureV1(
        archetype_id="archaludon-cinderace",
        required_counts={57: 1, 169: 4, 190: 4, 666: 4},
    )
    replacement_pool = (8, 1097, 1121, 1122, 1147, 1152, 1159, 1182, 1185, 1213, 1227, 1244)
    generated = generate_deck_mutation_candidates_v1(
        base_cards=parent_cards,
        signature=signature,
        replacement_pool=replacement_pool,
        swap_counts=(1, 2),
        candidates_per_swap=32,
        seed=generator_seed,
        known_card_ids=vocabulary.recognized_card_ids,
    )
    novel = [
        candidate for candidate in generated
        if candidate.swap_count == 1 and candidate.deck_multiset_sha256 not in prior
    ]
    if len(novel) < max_candidates:
        raise ResourceAwareWeightedDeckError("fewer than two provably novel legal candidates")
    chosen = novel[:max_candidates]
    if len({candidate.deck_multiset_sha256 for candidate in chosen}) != max_candidates:
        raise ResourceAwareWeightedDeckError("candidate multiset identities are not unique")
    return tuple({
        "candidate_id": candidate.candidate_id,
        "swap_count": candidate.swap_count,
        "removed_cards": list(candidate.removed_cards),
        "added_cards": list(candidate.added_cards),
        "card_ids": list(candidate.card_ids),
        "deck_multiset_sha256": candidate.deck_multiset_sha256,
        "novel_against_prior_decks": True,
    } for candidate in chosen)


def _write_deck_no_clobber(path: Path, cards: Sequence[int]) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite candidate deck: {path}")
    raw = ("\n".join(str(card) for card in cards) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
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


def materialize_manifest(
    *,
    output: Path,
    generator_seed: int = GENERATOR_SEED_DEFAULT,
) -> dict[str, object]:
    _fresh_root(output)
    subset = load_meta_train_subset(META_MANIFEST)
    candidate_specs = select_novel_candidates(generator_seed=generator_seed)
    vocabulary = load_production_card_vocabulary_v1()
    parent_cards = tuple(parse_deck_csv_bytes(PARENT_DECK.read_bytes()))
    validate_deck(parent_cards, known_card_ids=vocabulary.recognized_card_ids)
    parent_multiset = deck_multiset_identity_v1(parent_cards)
    candidates: list[dict[str, object]] = []
    for ordinal, candidate in enumerate(candidate_specs):
        candidate_path = output / "candidates" / str(candidate["candidate_id"]) / "deck.csv"
        deck_sha = _write_deck_no_clobber(candidate_path, candidate["card_ids"])
        candidates.append({
            **candidate,
            "ordinal": ordinal,
            "deck_path": str(candidate_path.resolve()),
            "deck_file_sha256": deck_sha,
            **AUTHORITY_FALSE,
        })
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "purpose": "RESOURCE_AWARE_META_TRAIN_WEIGHTED_DECK_HALVING",
        "parent": {
            "candidate_id": "role-8c8c69dc792c913f",
            "deck_path": str(PARENT_DECK.resolve()),
            "deck_file_sha256": _file_sha(PARENT_DECK),
            "deck_multiset_sha256": parent_multiset,
            "policy_path": str(POLICY_PATH.resolve()),
            "policy_sha256": _file_sha(POLICY_PATH),
            "usage_boundary": "local_eval_only",
        },
        "candidate_generation": {
            "generator_module": "src/mage_ptcg/meta_specialist/deck_mutation_v1.py",
            "generator_module_sha256": _file_sha(ROOT / "src/mage_ptcg/meta_specialist/deck_mutation_v1.py"),
            "generator_seed": generator_seed,
            "max_candidates": MAX_CANDIDATES,
            "known_card_database_path": str(KNOWN_CARD_DATABASE.resolve()),
            "known_card_database_sha256": _file_sha(KNOWN_CARD_DATABASE),
            "novelty_proof": "all candidate deck_multiset_sha256 absent from opponents/** and prior final-sprint runs/** deck.csv",
        },
        "meta_train_subset": subset,
        "hard_negative_focus": {
            "target_ids": list(TARGET_HARD_NEGATIVES),
            "heldout_excluded_from_weight_update": list(subset["heldout_target_ids"]),
            "meta_train_required_targets": list(META_TRAIN_REQUIRED),
        },
        "protocol": {
            "weighted_games_per_arm": len(subset["selected_ids"]) * 2 * GAMES_PER_OPPONENT_SEAT,
            "common24_games_per_arm": 24 * 2 * 2,
            "same_seed_schedule_across_arms": True,
            "warmup_ramp_workers": [1, 2, 4, 8, 12],
            "worker_recycle_games": ResourceBudget.from_json(RESOURCE_CONFIG).recycle_games,
            "weighted_base_seed": WEIGHTED_BASE_SEED,
            "common24_base_seed": COMMON24_BASE_SEED,
        },
        "candidates": candidates,
        "pool_manifest_path": str((POOL_ROOT / "pool_manifest.json").resolve()),
        "pool_manifest_sha256": _file_sha(POOL_ROOT / "pool_manifest.json"),
        "resource_budget_path": str(RESOURCE_CONFIG.resolve()),
        "resource_budget_sha256": _file_sha(RESOURCE_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_no_clobber(output / "candidate_manifest.json", manifest)
    return {**manifest, "manifest_sha256": manifest_sha, "output_root": str(output.resolve())}


def _candidate_spec(manifest: Mapping[str, object], candidate_id: str, deck_path: Path, deck_sha: str) -> dict[str, object]:
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
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
        "pool_root": str(POOL_ROOT),
    }


def _build_games(
    *,
    manifest: Mapping[str, object],
    arm_id: str,
    deck_path: Path,
    deck_sha: str,
    reference_ids: Sequence[str],
    games_per_opponent_seat: int,
    base_seed: int,
    block_id: str,
    metadata: Mapping[str, object],
) -> tuple[object, ...]:
    pool = load_opponent_pool_v1(POOL_ROOT)
    built = build_native_candidate_games_v1(
        candidate_id=arm_id,
        candidate=_candidate_spec(manifest, arm_id, deck_path, deck_sha),
        pool=pool,
        reference_ids=reference_ids,
        games_per_opponent_seat=games_per_opponent_seat,
        base_seed=base_seed,
        block_id=block_id,
    )
    from dataclasses import replace
    return tuple(
        replace(game, metadata={**dict(game.metadata), **dict(metadata), "comparison_arm": arm_id, **AUTHORITY_FALSE})
        for game in built
    )


def _arm_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, list[Mapping[str, object]]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata", {})
        arm_id = str(metadata.get("comparison_arm")) if isinstance(metadata, Mapping) else "unknown"
        grouped[arm_id].append(row)
    return grouped


def _weighted_summary(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata", {})
        opponent = str(metadata.get("opponent_id", row.get("opponent_id"))) if isinstance(metadata, Mapping) else str(row.get("opponent_id"))
        grouped[opponent].append(row)
    per_opponent: dict[str, object] = {}
    numerator = 0.0
    denominator = 0.0
    for opponent_id, weight in weights.items():
        values = grouped.get(opponent_id, [])
        score = sum(
            1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0
            for row in values
        )
        rate = score / len(values) if values else None
        per_opponent[opponent_id] = {"weight": weight, "games": len(values), "score": score, "rate": rate}
        if rate is not None:
            numerator += weight * rate
            denominator += weight
    aggregate = aggregate_ledger_v1(rows)
    return {
        **aggregate,
        "weighted_meta_score": numerator / denominator if denominator else None,
        "per_opponent": per_opponent,
        "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows),
        "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows),
        "seat_counts": {
            str(seat): len([row for row in rows if int(row.get("seat", -1)) == seat])
            for seat in (0, 1)
        },
    }


def run_warmup(
    *,
    output: Path,
    manifest: Mapping[str, object],
    budget: ResourceBudget,
    task_cap: int = 12,
) -> dict[str, object]:
    initial_snapshot = ResourceSnapshot.collect()
    plan = build_warmup_plan(
        budget=budget,
        task_cap=task_cap,
        snapshot=initial_snapshot,
        ramp_workers=(1, 2, 4, 8, 12),
    )
    if plan["warmup_status"] != "ready":
        raise ResourceAwareWeightedDeckError("resource governor blocked warm-up")
    selected = manifest["meta_train_subset"]
    assert isinstance(selected, Mapping)
    ids = [str(item) for item in selected["selected_ids"]]
    target_ids = [item for item in META_TRAIN_REQUIRED if item in ids]
    warmup_ids = tuple(target_ids or ids[:2])
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    records: list[dict[str, object]] = []
    for workers in (1, 2, 4, 8, 12):
        if workers > int(plan["safe_workers"]):
            records.append({"workers": workers, "status": "not_admitted"})
            continue
        before = ResourceSnapshot.collect()
        games = _build_games(
            manifest=manifest,
            arm_id="warmup-parent",
            deck_path=Path(str(parent["deck_path"])),
            deck_sha=str(parent["deck_file_sha256"]),
            reference_ids=warmup_ids,
            games_per_opponent_seat=1,
            base_seed=WARMUP_BASE_SEED + workers * 100,
            block_id=f"resource-aware-warmup-ramp-{workers}",
            metadata={"warmup": True, "warmup_workers": workers},
        )
        destination = output / "warmup" / f"workers-{workers}" / "evaluation"
        started = time.monotonic()
        result = run_parallel_cabt_evaluation(
            games,
            output_dir=destination,
            max_workers=workers,
            worker_recycle_games=budget.recycle_games,
            overwrite=False,
        )
        elapsed = max(time.monotonic() - started, 1e-9)
        after = ResourceSnapshot.collect()
        summary = dict(result["summary"])
        records.append({
            "workers": workers,
            "status": "DONE",
            "requested_games": summary["requested_games"],
            "completed_games": summary["completed_games"],
            "faults": summary["faults"],
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": summary["completed_games"] / elapsed,
            "memory_available_before_bytes": before.memory_available_bytes,
            "memory_available_after_bytes": after.memory_available_bytes,
            "rss_before_bytes": before.process_rss_bytes,
            "rss_after_bytes": after.process_rss_bytes,
            "worker_recycle_games": budget.recycle_games,
            "worker_restarts_observed": 0,
            "restart_observation": "games did not exceed recycle_games; evaluator does not expose PID restart count",
            "fault_gate": summary["faults"] == 0,
            "output_dir": str(destination.resolve()),
        })
    telemetry = {
        "schema_version": f"{SCHEMA}-warmup",
        "manifest_path": str((output / "candidate_manifest.json").resolve()),
        "manifest_file_sha256": _file_sha(output / "candidate_manifest.json"),
        "budget": budget.to_dict(),
        "initial_plan": plan,
        "ramp": records,
        "authority": dict(AUTHORITY_FALSE),
        "performance_run_started": False,
        "no_process_kill": True,
    }
    telemetry_sha = _write_no_clobber(output / "warmup_telemetry.json", telemetry)
    return {**telemetry, "telemetry_file_sha256": telemetry_sha}


def run_weighted(
    *,
    output: Path,
    manifest: Mapping[str, object],
    budget: ResourceBudget,
) -> dict[str, object]:
    selected = manifest["meta_train_subset"]
    assert isinstance(selected, Mapping)
    ids = tuple(str(item) for item in selected["selected_ids"])
    weights = {str(k): float(v) for k, v in selected["selected_weights"].items()}
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    candidates = manifest["candidates"]
    assert isinstance(candidates, list)
    specs: list[tuple[str, Path, str]] = [
        ("weighted-parent-role-8c8c", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))
    ]
    for index, row in enumerate(candidates):
        assert isinstance(row, Mapping)
        specs.append((
            f"weighted-candidate-{index:02d}-{str(row['candidate_id'])[:12]}",
            Path(str(row["deck_path"])),
            str(row["deck_file_sha256"]),
        ))
    before = ResourceSnapshot.collect()
    governor = ResourceGovernor(budget)
    decision = governor.decide(task_cap=budget.max_workers, snapshot=before)
    if decision.recommended_workers <= 0:
        raise ResourceAwareWeightedDeckError("resource governor blocked weighted evaluation")
    workers = min(decision.recommended_workers, budget.max_workers)
    games: list[object] = []
    for arm_id, deck_path, deck_sha in specs:
        games.extend(_build_games(
            manifest=manifest,
            arm_id=arm_id,
            deck_path=deck_path,
            deck_sha=deck_sha,
            reference_ids=ids,
            games_per_opponent_seat=GAMES_PER_OPPONENT_SEAT,
            base_seed=WEIGHTED_BASE_SEED,
            block_id=f"{SCHEMA}-weighted48",
            metadata={
                "weighted_meta_train": True,
                "weighted_subset_sha256": selected["subset_sha256"],
                "opponent_weights": weights,
                "heldout_excluded": True,
            },
        ))
    expected = len(specs) * len(ids) * 2 * GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise ResourceAwareWeightedDeckError("weighted game count/identity gate failed")
    destination = output / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        tuple(games), output_dir=destination, max_workers=workers,
        worker_recycle_games=budget.recycle_games, overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped = _arm_rows(result["rows"])
    arms = {arm: _weighted_summary(rows, weights) for arm, rows in sorted(grouped.items())}
    parent_arm = specs[0][0]
    parent_score = float(arms[parent_arm]["weighted_meta_score"])
    candidate_results: list[dict[str, object]] = []
    for arm_id, _deck_path, deck_sha in specs[1:]:
        summary = arms[arm_id]
        delta = float(summary["weighted_meta_score"]) - parent_score
        candidate_results.append({
            "arm_id": arm_id,
            "deck_file_sha256": deck_sha,
            "weighted_delta_points": delta * 100.0,
            "weighted_delta": delta,
            "fault_gate": int(summary["faults"]) == 0,
            "identity_gate": bool(summary["unique_game_ids"] and summary["unique_seeds"]),
            "status": "weighted_positive_common24_eligible" if int(summary["faults"]) == 0 and delta > 0.0 else "candidate_only",
        })
    telemetry = {
        "workers": workers,
        "governor_decision": decision.to_dict(),
        "requested_games": expected,
        "completed_games": result["summary"]["completed_games"],
        "faults": result["summary"]["faults"],
        "elapsed_seconds_wall": elapsed,
        "throughput_games_per_second": result["summary"]["completed_games"] / elapsed,
        "memory_available_before_bytes": before.memory_available_bytes,
        "memory_available_after_bytes": after.memory_available_bytes,
        "rss_before_bytes": before.process_rss_bytes,
        "rss_after_bytes": after.process_rss_bytes,
        "worker_recycle_games": budget.recycle_games,
        "worker_restarts_observed": 0,
        "restart_observation": "evaluator manifest tracks recycle policy but not per-PID restart count",
    }
    summary_payload = {
        "schema_version": f"{SCHEMA}-weighted48",
        "manifest_path": str((output / "candidate_manifest.json").resolve()),
        "manifest_file_sha256": _file_sha(output / "candidate_manifest.json"),
        "weighted_subset_sha256": selected["subset_sha256"],
        "arms": arms,
        "parent_arm_id": parent_arm,
        "parent_weighted_meta_score": parent_score,
        "candidates": candidate_results,
        "telemetry": telemetry,
        "all_faults_zero": int(result["summary"]["faults"]) == 0,
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "common24 only for weighted-positive candidates; no promotion or automatic 384",
    }
    summary_sha = _write_no_clobber(output / "weighted48_summary.json", summary_payload)
    _write_text_no_clobber(
        output / "weighted48_summary.md",
        "# Resource-aware weighted48\n\n" + "\n".join(
            f"- {row['arm_id']}: {row['weighted_delta_points']:+.3f}pt vs parent; faults={row['fault_gate']}; status={row['status']}"
            for row in candidate_results
        ) + "\n",
    )
    return {**summary_payload, "summary_file_sha256": summary_sha}


def run_common24(
    *,
    output: Path,
    manifest: Mapping[str, object],
    weighted: Mapping[str, object],
    budget: ResourceBudget,
) -> dict[str, object] | None:
    positive = [
        row for row in weighted["candidates"]
        if isinstance(row, Mapping) and row.get("fault_gate") is True and float(row.get("weighted_delta", 0.0)) > 0.0
    ]
    if not positive:
        return None
    config = json.loads(COMMON24_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(item) for item in config.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise ResourceAwareWeightedDeckError("common24 config must contain 24 unique IDs")
    pool = load_opponent_pool_v1(POOL_ROOT)
    if set(references) - set(pool):
        raise ResourceAwareWeightedDeckError("common24 config has unknown pool IDs")
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    candidate_by_arm = {
        f"weighted-candidate-{index:02d}-{str(row['candidate_id'])[:12]}": row
        for index, row in enumerate(manifest["candidates"])
        if isinstance(row, Mapping)
    }
    specs: list[tuple[str, Path, str]] = [
        ("common24-parent-role-8c8c", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))
    ]
    for row in positive:
        arm = str(row["arm_id"])
        candidate = candidate_by_arm[arm]
        specs.append((arm.replace("weighted-", "common24-"), Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"])))
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    if decision.recommended_workers <= 0:
        raise ResourceAwareWeightedDeckError("resource governor blocked common24")
    workers = min(decision.recommended_workers, budget.max_workers)
    games: list[object] = []
    for arm_id, deck_path, deck_sha in specs:
        games.extend(_build_games(
            manifest=manifest, arm_id=arm_id, deck_path=deck_path, deck_sha=deck_sha,
            reference_ids=references, games_per_opponent_seat=2,
            base_seed=COMMON24_BASE_SEED, block_id=f"{SCHEMA}-common24-96",
            metadata={"common24_evaluation_only": True, "reference_config_sha256": _file_sha(COMMON24_CONFIG)},
        ))
    expected = len(specs) * 24 * 2 * 2
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise ResourceAwareWeightedDeckError("common24 game count/identity gate failed")
    destination = output / "common24-96" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        tuple(games), output_dir=destination, max_workers=workers,
        worker_recycle_games=budget.recycle_games, overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    grouped = _arm_rows(result["rows"])
    arms = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(grouped.items())}
    parent_arm = specs[0][0]
    parent_score = float(arms[parent_arm]["score_rate"])
    candidates = []
    for arm_id, _path, _sha_value in specs[1:]:
        data = arms[arm_id]
        delta = float(data["score_rate"]) - parent_score
        candidates.append({
            "arm_id": arm_id,
            "score_rate": data["score_rate"],
            "delta_points": delta * 100.0,
            "fault_gate": int(data["faults"]) == 0,
            "status": "candidate_only" if delta <= 0.0 or int(data["faults"]) else "common24_positive",
        })
    payload = {
        "schema_version": f"{SCHEMA}-common24-96",
        "manifest_path": str((output / "candidate_manifest.json").resolve()),
        "manifest_file_sha256": _file_sha(output / "candidate_manifest.json"),
        "reference_config_sha256": _file_sha(COMMON24_CONFIG),
        "arms": arms,
        "parent_arm_id": parent_arm,
        "parent_score_rate": parent_score,
        "candidates": candidates,
        "telemetry": {
            "workers": workers,
            "governor_decision": decision.to_dict(),
            "requested_games": expected,
            "completed_games": result["summary"]["completed_games"],
            "faults": result["summary"]["faults"],
            "elapsed_seconds_wall": elapsed,
            "throughput_games_per_second": result["summary"]["completed_games"] / elapsed,
            "memory_available_before_bytes": before.memory_available_bytes,
            "memory_available_after_bytes": after.memory_available_bytes,
            "rss_before_bytes": before.process_rss_bytes,
            "rss_after_bytes": after.process_rss_bytes,
            "worker_recycle_games": budget.recycle_games,
            "worker_restarts_observed": 0,
        },
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "candidate-only; no promotion or automatic longrun",
    }
    summary_sha = _write_no_clobber(output / "common24_summary.json", payload)
    return {**payload, "summary_file_sha256": summary_sha}


def load_sealed_manifest(output: Path) -> dict[str, object]:
    """Reload a prepared root and recheck source/candidate bytes before execution."""

    path = output / "candidate_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResourceAwareWeightedDeckError(f"cannot reload sealed manifest: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise ResourceAwareWeightedDeckError("sealed manifest schema mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE:
        raise ResourceAwareWeightedDeckError("sealed manifest authority mismatch")
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise ResourceAwareWeightedDeckError("sealed manifest parent is malformed")
    if _file_sha(Path(str(parent["deck_path"]))) != parent.get("deck_file_sha256"):
        raise ResourceAwareWeightedDeckError("parent deck changed after seal")
    if _file_sha(Path(str(parent["policy_path"]))) != parent.get("policy_sha256"):
        raise ResourceAwareWeightedDeckError("parent policy changed after seal")
    subset = manifest.get("meta_train_subset")
    if not isinstance(subset, Mapping):
        raise ResourceAwareWeightedDeckError("sealed subset is malformed")
    reloaded_subset = load_meta_train_subset(Path(str(subset["source_path"])))
    if reloaded_subset["subset_sha256"] != subset.get("subset_sha256"):
        raise ResourceAwareWeightedDeckError("META_TRAIN subset changed after seal")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != MAX_CANDIDATES:
        raise ResourceAwareWeightedDeckError("sealed candidate count is not exactly two")
    for row in candidates:
        if not isinstance(row, Mapping):
            raise ResourceAwareWeightedDeckError("sealed candidate row is malformed")
        candidate_path = Path(str(row["deck_path"]))
        if _file_sha(candidate_path) != row.get("deck_file_sha256"):
            raise ResourceAwareWeightedDeckError(f"candidate deck changed after seal: {candidate_path}")
        cards = tuple(parse_deck_csv_bytes(candidate_path.read_bytes()))
        if deck_multiset_identity_v1(cards) != row.get("deck_multiset_sha256"):
            raise ResourceAwareWeightedDeckError(f"candidate multiset changed after seal: {candidate_path}")
        if row.get("novel_against_prior_decks") is not True:
            raise ResourceAwareWeightedDeckError("candidate novelty proof is not sealed")
    return manifest


def _execute_sealed(*, output: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = run_warmup(output=output, manifest=manifest, budget=budget)
    weighted = run_weighted(output=output, manifest=manifest, budget=budget)
    common24 = run_common24(output=output, manifest=manifest, weighted=weighted, budget=budget)
    final = {
        "schema_version": SCHEMA,
        "output_root": str(output.resolve()),
        "manifest_sha256": _file_sha(output / "candidate_manifest.json"),
        "warmup_telemetry_sha256": _file_sha(output / "warmup_telemetry.json"),
        "weighted_summary_sha256": _file_sha(output / "weighted48_summary.json"),
        "common24_summary": common24,
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": True,
    }
    _write_no_clobber(output / "final_summary.json", final)
    return final


def execute(*, output: Path, generator_seed: int = GENERATOR_SEED_DEFAULT) -> dict[str, object]:
    manifest = materialize_manifest(output=output, generator_seed=generator_seed)
    return _execute_sealed(output=output, manifest=manifest)


def execute_existing(*, output: Path) -> dict[str, object]:
    """Execute only after a separate prepare-only seal has completed."""

    return _execute_sealed(output=output, manifest=load_sealed_manifest(output))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--generator-seed", type=int, default=GENERATOR_SEED_DEFAULT)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-existing", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sum(bool(value) for value in (args.prepare_only, args.execute, args.run_existing)) != 1:
        raise SystemExit("choose exactly one of --prepare-only, --execute, or --run-existing")
    output = args.output.resolve()
    if args.prepare_only:
        manifest = materialize_manifest(output=output, generator_seed=args.generator_seed)
        print(json.dumps({
            "output": str(output),
            "manifest_sha256": manifest["manifest_sha256"],
            "candidate_ids": [row["candidate_id"] for row in manifest["candidates"]],
            "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"],
        }, ensure_ascii=False, sort_keys=True, indent=2))
    elif args.execute:
        print(json.dumps(execute(output=output, generator_seed=args.generator_seed), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(json.dumps(execute_existing(output=output), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTHORITY_FALSE",
    "ResourceAwareWeightedDeckError",
    "SCHEMA",
    "load_meta_train_subset",
    "load_sealed_manifest",
    "materialize_manifest",
    "execute_existing",
    "run_common24",
    "run_warmup",
    "run_weighted",
    "select_novel_candidates",
]
