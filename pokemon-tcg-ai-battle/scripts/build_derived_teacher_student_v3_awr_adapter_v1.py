#!/usr/bin/env python3
"""Build an exact raw-AWR weight sidecar for one formal Student v3 dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
    build_derived_teacher_student_v3_awr_sidecar_v1,
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
    parser = argparse.ArgumentParser(
        prog="build-derived-teacher-student-v3-awr-adapter-v1",
        description=__doc__,
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--awr-manifest", type=Path, required=True)
    parser.add_argument("--gpu-dataset-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-file-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=args.repo_root,
            awr_manifest_path=args.awr_manifest,
            gpu_dataset_dir=args.gpu_dataset_dir,
            catalog_path=args.catalog,
            expected_catalog_file_sha256=args.catalog_file_sha256,
            output_path=args.output,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(_canonical({"error": type(exc).__name__, "message": str(exc)}))
        return 2
    print(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
