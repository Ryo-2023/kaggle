#!/usr/bin/env python3
"""Ground-truth-free candidate scan over every Lane F scoring rule.

Scores an existing detector cache with each rule and reports how many
candidate edges survive, without building a graph, solving an ILP, writing a
GEFF or opening ground truth.  That is enough to answer "did this rule change
the ranking, or only the threshold?" for a fraction of the cost of a full
replay, and it is the screen that decides which rules earn an ILP run.

Usage::

    scan_association_rules.py --cache CACHE_ROOT --output OUT.json [--rules a,b]
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.association_research.cache_view import open_lean_cache  # noqa: E402
from biohub.association_research.runner import build_candidate_rows  # noqa: E402
from biohub.association_research.scoring import RESEARCH_RULES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rules", help="comma-separated subset; default is every rule")
    parser.add_argument("--sidecar-root", type=Path)
    args = parser.parse_args(argv)

    rule_ids = (
        [item.strip() for item in args.rules.split(",") if item.strip()]
        if args.rules
        else list(RESEARCH_RULES)
    )
    unknown = sorted(set(rule_ids) - set(RESEARCH_RULES))
    if unknown:
        raise SystemExit(f"unknown rules: {unknown}")

    sidecar_root = args.sidecar_root or (PROJECT_ROOT / "artifacts" / "lane_f" / "edge_columns")
    cache = open_lean_cache(args.cache, sidecar_root=sidecar_root)

    records = []
    for rule_id in rule_ids:
        started = time.monotonic()
        rows, diagnostics = build_candidate_rows(cache, RESEARCH_RULES[rule_id])
        record = {
            "rule_id": rule_id,
            "candidate_edge_count": len(rows),
            "seconds": time.monotonic() - started,
            **diagnostics,
            **RESEARCH_RULES[rule_id].describe(),
        }
        records.append(record)
        print(
            f"{rule_id:34s} candidates={len(rows):7d} "
            f"gate_only={diagnostics['gate_only_admitted_count']:6d} "
            f"contested_sources={diagnostics['contested_source_count']:6d} "
            f"recoverable={diagnostics['recoverable_target_count']:6d} "
            f"({record['seconds']:.2f}s)",
            flush=True,
        )
        del rows

    payload = {
        "sample_id": str(cache.manifest["sample_id"]),
        "cache_hash": cache.cache_hash,
        "cache_root": str(args.cache),
        "node_count": cache.nodes.length,
        "edge_count": cache.edge_count,
        "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0**2),
        "rules": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {args.output}  peak_rss={payload['peak_rss_gib']:.2f} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
