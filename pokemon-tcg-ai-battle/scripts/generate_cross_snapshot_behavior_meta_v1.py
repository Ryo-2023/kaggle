#!/usr/bin/env python3
"""Seal a research-only cross-snapshot behavior meta source family."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.opponent_ingest.cross_snapshot_behavior_meta_v1 import (
    CrossSnapshotBehaviorMetaError,
    seal_cross_snapshot_behavior_meta_v1,
)


def _read_spec(path: Path) -> list[Mapping[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, Mapping) else payload
    if not isinstance(entries, list):
        raise CrossSnapshotBehaviorMetaError("spec must be a JSON list or an object with an entries list")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--current-pool-manifest", type=Path)
    parser.add_argument("--scan-root", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    try:
        result = seal_cross_snapshot_behavior_meta_v1(
            entries=_read_spec(args.spec),
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            p1_package=args.p1_package,
            current_pool_manifest=args.current_pool_manifest,
            scan_roots=args.scan_root,
        )
    except (CrossSnapshotBehaviorMetaError, FileExistsError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
