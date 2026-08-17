#!/usr/bin/env python3
"""Collect sealed teacher-vs-Rule-v0 primary evidence for two lanes.

The runner deliberately produces only performance/fault provenance.  It never
assigns a quality weight or changes the teacher-quality trust set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mage_ptcg.meta_specialist.teacher_quality_evidence_v3 import (  # noqa: E402
    LaneEvidenceInputV3,
    build_campaign_plan_v3,
    build_live_attempt_runner_v3,
    collect_teacher_quality_evidence_v3,
)
from mage_ptcg.meta_specialist.teacher_quality_worker_v3 import (  # noqa: E402
    seal_teacher_quality_source_snapshot_v3,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot resolve the current source commit") from exc


def _lane(value: str) -> LaneEvidenceInputV3:
    """Parse ``lane=teacher_id=revision=deck_path`` without silent defaults."""
    pieces = value.split("=", 3)
    if len(pieces) != 4 or any(not part for part in pieces):
        raise argparse.ArgumentTypeError(
            "--lane must be lane=teacher_id=revision=deck_path"
        )
    lane, teacher_id, revision, deck = pieces
    deck_path = Path(deck).resolve()
    if not deck_path.is_file():
        raise argparse.ArgumentTypeError(f"lane deck does not exist: {deck_path}")
    return LaneEvidenceInputV3(
        lane, teacher_id, revision, str(deck_path), _sha(deck_path),
    )


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("worker timeout must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("worker timeout must be positive")
    return timeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("calibration", "full"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--lane", action="append", type=_lane, required=True,
        help="repeat exactly twice: lane=teacher_id=revision=deck_path",
    )
    parser.add_argument(
        "--schedule", type=Path,
        default=ROOT / "configs/meta_specialist/opponent_schedule_v1.json",
    )
    parser.add_argument("--schedule-sha256", required=True)
    parser.add_argument("--pool-root", type=Path, default=ROOT / "opponents")
    parser.add_argument("--pool-manifest-sha256", required=True)
    parser.add_argument("--engine-entry-point", type=Path, default=ROOT / "scripts/test_sim.py")
    parser.add_argument("--engine-sha256", required=True)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--source-commit-sha256", required=True)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument(
        "--source-snapshot-root", type=Path,
        default=ROOT / ".teacher-quality-v3-snapshots",
        help="private local directory used to seal the worker source snapshot",
    )
    parser.add_argument(
        "--worker-timeout-seconds", type=_positive_timeout, default=30.0,
        help="per-attempt fresh-worker timeout (must be positive)",
    )
    parser.add_argument(
        "--plan-only", action="store_true",
        help="freeze and print the campaign without starting CABT games",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if len(args.lane) != 2 or len({item.lane for item in args.lane}) != 2:
        parser.error("--lane must specify exactly two distinct lanes")
    commit = args.source_commit or _commit()
    plan = build_campaign_plan_v3(
        profile=args.profile,
        lanes=tuple(args.lane),
        schedule_path=args.schedule.resolve(),
        expected_schedule_sha256=args.schedule_sha256,
        pool_root=args.pool_root.resolve(),
        expected_pool_manifest_sha256=args.pool_manifest_sha256,
        engine_entry_point=args.engine_entry_point.resolve(),
        expected_engine_sha256=args.engine_sha256,
        source_commit=commit,
        expected_source_commit_sha256=args.source_commit_sha256,
    )
    # Git HEAD remains reference metadata only.  The actual authority is this
    # sealed source closure, so a dirty worktree is valid as long as every
    # executed byte is copied and hashed before the plan is printed or run.
    snapshot = seal_teacher_quality_source_snapshot_v3(
        plan=plan, staging_root=args.source_snapshot_root.resolve(),
    )
    try:
        if args.plan_only:
            payload = plan.to_payload()
            payload["source_snapshot_file_sha256"] = snapshot.file_sha256
            payload["source_snapshot_tree_sha256"] = snapshot.tree_sha256
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        runner = build_live_attempt_runner_v3(
            plan=plan, source_snapshot=snapshot,
            max_steps=args.max_steps, worker_timeout_seconds=args.worker_timeout_seconds,
        )
        try:
            manifest = collect_teacher_quality_evidence_v3(
                plan=plan, output_dir=args.output.resolve(), runner=runner,
            )
        finally:
            runner.close()
    finally:
        snapshot.close()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
