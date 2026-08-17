#!/usr/bin/env python3
"""Generate a research-only cross-lineage policy/deck meta pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.cross_lineage_meta_v1 import (
    CrossLineageMetaError,
    seal_cross_lineage_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage-root", action="append", default=[], type=Path, help="use the same sealed roots for policy and deck parents")
    parser.add_argument("--policy-root", action="append", default=[], type=Path, help="sealed root supplying a policy parent; repeatable")
    parser.add_argument("--deck-root", action="append", default=[], type=Path, help="sealed root supplying a deck parent; repeatable")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--p1-package", required=True, type=Path)
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--scan-root", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    if args.lineage_root and (args.policy_root or args.deck_root):
        parser.error("use either --lineage-root or --policy-root/--deck-root")
    if not args.lineage_root and (not args.policy_root or not args.deck_root):
        parser.error("provide --lineage-root, or at least one --policy-root and --deck-root")
    try:
        report = seal_cross_lineage_meta_v1(
            lineage_roots=tuple(args.lineage_root),
            policy_roots=tuple(args.policy_root) if args.policy_root else None,
            deck_roots=tuple(args.deck_root) if args.deck_root else None,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=tuple(args.scan_root),
        )
    except (CrossLineageMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
