#!/usr/bin/env python3
"""Build/verify the research-only Full6 split/quarantine repair dry run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.student_v3_full6_repair_v1 import (
    build_full6_repair_manifest_v1,
    verify_full6_repair_manifest_v1,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--blocked-full6-bridge", type=Path, required=True)
    parser.add_argument("--tomato-clean-bridge", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--seed", default="full6-component-repair-v1")
    parser.add_argument(
        "--reproduce-primary",
        action="store_true",
        help="Opt into the expensive complete raw-record reproduction.",
    )
    args = parser.parse_args()
    built = build_full6_repair_manifest_v1(
        repo_root=args.repo_root,
        blocked_bridge_manifest_path=args.blocked_full6_bridge,
        tomato_bridge_manifest_path=args.tomato_clean_bridge,
        output_manifest_path=args.output_manifest,
        seed=args.seed,
        reproduce_primary=args.reproduce_primary,
    )
    verified = verify_full6_repair_manifest_v1(
        args.output_manifest,
        args.repo_root,
        reproduce_primary=args.reproduce_primary,
    )
    if built != verified:
        raise RuntimeError("Full6 repair post-write verification drift")
    print(
        json.dumps(
            {
                "manifest": str(args.output_manifest.resolve()),
                "repair_sha256": built["repair_sha256"],
                "performance_training_ready": built["performance_training_ready"],
                "blocked_reasons": built["blocked_reasons"],
                "source_decisions": built["derivation"]["source_decisions"],
                "unordered_set_decisions": built["derivation"]["unordered_set_decisions"],
                "primary_reproduction": built["derivation"].get(
                    "primary_reproduction",
                    {"complete": True, "reproduction_skipped": False},
                ),
                "ordered_quarantine": built["derivation"].get(
                    "ordered_pointer_head_quarantine_count",
                    built["derivation"].get(
                        "ordered_pointer_head_quarantine", {}
                    ).get("count"),
                ),
                "moved_records": built["derivation"]["component_split_repair"].get(
                    "moved_record_count"
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
