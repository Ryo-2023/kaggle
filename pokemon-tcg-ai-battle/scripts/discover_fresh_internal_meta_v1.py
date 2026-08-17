#!/usr/bin/env python3
"""Seal permitted internal branch snapshots into an isolated research pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.fresh_internal_meta_v1 import (
    DEFAULT_REF_GLOB_V1,
    FreshInternalMetaError,
    seal_fresh_internal_meta_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only discovery of refs/remotes/origin/agents/* snapshots. "
            "The output is a new local-evaluation-only staged pool; the current pool is never modified."
        )
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--pool-manifest", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    parser.add_argument("--ref-glob", default=DEFAULT_REF_GLOB_V1)
    parser.add_argument(
        "--include-ref",
        action="append",
        default=[],
        help="restrict intake to exact remote refs; may be repeated",
    )
    parser.add_argument(
        "--history-depth",
        type=int,
        default=0,
        help="opt-in first-parent historical snapshots per selected ref (0=head only)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="stop after this many fresh snapshots have been accepted",
    )
    parser.add_argument("--exclude-ref", action="append", default=[])
    parser.add_argument(
        "--allow-readonly-telemetry-ref",
        action="append",
        default=[],
        help="explicit ref allowed to receive the exact in-memory-only telemetry sanitizer; never enabled by default",
    )
    parser.add_argument("--consumed-ledger", type=Path, default=None)
    parser.add_argument(
        "--scan-root",
        action="append",
        type=Path,
        default=[],
        help="identity-scan root; may be repeated (default: configs, docs/evidence, docs/status; add a bounded runs subdirectory explicitly)",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    pool_manifest = (args.pool_manifest or (repo / "opponents/pool_manifest.json")).resolve()
    scan_roots = args.scan_root or [
        repo / "configs",
        repo / "docs/evidence",
        repo / "docs/status",
    ]
    try:
        report = seal_fresh_internal_meta_v1(
            repo=repo,
            pool_manifest_path=pool_manifest,
            output_root=args.output,
            source_epoch=args.source_epoch,
            seed_namespace=args.seed_namespace,
            ref_glob=args.ref_glob,
            excluded_refs=tuple(args.exclude_ref),
            readonly_telemetry_refs=tuple(args.allow_readonly_telemetry_ref),
            consumed_ledger_path=args.consumed_ledger,
            scan_roots=tuple(scan_roots),
            include_refs=tuple(args.include_ref),
            history_depth=args.history_depth,
            max_candidates=args.max_candidates,
        )
    except (FreshInternalMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
