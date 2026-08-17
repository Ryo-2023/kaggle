#!/usr/bin/env python3
"""Smoke-gated META_TRAIN deck neighborhood around the sealed 95cc asset.

This is a research-only wrapper around the generic weighted deck search.  It
fixes the parent to the previously evaluated Tomato-native 95cc deck, runs a
small real-engine smoke for the parent and every generated child, and only
then starts the workers=12 weighted48 stage.  No training, promotion,
submission, or long-run authority is granted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from scripts import run_meta_weighted_deck_search_v1 as base
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, run_parallel_cabt_evaluation


ROOT = base.ROOT
PARENT_ID = "tomato-native-95cc-meta-neighborhood-parent"
PARENT_DECK = ROOT / (
    "runs/final-sprint-autonomous/deck-mutation-weighted-halving-v1-20260813/"
    "candidates/95cc2c77a31de5dc3a79b9cdffd5a7f81e0d4e42b05734ad36da453facc45145/deck.csv"
)
PARENT_POLICY = ROOT / "opponents/tomatomato_archaludon/main.py"
SCHEMA = "meta-specialist-meta-weighted-95cc-neighborhood-v1"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-v1-20260814"
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 2
DEFAULT_GENERATOR_SEED = 23670000
DEFAULT_BASE_SEED = 23671000
SMOKE_BASE_SEED = 23670000
SMOKE_OPPONENT = "tomatomato_archaludon"

# The generic module resolves these names at call time.  Patching only this
# research wrapper's imported module keeps the production runner untouched.
base.PARENT_ID = PARENT_ID
base.PARENT_DECK = PARENT_DECK
base.PARENT_POLICY = PARENT_POLICY
base.SCHEMA = SCHEMA
base.OUTPUT_DEFAULT = OUTPUT_DEFAULT


def smoke_passes(summary: Mapping[str, object]) -> bool:
    return int(summary.get("completed_games", -1)) == 2 and int(summary.get("faults", -1)) == 0


def smoke_identity_matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return (
        str(left.get("candidate_id")) == str(right.get("candidate_id"))
        and str(left.get("deck_multiset_sha256")) == str(right.get("deck_multiset_sha256"))
    )


def _manifest_arm_rows(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
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
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("manifest has no generated candidates")
    for row in candidates:
        if not isinstance(row, Mapping):
            raise ValueError("manifest candidate row is malformed")
        rows.append({
            "candidate_id": str(row["candidate_id"]),
            "deck_path": str(row["deck_path"]),
            "deck_file_sha256": str(row["deck_file_sha256"]),
            "deck_multiset_sha256": str(row["deck_multiset_sha256"]),
        })
    return tuple(rows)


def run_smoke(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """Run two real-engine games (both seats) for every materialized arm."""

    temp_root = Path(tempfile.mkdtemp(prefix="meta-weighted-95cc-smoke-", dir=str(ROOT / "runs/final-sprint-autonomous")))
    rows: list[dict[str, object]] = []
    try:
        pool = base.load_opponent_pool_v1(base.POOL_ROOT)
        references = (SMOKE_OPPONENT,)
        for ordinal, arm in enumerate(_manifest_arm_rows(manifest)):
            deck_path = Path(str(arm["deck_path"])).resolve()
            cards = tuple(parse_deck_csv_bytes(deck_path.read_bytes()))
            vocabulary = base.load_production_card_vocabulary_v1()
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
            candidate = base._candidate_spec(
                manifest,
                deck_path,
                str(arm["deck_file_sha256"]),
            )
            games = base.build_native_candidate_games_v1(
                candidate_id=f"smoke-{arm['candidate_id']}",
                candidate=candidate,
                pool=pool,
                reference_ids=references,
                games_per_opponent_seat=1,
                base_seed=SMOKE_BASE_SEED + ordinal * 10,
                block_id=f"{SCHEMA}-smoke-{ordinal}",
            )
            result = run_parallel_cabt_evaluation(
                tuple(games),
                output_dir=temp_root / "evaluation" / str(arm["candidate_id"]),
                max_workers=1,
                worker_recycle_games=16,
                overwrite=False,
            )
            aggregate = aggregate_ledger_v1(result["rows"])
            rows.append({
                **dict(arm),
                "opponent_id": SMOKE_OPPONENT,
                "requested_games": len(games),
                "completed_games": int(aggregate["completed_games"]),
                "faults": int(aggregate["faults"]),
                "wins": int(aggregate["wins"]),
                "draws": int(aggregate["draws"]),
                "losses": int(aggregate["losses"]),
                "smoke_pass": smoke_passes(aggregate),
            })
        return tuple(rows)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _identity_rows(manifest: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    return tuple(
        {key: row[key] for key in ("candidate_id", "deck_multiset_sha256")}
        for row in _manifest_arm_rows(manifest)
    )


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
    staging = Path(tempfile.mkdtemp(prefix="meta-weighted-95cc-materialize-", dir=str(ROOT / "runs/final-sprint-autonomous")))
    try:
        staged_manifest = base.materialize_manifest(
            output=staging,
            candidate_count=candidate_count,
            generator_seed=generator_seed,
            workers=workers,
            worker_recycle_games=worker_recycle_games,
        )
        smoke_rows = run_smoke(staged_manifest)
        if not smoke_rows or not all(bool(row["smoke_pass"]) for row in smoke_rows):
            raise RuntimeError("runtime smoke gate failed; weighted48 was not started")
        staged_identity = _identity_rows(staged_manifest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    result = base.execute(
        output=output,
        candidate_count=candidate_count,
        generator_seed=generator_seed,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    target_manifest_path = Path(output) / "candidate_manifest.json"
    target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    target_identity = _identity_rows(target_manifest)
    if len(staged_identity) != len(target_identity) or any(
        not smoke_identity_matches(left, right) for left, right in zip(staged_identity, target_identity)
    ):
        raise RuntimeError("smoke and weighted manifest identities diverged")
    smoke_payload = {
        "schema_version": f"{SCHEMA}-runtime-smoke",
        "purpose": "real_engine_smoke_precedes_weighted48",
        "smoke": list(smoke_rows),
        "staged_identity": list(staged_identity),
        "performance_score_allowed": False,
        "authority": dict(base.AUTHORITY_FALSE),
        "weighted_output_root": str(Path(output).resolve()),
    }
    smoke_sha = base._write_json_no_clobber(Path(output) / "runtime_smoke.json", smoke_payload)
    result["runtime_smoke_sha256"] = smoke_sha
    result["runtime_smoke_games"] = len(smoke_rows) * 2
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


__all__ = [
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_WORKER_RECYCLE_GAMES",
    "DEFAULT_WORKERS",
    "PARENT_DECK",
    "PARENT_ID",
    "PARENT_POLICY",
    "SCHEMA",
    "execute_with_smoke",
    "run_smoke",
    "smoke_identity_matches",
    "smoke_passes",
]
