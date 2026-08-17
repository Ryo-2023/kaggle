#!/usr/bin/env python3
"""Audit sealed derived teachers and conditionally build a Student v2 dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (  # noqa: E402
    DerivedTeacherCatalogError,
)
from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (  # noqa: E402
    DEFAULT_SPLIT_SEED,
    TeacherSnapshotStudentV2BridgeError,
    build_teacher_snapshot_student_v2_bridge_v1,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dataset", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument(
        "--teacher-id",
        action="append",
        default=[],
        help="対象teacher。省略時はcatalog内のSEALED teacherを全て使用する",
    )
    parser.add_argument("--split-seed", default=DEFAULT_SPLIT_SEED)
    args = parser.parse_args(argv)
    try:
        result = build_teacher_snapshot_student_v2_bridge_v1(
            repo_root=args.repo_root,
            catalog_path=args.catalog,
            output_dataset_path=args.output_dataset,
            output_manifest_path=args.output_manifest,
            teacher_ids=tuple(args.teacher_id),
            split_seed=args.split_seed,
        )
    except (
        DerivedTeacherCatalogError,
        TeacherSnapshotStudentV2BridgeError,
        FileExistsError,
        OSError,
        ValueError,
    ) as exc:
        print(_canonical({"error": type(exc).__name__, "message": str(exc)}))
        return 2
    print(_canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
