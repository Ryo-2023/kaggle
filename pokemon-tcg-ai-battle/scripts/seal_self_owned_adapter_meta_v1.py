#!/usr/bin/env python3
"""Seal a generated self-owned adapter as a research-only meta pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.opponent_ingest.self_owned_adapter_v1 import seal_self_owned_adapter_pool_v1  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", required=True)
    parser.add_argument("--seed-namespace", required=True)
    args = parser.parse_args(argv)
    result = seal_self_owned_adapter_pool_v1(
        candidate_package_root=args.candidate_package_root,
        output_root=args.output,
        source_epoch=args.source_epoch,
        seed_namespace=args.seed_namespace,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
