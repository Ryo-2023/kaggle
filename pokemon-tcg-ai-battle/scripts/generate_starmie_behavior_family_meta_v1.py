#!/usr/bin/env python3
"""Seal explicit visible-state behavior-family proxy opponents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.behavior_family_meta_v1 import (
    ALAKAZAM_BEHAVIOR_VARIANTS_V1,
    PSYCHIC_BEHAVIOR_VARIANTS_V1,
    COMFEY_BEHAVIOR_VARIANTS_V1,
    STARMIE_BEHAVIOR_VARIANTS_V1,
    FESTIVAL_BEHAVIOR_VARIANTS_V1,
    METAL_BEHAVIOR_VARIANTS_V1,
    METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1,
    DerivedInternalMetaError,
    seal_comfey_behavior_family_v1,
    seal_alakazam_behavior_family_v1,
    seal_psychic_behavior_family_v1,
    seal_festival_behavior_family_v1,
    seal_metal_behavior_family_v1,
    seal_metal_runtime_safe_behavior_family_v1,
    seal_starmie_behavior_family_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("starmie", "comfey", "festival", "metal", "metal_runtime_safe", "alakazam", "psychic"), default="starmie")
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--variant", action="append", dest="variants", default=[])
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--scan-root", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    if args.family == "starmie":
        default_variants = STARMIE_BEHAVIOR_VARIANTS_V1
        seal = seal_starmie_behavior_family_v1
    elif args.family == "comfey":
        default_variants = COMFEY_BEHAVIOR_VARIANTS_V1
        seal = seal_comfey_behavior_family_v1
    elif args.family == "festival":
        default_variants = FESTIVAL_BEHAVIOR_VARIANTS_V1
        seal = seal_festival_behavior_family_v1
    elif args.family == "metal":
        default_variants = METAL_BEHAVIOR_VARIANTS_V1
        seal = seal_metal_behavior_family_v1
    elif args.family == "metal_runtime_safe":
        default_variants = METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1
        seal = seal_metal_runtime_safe_behavior_family_v1
    elif args.family == "alakazam":
        default_variants = ALAKAZAM_BEHAVIOR_VARIANTS_V1
        seal = seal_alakazam_behavior_family_v1
    else:
        default_variants = PSYCHIC_BEHAVIOR_VARIANTS_V1
        seal = seal_psychic_behavior_family_v1
    variants = tuple(args.variants) if args.variants else default_variants
    try:
        report = seal(
            base_root=args.base_root,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            variants=variants,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=tuple(args.scan_root),
        )
    except (DerivedInternalMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
