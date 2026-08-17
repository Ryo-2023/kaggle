#!/usr/bin/env python3
"""Generate a bounded derived internal meta pool and cg split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.derived_internal_meta_v1 import (
    ROCKET_THETA_VARIANTS_V1,
    DerivedInternalMetaError,
    seal_derived_internal_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate only the fixed Rocket theta-selection variants from a sealed "
            "internal source; output is local-eval-only and no current pool is modified."
        )
    )
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--variant", action="append", dest="variants", default=[])
    parser.add_argument("--no-base", action="store_true")
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument(
        "--scan-root",
        action="append",
        type=Path,
        default=[],
        help="bounded artifact identity-scan root; may be repeated",
    )
    args = parser.parse_args(argv)
    variants = tuple(args.variants) if args.variants else ROCKET_THETA_VARIANTS_V1
    try:
        report = seal_derived_internal_meta_v1(
            base_root=args.base_root,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            variants=variants,
            include_base=not args.no_base,
            current_pool_manifest=args.current_pool_manifest,
            p1_package=args.p1_package,
            scan_roots=tuple(args.scan_root),
        )
    except (DerivedInternalMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
