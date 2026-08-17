"""Research-only fresh-block Tomato-policy deck interaction run.

This wrapper intentionally lives outside production/evaluator code.  It binds
the v2 role-surface candidate deck to the Tomato native policy and compares it
with the Tomato native deck+policy pair under a fresh common24 schedule.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from scripts.parallel_cabt_evaluator_v1 import (
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_deck_mutation_global_control_v1 import (
    build_global_control_games_v1,
    summarize_global_control_v1,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/candidate-manifest/candidates.json"
REFERENCE = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
POOL = ROOT / "opponents"
CANDIDATE_ID = "role-8c8c69dc792c913f"
OUTPUT = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2-tomato-policy-interaction-384-retry-v1"
BLOCK_ID = "deck-mutation-role-v2-tomato-policy-interaction-384-retry-v1"
BASE_SEED = 18130000


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing existing output root: {OUTPUT}")
    games = build_global_control_games_v1(
        manifest_path=MANIFEST.resolve(),
        candidate_id=CANDIDATE_ID,
        reference_config=REFERENCE.resolve(),
        pool_root=POOL.resolve(),
        games_per_opponent_seat=8,
        base_seed=BASE_SEED,
        block_id=BLOCK_ID,
        candidate_policy_asset_id="tomatomato_archaludon",
    )
    if len(games) != 768 or len({game.game_id for game in games}) != 768:
        raise AssertionError("fresh interaction run must contain 768 unique games")
    candidate_seeds = [game.seed for game in games if game.metadata["comparison_arm"] == "candidate"]
    control_seeds = [game.seed for game in games if game.metadata["comparison_arm"] == "tomato_native"]
    if candidate_seeds != control_seeds:
        raise AssertionError("candidate and Tomato control must share exact seeds")
    if any(
        game.metadata.get(key) is not expected
        for game in games
        for key, expected in (
            ("research_only", True),
            ("promotion_authority", False),
            ("training_authority", False),
            ("submission_authority", False),
        )
    ):
        raise AssertionError("interaction games must remain research-only and authority-false")
    # Fresh runs use the repository-wide parallel default.  Fault diagnosis
    # remains available by explicitly passing max_workers=1 to the evaluator.
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=OUTPUT,
        max_workers=12,
        worker_recycle_games=16,
        overwrite=False,
    )
    summary = summarize_global_control_v1(result["rows"])
    summary.update(
        {
            "schema_version": "meta-specialist-deck-mutation-role-v2-tomato-policy-interaction-384",
            "candidate_id": CANDIDATE_ID,
            "candidate_manifest_sha256": _sha(MANIFEST),
            "reference_config_sha256": _sha(REFERENCE),
            "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
            "candidate_policy_asset_id": "tomatomato_archaludon",
            "candidate_policy_sha256": "8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e",
            "candidate_deck_sha256": "c69076bc43426b5453e39e910c37ad62b2af42992abe1093157b893d44f3038d",
            "candidate_deck_multiset_sha256": "a90a6e08321f2c7199495d6ea0a6e5df0deb32a7cc4a13f22e5bfa9f19f2f11d",
            "tomato_policy_sha256": "8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e",
            "tomato_deck_sha256": "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e",
            "block_id": BLOCK_ID,
            "base_seed": BASE_SEED,
            "games_per_opponent_seat": 8,
            "game_count": len(games),
            "arena_summary": result["summary"],
            "existing_artifacts_modified": False,
            "production_modified": False,
            "permission_granted": False,
        }
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / "interaction_summary.json"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=OUTPUT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    print(json.dumps({**summary, "summary_sha256": _sha(path)}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
