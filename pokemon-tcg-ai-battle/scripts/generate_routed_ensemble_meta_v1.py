#!/usr/bin/env python3
"""Generate a research-only actor-visible routed-ensemble meta pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.routed_ensemble_meta_v1 import (
    RoutedEnsembleMetaError,
    seal_routed_ensemble_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", action="append", default=[], metavar="KEY=ROOT", help="parent key and sealed candidate root; repeatable")
    parser.add_argument("--spec", action="append", default=[], metavar="ID:A:B:DECK:ROUTE", help="candidate specification; repeatable")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--p1-package", required=True, type=Path)
    parser.add_argument("--current-pool-manifest", type=Path)
    parser.add_argument("--scan-root", action="append", default=[], type=Path)
    args = parser.parse_args(argv)

    parents: dict[str, Path] = {}
    for value in args.parent:
        if "=" not in value:
            parser.error("--parent must be KEY=ROOT")
        key, raw_root = value.split("=", 1)
        if not key or not raw_root or key in parents:
            parser.error("--parent keys must be non-empty and unique")
        parents[key] = Path(raw_root)
    specifications: list[dict[str, str]] = []
    for value in args.spec:
        parts = value.split(":")
        if len(parts) != 5 or any(not part for part in parts):
            parser.error("--spec must be ID:POLICY_A:POLICY_B:DECK_PARENT:ROUTING_RECIPE")
        candidate_id, policy_a, policy_b, deck_parent, routing_recipe = parts
        specifications.append({"id": candidate_id, "policy_a": policy_a, "policy_b": policy_b, "deck_parent": deck_parent, "routing_recipe": routing_recipe})
    if not parents or not specifications:
        parser.error("at least one --parent and one --spec are required")
    try:
        report = seal_routed_ensemble_meta_v1(
            parent_roots=parents,
            specifications=tuple(specifications),
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=tuple(args.scan_root),
        )
    except (RoutedEnsembleMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
