#!/usr/bin/env python3
"""Narrow image-only entrypoint for the benchmark-race classical lanes.

The script exposes bounded ``smoke`` and ``infer`` commands for ``blob_lap``,
``cc_flow``, and ``motion_lap``.  Neither command accepts a ground-truth path
or opens a GEFF input.  ``motion_lap`` consumes a fixed ``blob_lap`` candidate
cache; when one is not supplied, the existing blob adapter creates it once.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.benchmark_race.blob_lap import run_blob_lap  # noqa: E402
from biohub.benchmark_race.cc_flow import run_cc_flow, stream_image_quantiles  # noqa: E402
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec  # noqa: E402
from biohub.benchmark_race.motion import ensure_blob_cache, run_motion_lap  # noqa: E402
from biohub.benchmark_race.report import write_summary  # noqa: E402
from biohub.strong_baseline.evaluation import evaluate_prediction  # noqa: E402
from biohub.strong_baseline.manifest import validate_prediction_manifest  # noqa: E402

DEFAULT_SCALE = (1.625, 0.40625, 0.40625)


def _image_metadata(image_stem: Path) -> tuple[tuple[int, int, int, int], dict[str, float]]:
    import zarr

    image_path = image_stem if image_stem.suffix.casefold() == ".zarr" else image_stem.with_suffix(".zarr")
    root = zarr.open(str(image_path), mode="r")
    array = root if hasattr(root, "shape") else root["0"]
    shape = tuple(int(value) for value in array.shape)
    if len(shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {shape!r}")
    attrs = dict(getattr(root, "attrs", {}))
    metadata = attrs.get("image_statistics", {})
    quantiles = metadata.get("quantiles", {}) if isinstance(metadata, dict) else {}
    if not {"0.001", "0.999"}.issubset(quantiles):
        quantiles = stream_image_quantiles(array, (0.001, 0.999))
    return shape, {"0.001": float(quantiles["0.001"]), "0.999": float(quantiles["0.999"])}


def _build_request(args: argparse.Namespace, *, max_frames: int | None) -> RaceRequest:
    image_stem = Path(args.image_stem)
    if image_stem.suffix.casefold() == ".geff":
        raise ValueError("image stem must identify an image, not a .geff ground-truth graph")
    shape, quantiles = _image_metadata(image_stem)
    config: dict[str, object] = {}
    if max_frames is not None:
        config["max_frames"] = max_frames
    return RaceRequest(
        sample=SampleSpec(
            sample_id=image_stem.stem,
            image_stem=image_stem,
            shape=shape,
            scale=DEFAULT_SCALE,
            quantiles=quantiles,
        ),
        cache_root=Path(args.cache_root),
        output_root=Path(args.output_root),
        expected_device=args.expected_device,
        config=config,
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--method",
        choices=("blob_lap", "cc_flow", "motion_lap"),
        default="blob_lap",
        help="Image-only detector/linker lane (default: blob_lap)",
    )
    parser.add_argument("--image-stem", type=Path, required=True, help="Relative OME-Zarr image path/stem")
    parser.add_argument("--cache-root", type=Path, default=Path("artifacts/multi_method_race/cache"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/multi_method_race"))
    parser.add_argument("--expected-device", default="cpu", choices=("cpu",))
    parser.add_argument(
        "--blob-cache",
        type=Path,
        help="Fixed blob_lap candidate cache directory/manifest for motion_lap",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run image-only benchmark-race classical inference.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="Run a bounded two-frame image-only smoke")
    _add_common_arguments(smoke)
    smoke.add_argument("--max-frames", type=int, default=2)
    infer = subparsers.add_parser("infer", help="Run image-only benchmark-race inference")
    _add_common_arguments(infer)
    infer.add_argument("--max-frames", type=int)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate a persisted prediction against GT after manifest validation",
    )
    evaluate.add_argument("--prediction", type=Path, required=True)
    evaluate.add_argument("--ground-truth", type=Path, required=True)
    evaluate.add_argument("--metrics", type=Path)
    evaluate.add_argument("--scale", type=float, nargs=3, default=DEFAULT_SCALE)
    evaluate.add_argument("--max-distance", type=float, default=7.0)

    summarize = subparsers.add_parser(
        "summarize",
        help="Render a deterministic Japanese report from persisted receipts and metrics",
    )
    summarize.add_argument(
        "--root",
        "--input-root",
        "--race-root",
        dest="root",
        type=Path,
        required=True,
        help="Project/artifacts root containing race and/or strong-baseline receipts",
    )
    summarize.add_argument(
        "--output",
        type=Path,
        default=Path("docs/results/multi_method_benchmark_race.md"),
    )
    summarize.add_argument("--summary-json", type=Path)
    return parser


def evaluate_prediction_after_manifest(
    prediction: Path,
    ground_truth: Path,
    *,
    scale: tuple[float, float, float],
    max_distance: float = 7.0,
) -> dict[str, Any]:
    """Evaluate only after the persisted prediction manifest is validated.

    ``evaluate_prediction`` already performs this validation as a defensive
    boundary.  Keeping an explicit validation here makes the CLI phase
    boundary visible and testable: a missing or tampered manifest prevents any
    attempt to open the GT path.
    """

    manifest_receipt = validate_prediction_manifest(Path(prediction))
    metrics = evaluate_prediction(
        Path(prediction),
        Path(ground_truth),
        scale=scale,
        max_distance=max_distance,
    )
    result = dict(metrics)
    result["prediction_manifest_validation_receipt"] = manifest_receipt
    return result


def run_evaluate(
    *,
    prediction: Path,
    ground_truth: Path,
    metrics_path: Path | None,
    scale: tuple[float, float, float],
    max_distance: float,
) -> dict[str, Any]:
    """Run post-hoc official evaluation and persist its metric receipt."""

    metrics = evaluate_prediction_after_manifest(
        prediction,
        ground_truth,
        scale=scale,
        max_distance=max_distance,
    )
    target = Path(metrics_path) if metrics_path is not None else Path(prediction).parent / "metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "evaluate":
        metrics = run_evaluate(
            prediction=args.prediction,
            ground_truth=args.ground_truth,
            metrics_path=args.metrics,
            scale=tuple(args.scale),
            max_distance=args.max_distance,
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "summarize":
        summary = write_summary(
            args.root,
            args.output,
            args.summary_json,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    max_frames = args.max_frames
    request = _build_request(args, max_frames=max_frames)
    if args.method == "motion_lap":
        blob_cache = args.blob_cache if args.blob_cache is not None else ensure_blob_cache(request)
        artifact = run_motion_lap(request, blob_cache)
    else:
        runners = {"blob_lap": run_blob_lap, "cc_flow": run_cc_flow}
        artifact = runners[args.method](request)
    print(
        json.dumps(
            {
                "method_id": args.method,
                "prediction_path": str(artifact.prediction_path),
                "prediction_manifest": str(artifact.prediction_manifest_path),
                "run_json": str(artifact.run_json_path),
                "cache_manifest": str(artifact.cache_manifest_path),
                "candidate_count": artifact.candidate_count,
                "edge_count": artifact.edge_count,
            },
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
