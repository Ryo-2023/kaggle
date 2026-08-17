#!/usr/bin/env python3
"""Audit derived-teacher snapshots and optionally publish Student v3 source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
    DEFAULT_V3_SPLIT_SEED,
    build_teacher_snapshot_student_v3_bridge_v1,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-teacher-snapshot-student-v3-bridge-v1")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dataset", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--teacher-id", action="append", default=[])
    parser.add_argument("--split-seed", default=DEFAULT_V3_SPLIT_SEED)
    args = parser.parse_args(argv)
    try:
        result = build_teacher_snapshot_student_v3_bridge_v1(
            repo_root=args.repo_root,
            catalog_path=args.catalog,
            output_dataset_path=args.output_dataset,
            output_manifest_path=args.output_manifest,
            teacher_ids=tuple(args.teacher_id),
            split_seed=args.split_seed,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(_canonical({"error": type(exc).__name__, "message": str(exc)}))
        return 2
    print(_canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
