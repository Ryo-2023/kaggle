#!/usr/bin/env python3
"""Rebind a failure-adapter split after fault-free smoke promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.opponent_ingest.self_owned_failure_adapter_v1 import (
    FailureAdapterMetaError,
    build_failure_adapter_split_v1,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--p1-package", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_failure_adapter_split_v1(output_root=args.output_root, p1_package=args.p1_package)
    except (FailureAdapterMetaError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
