#!/usr/bin/env python3
"""Automatically search META_TRAIN-weighted one-card deck candidates.

This is a research-only bridge from the sealed META_TRAIN distribution to the
existing native arena.  It materializes a deterministic parent plus novel
candidate decks, evaluates the complete block in parallel, and stops at the
weighted 48-game stage.  A positive arm is reported for a separate common24
gate; no promotion, training, submission, or longrun is started here.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Mapping, Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1, deck_multiset_identity_v1
from mage_ptcg.meta_specialist.meta_weighted_deck_search_v1 import (
    MetaWeightedDeckSearchError,
    build_replacement_pool_v1,
    build_weighted_card_frequency_v1,
    generate_meta_weighted_candidates_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-meta-weighted-deck-search-v1-runner"
PARENT_ID = "tomatomato_archaludon-meta-auto-parent"
PARENT_DECK = ROOT / "opponents/tomatomato_archaludon/deck.csv"
PARENT_POLICY = ROOT / "opponents/tomatomato_archaludon/main.py"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_ROOT = ROOT / "opponents"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/meta-weighted-deck-search-v1-20260814"
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 4
DEFAULT_GENERATOR_SEED = 20260814
DEFAULT_BASE_SEED = 23600000
GAMES_PER_OPPONENT_SEAT = 2
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class MetaWeightedDeckRunnerError(ValueError):
    """Raised when the automatic deck-search runner cannot close its contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MetaWeightedDeckRunnerError(f"cannot read artifact: {path}") from exc


def _write_json_no_clobber(path: Path, payload: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
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
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def _write_deck_no_clobber(path: Path, cards: Sequence[int]) -> str:
    return _write_bytes_no_clobber(path, ("\n".join(str(int(card)) for card in cards) + "\n").encode("utf-8"))


def _write_bytes_no_clobber(path: Path, raw: bytes) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
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


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise MetaWeightedDeckRunnerError("output must be a child of runs/final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise MetaWeightedDeckRunnerError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _existing_multisets() -> set[str]:
    vocabulary = load_production_card_vocabulary_v1()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    identities: set[str] = set()
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:
            raise MetaWeightedDeckRunnerError(f"malformed existing deck while proving novelty: {path}") from exc
        identities.add(deck_multiset_identity_v1(cards))
    return identities


def _parent_cards() -> tuple[int, ...]:
    vocabulary = load_production_card_vocabulary_v1()
    try:
        cards = tuple(parse_deck_csv_bytes(PARENT_DECK.read_bytes()))
        validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
    except Exception as exc:
        raise MetaWeightedDeckRunnerError("parent deck failed legality validation") from exc
    return cards


def _selected_deck_paths(subset: Mapping[str, object], pool: Mapping[str, object]) -> dict[str, Path]:
    selected = tuple(str(item) for item in subset["selected_ids"])
    paths: dict[str, Path] = {}
    for opponent_id in selected:
        instance = pool.get(opponent_id)
        if instance is None:
            raise MetaWeightedDeckRunnerError(f"selected META_TRAIN opponent is absent from pool: {opponent_id}")
        path = Path(str(instance.deck_csv_path)).resolve()
        if not path.is_file():
            raise MetaWeightedDeckRunnerError(f"selected META_TRAIN deck is missing: {path}")
        paths[opponent_id] = path
    return paths


def materialize_manifest(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    """Materialize the parent, distribution-derived candidates, and manifest."""

    try:
        output = _fresh_root(output)
        parent_cards = _parent_cards()
        pool = load_opponent_pool_v1(POOL_ROOT)
        subset = load_meta_train_subset(META_MANIFEST)
        deck_paths = _selected_deck_paths(subset, pool)
        frequency_rows = build_weighted_card_frequency_v1(
            deck_paths=deck_paths,
            selected_ids=tuple(str(item) for item in subset["selected_ids"]),
            selected_weights={str(key): float(value) for key, value in subset["selected_weights"].items()},
        )
        vocabulary = load_production_card_vocabulary_v1()
        replacement_pool = build_replacement_pool_v1(
            frequency_rows=frequency_rows,
            parent_cards=parent_cards,
            known_card_ids=vocabulary.recognized_card_ids,
            limit=64,
        )
        candidates = generate_meta_weighted_candidates_v1(
            parent_cards=parent_cards,
            replacement_pool=replacement_pool,
            card_frequency={int(card): float(freq) for card, freq, _support in frequency_rows},
            prior_multisets=_existing_multisets(),
            known_card_ids=vocabulary.recognized_card_ids,
            core_signature=CoreSignatureV1(
                archetype_id="archaludon-cinderace",
                required_counts={57: 1, 169: 4, 190: 4, 666: 4},
            ),
            candidate_count=candidate_count,
            seed=generator_seed,
            candidates_per_swap=max(512, candidate_count * 128),
        )
    except (MetaWeightedDeckSearchError, OSError, ValueError, TypeError) as exc:
        raise MetaWeightedDeckRunnerError(str(exc)) from exc

    materialized: list[dict[str, object]] = []
    for ordinal, candidate in enumerate(candidates):
        deck_path = output / "candidates" / candidate.candidate_id / "deck.csv"
        deck_sha = _write_deck_no_clobber(deck_path, candidate.card_ids)
        materialized.append({
            **candidate.to_dict(),
            "ordinal": ordinal,
            "deck_path": str(deck_path.resolve()),
            "deck_file_sha256": deck_sha,
            "weighted_frequency_added_card": next(
                (float(freq) for card, freq, _support in frequency_rows if int(card) == candidate.added_cards[0]),
                0.0,
            ),
            **AUTHORITY_FALSE,
        })

    manifest = {
        "schema_version": SCHEMA,
        "purpose": "META_TRAIN_WEIGHTED_AUTOMATIC_DECK_SEARCH_RESEARCH_ONLY",
        "parent": {
            "candidate_id": PARENT_ID,
            "deck_path": str(PARENT_DECK.resolve()),
            "deck_file_sha256": _file_sha(PARENT_DECK),
            "deck_multiset_sha256": deck_multiset_identity_v1(parent_cards),
            "policy_path": str(PARENT_POLICY.resolve()),
            "policy_file_sha256": _file_sha(PARENT_POLICY),
            "usage_boundary": "local_eval_only",
        },
        "meta_train": {
            **dict(subset),
            "selected_deck_paths": {key: str(value) for key, value in sorted(deck_paths.items())},
            "frequency_rows": [
                {"card_id": int(card), "weighted_frequency": float(freq), "weighted_deck_support": float(support)}
                for card, freq, support in frequency_rows
            ],
            "replacement_pool": list(replacement_pool),
        },
        "candidate_generation": {
            "module": "src/mage_ptcg/meta_specialist/meta_weighted_deck_search_v1.py",
            "module_sha256": _file_sha(ROOT / "src/mage_ptcg/meta_specialist/meta_weighted_deck_search_v1.py"),
            "generator_seed": generator_seed,
            "candidate_count": candidate_count,
            "novelty_scan": "opponents/**/deck.csv + runs/final-sprint-autonomous/**/deck.csv",
        },
        "protocol": {
            "weighted_games_per_arm": len(subset["selected_ids"]) * 2 * GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "workers_requested": workers,
            "worker_recycle_games": worker_recycle_games,
            "stages": [48, 96, 384, 768, 1536],
            "common24_auto_start": False,
            "confirmation_auto_start": False,
        },
        "candidates": materialized,
        "pool_manifest_path": str((POOL_ROOT / "pool_manifest.json").resolve()),
        "pool_manifest_sha256": _file_sha(POOL_ROOT / "pool_manifest.json"),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "resource_budget_path": str(RESOURCE_CONFIG.resolve()),
        "resource_budget_sha256": _file_sha(RESOURCE_CONFIG),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_json_no_clobber(output / "candidate_manifest.json", manifest)
    return {**manifest, "manifest_sha256": manifest_sha, "output_root": str(output)}


def _candidate_spec(manifest: Mapping[str, object], deck_path: Path, deck_sha: str) -> dict[str, object]:
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {
        "main_path": str(parent["policy_path"]),
        "deck_path": str(deck_path),
        "policy_sha256": str(parent["policy_file_sha256"]),
        "deck_sha256": deck_sha,
        "env": env,
        "biases": biases,
        "config_sha256": _config_sha(env, biases),
        "pool_root": str(POOL_ROOT.resolve()),
    }


def _with_metadata(game: object, metadata: Mapping[str, object]) -> object:
    # EvaluationGameV1 is a frozen slots dataclass; use dataclasses.replace
    # without importing its concrete type into the search contract.
    from dataclasses import replace
    return replace(game, metadata={**dict(game.metadata), **dict(metadata)})


def _weighted(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    per_opponent: dict[str, object] = {}
    numerator = denominator = 0.0
    for opponent_id, weight in weights.items():
        values = [row for row in rows if str(row.get("opponent_id")) == opponent_id]
        score = sum(1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0 for row in values)
        rate = score / len(values) if values else None
        per_opponent[opponent_id] = {"weight": float(weight), "games": len(values), "rate": rate}
        if rate is not None:
            numerator += float(weight) * rate
            denominator += float(weight)
    aggregate = aggregate_ledger_v1(rows)
    return {
        **aggregate,
        "weighted_meta_score": numerator / denominator if denominator else None,
        "per_opponent": per_opponent,
        "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows),
        "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows),
        "seat_counts": {str(seat): sum(1 for row in rows if int(row.get("seat", -1)) == seat) for seat in (0, 1)},
    }


def execute(
    *, output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if type(workers) is not int or workers < 1 or type(worker_recycle_games) is not int or worker_recycle_games < 1:
        raise MetaWeightedDeckRunnerError("workers and worker_recycle_games must be positive ints")
    manifest = materialize_manifest(
        output=output,
        candidate_count=candidate_count,
        generator_seed=generator_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    output_path = Path(str(manifest["output_root"]))
    references = tuple(str(item) for item in manifest["meta_train"]["selected_ids"])
    parent = manifest["parent"]
    assert isinstance(parent, Mapping)
    specs: list[tuple[str, Path, str]] = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in manifest["candidates"]:
        assert isinstance(row, Mapping)
        specs.append((f"candidate-{str(row['candidate_id'])[:12]}", Path(str(row["deck_path"])), str(row["deck_file_sha256"])))

    games: list[object] = []
    for arm_id, deck_path, deck_sha in specs:
        games.extend(
            _with_metadata(game, {"comparison_arm": arm_id, "meta_weighted_search": True, **AUTHORITY_FALSE})
            for game in build_native_candidate_games_v1(
                candidate_id=arm_id,
                candidate=_candidate_spec(manifest, deck_path, deck_sha),
                pool=load_opponent_pool_v1(POOL_ROOT),
                reference_ids=references,
                games_per_opponent_seat=GAMES_PER_OPPONENT_SEAT,
                base_seed=base_seed,
                block_id=f"{SCHEMA}-weighted48",
            )
        )
    expected_per_arm = len(references) * 2 * GAMES_PER_OPPONENT_SEAT
    expected = expected_per_arm * len(specs)
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise MetaWeightedDeckRunnerError("game count or global game identity gate failed")
    by_arm = defaultdict(list)
    for game in games:
        by_arm[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in by_arm["parent"]}
    for arm_id in sorted(set(by_arm) - {"parent"}):
        candidate_keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in by_arm[arm_id]}
        if candidate_keys.keys() != parent_keys.keys() or any(candidate_keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise MetaWeightedDeckRunnerError(f"paired schedule mismatch: {arm_id}")

    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=min(workers, budget.max_workers), snapshot=before)
    admitted_workers = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted_workers < 1:
        raise MetaWeightedDeckRunnerError(f"resource governor did not admit workers: {decision.to_dict()}")
    destination = output_path / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        tuple(games),
        output_dir=destination,
        max_workers=admitted_workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    weights = {str(key): float(value) for key, value in manifest["meta_train"]["selected_weights"].items()}
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    arms = {arm: _weighted(rows, weights) for arm, rows in sorted(grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates: list[dict[str, object]] = []
    for arm_id in sorted(set(arms) - {"parent"}):
        row = next(item for item in manifest["candidates"] if arm_id.endswith(str(item["candidate_id"])[:12]))
        delta = float(arms[arm_id]["weighted_meta_score"]) - parent_score
        candidates.append({
            "arm_id": arm_id,
            "candidate_id": row["candidate_id"],
            "deck_file_sha256": row["deck_file_sha256"],
            "deck_multiset_sha256": row["deck_multiset_sha256"],
            "weighted_delta": delta,
            "weighted_delta_points": delta * 100.0,
            "fault_gate": int(arms[arm_id]["faults"]) == 0,
            "identity_gate": bool(arms[arm_id]["unique_game_ids"] and arms[arm_id]["unique_seeds"]),
            "status": "weighted_positive_candidate_only" if int(arms[arm_id]["faults"]) == 0 and delta > 0.0 else "candidate_only",
        })
    summary = {
        "schema_version": f"{SCHEMA}-weighted48",
        "manifest_sha256": _file_sha(output_path / "candidate_manifest.json"),
        "arms": arms,
        "parent_weighted_meta_score": parent_score,
        "candidates": candidates,
        "all_faults_zero": int(result["summary"]["faults"]) == 0,
        "telemetry": {
            "workers_requested": workers,
            "workers_admitted": admitted_workers,
            "worker_recycle_games": worker_recycle_games,
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
        },
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "next_gate": "weighted48 only; positive candidates require a separate common24 gate",
    }
    summary_sha = _write_json_no_clobber(output_path / "weighted48_summary.json", summary)
    summary_md_sha = _write_text_no_clobber(
        output_path / "weighted48_summary.md",
        "# META_TRAIN weighted automatic deck search\n\n" + "\n".join(
            f"- {row['candidate_id']}: {float(row['weighted_delta_points']):+.3f}pt vs parent; faults={row['fault_gate']}; status={row['status']}"
            for row in candidates
        ) + "\n",
    )
    final_sha = _write_json_no_clobber(
        output_path / "final_summary.json",
        {
            "schema_version": SCHEMA,
            "output_root": str(output_path),
            "weighted_summary_sha256": summary_sha,
            "weighted_summary_md_sha256": summary_md_sha,
            "authority": dict(AUTHORITY_FALSE),
            "performance_run_started": True,
            "common24_auto_started": False,
        },
    )
    summary.update({"summary_sha256": summary_sha, "summary_md_sha256": summary_md_sha, "final_summary_sha256": final_sha})
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--generator-seed", type=int, default=DEFAULT_GENERATOR_SEED)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args()
    print(json.dumps(execute(**vars(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_WORKER_RECYCLE_GAMES",
    "DEFAULT_WORKERS",
    "MetaWeightedDeckRunnerError",
    "execute",
    "materialize_manifest",
]
