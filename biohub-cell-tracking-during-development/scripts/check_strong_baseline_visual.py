#!/usr/bin/env python3
"""Run the fixed Strong Baseline v1 headless viewer sanity check."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from biohub.strong_baseline.visual_check import (
    DEFAULT_MAX_DISTANCE,
    DEFAULT_SCALE,
    run_visual_sanity,
    write_visual_outputs,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Strong Baseline v1 GEFF overlays through the existing viewer path."
    )
    parser.add_argument("--image", type=Path, required=True, help="OME-Zarr image path")
    parser.add_argument("--prediction", type=Path, required=True, help="prediction GEFF path")
    parser.add_argument("--ground-truth", type=Path, required=True, help="evaluation-only GT GEFF path")
    parser.add_argument(
        "--scale",
        nargs=3,
        type=float,
        default=DEFAULT_SCALE,
        metavar=("Z_UM", "Y_UM", "X_UM"),
    )
    parser.add_argument("--max-distance", type=float, default=DEFAULT_MAX_DISTANCE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/strong_baseline_v1/visual_sanity"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run_visual_sanity(
        args.image,
        args.prediction,
        args.ground_truth,
        scale=tuple(args.scale),
        max_distance=args.max_distance,
    )
    json_path, text_path = write_visual_outputs(summary, args.output_dir)
    print(json.dumps({"json": str(json_path), "text": str(text_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
