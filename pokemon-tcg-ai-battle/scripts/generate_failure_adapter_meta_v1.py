#!/usr/bin/env python3
"""Generate a research-only self-owned public-state failure-adapter pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.self_owned_failure_adapter_v1 import (
    FailureAdapterMetaError,
    VARIANT_IDS,
    seal_failure_adapter_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--p1-package", required=True, type=Path)
    parser.add_argument("--variant", action="append", dest="variants", default=[], help="declared adapter variant; repeatable")
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--scan-root", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    variants = tuple(args.variants) if args.variants else VARIANT_IDS
    try:
        report = seal_failure_adapter_meta_v1(
            source_package=args.source_package,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            variants=variants,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=tuple(args.scan_root),
        )
    except (FailureAdapterMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
