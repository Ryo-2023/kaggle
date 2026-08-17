#!/usr/bin/env python3
"""Resumable relative teacher tournament for submitted proxy assets.

No native/CABT module is imported by this controller.  Every candidate versus
opponent batch is a new session owned by ``run_isolated``.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mage_ptcg.evaluation.isolated_runtime import run_isolated


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def schedule(
    rows: list[dict[str, str]], games_per_asset: int, *, candidate_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    # Water Box's 0.05s proxy smoke passes, but invoking its native search as
    # an opponent can terminate the outer sandbox on this host.  Preserve it
    # as a separately classified runtime proxy and keep the other assets
    # progressing through an all-pure-Python opponent panel.
    candidates = [row for row in rows if row["local_runtime_status"] == "PROXY_RUNTIME_PASSED" and row["asset_id"] != "dev/waterbox_search_v3"]
    panel = [row for row in candidates if row["asset_id"].startswith("dev/")]
    if len(candidates) != 15 or len(panel) != 7:
        raise ValueError("expected 15 cross-asset-safe candidates and 7 pure-Python panel assets")
    scheduled: list[dict[str, object]] = []
    for candidate in candidates:
        if candidate_ids is not None and candidate["asset_id"] not in candidate_ids:
            continue
        opponents = [row for row in panel if row["asset_id"] != candidate["asset_id"]]
        base, remainder = divmod(games_per_asset, len(opponents))
        for index, opponent in enumerate(opponents):
            scheduled.append({"candidate_id": candidate["asset_id"], "candidate_path": candidate["extraction_path"], "candidate_policy_hash": candidate["policy_hash"], "candidate_deck_hash": candidate["deck_hash"], "opponent_id": opponent["asset_id"], "opponent_path": opponent["extraction_path"], "opponent_policy_hash": opponent["policy_hash"], "opponent_deck_hash": opponent["deck_hash"], "games": base + (1 if index < remainder else 0), "side_balanced": True, "self_lineage_excluded": True})
    return scheduled


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def run(
    artifact_root: Path, *, games_per_asset: int, resume: bool, output_dir: str,
    candidate_ids: set[str] | None = None, limit: int | None = None,
) -> int:
    source = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-assets-calibration-teacher-v1-20260726_181000/runtime_qualification.csv")
    phase = artifact_root / output_dir; shards = phase / "shards"; schedule_rows = schedule(_read(source), games_per_asset, candidate_ids=candidate_ids)
    if limit is not None:
        schedule_rows = schedule_rows[:limit]
    _csv(phase / "teacher_screen_schedule.csv", schedule_rows)
    results: list[dict[str, object]] = []
    for item in schedule_rows:
        shard = shards / str(item["candidate_id"]).replace("/", "__") / f"{str(item['opponent_id']).replace('/', '__')}.json"
        if resume and shard.exists():
            results.append(json.loads(shard.read_text(encoding="utf-8"))); continue
        command = (sys.executable, str(ROOT / "scripts" / "run_submitted_asset_lifecycle.py"), "--smoke-child", "--asset", str(item["candidate_path"]), "--opponent", str(item["opponent_path"]), "--games", str(item["games"]))
        result = run_isolated(command, cwd=Path("/tmp"), shard_path=shard, timeout_seconds=120)
        parsed: dict[str, object] = {}
        if result.status == "NORMAL_EXIT":
            try: parsed = json.loads(result.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError): parsed = {"status": "CHILD_PROTOCOL_FAILURE", "wins": 0, "smoke_games": 0, "illegal": 0, "crash": 1, "timeout": 0}
        else:
            parsed = {"status": result.status, "wins": 0, "smoke_games": 0, "illegal": 0, "crash": int(result.status == "SIGSEGV"), "timeout": int(result.status == "TIMEOUT")}
        row = {**item, **parsed, "isolation_status": result.status, "exit_code": result.exit_code, "signal_number": result.signal_number, "worker_pid": result.pid, "worker_pgid": result.process_group_id, "runtime_seconds": result.ended_at-result.started_at}
        _write(shard, row); results.append(row)
    _csv(phase / "teacher_screen_results.csv", results)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in results: grouped[str(row["candidate_id"])].append(row)
    summary = []
    for candidate, own in sorted(grouped.items()):
        games = sum(int(row.get("smoke_games") or 0) for row in own); wins = sum(int(row.get("wins") or 0) for row in own)
        rates = [int(row.get("wins") or 0) / int(row["smoke_games"]) for row in own if int(row.get("smoke_games") or 0)]
        summary.append({"asset_id": candidate, "games": games, "wins": wins, "policy_uniform_win_rate": sum(rates)/len(rates) if rates else None, "worst_opponent_rate": min(rates) if rates else None, "illegal": sum(int(row.get("illegal") or 0) for row in own), "crash": sum(int(row.get("crash") or 0) for row in own), "timeout": sum(int(row.get("timeout") or 0) for row in own), "status": "COMPLETE" if games == games_per_asset else "INCOMPLETE"})
    _csv(phase / "teacher_screen_summary.csv", summary)
    _write(phase / "checkpoint.json", {"status": "COMPLETE" if len(results) == len(schedule_rows) and all(row["status"] == "COMPLETE" for row in summary) else "PARTIAL", "planned_batches": len(schedule_rows), "completed_batches": len(results), "games": sum(int(row.get("smoke_games") or 0) for row in results)})
    return 0


def aggregate(
    artifact_root: Path, *, games_per_asset: int, output_dir: str,
    candidate_ids: set[str] | None = None,
) -> int:
    phase = artifact_root / output_dir
    results = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((phase / "shards").rglob("*.json"))]
    _csv(phase / "teacher_screen_results.csv", results)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in results: grouped[str(row["candidate_id"])].append(row)
    summary = []
    for candidate, own in sorted(grouped.items()):
        games = sum(int(row.get("smoke_games") or 0) for row in own); wins = sum(int(row.get("wins") or 0) for row in own)
        rates = [int(row.get("wins") or 0) / int(row["smoke_games"]) for row in own if int(row.get("smoke_games") or 0)]
        summary.append({"asset_id": candidate, "games": games, "wins": wins, "policy_uniform_win_rate": sum(rates)/len(rates) if rates else None, "worst_opponent_rate": min(rates) if rates else None, "illegal": sum(int(row.get("illegal") or 0) for row in own), "crash": sum(int(row.get("crash") or 0) for row in own), "timeout": sum(int(row.get("timeout") or 0) for row in own), "status": "COMPLETE" if games == games_per_asset else "PARTIAL"})
    _csv(phase / "teacher_screen_summary.csv", summary)
    planned = schedule(_read(Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-assets-calibration-teacher-v1-20260726_181000/runtime_qualification.csv")), games_per_asset, candidate_ids=candidate_ids)
    complete = len(results) == len(planned) and all(row["status"] == "COMPLETE" for row in summary)
    _write(phase / "checkpoint.json", {"status": "COMPLETE" if complete else "PARTIAL", "planned_batches": len(planned), "completed_batches": len(results), "games": sum(int(row.get("smoke_games") or 0) for row in results), "completed_assets": sum(row["status"] == "COMPLETE" for row in summary)})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--artifact-root", type=Path, required=True); parser.add_argument("--games-per-asset", type=int, default=96); parser.add_argument("--resume", action="store_true"); parser.add_argument("--limit", type=int); parser.add_argument("--aggregate-only", action="store_true"); parser.add_argument("--output-dir", default="03_proxy_teacher_screen/isolated_v2"); parser.add_argument("--candidate-id", action="append", default=[])
    args = parser.parse_args(); candidate_ids = set(args.candidate_id) or None
    return aggregate(args.artifact_root, games_per_asset=args.games_per_asset, output_dir=args.output_dir, candidate_ids=candidate_ids) if args.aggregate_only else run(args.artifact_root, games_per_asset=args.games_per_asset, resume=args.resume, output_dir=args.output_dir, candidate_ids=candidate_ids, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
