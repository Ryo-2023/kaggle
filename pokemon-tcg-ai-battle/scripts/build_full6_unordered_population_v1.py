#!/usr/bin/env python3
"""Build the research-only Full6 unordered population descriptor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.full6_unordered_population_v1 import (
    build_full6_unordered_population_manifest_v1,
    verify_full6_unordered_population_manifest_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--blocked-full6-bridge", type=Path, required=True)
    parser.add_argument("--tomato-clean-bridge", type=Path, required=True)
    parser.add_argument("--repair-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    built = build_full6_unordered_population_manifest_v1(
        repo_root=args.repo_root,
        blocked_full6_bridge_manifest_path=args.blocked_full6_bridge,
        tomato_clean_bridge_manifest_path=args.tomato_clean_bridge,
        repair_manifest_path=args.repair_manifest,
        output_manifest_path=args.output_manifest,
    )
    verified = verify_full6_unordered_population_manifest_v1(
        args.output_manifest, args.repo_root
    )
    if built != verified:
        raise RuntimeError("Full6 unordered population descriptor drifted after write")
    print(
        json.dumps(
            {
                "manifest": str(args.output_manifest.resolve()),
                "manifest_sha256": built["manifest_sha256"],
                "purpose": built["purpose"],
                "coverage": built["coverage"],
                "ordered_quarantine": built["ordered_quarantine"],
                "performance_training_ready": built["readiness"]["performance_training_ready"],
                "published_rows": built["materialization"]["published_rows"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
