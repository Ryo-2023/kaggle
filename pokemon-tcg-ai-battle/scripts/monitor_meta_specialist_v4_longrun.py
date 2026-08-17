#!/usr/bin/env python3
"""Monitor a V4 longrun with one terminal progress bar and aggregate fields.

The monitor is read-only: it consumes the atomic manifest/progress artifacts
written by the longrun wrapper and never changes run state.  In a TTY it owns
one updating tqdm bar.  When piped or redirected it emits one aggregate
snapshot at the configured interval, so it is safe to paste into a terminal
without producing one line per game/update.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any

from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1


@dataclass(frozen=True)
class MonitorSnapshotV4:
    status: str
    stage: str
    completed: int
    total: int
    seed: int | None = None
    epoch: int | None = None
    optimizer_updates: int | None = None
    validation_nll: float | None = None
    train_nll: float | None = None
    gradient_norm: float | None = None
    faults: int = 0
    games_completed: int = 0
    games_total: int = 0
    score_rate: float | None = None
    elapsed_seconds: float | None = None
    error: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: object, default: float | None = None) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _int(value: object, default: int | None = None) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _evaluation_progress(root: Path, *, allow_expected_total: bool = False) -> tuple[int, int, int, float | None]:
    completed = total = faults = 0
    score: float | None = None
    for path in sorted(root.glob("heldout_seed_*.progress.json")):
        payload = _read_json(path)
        completed += _int(payload.get("completed"), 0) or 0
        total += _int(payload.get("total"), 0) or 0
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        faults += _int(fields.get("faults"), 0) or 0
        candidate_score = _number(fields.get("rate"))
        if candidate_score is not None:
            score = candidate_score
    manifest = _read_json(root / "run-manifest.json")
    if not total:
        config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
        seeds = config.get("seeds") if isinstance(config.get("seeds"), list) else [0, 1]
        games_per_seat = _int(config.get("games_per_seat"), 0) or 0
        can_estimate = manifest.get("status") == "complete" or allow_expected_total
        total = len(seeds) * 6 * 2 * games_per_seat if can_estimate else 0
        if manifest.get("status") == "complete":
            completed = total
    return completed, total, faults, score


def _snapshot_from_files_v4(root: Path) -> MonitorSnapshotV4:
    manifest = _read_json(root / "run-manifest.json")
    summary = _read_json(root / "progress_summary.json")
    training = _read_json(root / "training-progress.json")
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    seeds = config.get("seeds") if isinstance(config.get("seeds"), list) else [0, 1]
    epochs_requested = _int(config.get("epochs"), _int(summary.get("epochs_requested"), 0) or 0) or 0
    train_total = len(seeds) * epochs_requested
    seed = _int(summary.get("seed"), _int(training.get("seed")))
    epochs_completed = _int(summary.get("epochs_completed"), _int(training.get("epochs_completed"), 0) or 0) or 0
    training_completed = 0
    if seed in seeds:
        training_completed = seeds.index(seed) * epochs_requested + epochs_completed
    elif manifest.get("status") == "complete":
        training_completed = train_total
    training_finished = (
        _int(training.get("epochs_completed"), _int(summary.get("epochs_completed"), 0) or 0) == epochs_requested
        and epochs_requested > 0
        and (training.get("status") == "complete" or summary.get("status") == "complete")
    )
    eval_completed, eval_total, eval_faults, eval_score = _evaluation_progress(
        root, allow_expected_total=training_finished,
    )
    status = str(manifest.get("status") or summary.get("status") or training.get("status") or "unknown")
    if status == "running" and training_finished:
        status = "evaluation_pending"
    child_stage = summary.get("child_stage") or training.get("stage")
    stage = "complete" if status == "complete" else ("evaluation_pending" if status == "evaluation_pending" else str(child_stage or manifest.get("stage") or "unknown"))
    total = train_total + eval_total
    completed = training_completed + eval_completed
    if status == "complete":
        completed = total = max(1, total)
    history = summary.get("history_row") if isinstance(summary.get("history_row"), dict) else {}
    return MonitorSnapshotV4(
        status=status, stage=stage, completed=completed, total=max(1, total), seed=seed,
        epoch=_int(summary.get("epoch"), _int(training.get("epoch"))),
        optimizer_updates=_int(summary.get("optimizer_updates_completed"), _int(training.get("optimizer_updates_completed"))),
        validation_nll=_number(summary.get("latest_validation_complete_action_nll"), _number(history.get("validation_complete_action_nll"))),
        train_nll=_number(summary.get("latest_train_complete_action_nll"), _number(history.get("train_complete_action_nll"))),
        gradient_norm=_number(summary.get("latest_gradient_norm"), _number(history.get("mean_preclip_gradient_norm"))),
        faults=eval_faults, games_completed=eval_completed, games_total=eval_total,
        score_rate=eval_score, elapsed_seconds=_number(summary.get("invocation_elapsed_seconds")),
        error=str(summary.get("error")) if summary.get("error") else None,
    )


def _fields(snapshot: MonitorSnapshotV4) -> dict[str, object]:
    return {
        "stage": snapshot.stage, "seed": snapshot.seed, "epoch": snapshot.epoch,
        "update": snapshot.optimizer_updates, "valid_nll": snapshot.validation_nll,
        "train_nll": snapshot.train_nll, "grad": snapshot.gradient_norm,
        "games": f"{snapshot.games_completed}/{snapshot.games_total}" if snapshot.games_total else None,
        "faults": snapshot.faults, "score": snapshot.score_rate,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path, help="longrun output root containing progress artifacts")
    parser.add_argument("--interval", type=float, default=10.0, help="refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    root = args.output_root.resolve()
    if args.once:
        snapshot = _snapshot_from_files_v4(root)
        print(json.dumps({"status": snapshot.status, "stage": snapshot.stage, "completed": snapshot.completed,
                          "total": snapshot.total, **_fields(snapshot)}, ensure_ascii=False, sort_keys=True))
        return 0
    reporter: ProgressReporterV1 | None = None
    last_completed = 0
    last_stage: str | None = None
    try:
        while True:
            snapshot = _snapshot_from_files_v4(root)
            if reporter is None:
                reporter = ProgressReporterV1(total=max(1, snapshot.total), desc="v4-longrun", stream=sys.stderr,
                                               snapshot_interval_seconds=args.interval)
            if last_stage != snapshot.stage:
                reporter.note(f"[v4-longrun] stage={snapshot.stage} status={snapshot.status}")
                last_stage = snapshot.stage
            reporter.update(max(0, snapshot.completed - last_completed), **_fields(snapshot))
            last_completed = max(last_completed, snapshot.completed)
            if snapshot.status in {"complete", "failed", "interrupted_epoch_boundary_resumable", "interrupted_restartable"}:
                reporter.close(status="done" if snapshot.status == "complete" else snapshot.status)
                return 0 if snapshot.status == "complete" else 2
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if reporter is not None:
            reporter.close(status="monitor_interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
