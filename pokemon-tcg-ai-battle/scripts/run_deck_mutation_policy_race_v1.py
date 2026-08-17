"""Research-only policy race on a fixed positive deck-mutation candidate."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from scripts.parallel_cabt_evaluator_v1 import EvaluationGameV1, aggregate_ledger_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import build_native_candidate_games_v1
from scripts.run_deck_mutation_native_pilot_v1 import load_candidate_manifest_v1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1


SCHEMA_V1 = "meta-specialist-deck-mutation-policy-race-v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _references(path: Path, subject_id: str) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    refs = tuple(item for item in payload["opponent_ids"] if item != subject_id)
    if len(refs) != 23:
        raise ValueError(f"expected 23 non-self references, got {len(refs)}")
    return refs


def build_fixed_deck_policy_games_v1(
    *, manifest_path: Path, candidate_id: str, reference_config: Path, pool_root: Path,
    games_per_opponent_seat: int, base_seed: int,
) -> tuple[EvaluationGameV1, ...]:
    manifest = load_candidate_manifest_v1(manifest_path)
    row = next(item for item in manifest.candidates if item.candidate_id == candidate_id)
    pool = load_opponent_pool_v1(pool_root)
    refs = _references(reference_config, manifest.subject_id)
    config_base = hashlib.sha256(f"{candidate_id}:fixed-deck".encode()).hexdigest()
    games: list[EvaluationGameV1] = []
    for arm_index, (arm, env) in enumerate((("native", {}), ("use-search-0", {"USE_SEARCH": "0"}))):
        candidate = {
            "main_path": pool[manifest.subject_id].policy_path,
            "deck_path": str(row.deck_csv_path),
            "policy_sha256": manifest.parent_policy_sha256,
            "deck_sha256": row.deck_csv_sha256,
            "env": env,
            "biases": {},
            "config_sha256": hashlib.sha256(json.dumps({"arm": arm, "base": config_base}, sort_keys=True).encode()).hexdigest(),
            "pool_root": str(pool_root.resolve()),
        }
        built = build_native_candidate_games_v1(
            candidate_id=f"{candidate_id}:{arm}", candidate=candidate, pool=pool,
            reference_ids=refs, games_per_opponent_seat=games_per_opponent_seat,
            base_seed=base_seed + arm_index * 100_000, block_id="deck-mutation-policy-race-v1",
        )
        games.extend(replace(game, metadata={**dict(game.metadata), "race_arm": arm, "research_only": True}) for game in built)
    return tuple(games)


def summarize_policy_race_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    arms: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        arm = str(row.get("metadata", {}).get("race_arm", "unknown"))
        arms.setdefault(arm, []).append(row)
    return {arm: aggregate_ledger_v1(values) for arm, values in sorted(arms.items())}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--manifest", type=Path, default=root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--reference-config", type=Path, default=root / "configs/meta_specialist/performance_first_broad_pool_v1.json")
    parser.add_argument("--pool-root", type=Path, default=root / "opponents")
    parser.add_argument("--games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=11200000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    games = build_fixed_deck_policy_games_v1(
        manifest_path=args.manifest, candidate_id=args.candidate_id,
        reference_config=args.reference_config, pool_root=args.pool_root,
        games_per_opponent_seat=args.games_per_opponent_seat, base_seed=args.base_seed,
    )
    result = run_parallel_cabt_evaluation(games, output_dir=args.output, max_workers=args.workers, worker_recycle_games=32, overwrite=args.overwrite)
    payload = {
        "schema_version": SCHEMA_V1,
        "candidate_id": args.candidate_id,
        "candidate_manifest_sha256": _sha(args.manifest.resolve()),
        "arms": summarize_policy_race_rows_v1(result["rows"]),
        "research_only": True,
        "authority": {"promotion_allowed": False, "training_allowed": False, "submission_allowed": False},
        "arena_summary": result["summary"],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    summary = args.output / "policy_race_summary.json"
    summary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**payload, "summary_sha256": _sha(summary)}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
