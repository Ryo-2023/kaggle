"""Research-only common-protocol comparison for a deck mutation candidate.

The existing mutation confirmation intentionally excludes the parent subject from
its opponent list (23 opponents).  This runner uses the sealed 24-ID broad-pool
protocol directly for both the mutation deck and its parent native deck.  The
candidate IDs are synthetic and therefore the parent ``plamen06_steel`` row is a
valid external opponent for both arms; no self-play is introduced.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from scripts.parallel_cabt_evaluator_v1 import (
    EvaluationGameV1,
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_deck_mutation_native_pilot_v1 import load_candidate_manifest_v1
from scripts.run_native_policy_candidate_pilot_v1 import (
    _config_sha,
    _sha256,
    build_native_candidate_games_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1


SCHEMA_V1 = "meta-specialist-deck-mutation-common-protocol-v1"
RUNNER_REF_V1 = "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"


def _canonical_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_reference_ids(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("promotion_authority") is not False:
        raise ValueError("reference config must be research-only")
    raw = payload.get("opponent_ids")
    if not isinstance(raw, list) or len(raw) != 24 or len(set(raw)) != 24:
        raise ValueError("common protocol requires exactly 24 unique opponent IDs")
    if any(type(item) is not str or not item for item in raw):
        raise ValueError("reference IDs must be non-empty strings")
    return tuple(raw)


def _candidate_spec(*, candidate_id: str, main_path: Path, deck_path: Path,
                    policy_sha: str, deck_sha: str, pool_root: Path) -> dict[str, object]:
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
        "pool_root": str(pool_root),
    }


def build_common_protocol_games_v1(
    *, manifest_path: Path,
    candidate_id: str,
    reference_config: Path,
    pool_root: Path,
    games_per_opponent_seat: int = 8,
    candidate_base_seed: int = 12_400_000,
    native_base_seed: int = 12_500_000,
    block_id: str = "deck-mutation-common-protocol-v1",
) -> tuple[EvaluationGameV1, ...]:
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    manifest = load_candidate_manifest_v1(manifest_path)
    row = next((item for item in manifest.candidates if item.candidate_id == candidate_id), None)
    if row is None:
        raise ValueError(f"unknown candidate_id: {candidate_id}")
    pool_root = pool_root.resolve()
    pool = load_opponent_pool_v1(pool_root)
    references = _load_reference_ids(reference_config.resolve())
    if set(references) - set(pool):
        raise ValueError("common protocol contains unknown pool IDs")
    candidate_arm_id = f"{candidate_id}:common-candidate"
    native_arm_id = f"{candidate_id}:common-parent-native"
    candidate = _candidate_spec(
        candidate_id=candidate_arm_id,
        main_path=manifest.parent_policy_path,
        deck_path=row.deck_csv_path,
        policy_sha=manifest.parent_policy_sha256,
        deck_sha=row.deck_csv_sha256,
        pool_root=pool_root,
    )
    native = _candidate_spec(
        candidate_id=native_arm_id,
        main_path=manifest.parent_policy_path,
        deck_path=manifest.parent_deck_path,
        policy_sha=manifest.parent_policy_sha256,
        deck_sha=manifest.parent_deck_file_sha256,
        pool_root=pool_root,
    )
    games: list[EvaluationGameV1] = []
    for arm, arm_id, spec, seed in (
        ("candidate", candidate_arm_id, candidate, candidate_base_seed),
        ("native", native_arm_id, native, native_base_seed),
    ):
        built = build_native_candidate_games_v1(
            candidate_id=arm_id,
            candidate=spec,
            pool=pool,
            reference_ids=references,
            games_per_opponent_seat=games_per_opponent_seat,
            base_seed=seed,
            block_id=block_id,
        )
        games.extend(
            replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "common_protocol": True,
                    "common_reference_count": len(references),
                    "mutation_candidate_id": candidate_id,
                    "comparison_arm": arm,
                    "research_only": True,
                    "promotion_authority": False,
                    "training_authority": False,
                    "submission_authority": False,
                },
                block_id=f"{block_id}-{arm}",
            )
            for game in built
        )
    expected = 2 * len(references) * 2 * games_per_opponent_seat
    if len(games) != expected:
        raise AssertionError(f"expected {expected} common-protocol games, got {len(games)}")
    return tuple(games)


def summarize_common_protocol_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    arms: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        metadata = row.get("metadata", {})
        arm = metadata.get("comparison_arm") if isinstance(metadata, Mapping) else None
        arms.setdefault(str(arm or "unknown"), []).append(row)
    return {
        "schema_version": SCHEMA_V1,
        "arms": {arm: aggregate_ledger_v1(values) for arm, values in sorted(arms.items())},
        "research_only": True,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False},
    }


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--reference-config", type=Path, default=root / "configs/meta_specialist/performance_first_broad_pool_v1.json")
    parser.add_argument("--pool-root", type=Path, default=root / "opponents")
    parser.add_argument("--games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--candidate-base-seed", type=int, default=12_400_000)
    parser.add_argument("--native-base-seed", type=int, default=12_500_000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    games = build_common_protocol_games_v1(
        manifest_path=args.manifest.resolve(), candidate_id=args.candidate_id,
        reference_config=args.reference_config.resolve(), pool_root=args.pool_root.resolve(),
        games_per_opponent_seat=args.games_per_opponent_seat,
        candidate_base_seed=args.candidate_base_seed, native_base_seed=args.native_base_seed,
    )
    result = run_parallel_cabt_evaluation(
        games, output_dir=args.output, max_workers=args.workers,
        worker_recycle_games=16, overwrite=args.overwrite,
    )
    summary = summarize_common_protocol_v1(result["rows"])
    summary.update({
        "candidate_id": args.candidate_id,
        "candidate_manifest_sha256": _canonical_sha(args.manifest.resolve()),
        "reference_config_sha256": _canonical_sha(args.reference_config.resolve()),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "arena_summary": result["summary"],
    })
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "common_protocol_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**summary, "summary_sha256": _canonical_sha(path)}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA_V1", "build_common_protocol_games_v1", "summarize_common_protocol_v1"]
