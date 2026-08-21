#!/usr/bin/env python3
"""Replay one Lane F scoring rule against an existing detector-fixed cache.

Cache-only.  This script never runs the detector, never loads
``edge_predictor_best.pth`` and never opens a zarr image.  Ground truth is
opened only by the official metric, after the prediction manifest has been
written and validated.

Usage::

    run_association_research.py --cache CACHE_ROOT --rule RULE_ID \\
        --output OUTPUT_ROOT --upstream-root UPSTREAM --ground-truth GT.geff

One rule per process so that peak resident memory stays bounded.
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
from biohub.association_research.runner import (  # noqa: E402
    associate_research_rule,
    available_rules,
    write_research_prediction,
)
from biohub.detector_fixed_race.panel import _association_components  # noqa: E402
from biohub.detector_fixed_race.prediction import evaluate_prediction  # noqa: E402
from biohub.detector_fixed_race.upstream_adapter import _load_upstream_predictor  # noqa: E402


def _peak_rss_gib() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kibibytes, macOS reports bytes.
    divisor = 1024.0**3 if sys.platform == "darwin" else 1024.0**2
    return float(usage) / divisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="READY detector cache root")
    parser.add_argument("--rule", required=True, help=f"one of: {', '.join(available_rules())}")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, help="omit to skip metric evaluation")
    parser.add_argument("--sidecar-root", type=Path, help="where per-column .npy sidecars live")
    parser.add_argument("--max-distance", type=float, default=7.0)
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="score the cache and report candidate counts without building a graph or solving",
    )
    args = parser.parse_args(argv)

    sidecar_root = args.sidecar_root or (PROJECT_ROOT / "artifacts" / "lane_f" / "edge_columns")
    started = time.monotonic()
    cache = open_lean_cache(args.cache, sidecar_root=sidecar_root)
    load_seconds = time.monotonic() - started
    sample_id = str(cache.manifest["sample_id"])

    if args.score_only:
        from biohub.association_research.runner import build_candidate_rows
        from biohub.association_research.scoring import RESEARCH_RULES

        scoring_started = time.monotonic()
        rows, diagnostics = build_candidate_rows(cache, RESEARCH_RULES[args.rule])
        payload = {
            "sample_id": sample_id,
            "rule_id": args.rule,
            "cache_hash": cache.cache_hash,
            "mode": "score-only",
            "diagnostics": diagnostics,
            "candidate_edge_count": len(rows),
            "cache_load_seconds": load_seconds,
            "scoring_seconds": time.monotonic() - scoring_started,
            "peak_rss_gib": _peak_rss_gib(),
        }
        destination = Path(args.output) / sample_id
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{args.rule}_score_only.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    predictor = _load_upstream_predictor(Path(args.upstream_root))
    graph_builder, ilp_solver = _association_components(predictor)
    result, receipt = associate_research_rule(
        cache,
        args.rule,
        graph_builder=graph_builder,
        ilp_solver=ilp_solver,
    )

    prediction_path = Path(args.output) / sample_id / f"{args.rule}.geff"
    write_research_prediction(cache, result, predictor, prediction_path)

    record = {
        "sample_id": sample_id,
        "rule_id": args.rule,
        "cache_hash": cache.cache_hash,
        "cache_root": str(args.cache),
        "prediction_path": str(prediction_path),
        "config": dict(result.config),
        "timing": {
            "cache_load_seconds": load_seconds,
            "scoring_seconds": receipt["scoring_seconds"],
            "ilp_seconds": receipt["ilp_seconds"],
        },
        "diagnostics": {
            key: value
            for key, value in receipt.items()
            if key not in {"scoring_seconds", "ilp_seconds"}
        },
    }
    if args.ground_truth is not None:
        scale = tuple(float(value) for value in cache.manifest["scale"])
        record["metrics"] = evaluate_prediction(
            prediction_path,
            Path(args.ground_truth),
            {"scale": scale, "max_distance": float(args.max_distance)},
        )
    record["peak_rss_gib"] = _peak_rss_gib()
    record["total_seconds"] = time.monotonic() - started

    receipt_path = prediction_path.parent / f"{args.rule}_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
