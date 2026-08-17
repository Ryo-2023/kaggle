#!/usr/bin/env python3
"""Research-only META_TRAIN weighted successive-halving for deck mutations.

This wrapper starts from the already observed role-8c8c69dc792c913f deck and
keeps the Tomato native policy fixed.  It materializes at most four new legal
1/2-card mutations, excludes every multiset already present under the sprint
roots, and evaluates only a hash-bound top-weight META_TRAIN subset.  The
result is an evaluation artifact; it never grants training, promotion,
submission, or long-run authority and never edits a production entrypoint.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    generate_deck_mutation_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    deck_multiset_identity_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
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


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-deck-mutation-weighted-halving-v1"
SCHEDULE_PATH = ROOT / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json"
PARENT_DECK = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/candidate-manifest/role-8c8c69dc792c913f/deck.csv"
POLICY_PATH = ROOT / "opponents/tomatomato_archaludon/main.py"
POOL_ROOT = ROOT / "opponents"
REFERENCE_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
KNOWN_CARD_DATABASE = ROOT / "data/raw/EN_Card_Data.csv"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/deck-mutation-weighted-halving-v1-20260813"
BASE_SEED_DEFAULT = 18_200_000
COMMON24_BASE_SEED_DEFAULT = 18_210_000
COMMON24_384_BASE_SEED_DEFAULT = 18_220_000
GENERATOR_SEED_DEFAULT = 20_260_814
BLOCK_ID = "deck-mutation-weighted-halving-v1-20260813"
TOP_K = 12
GAMES_PER_OPPONENT_SEAT = 2
MAX_CANDIDATES = 4
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}
WEIGHT_FORMULA = (
    "score=win+0.5*draw; r_i=(W_i+0.5*D_i)/N_i; "
    "S_w=sum_i(weight_i*r_i)/sum_i(weight_i); delta_w=S_w(candidate)-S_w(parent)"
)


class WeightedHalvingError(ValueError):
    """Raised when the sealed research-only contract is not closed."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha_bytes(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, payload: object) -> str:
    return _atomic_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _fresh_root(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"refusing non-empty output root: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _read_schedule(path: Path) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise WeightedHalvingError("schedule must be an object with entries")
    if document.get("research_only") is not True:
        raise WeightedHalvingError("schedule is not research-only")
    if document.get("authority", {}).get("promotion_authority") is not False:
        raise WeightedHalvingError("schedule promotion authority is not false")
    rows = [row for row in document["entries"] if isinstance(row, dict)]
    selected = [row for row in rows if row.get("split") == "META_TRAIN"]
    if len(selected) < TOP_K:
        raise WeightedHalvingError("META_TRAIN schedule has fewer than top-k entries")
    selected.sort(key=lambda row: (-float(row["weight"]), str(row["opponent_id"])))
    selected = selected[:TOP_K]
    if len({row.get("opponent_id") for row in selected}) != TOP_K:
        raise WeightedHalvingError("selected META_TRAIN IDs are not unique")
    if any(float(row.get("weight", 0.0)) <= 0.0 for row in selected):
        raise WeightedHalvingError("selected META_TRAIN weights must be positive")
    if any(row.get("teacher_behavior_allowed") is not False or row.get("training_exposure_allowed") is not False for row in selected):
        raise WeightedHalvingError("selected schedule leaks teacher/training authority")
    return document, tuple(selected)


def _existing_multisets() -> dict[str, list[str]]:
    """Collect all prior sprint deck identities, failing closed on malformed decks."""
    candidates = {ROOT / "opponents" / "tomatomato_archaludon" / "deck.csv", PARENT_DECK}
    candidates.update(ROOT.glob("runs/final-sprint-autonomous/**/deck.csv"))
    identities: dict[str, list[str]] = {}
    vocabulary = load_production_card_vocabulary_v1()
    for path in sorted(candidates):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:  # no silent omission of a prior artifact
            raise WeightedHalvingError(f"malformed prior deck artifact: {path}: {exc}") from exc
        identity = deck_multiset_identity_v1(cards)
        identities.setdefault(identity, []).append(str(path.resolve()))
    return identities


def _write_deck(path: Path, cards: Sequence[int]) -> str:
    payload = ("\n".join(str(card) for card in cards) + "\n").encode("utf-8")
    return _atomic_bytes(path, payload)


def prepare_manifest(*, output: Path, generator_seed: int = GENERATOR_SEED_DEFAULT) -> dict[str, object]:
    _fresh_root(output)
    schedule, selected = _read_schedule(SCHEDULE_PATH)
    vocabulary = load_production_card_vocabulary_v1()
    parent_cards = tuple(parse_deck_csv_bytes(PARENT_DECK.read_bytes()))
    validate_deck(parent_cards, known_card_ids=vocabulary.recognized_card_ids)
    parent_multiset = deck_multiset_identity_v1(parent_cards)
    prior = _existing_multisets()
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
        candidates_per_swap=8,
        seed=generator_seed,
        known_card_ids=vocabulary.recognized_card_ids,
    )
    chosen = [candidate for candidate in generated if candidate.deck_multiset_sha256 not in prior]
    # Successive-halving starts with two one-card and two two-card surfaces.
    chosen = [candidate for candidate in chosen if candidate.swap_count == 1][:2] + [
        candidate for candidate in chosen if candidate.swap_count == 2
    ][:2]
    if not chosen:
        raise WeightedHalvingError("no untested mutation candidates remain")
    policy_sha = _sha_file(POLICY_PATH)
    parent_file_sha = _sha_file(PARENT_DECK)
    candidate_rows: list[dict[str, object]] = []
    for ordinal, candidate in enumerate(chosen):
        deck_path = output / "candidates" / candidate.candidate_id / "deck.csv"
        deck_sha = _write_deck(deck_path, candidate.card_ids)
        candidate_rows.append({
            "candidate_id": candidate.candidate_id,
            "ordinal": ordinal,
            "swap_count": candidate.swap_count,
            "removed_cards": list(candidate.removed_cards),
            "added_cards": list(candidate.added_cards),
            "deck_path": str(deck_path.resolve()),
            "deck_file_sha256": deck_sha,
            "deck_multiset_sha256": candidate.deck_multiset_sha256,
            "parent_deck_multiset_sha256": parent_multiset,
            **AUTHORITY_FALSE,
        })
    selected_serial = [dict(row) for row in selected]
    selected_ids = [str(row["opponent_id"]) for row in selected]
    selected_weights = {str(row["opponent_id"]): float(row["weight"]) for row in selected}
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "purpose": "META_TRAIN_WEIGHTED_DECK_SUCCESSIVE_HALVING_RESEARCH_ONLY",
        "iteration": 1,
        "parent": {
            "candidate_id": "role-8c8c69dc792c913f",
            "deck_path": str(PARENT_DECK.resolve()),
            "deck_file_sha256": parent_file_sha,
            "deck_multiset_sha256": parent_multiset,
            "policy_path": str(POLICY_PATH.resolve()),
            "policy_sha256": policy_sha,
            "usage_boundary": "local_eval_only",
        },
        "candidate_generation": {
            "generator_module": "src/mage_ptcg/meta_specialist/deck_mutation_v1.py",
            "generator_module_sha256": _sha_file(ROOT / "src/mage_ptcg/meta_specialist/deck_mutation_v1.py"),
            "generator_seed": generator_seed,
            "replacement_pool": list(replacement_pool),
            "known_card_database_path": str(KNOWN_CARD_DATABASE.resolve()),
            "known_card_database_sha256": _sha_file(KNOWN_CARD_DATABASE),
            "core_signature": {"57": 1, "169": 4, "190": 4, "666": 4},
            "prior_multiset_count": len(prior),
            "prior_multiset_sha256": _sha_bytes(sorted(prior)),
        },
        "schedule": {
            "path": str(SCHEDULE_PATH.resolve()),
            "file_sha256": _sha_file(SCHEDULE_PATH),
            "semantic_sha256": str(schedule.get("schedule_sha256")),
            "split": "META_TRAIN",
            "top_k": TOP_K,
            "selected_ids": selected_ids,
            "selected_weights": selected_weights,
            "selected_weight_sum": sum(selected_weights.values()),
            "entries": selected_serial,
            "subset_sha256": _sha_bytes(selected_serial),
            "heldout_excluded": list(schedule.get("excluded_heldout", [])),
        },
        "objective": {
            "formula": WEIGHT_FORMULA,
            "games_per_opponent_seat": GAMES_PER_OPPONENT_SEAT,
            "games_per_arm": TOP_K * 2 * 2,
            "base_seed": BASE_SEED_DEFAULT,
            "same_seed_schedule_across_arms": True,
            "faults_preserve_requested_denominator": True,
            "positive_weighted_next_gate": "common24-96",
            "strong_positive_384_gate": "+3.0 weighted points with fault0",
        },
        "candidates": candidate_rows,
        "pool_manifest_path": str((POOL_ROOT / "pool_manifest.json").resolve()),
        "pool_manifest_sha256": _sha_file(POOL_ROOT / "pool_manifest.json"),
        "reference_config_path": str(REFERENCE_CONFIG.resolve()),
        "reference_config_sha256": _sha_file(REFERENCE_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "block_id": BLOCK_ID,
        "authority": dict(AUTHORITY_FALSE),
        "research_execution_allowed": True,
        "candidate_status": "candidate_only",
    }
    manifest_sha = _atomic_json(output / "candidate_manifest.json", manifest)
    return {**manifest, "manifest_sha256": manifest_sha, "output_root": str(output.resolve())}


def _verify_manifest(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA or manifest.get("authority") != AUTHORITY_FALSE:
        raise WeightedHalvingError("manifest schema/authority mismatch")
    schedule, selected = _read_schedule(SCHEDULE_PATH)
    schedule_info = manifest.get("schedule", {})
    if schedule_info.get("file_sha256") != _sha_file(SCHEDULE_PATH) or schedule_info.get("semantic_sha256") != schedule.get("schedule_sha256"):
        raise WeightedHalvingError("schedule source changed after manifest seal")
    if schedule_info.get("subset_sha256") != _sha_bytes([dict(row) for row in selected]):
        raise WeightedHalvingError("weighted subset changed after manifest seal")
    if manifest.get("parent", {}).get("policy_sha256") != _sha_file(POLICY_PATH):
        raise WeightedHalvingError("native policy bytes changed after manifest seal")
    if manifest.get("pool_manifest_sha256") != _sha_file(POOL_ROOT / "pool_manifest.json"):
        raise WeightedHalvingError("pool manifest changed after manifest seal")
    for row in manifest.get("candidates", []):
        path = Path(str(row["deck_path"]))
        if _sha_file(path) != row.get("deck_file_sha256"):
            raise WeightedHalvingError(f"candidate deck bytes changed: {path}")
        cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
        if deck_multiset_identity_v1(cards) != row.get("deck_multiset_sha256"):
            raise WeightedHalvingError(f"candidate multiset changed: {path}")
    return manifest


def _weighted_arm(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    by_opponent: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_opponent[str(row.get("opponent_id"))].append(row)
    per_opponent: dict[str, object] = {}
    weighted_numerator = 0.0
    weighted_denominator = 0.0
    for opponent_id, weight in weights.items():
        values = by_opponent.get(opponent_id, [])
        score = sum(1.0 if r.get("outcome") == "win" else 0.5 if r.get("outcome") == "draw" else 0.0 for r in values)
        denominator = len(values)
        rate = score / denominator if denominator else None
        per_opponent[opponent_id] = {"weight": weight, "requested": denominator, "score": score, "rate": rate}
        if rate is not None:
            weighted_numerator += weight * rate
            weighted_denominator += weight
    seats = {str(seat): aggregate_ledger_v1([r for r in rows if r.get("seat") == seat]) for seat in (0, 1)}
    aggregate = aggregate_ledger_v1(rows)
    return {
        **aggregate,
        "weighted_meta_score": weighted_numerator / weighted_denominator if weighted_denominator else None,
        "weighted_meta_numerator": weighted_numerator,
        "weighted_meta_weight_denominator": weighted_denominator,
        "per_opponent": per_opponent,
        "seat_summaries": seats,
        "unique_game_ids": len({str(r.get("game_id")) for r in rows}) == len(rows),
        "unique_seeds": len({int(r.get("seed")) for r in rows}) == len(rows),
    }


def execute(*, manifest_path: Path, output: Path, workers: int = 12) -> dict[str, object]:
    manifest = _verify_manifest(manifest_path)
    pool = load_opponent_pool_v1(POOL_ROOT)
    selected_ids = tuple(str(item) for item in manifest["schedule"]["selected_ids"])
    weights = {str(k): float(v) for k, v in manifest["schedule"]["selected_weights"].items()}
    policy_path = Path(str(manifest["parent"]["policy_path"]))
    policy_sha = str(manifest["parent"]["policy_sha256"])
    specs: list[tuple[str, Path, str]] = [("weighted-parent-role-8c8c", Path(str(manifest["parent"]["deck_path"])), str(manifest["parent"]["deck_file_sha256"]))]
    specs.extend((f"weighted-candidate-{index:02d}-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"])) for index, row in enumerate(manifest["candidates"]))
    all_games = []
    for arm_id, deck_path, deck_sha in specs:
        env: dict[str, str] = {}
        biases: dict[str, float] = {}
        candidate_spec = {
            "main_path": str(policy_path),
            "deck_path": str(deck_path),
            "policy_sha256": policy_sha,
            "deck_sha256": deck_sha,
            "env": env,
            "biases": biases,
            "config_sha256": _config_sha(env, biases),
            "pool_root": str(POOL_ROOT),
        }
        built = build_native_candidate_games_v1(
            candidate_id=arm_id,
            candidate=candidate_spec,
            pool=pool,
            reference_ids=selected_ids,
            games_per_opponent_seat=GAMES_PER_OPPONENT_SEAT,
            base_seed=int(manifest["objective"]["base_seed"]),
            block_id=BLOCK_ID,
        )
        all_games.extend(replace(game, metadata={
            **dict(game.metadata),
            "comparison_arm": arm_id,
            "weighted_subset": True,
            "weighted_subset_sha256": manifest["schedule"]["subset_sha256"],
            "opponent_weight": weights.get(game.opponent_id),
            "manifest_sha256": _sha_file(manifest_path),
            **AUTHORITY_FALSE,
        }) for game in built)
    expected = len(specs) * TOP_K * 2 * GAMES_PER_OPPONENT_SEAT
    if len(all_games) != expected or len({game.game_id for game in all_games}) != expected:
        raise WeightedHalvingError(f"game identity/count gate failed: {len(all_games)} expected {expected}")
    result = run_parallel_cabt_evaluation(
        tuple(all_games), output_dir=output / "evaluation", max_workers=workers,
        worker_recycle_games=16, overwrite=False,
    )
    arms: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        arms[str(row.get("metadata", {}).get("comparison_arm"))].append(row)
    arm_summary = {arm: _weighted_arm(rows, weights) for arm, rows in sorted(arms.items())}
    parent_score = float(arm_summary[specs[0][0]]["weighted_meta_score"])
    candidate_summary: list[dict[str, object]] = []
    for arm_id, deck_path, deck_sha in specs[1:]:
        summary = arm_summary[arm_id]
        delta = float(summary["weighted_meta_score"]) - parent_score
        faults_ok = int(summary["faults"]) == 0
        candidate_summary.append({
            "arm_id": arm_id,
            "deck_path": str(deck_path),
            "deck_file_sha256": deck_sha,
            "weighted_delta_vs_parent": delta,
            "weighted_delta_points": 100.0 * delta,
            "fault_gate": faults_ok,
            "identity_gate": bool(summary["unique_game_ids"] and summary["unique_seeds"]),
            "status": "positive_weighted_next_common24_96" if faults_ok and delta > 0.0 else "candidate_only",
            "strong_positive_384_gate": bool(faults_ok and delta >= 0.03),
        })
    summary = {
        "schema_version": SCHEMA,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": _sha_file(manifest_path),
        "output_root": str(output.resolve()),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "requested_games": expected,
        "arms": arm_summary,
        "parent_arm_id": specs[0][0],
        "parent_weighted_meta_score": parent_score,
        "candidates": candidate_summary,
        "all_faults_zero": all(int(value["faults"]) == 0 for value in arm_summary.values()),
        **AUTHORITY_FALSE,
        "next_gate": "only positive weighted candidates may receive common24-96; no 384 auto-start",
    }
    summary_sha = _atomic_json(output / "weighted_summary.json", summary)
    md = [
        f"# META_TRAIN weighted deck halving ({SCHEMA})",
        "",
        f"- manifest SHA: `{summary['manifest_sha256']}`",
        f"- weighted subset: `{manifest['schedule']['subset_sha256']}` ({TOP_K} opponents, {TOP_K * 2 * 2} games/arm)",
        f"- parent weighted score: `{parent_score:.6f}`",
        "",
        "| arm | weighted score | delta pt | faults | status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in candidate_summary:
        arm = str(row["arm_id"])
        arm_data = arm_summary[arm]
        md.append(f"| {arm} | {float(arm_data['weighted_meta_score']):.6f} | {float(row['weighted_delta_points']):+.3f} | {arm_data['faults']} | {row['status']} |")
    md.extend(["", "No training, promotion, submission, or long-run authority is granted.", ""])
    md_sha = _atomic_bytes(output / "weighted_summary.md", "\n".join(md).encode("utf-8"))
    summary["summary_sha256"] = summary_sha
    summary["markdown_sha256"] = md_sha
    _atomic_json(output / "weighted_summary.json", summary)
    return summary


def execute_common24(*, manifest_path: Path, output: Path, workers: int = 12,
                     games_per_opponent_seat: int = GAMES_PER_OPPONENT_SEAT,
                     base_seed: int = COMMON24_BASE_SEED_DEFAULT,
                     candidate_indices: Sequence[int] | None = None,
                     output_suffix: str = "common24-96") -> dict[str, object]:
    """Run the next equal-weight common24 guardrail for positive screen arms."""
    manifest = _verify_manifest(manifest_path)
    weighted_path = output / "weighted_summary.json"
    if not weighted_path.is_file():
        raise WeightedHalvingError("weighted screen summary is required before common24")
    weighted = json.loads(weighted_path.read_text(encoding="utf-8"))
    positive = {
        str(row["arm_id"])
        for row in weighted.get("candidates", [])
        if row.get("fault_gate") is True and float(row.get("weighted_delta_vs_parent", 0.0)) > 0.0
    }
    all_candidate_rows = list(manifest["candidates"])
    selected_indices = tuple(range(len(all_candidate_rows))) if candidate_indices is None else tuple(candidate_indices)
    if not selected_indices or any(index < 0 or index >= len(all_candidate_rows) for index in selected_indices):
        raise WeightedHalvingError("common24 candidate index selection is empty or out of range")
    candidate_rows = [all_candidate_rows[index] for index in selected_indices]
    expected_positive_arm_ids = {
        f"weighted-candidate-{index:02d}-{str(all_candidate_rows[index]['candidate_id'])[:12]}"
        for index in selected_indices
    }
    if not expected_positive_arm_ids.issubset(positive):
        raise WeightedHalvingError("common24 gate requires every selected candidate to be positive and fault-free")
    references_raw = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(item) for item in references_raw.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise WeightedHalvingError("common24 reference must contain exactly 24 unique IDs")
    pool = load_opponent_pool_v1(POOL_ROOT)
    if set(references) - set(pool):
        raise WeightedHalvingError("common24 reference contains unknown pool IDs")
    common_root = output / output_suffix
    if common_root.exists() and any(common_root.iterdir()):
        raise FileExistsError(f"refusing non-empty common24 root: {common_root}")
    common_root.mkdir(parents=True, exist_ok=True)
    policy_path = Path(str(manifest["parent"]["policy_path"]))
    policy_sha = str(manifest["parent"]["policy_sha256"])
    specs: list[tuple[str, Path, str]] = [
        ("common24-parent-role-8c8c", Path(str(manifest["parent"]["deck_path"])), str(manifest["parent"]["deck_file_sha256"]))
    ]
    specs.extend(
        (f"common24-candidate-{index:02d}-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"]))
        for index, row in enumerate(candidate_rows)
    )
    common_manifest = {
        "schema_version": f"{SCHEMA}-{output_suffix}",
        "source_manifest_path": str(manifest_path.resolve()),
        "source_manifest_sha256": _sha_file(manifest_path),
        "weighted_summary_sha256": _sha_file(weighted_path),
        "reference_config_path": str(REFERENCE_CONFIG.resolve()),
        "reference_config_sha256": _sha_file(REFERENCE_CONFIG),
        "reference_ids": list(references),
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "games_per_arm": len(references) * 2 * games_per_opponent_seat,
        "same_seed_schedule_across_arms": True,
        "candidate_arms": [arm for arm, _path, _sha in specs],
        "policy_sha256": policy_sha,
        "authority": dict(AUTHORITY_FALSE),
        "research_execution_allowed": True,
    }
    common_manifest_sha = _atomic_json(common_root / "common24_manifest.json", common_manifest)
    all_games = []
    for arm_id, deck_path, deck_sha in specs:
        env: dict[str, str] = {}
        biases: dict[str, float] = {}
        candidate_spec = {
            "main_path": str(policy_path),
            "deck_path": str(deck_path),
            "policy_sha256": policy_sha,
            "deck_sha256": deck_sha,
            "env": env,
            "biases": biases,
            "config_sha256": _config_sha(env, biases),
            "pool_root": str(POOL_ROOT),
        }
        built = build_native_candidate_games_v1(
            candidate_id=arm_id,
            candidate=candidate_spec,
            pool=pool,
            reference_ids=references,
            games_per_opponent_seat=games_per_opponent_seat,
            base_seed=base_seed,
            block_id=f"{BLOCK_ID}-{output_suffix}",
        )
        all_games.extend(replace(game, metadata={
            **dict(game.metadata),
            "comparison_arm": arm_id,
            "common24_guardrail": True,
            "common24_reference_count": len(references),
            "source_manifest_sha256": common_manifest_sha,
            **AUTHORITY_FALSE,
        }) for game in built)
    expected = len(specs) * len(references) * 2 * games_per_opponent_seat
    if len(all_games) != expected or len({game.game_id for game in all_games}) != expected:
        raise WeightedHalvingError(f"common24 game identity/count gate failed: {len(all_games)} expected {expected}")
    result = run_parallel_cabt_evaluation(
        tuple(all_games), output_dir=common_root / "evaluation", max_workers=workers,
        worker_recycle_games=16, overwrite=False,
    )
    arms: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        arms[str(row.get("metadata", {}).get("comparison_arm"))].append(row)
    arm_summary = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(arms.items())}
    parent_arm = specs[0][0]
    parent_rate = float(arm_summary[parent_arm]["score_rate"])
    candidates: list[dict[str, object]] = []
    for arm, _deck_path, _deck_sha in specs[1:]:
        delta = float(arm_summary[arm]["score_rate"]) - parent_rate
        data = arm_summary[arm]
        candidates.append({
            "arm_id": arm,
            "score_rate": float(data["score_rate"]),
            "delta_vs_parent": delta,
            "delta_points": 100.0 * delta,
            "fault_gate": int(data["faults"]) == 0,
            "status": ("common24_positive_next_768" if output_suffix != "common24-96" else "common24_positive_next_384") if int(data["faults"]) == 0 and delta > 0.0 else "candidate_only",
            "strong_positive_384_gate": bool(int(data["faults"]) == 0 and delta >= 0.03),
        })
    summary = {
        "schema_version": f"{SCHEMA}-{output_suffix}",
        "manifest_path": str((common_root / "common24_manifest.json").resolve()),
        "manifest_sha256": common_manifest_sha,
        "source_weighted_manifest_sha256": _sha_file(manifest_path),
        "source_weighted_summary_sha256": _sha_file(weighted_path),
        "output_root": str(common_root.resolve()),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "requested_games": expected,
        "arms": arm_summary,
        "parent_arm_id": parent_arm,
        "parent_score_rate": parent_rate,
        "candidates": candidates,
        "all_faults_zero": all(int(value["faults"]) == 0 for value in arm_summary.values()),
        **AUTHORITY_FALSE,
        "next_gate": "only common24-positive candidates may receive 384/768; no further auto-start",
    }
    summary_sha = _atomic_json(common_root / "common24_summary.json", summary)
    md = [
        f"# common24 guardrail ({SCHEMA})", "",
        f"- manifest SHA: `{common_manifest_sha}`",
        f"- weighted source SHA: `{summary['source_weighted_summary_sha256']}`",
        f"- parent score: `{parent_rate:.6f}`", "",
        "| arm | score | delta pt | faults | status |", "|---|---:|---:|---:|---|",
    ]
    for row in candidates:
        md.append(f"| {row['arm_id']} | {float(row['score_rate']):.6f} | {float(row['delta_points']):+.3f} | {arm_summary[row['arm_id']]['faults']} | {row['status']} |")
    md.extend(["", "No training, promotion, submission, or long-run authority is granted.", ""])
    md_sha = _atomic_bytes(common_root / "common24_summary.md", "\n".join(md).encode("utf-8"))
    summary["summary_sha256"] = summary_sha
    summary["markdown_sha256"] = md_sha
    _atomic_json(common_root / "common24_summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--common24-96", action="store_true")
    parser.add_argument("--common24-384", action="store_true")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--generator-seed", type=int, default=GENERATOR_SEED_DEFAULT)
    parser.add_argument("--base-seed", type=int, default=BASE_SEED_DEFAULT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.base_seed != BASE_SEED_DEFAULT:
        raise SystemExit("base seed is sealed by the manifest design")
    if sum(bool(value) for value in (args.prepare_only, args.execute, args.common24_96, args.common24_384)) != 1:
        raise SystemExit("choose exactly one of --prepare-only, --execute, --common24-96, or --common24-384")
    output = args.output.resolve()
    if args.prepare_only:
        payload = prepare_manifest(output=output, generator_seed=args.generator_seed)
        print(json.dumps({"output": str(output), "manifest_sha256": payload["manifest_sha256"], "candidates": payload["candidates"], "weighted_subset_sha256": payload["schedule"]["subset_sha256"]}, ensure_ascii=False, sort_keys=True, indent=2))
    elif args.execute:
        payload = execute(manifest_path=output / "candidate_manifest.json", output=output, workers=args.workers)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    elif args.common24_96:
        payload = execute_common24(manifest_path=output / "candidate_manifest.json", output=output, workers=args.workers)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        payload = execute_common24(
            manifest_path=output / "candidate_manifest.json", output=output, workers=args.workers,
            games_per_opponent_seat=8, base_seed=COMMON24_384_BASE_SEED_DEFAULT,
            candidate_indices=(0,), output_suffix="common24-384-a73",
        )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
