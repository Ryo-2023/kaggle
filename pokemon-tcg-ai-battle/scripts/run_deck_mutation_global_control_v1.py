"""Research-only common24 control for a deck mutation against Tomato native.

The Plamen native-parent comparison is useful for mutation screening but does
not establish that a mutation beats the current Archaludon BestKnown.  This
runner compares one materialized Plamen-policy mutation with the native Tomato
pair under the same 24-opponent reference list and paired seed schedule.  Both
arms are synthetic candidate identities, so the 24 reference IDs are retained
for both arms (including the corresponding native asset) and no promotion,
training, or submission authority is granted.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
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


SCHEMA_V1 = "meta-specialist-deck-mutation-global-control-v1"
RUNNER_REF_V1 = (
    "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"
)


def _canonical_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_ids(path: Path, pool: Mapping[str, object]) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("reference config must be an object")
    if payload.get("promotion_authority") is not False:
        raise ValueError("reference config must be research-only")
    raw = payload.get("opponent_ids")
    if not isinstance(raw, list) or len(raw) != 24 or len(set(raw)) != 24:
        raise ValueError("global control requires exactly 24 unique references")
    if any(type(item) is not str or not item for item in raw):
        raise ValueError("reference IDs must be non-empty strings")
    if set(raw) - set(pool):
        raise ValueError("reference config contains unknown pool IDs")
    return tuple(raw)


def _candidate_spec(
    *,
    candidate_id: str,
    main_path: Path,
    deck_path: Path,
    policy_sha: str,
    deck_sha: str,
    pool_root: Path,
) -> dict[str, object]:
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


def build_global_control_games_v1(
    *,
    manifest_path: Path,
    candidate_id: str,
    reference_config: Path,
    pool_root: Path,
    games_per_opponent_seat: int = 8,
    base_seed: int = 14_400_000,
    block_id: str = "deck-mutation-global-control-v1",
    candidate_policy_asset_id: str | None = None,
) -> tuple[EvaluationGameV1, ...]:
    """Build paired candidate/Tomato games with a shared seed schedule."""
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    manifest = load_candidate_manifest_v1(manifest_path)
    candidate = next(
        (item for item in manifest.candidates if item.candidate_id == candidate_id),
        None,
    )
    if candidate is None:
        raise ValueError(f"unknown candidate_id: {candidate_id}")
    pool_root = pool_root.resolve()
    pool = load_opponent_pool_v1(pool_root)
    references = _reference_ids(reference_config.resolve(), pool)
    tomato = pool.get("tomatomato_archaludon")
    if tomato is None:
        raise ValueError("Tomato native pair is missing from the opponent pool")
    tomato_policy_sha = _sha256(tomato.policy_path)
    tomato_deck_sha = _sha256(tomato.deck_csv_path)
    if candidate_policy_asset_id is None:
        candidate_policy_path = manifest.parent_policy_path
        candidate_policy_sha = manifest.parent_policy_sha256
        candidate_policy_label = "plamen_native"
    else:
        policy_asset = pool.get(candidate_policy_asset_id)
        if policy_asset is None:
            raise ValueError(f"unknown candidate policy asset: {candidate_policy_asset_id}")
        candidate_policy_path = Path(policy_asset.policy_path).resolve()
        candidate_policy_sha = _sha256(candidate_policy_path)
        candidate_policy_label = candidate_policy_asset_id
    candidate_arm_id = f"{candidate_id}:{candidate_policy_label}:mutated-deck"
    tomato_arm_id = f"{candidate_id}:tomato-native-control"
    candidate_spec = _candidate_spec(
        candidate_id=candidate_arm_id,
        main_path=candidate_policy_path,
        deck_path=candidate.deck_csv_path,
        policy_sha=candidate_policy_sha,
        deck_sha=candidate.deck_csv_sha256,
        pool_root=pool_root,
    )
    tomato_spec = _candidate_spec(
        candidate_id=tomato_arm_id,
        main_path=tomato.policy_path,
        deck_path=tomato.deck_csv_path,
        policy_sha=tomato_policy_sha,
        deck_sha=tomato_deck_sha,
        pool_root=pool_root,
    )
    games: list[EvaluationGameV1] = []
    for arm, arm_id, spec in (
        ("candidate", candidate_arm_id, candidate_spec),
        ("tomato_native", tomato_arm_id, tomato_spec),
    ):
        built = build_native_candidate_games_v1(
            candidate_id=arm_id,
            candidate=spec,
            pool=pool,
            reference_ids=references,
            games_per_opponent_seat=games_per_opponent_seat,
            # Same base seed is intentional: corresponding reference/seat/
            # repetition cells form a paired common-protocol schedule.
            base_seed=base_seed,
            block_id=f"{block_id}-{arm}",
        )
        games.extend(
            replace(
                game,
                metadata={
                    **dict(game.metadata),
                    "schema_version": SCHEMA_V1,
                    "common_protocol": True,
                    "common_reference_count": len(references),
                    "comparison_arm": arm,
                    "mutation_candidate_id": candidate_id,
                    "candidate_manifest_path": str(manifest.manifest_path),
                    "candidate_manifest_sha256": manifest.manifest_sha256,
                    "candidate_deck_multiset_sha256": candidate.deck_multiset_sha256,
                    "candidate_policy_asset_id": candidate_policy_asset_id or manifest.subject_id,
                    "candidate_policy_label": candidate_policy_label,
                    "candidate_policy_sha256": candidate_policy_sha,
                    "tomato_policy_sha256": tomato_policy_sha,
                    "tomato_deck_sha256": tomato_deck_sha,
                    "shared_seed_schedule": True,
                    "research_only": True,
                    "promotion_authority": False,
                    "training_authority": False,
                    "submission_authority": False,
                },
                runner_ref=RUNNER_REF_V1,
            )
            for game in built
        )
    expected = 2 * len(references) * 2 * games_per_opponent_seat
    if len(games) != expected:
        raise AssertionError(f"expected {expected} global-control games, got {len(games)}")
    if len({game.game_id for game in games}) != len(games):
        raise AssertionError("global-control game IDs must be unique")
    candidate_seeds = [game.seed for game in games if game.metadata["comparison_arm"] == "candidate"]
    tomato_seeds = [game.seed for game in games if game.metadata["comparison_arm"] == "tomato_native"]
    if candidate_seeds != tomato_seeds:
        raise AssertionError("candidate/Tomato arms must share the exact seed schedule")
    return tuple(games)


def summarize_global_control_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    arms: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        metadata = row.get("metadata", {})
        arm = metadata.get("comparison_arm") if isinstance(metadata, Mapping) else None
        arms.setdefault(str(arm or "unknown"), []).append(row)
    return {
        "schema_version": SCHEMA_V1,
        "arms": {arm: aggregate_ledger_v1(values) for arm, values in sorted(arms.items())},
        "shared_seed_schedule": True,
        "research_only": True,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json",
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument(
        "--reference-config", type=Path,
        default=root / "configs/meta_specialist/performance_first_broad_pool_v1.json",
    )
    parser.add_argument("--pool-root", type=Path, default=root / "opponents")
    parser.add_argument("--games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=14_400_000)
    parser.add_argument(
        "--candidate-policy-asset-id",
        default=None,
        help="optional pool asset whose native policy is paired with the mutation deck",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    games = build_global_control_games_v1(
        manifest_path=args.manifest.resolve(),
        candidate_id=args.candidate_id,
        reference_config=args.reference_config.resolve(),
        pool_root=args.pool_root.resolve(),
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        block_id="deck-mutation-global-control-v1",
        candidate_policy_asset_id=args.candidate_policy_asset_id,
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=args.output,
        max_workers=args.workers,
        worker_recycle_games=16,
        overwrite=args.overwrite,
    )
    summary = summarize_global_control_v1(result["rows"])
    summary.update(
        {
            "candidate_id": args.candidate_id,
            "candidate_manifest_sha256": _canonical_sha(args.manifest.resolve()),
            "reference_config_sha256": _canonical_sha(args.reference_config.resolve()),
            "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
            "base_seed": args.base_seed,
            "arena_summary": result["summary"],
        }
    )
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "global_control_summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {**summary, "summary_sha256": _canonical_sha(path)},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA_V1",
    "build_global_control_games_v1",
    "summarize_global_control_v1",
]
