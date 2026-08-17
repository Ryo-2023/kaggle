#!/usr/bin/env python3
"""Stage or smoke-promote one official-data-only self-owned CG meta source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    SelfOwnedCgMetaSourceError,
    materialize_self_owned_cg_meta_batch_v1,
    materialize_self_owned_cg_meta_source_v1,
    promote_self_owned_cg_meta_batch_v1,
    promote_self_owned_cg_meta_source_v1,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="enable source artifact writes")
    parser.add_argument("--promote", action="store_true", help="promote an existing staged root")
    parser.add_argument("--batch", action="store_true", help="stage/promote a multi-source batch")
    parser.add_argument("--candidate-package", type=Path, action="append", default=[])
    parser.add_argument("--staged-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-namespace")
    parser.add_argument("--source-id")
    parser.add_argument("--generation-manifest", type=Path, action="append", default=[])
    parser.add_argument("--smoke-summary", type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        if args.promote:
            if args.staged_root is None or args.smoke_summary is None:
                raise SelfOwnedCgMetaSourceError("--promote requires --staged-root and --smoke-summary")
            if args.batch:
                result = promote_self_owned_cg_meta_batch_v1(
                    staged_root=args.staged_root,
                    output_root=args.output,
                    smoke_summary=args.smoke_summary,
                )
            else:
                result = promote_self_owned_cg_meta_source_v1(
                    staged_root=args.staged_root,
                    output_root=args.output,
                    smoke_summary=args.smoke_summary,
                )
        else:
            if not args.candidate_package or args.seed_namespace is None:
                raise SelfOwnedCgMetaSourceError(
                    "staging requires --candidate-package and --seed-namespace"
                )
            if args.batch:
                result = materialize_self_owned_cg_meta_batch_v1(
                    candidate_packages=tuple(args.candidate_package),
                    output_root=args.output,
                    seed_namespace=args.seed_namespace,
                    generation_manifests=tuple(args.generation_manifest),
                )
            else:
                if len(args.candidate_package) != 1 or len(args.generation_manifest) > 1:
                    raise SelfOwnedCgMetaSourceError(
                        "single-source staging accepts one --candidate-package and at most one --generation-manifest"
                    )
                result = materialize_self_owned_cg_meta_source_v1(
                    candidate_package=args.candidate_package[0],
                    output_root=args.output,
                    seed_namespace=args.seed_namespace,
                    source_id=args.source_id,
                    generation_manifest=args.generation_manifest[0] if args.generation_manifest else None,
                )
    except (SelfOwnedCgMetaSourceError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
