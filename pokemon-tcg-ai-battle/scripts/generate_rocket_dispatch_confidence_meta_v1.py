#!/usr/bin/env python3
"""Seal Rocket dispatch-confidence meta source variants."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.rocket_dispatch_confidence_meta_v1 import (
    ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1,
    RocketDispatchConfidenceMetaError,
    seal_rocket_dispatch_confidence_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--variant", action="append", dest="variants", default=[])
    parser.add_argument("--split-by-variant", type=Path, required=True)
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--scan-root", action="append", type=Path, default=[])
    args = parser.parse_args(argv)

    variants = tuple(args.variants) if args.variants else ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1
    split_payload = json.loads(args.split_by_variant.read_text(encoding="utf-8"))
    split_by_variant = split_payload.get("split_by_variant", split_payload)
    try:
        report = seal_rocket_dispatch_confidence_meta_v1(
            base_root=args.base_root,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            variants=variants,
            split_by_variant=split_by_variant,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=tuple(args.scan_root),
        )
    except (RocketDispatchConfidenceMetaError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
