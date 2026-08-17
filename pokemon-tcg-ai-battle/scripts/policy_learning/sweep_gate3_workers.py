#!/usr/bin/env python3
"""Run the bounded Gate 3 actor-count diagnostic with terminal progress.

Each actor-count run has an independently immutable schedule and never exceeds
64 games.  Jobs are sampled round-robin over opponent and candidate side from
the supplied source schedule, retaining the source seeds and deck bindings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from mage_ptcg.offline_scaleup import pipeline  # noqa: E402


def _balanced_jobs(schedule: dict[str, object], count: int, suffix: str) -> list[dict[str, object]]:
    buckets: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for job in schedule["games"]:
        buckets[(str(job["opponent"]), int(job["candidate_side"]))].append(dict(job))
    keys = sorted(buckets)
    selected: list[dict[str, object]] = []
    index = 0
    while len(selected) < count:
        key = keys[index % len(keys)]
        bucket = buckets[key]
        position = index // len(keys)
        if position >= len(bucket):
            raise ValueError("source schedule has insufficient jobs for the requested diagnostic")
        original = bucket[position]
        clone = dict(original)
        clone["source_game_id"] = original["game_id"]
        clone["game_id"] = f"{original['game_id']}-{suffix}"
        selected.append(clone)
        index += 1
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--actors", type=int, nargs="+", default=[1, 4, 12, 24])
    parser.add_argument("--games-per-run", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--stop-after", type=int, default=None,
                        help="intentional per-run pause for terminal-visible resume testing")
    args = parser.parse_args(argv)
    if not args.actors or any(value < 1 for value in args.actors) or not 1 <= args.games_per_run <= 64:
        parser.error("actors must be positive and games-per-run must be 1..64")
    if len(set(args.actors)) != len(args.actors):
        parser.error("actor counts must be unique")
    if len(args.actors) * args.games_per_run > 512:
        parser.error("combined diagnostic cap is 512 games")
    args.output_root.mkdir(parents=True, exist_ok=True)
    source = json.loads((args.source_run / "schedule.json").read_text(encoding="utf-8"))
    reports: dict[str, object] = {}
    for actors in args.actors:
        run_dir = args.output_root / f"actors-{actors}"
        run_dir.mkdir(exist_ok=True)
        if not (run_dir / "schedule.json").exists():
            jobs = _balanced_jobs(source, args.games_per_run, f"actors-{actors}")
            schedule = {
                "schema_version": pipeline.SCHEDULE_SCHEMA,
                "schedule_digest": pipeline._digest(jobs, "gate3-actor-sweep"),
                "population_digest": source["population_digest"], "candidate": source["candidate"],
                "opponents": source["opponents"], "planned_games": len(jobs),
                "engine_seed_supported": "UNKNOWN_UNTIL_RUNTIME", "diagnostic_cap_per_run": 64,
                "source_run": str(args.source_run), "actor_count": actors, "games": jobs,
            }
            pipeline._atomic_json(run_dir / "schedule.json", schedule)
        reports[str(actors)] = pipeline.run_league(
            run_dir=run_dir, population_path=args.population, repo=args.repo.resolve(), executor="cabt",
            timeout=args.timeout, max_attempts=1, workers=actors, progress=True,
            progress_interval_seconds=10.0, start_method="spawn", worker_recycle_games=8,
            stop_after=args.stop_after,
        )
        if reports[str(actors)]["gate"] != "PASS":
            pipeline._atomic_json(args.output_root / "worker_sweep_summary.json", {
                "schema_version": "policy-learning-gate3-worker-sweep-v1", "reports": reports,
                "gate": "BLOCKED", "failed_actor_count": actors,
            })
            return 2
    payload = {"schema_version": "policy-learning-gate3-worker-sweep-v1", "reports": reports, "gate": "PASS"}
    pipeline._atomic_json(args.output_root / "worker_sweep_summary.json", payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
