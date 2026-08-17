#!/usr/bin/env python3
"""Smoke-gated two-card coordinated packages for the submission-compatible P0.

The generic Rule v0 META_TRAIN runner is reused for the evaluator and
resource governor, but candidate generation is replaced by a bounded
``swap_count=2`` package generator.  This keeps the policy fixed, makes deck
causal attribution possible, and leaves all production/submission authority
closed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.deck_mutation_v1 import DeckMutationCandidateV1, generate_deck_mutation_candidates_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1, deck_multiset_identity_v1
from scripts import run_rule_v0_meta_weighted_auto_search_v1 as base
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, run_parallel_cabt_evaluation
from scripts.run_performance_first_arena_v1 import build_root_arena_games


ROOT = base.ROOT
SCHEMA = "meta-specialist-rule-v0-root-deck-coordinated-package-v1"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-package-v1-20260814"
PARENT_DECK = ROOT / "deck.csv"
PARENT_POLICY = ROOT / "main.py"
PARENT_ID = "rule-v0-root-deck-package-parent"
PACKAGE_SWAP_COUNT = 2
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 2
DEFAULT_GENERATOR_SEED = 23680000
DEFAULT_BASE_SEED = 23681000
SMOKE_BASE_SEED = 23680000
SMOKE_OPPONENT = "tomatomato_archaludon"

base.SCHEMA = SCHEMA
base.OUTPUT_DEFAULT = OUTPUT_DEFAULT


def package_rank(candidate: Mapping[str, object], frequency: Mapping[int, float]) -> tuple[float, tuple[int, ...]] | None:
    added = candidate.get("added_cards")
    if not isinstance(added, Sequence) or isinstance(added, (str, bytes)) or len(added) != PACKAGE_SWAP_COUNT:
        return None
    cards = tuple(sorted(int(card) for card in added))
    return (-sum(float(frequency.get(card, 0.0)) for card in cards), cards)


def generate_root_package_candidates(
    *,
    parent_cards: Sequence[int],
    frequency_rows: Sequence[tuple[int, float, float]],
    prior_multisets: set[str],
    known_card_ids: Sequence[int],
    candidate_count: int,
    seed: int,
) -> tuple[DeckMutationCandidateV1, ...]:
    if type(candidate_count) is not int or candidate_count < 1:
        raise ValueError("candidate_count must be positive")
    frequency = {int(card): float(weight) for card, weight, _support in frequency_rows}
    replacement_pool = base.build_replacement_pool_v1(
        frequency_rows=frequency_rows,
        parent_cards=parent_cards,
        known_card_ids=known_card_ids,
        limit=64,
    )
    signature = CoreSignatureV1(archetype_id="rule-v0-root-deck", required_counts=base.ROOT_CORE_COUNTS)
    generated = generate_deck_mutation_candidates_v1(
        base_cards=parent_cards,
        signature=signature,
        replacement_pool=replacement_pool,
        swap_counts=(PACKAGE_SWAP_COUNT,),
        candidates_per_swap=max(1024, candidate_count * 256),
        seed=seed,
        known_card_ids=known_card_ids,
    )
    novel = [candidate for candidate in generated if candidate.deck_multiset_sha256 not in prior_multisets]
    novel = [candidate for candidate in novel if candidate.swap_count == PACKAGE_SWAP_COUNT and package_rank(candidate.to_dict(), frequency) is not None]
    novel.sort(key=lambda candidate: (package_rank(candidate.to_dict(), frequency), candidate.candidate_id))
    chosen: list[DeckMutationCandidateV1] = []
    used_pairs: set[tuple[int, ...]] = set()
    for candidate in novel:
        pair = tuple(sorted(candidate.added_cards))
        if pair in used_pairs:
            continue
        chosen.append(candidate)
        used_pairs.add(pair)
        if len(chosen) == candidate_count:
            break
    if len(chosen) != candidate_count:
        raise ValueError(f"only {len(chosen)} novel two-card packages available; requested {candidate_count}")
    return tuple(chosen)


# Patch only the imported research module; production runner files remain unchanged.
base.generate_root_meta_candidates = generate_root_package_candidates
base.SCHEMA = SCHEMA


def smoke_passes(summary: Mapping[str, object]) -> bool:
    return int(summary.get("completed_games", -1)) == 2 and int(summary.get("faults", -1)) == 0


def _manifest_arms(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping):
        raise ValueError("manifest parent is malformed")
    rows: list[dict[str, object]] = [{
        "candidate_id": "parent",
        "deck_path": str(parent["deck_path"]),
        "deck_file_sha256": str(parent["deck_file_sha256"]),
        "deck_multiset_sha256": str(parent["deck_multiset_sha256"]),
    }]
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("manifest candidates are malformed")
    for row in candidates:
        if not isinstance(row, Mapping) or int(row.get("swap_count", -1)) != PACKAGE_SWAP_COUNT:
            raise ValueError("manifest contains a non-package candidate")
        rows.append({
            "candidate_id": str(row["candidate_id"]),
            "deck_path": str(row["deck_path"]),
            "deck_file_sha256": str(row["deck_file_sha256"]),
            "deck_multiset_sha256": str(row["deck_multiset_sha256"]),
        })
    return tuple(rows)


def run_smoke(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    staging = Path(tempfile.mkdtemp(prefix="rule-v0-package-smoke-", dir=str(ROOT / "runs/final-sprint-autonomous")))
    rows: list[dict[str, object]] = []
    try:
        vocabulary = base.load_production_card_vocabulary_v1()
        for ordinal, arm in enumerate(_manifest_arms(manifest)):
            deck_path = Path(str(arm["deck_path"])).resolve()
            cards = tuple(parse_deck_csv_bytes(deck_path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
            games = build_root_arena_games(
                opponent_ids=(SMOKE_OPPONENT,),
                games_per_seat=1,
                base_seed=SMOKE_BASE_SEED + ordinal * 10,
                subject_deck=deck_path,
                block_id=f"{SCHEMA}-smoke-{ordinal}",
            )
            result = run_parallel_cabt_evaluation(
                tuple(games),
                output_dir=staging / "evaluation" / str(arm["candidate_id"]),
                max_workers=1,
                worker_recycle_games=16,
                overwrite=False,
            )
            aggregate = aggregate_ledger_v1(result["rows"])
            rows.append({**dict(arm), "opponent_id": SMOKE_OPPONENT, "requested_games": len(games), **{key: int(aggregate[key]) for key in ("completed_games", "faults", "wins", "draws", "losses")}, "smoke_pass": smoke_passes(aggregate)})
        return tuple(rows)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _identity_rows(manifest: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple((str(row["candidate_id"]), str(row["deck_multiset_sha256"])) for row in _manifest_arms(manifest))


def execute_with_smoke(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if workers != DEFAULT_WORKERS:
        raise ValueError("this lane is sealed to workers=12")
    staging = Path(tempfile.mkdtemp(prefix="rule-v0-package-materialize-", dir=str(ROOT / "runs/final-sprint-autonomous")))
    try:
        staged = base.materialize_manifest(output=staging, candidate_count=candidate_count, generator_seed=generator_seed, workers=workers, worker_recycle_games=worker_recycle_games)
        smoke = run_smoke(staged)
        if not smoke or not all(bool(row["smoke_pass"]) for row in smoke):
            raise RuntimeError("runtime smoke gate failed; weighted48 was not started")
        staged_identity = _identity_rows(staged)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = base.execute(output=output, candidate_count=candidate_count, generator_seed=generator_seed, base_seed=base_seed, workers=workers, worker_recycle_games=worker_recycle_games)
    manifest_path = Path(output) / "candidate_manifest.json"
    target = json.loads(manifest_path.read_text(encoding="utf-8"))
    if staged_identity != _identity_rows(target):
        raise RuntimeError("smoke and weighted package identities diverged")
    smoke_payload = {"schema_version": f"{SCHEMA}-runtime-smoke", "smoke": list(smoke), "staged_identity": list(staged_identity), "performance_score_allowed": False, "authority": dict(base.AUTHORITY_FALSE)}
    result["runtime_smoke_sha256"] = base._write_json_no_clobber(Path(output) / "runtime_smoke.json", smoke_payload)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--generator-seed", type=int, default=DEFAULT_GENERATOR_SEED)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    args = parser.parse_args()
    print(json.dumps(execute_with_smoke(**vars(args)), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_CANDIDATE_COUNT", "DEFAULT_WORKERS", "PACKAGE_SWAP_COUNT", "PARENT_DECK", "PARENT_POLICY", "execute_with_smoke", "generate_root_package_candidates", "package_rank", "smoke_passes"]
