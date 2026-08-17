#!/usr/bin/env python3
"""Seal the research-only Water Box runtime-safe meta source pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.waterbox_runtime_safe_meta_v1 import (
    WATERBOX_RUNTIME_SAFE_VARIANTS_V1,
    seal_waterbox_runtime_safe_meta_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--split-by-variant", type=Path, required=True)
    parser.add_argument("--current-pool-manifest", type=Path)
    parser.add_argument("--scan-root", type=Path, action="append", default=[])
    args = parser.parse_args()

    config = json.loads(args.split_by_variant.read_text(encoding="utf-8"))
    variants = tuple(config.get("variants", WATERBOX_RUNTIME_SAFE_VARIANTS_V1))
    split = config.get("split_by_variant", {})
    report = seal_waterbox_runtime_safe_meta_v1(
        base_root=args.base_root,
        output_root=args.output,
        source_epoch=args.source_epoch,
        seed_namespace=args.seed_namespace,
        p1_package=args.p1_package,
        variants=variants,
        split_by_variant=split,
        current_pool_manifest=args.current_pool_manifest,
        scan_roots=args.scan_root,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
