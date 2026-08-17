#!/usr/bin/env python3
"""Build the hash-bound six-teacher actor-visible AWR sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
    build_derived_teacher_awr_artifact_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--catalog",
        default="runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json",
    )
    parser.add_argument(
        "--output-dir",
        default="runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v1",
    )
    parser.add_argument("--fold-count", type=int, default=5)
    parser.add_argument("--ridge-lambda", type=float, default=1.0)
    parser.add_argument("--beta", type=float, default=1.0)
    parser.add_argument("--max-weight", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root).resolve()
    catalog = (root / args.catalog).resolve() if not Path(args.catalog).is_absolute() else Path(args.catalog).resolve()
    output = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir).resolve()
    manifest = build_derived_teacher_awr_artifact_v1(
        repo_root=root,
        catalog_path=catalog,
        output_sidecar_path=output / "weights.jsonl",
        output_manifest_path=output / "manifest.json",
        fold_count=args.fold_count,
        ridge_lambda=args.ridge_lambda,
        beta=args.beta,
        max_weight=args.max_weight,
    )
    print(json.dumps({
        "manifest": str(output / "manifest.json"),
        "manifest_sha256": manifest["manifest_sha256"],
        "sidecar_sha256": manifest["sidecar"]["sha256"],
        "rows": manifest["counts"]["rows"],
        "teachers": manifest["counts"]["teachers"],
        "train_rows": manifest["counts"]["train_rows"],
        "heldout_rows": manifest["counts"]["heldout_rows"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
