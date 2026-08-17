"""Run O2's Rule-vs-Random actual match plan through the full C4/Offline
Training v1 pipeline with O2 lineage attached.

This reuses ``Pipeline``'s existing build-dataset/train/export/evaluate/
screen/package/verify phase methods unmodified; it never re-implements
collection, gating, training, export, or packaging.  Only the ``collect``
phase is replaced -- with O2's real Rule-vs-opponent match plan driven
through ``collect_actual_dataset``'s O2 lineage mode instead of Rule v0
self-play -- and then marked complete in the same run state the CLI itself
uses, so every later phase runs exactly as it would from the CLI.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mage_ptcg.o2_training_loop.c4_bridge import run_o2_actual_collection  # noqa: E402
from mage_ptcg.o2_training_loop.core import build_match_matrix, load_deck_pool, load_opponent_pool  # noqa: E402
from mage_ptcg.offline_training import runstate  # noqa: E402
from mage_ptcg.offline_training.cli import Pipeline  # noqa: E402
from mage_ptcg.offline_training.config import load_config  # noqa: E402


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-pool", type=Path, default=REPOSITORY_ROOT / "configs/competition/deck_pool_o2_v1.yaml")
    parser.add_argument("--opponent-pool", type=Path, default=REPOSITORY_ROOT / "configs/competition/opponent_pool_o2_v1.yaml")
    parser.add_argument("--challenger", default="rule-agent-v0")
    parser.add_argument("--opponent", default="random-legal-v0")
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--offline-training-config", type=Path, default=REPOSITORY_ROOT / "configs/competition/o2_actual_smoke_training.json")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="o2-actual-lineage")
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--validation-percent", type=int, default=25)
    parser.add_argument("--split-seed", type=int, default=93)
    parser.add_argument("--base-seed", type=int, default=0)
    args = parser.parse_args(argv)

    decks = load_deck_pool(args.deck_pool)
    opponents = load_opponent_pool(args.opponent_pool, deck_ids=decks)
    specs = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id=args.challenger,
        opponent_ids=[args.opponent], seeds=args.seeds, engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )

    run_dir = args.run_dir.resolve()
    collection_root = run_dir / "collection"
    summary = run_o2_actual_collection(
        specs=specs, challenger_id=args.challenger, opponents=opponents, decks=decks,
        repository_root=REPOSITORY_ROOT, output_root=collection_root, run_id="cabt",
        base_seed=args.base_seed, canonical_base_sha=_git_head(), max_steps=args.max_steps,
        validation_percent=args.validation_percent, split_seed=args.split_seed,
    )
    summary = dict(summary)
    summary["collection_source"] = "actual"
    summary["actual_cabt"] = "ACTUAL_CABT_RUN"
    collection_root.mkdir(parents=True, exist_ok=True)
    (collection_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )

    config = load_config(args.offline_training_config)
    pipeline = Pipeline(config, run_dir)
    with runstate.run_lock(runstate.RunPaths(run_dir), args.run_id):
        pipeline.open(run_id=args.run_id, resume=False)
        pipeline.state.set_phase(
            "collect", runstate.STATUS_COMPLETE,
            collection_source="actual", actual_cabt="ACTUAL_CABT_RUN",
        )
        results = [
            {"phase": "collect", "status": "COMPLETE", "matches_planned": len(specs),
             "episodes": summary.get("episode_count"), "decisions": summary.get("decision_count"),
             "o2_match_ids": summary.get("o2_match_ids"), "o2_plan_hashes": summary.get("o2_plan_hashes")},
            pipeline.phase_build_dataset(force=False),
            pipeline.phase_train(force=False),
            pipeline.phase_export(force=False),
            pipeline.phase_evaluate(force=False),
            pipeline.phase_screen(force=False),
            pipeline.phase_package(force=False),
            pipeline.phase_verify(force=False),
        ]
        pipeline.state.save()
    print(json.dumps({"run_dir": str(run_dir), "phases": results}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
