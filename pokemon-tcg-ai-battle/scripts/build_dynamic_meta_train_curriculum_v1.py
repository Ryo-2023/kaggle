#!/usr/bin/env python3
"""Build/verify one research-only dynamic META_TRAIN curriculum iteration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.dynamic_meta_train_curriculum_v1 import (
    build_dynamic_curriculum_manifest_v1,
    verify_dynamic_curriculum_manifest_v1,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--meta-manifest", type=Path, required=True)
    parser.add_argument("--meta-schedule", type=Path, required=True)
    parser.add_argument("--broad-pool-config", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--quota", type=int, default=96)
    parser.add_argument("--seed", default="common24-dynamic-curriculum-v1")
    parser.add_argument("--iteration", type=int, default=0)
    parser.add_argument("--outcome-ledger", type=Path)
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--max-opponent-weight", type=float, default=0.35)
    parser.add_argument("--max-family-weight", type=float, default=0.55)
    parser.add_argument("--min-family-quota", type=int, default=1)
    args = parser.parse_args()
    manifest = build_dynamic_curriculum_manifest_v1(
        repo_root=args.repo_root,
        meta_manifest_path=args.meta_manifest,
        meta_schedule_path=args.meta_schedule,
        broad_pool_config_path=args.broad_pool_config,
        output_manifest_path=args.output_manifest,
        quota=args.quota,
        seed=args.seed,
        iteration=args.iteration,
        outcome_ledger_path=args.outcome_ledger,
        previous_manifest_path=args.previous_manifest,
        max_opponent_weight=args.max_opponent_weight,
        max_family_weight=args.max_family_weight,
        min_family_quota=args.min_family_quota,
    )
    verified = verify_dynamic_curriculum_manifest_v1(
        args.output_manifest, args.repo_root
    )
    if verified != manifest:
        raise RuntimeError("curriculum post-write verification drift")
    print(
        json.dumps(
            {
                "manifest": str(args.output_manifest.resolve()),
                "curriculum_sha256": manifest["curriculum_sha256"],
                "summary": manifest["summary"],
                "authority": manifest["authority"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
