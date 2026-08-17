#!/usr/bin/env python3
"""Produce bounded, seat-aware evidence for a blocked Gate 3 CABT league.

The script never edits the source run.  It first extracts a redacted failure
registry, then (unless --no-replay is selected) replays each failed scheduled
slot exactly ``--replays`` times in a new spawned-worker run.  The default is
24 games for the eight known failures, below the 512-game diagnostic cap.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.offline_scaleup import pipeline  # noqa: E402


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _registry(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for row in rows:
        if row.get("status") == "DONE":
            continue
        fault = row.get("fault") if isinstance(row.get("fault"), dict) else {}
        selected.append({
            "game_id": row.get("game_id"), "seed": row.get("seed"), "candidate": row.get("candidate"),
            "opponent": row.get("opponent"), "candidate_side": row.get("candidate_side"),
            "status": row.get("status"), "fault_kind": fault.get("kind"),
            "fault_attribution": row.get("fault_attribution"),
            "candidate_fault": row.get("candidate_fault"),
            "candidate_error_code": row.get("candidate_error_code"),
            # The old run predates seat telemetry.  Record absence explicitly
            # rather than infer a candidate/opponent fault from AGENT_ERROR.
            "agent_status": row.get("agent_status", "NOT_RECORDED_OLD_RUN"),
            "engine_failure_scope": row.get("engine_failure_scope", "NOT_RECORDED_OLD_RUN"),
            "terminal_reason": row.get("terminal_reason", "NOT_RECORDED_OLD_RUN"),
        })
    return selected


def _write_registry(path: Path, rows: list[dict[str, object]]) -> None:
    fields = ["game_id", "seed", "candidate", "opponent", "candidate_side", "status", "fault_kind",
              "fault_attribution", "candidate_fault", "candidate_error_code", "agent_status",
              "engine_failure_scope", "terminal_reason"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def _replay_jobs(registry: list[dict[str, object]], schedule: dict[str, object], repetitions: int) -> list[dict[str, object]]:
    original = {str(row["game_id"]): row for row in schedule["games"]}
    jobs: list[dict[str, object]] = []
    for repetition in range(1, repetitions + 1):
        for item in registry:
            source = original[str(item["game_id"])]
            clone = dict(source)
            clone["source_game_id"] = source["game_id"]
            clone["diagnostic_replay"] = repetition
            clone["game_id"] = f"{source['game_id']}-r{repetition}"
            jobs.append(clone)
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--replays", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--game-id", action="append", default=[], help="additional source schedule slot to replay")
    parser.add_argument("--no-replay", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.replays <= 5 or args.workers < 1:
        parser.error("--replays must be 1..5 and --workers must be positive")
    source_rows = _rows(args.run_dir / "game_results.jsonl")
    source_schedule = json.loads((args.run_dir / "schedule.json").read_text(encoding="utf-8"))
    registry = _registry(source_rows)
    source_jobs = {str(row["game_id"]): row for row in source_schedule["games"]}
    existing_ids = {str(row["game_id"]) for row in registry}
    for game_id in args.game_id:
        if game_id in existing_ids:
            continue
        job = source_jobs.get(game_id)
        if job is None:
            parser.error(f"--game-id is absent from source schedule: {game_id}")
        registry.append({
            "game_id": game_id, "seed": job["seed"], "candidate": job["candidate"],
            "opponent": job["opponent"], "candidate_side": job["candidate_side"],
            "status": "SELECTED_FOR_REPLAY", "fault_kind": "EXPLICIT_SLOT", "fault_attribution": "UNRESOLVED",
            "candidate_fault": False, "candidate_error_code": None, "agent_status": "NOT_RECORDED_SOURCE_RUN",
            "engine_failure_scope": "NOT_RECORDED_SOURCE_RUN", "terminal_reason": "NOT_RECORDED_SOURCE_RUN",
        })
    if not registry:
        raise SystemExit("no non-DONE games found; diagnostic is not applicable")
    planned = len(registry) * args.replays
    if planned > 512:
        raise SystemExit(f"diagnostic replay cap exceeded: {planned} > 512")
    args.output_root.mkdir(parents=True, exist_ok=False)
    _write_registry(args.output_root / "failed_game_registry.csv", registry)
    distribution = {
        "schema_version": "policy-learning-gate3-failure-distribution-v1",
        "source_run": str(args.run_dir), "failed_games": len(registry),
        "by_opponent": dict(sorted(Counter(str(row["opponent"]) for row in registry).items())),
        "by_candidate_side": dict(sorted(Counter(str(row["candidate_side"]) for row in registry).items())),
        "by_status": dict(sorted(Counter(str(row["status"]) for row in registry).items())),
        "candidate_faults": sum(bool(row["candidate_fault"]) for row in registry),
        "old_run_seat_telemetry": "NOT_RECORDED_OLD_RUN",
    }
    pipeline._atomic_json(args.output_root / "failure_distribution.json", distribution)
    (args.output_root / "hypotheses.md").write_text(
        "# Gate 3 failure hypotheses\n\n"
        "旧runはCABTの`agent_status`とterminal reasonを保存していないため、8件の`AGENT_ERROR`はcandidate、opponent、engineを区別できない。"
        "candidate wrapperの例外は0件であるが、candidate seatのengine statusが正常だった根拠にはならない。\n\n"
        "再現runではspawn worker、gameごとのscratch、seat-aware telemetry、失敗時engine resultを使う。"
        "同一scheduled slotを3回再実行し、再現率とseatを集計する。\n",
        encoding="utf-8",
    )
    if args.no_replay:
        print(json.dumps({"output_root": str(args.output_root), "failed_games": len(registry), "replays": 0}, ensure_ascii=False))
        return 0
    schedule = source_schedule
    jobs = _replay_jobs(registry, schedule, args.replays)
    replay_run = args.output_root / "replays"
    replay_run.mkdir()
    replay_schedule = {
        "schema_version": pipeline.SCHEDULE_SCHEMA,
        "schedule_digest": pipeline._digest(jobs, "gate3-diagnostic-schedule"),
        "population_digest": schedule["population_digest"], "candidate": schedule["candidate"],
        "opponents": schedule["opponents"], "planned_games": len(jobs), "engine_seed_supported": "UNKNOWN_UNTIL_RUNTIME",
        "diagnostic_cap": 512, "source_run": str(args.run_dir), "games": jobs,
    }
    pipeline._atomic_json(replay_run / "schedule.json", replay_schedule)
    summary = pipeline.run_league(run_dir=replay_run, population_path=args.population, repo=args.repo.resolve(),
                                  executor="cabt", timeout=args.timeout, max_attempts=1, workers=args.workers,
                                  progress=True, progress_interval_seconds=10.0, start_method="spawn",
                                  worker_recycle_games=8)
    replay_rows = _rows(replay_run / "game_results.jsonl")
    replay_report = {
        "schema_version": "policy-learning-gate3-replay-report-v1", "planned": planned,
        "summary": summary,
        "by_engine_failure_scope": dict(sorted(Counter(str(row.get("engine_failure_scope", "UNAVAILABLE")) for row in replay_rows).items())),
        "by_status": dict(sorted(Counter(str(row.get("status")) for row in replay_rows).items())),
        "source_game_reproductions": dict(sorted(Counter(str(row.get("source_game_id")) for row in replay_rows if row.get("status") != "DONE").items())),
    }
    pipeline._atomic_json(args.output_root / "replay_report.json", replay_report)
    print(json.dumps({"output_root": str(args.output_root), "failed_games": len(registry), "replays": planned,
                      "gate": summary["gate"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
