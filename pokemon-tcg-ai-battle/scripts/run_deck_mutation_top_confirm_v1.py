"""Run one deck-mutation leader against its native parent baseline."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import argparse

from mage_ptcg.deck_io import read_deck_csv
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, run_parallel_cabt_evaluation
from scripts.run_deck_mutation_native_pilot_v1 import (
    CandidateDeckV1,
    build_native_candidate_games_v1,
    load_candidate_manifest_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-base-seed", type=int, default=10400000)
    parser.add_argument("--native-base-seed", type=int, default=10500000)
    parser.add_argument("--output-name", default="top-confirm-736")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json"
    screen_path = root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/screen-736/candidate_summaries.json"
    manifest = load_candidate_manifest_v1(manifest_path)
    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    top = max(screen["summaries"].values(), key=lambda item: item["score_rate"])
    top_id = str(top["candidate_id"])
    candidate = next(item for item in manifest.candidates if item.candidate_id == top_id)
    native = CandidateDeckV1(
        "plamen06_steel-native-confirm-v1", manifest.parent_deck_path,
        manifest.parent_deck_file_sha256, manifest.parent_deck_multiset_sha256,
        tuple(read_deck_csv(manifest.parent_deck_path)),
        {"promotion_allowed": False, "training_allowed": False, "submission_allowed": False},
    )
    games = []
    for arm, row, seed in (("candidate", candidate, args.candidate_base_seed), ("native", native, args.native_base_seed)):
        state = replace(manifest, candidates=(row,))
        block = build_native_candidate_games_v1(
            state, reference_config_path=root / "configs/meta_specialist/performance_first_broad_pool_v1.json",
            pool_root=root / "opponents", games_per_opponent_seat=8, base_seed=seed,
            block_id="deck-mutation-top-confirm-v1",
        )
        games.extend(replace(game, metadata={**dict(game.metadata), "arm": arm}) for game in block)
    output = root / "runs/final-sprint-autonomous/deck-mutation-plamen-v1" / args.output_name
    result = run_parallel_cabt_evaluation(games, output_dir=output, max_workers=8, worker_recycle_games=32, overwrite=True)
    arms = {}
    for arm in ("candidate", "native"):
        rows = [row for row in result["rows"] if row.get("metadata", {}).get("arm") == arm]
        arms[arm] = aggregate_ledger_v1(rows)
    payload = {
        "schema_version": "meta-specialist-deck-mutation-top-confirm-v1",
        "top_candidate_id": top_id,
        "native_policy_sha256": manifest.parent_policy_sha256,
        "arms": arms,
        "research_only": True,
        "authority": {"promotion_allowed": False, "training_allowed": False, "submission_allowed": False},
    }
    summary = output / "arm_summaries.json"
    summary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**payload, "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest()}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
