#!/usr/bin/env python3
"""CLI for provenance verification, image-only inference, and post-hoc scoring."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.strong_baseline.evaluation import evaluate_prediction  # noqa: E402
from biohub.strong_baseline.provenance import LOCAL_CHECKPOINT_SHA256, OFFICIAL_COMMIT  # noqa: E402
from biohub.strong_baseline.runner import (  # noqa: E402
    DEFAULT_HARMONIC_REVERSE_WEIGHT,
    DEFAULT_MAX_FRAMES,
    InferenceRequest,
    run_harmonic_inference,
    run_harmonic_smoke,
    run_official_inference,
    run_official_smoke,
    verify_inference_inputs,
)


def _add_request_arguments(parser: argparse.ArgumentParser, *, output_required: bool = True) -> None:
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--image-stem", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=output_required)
    parser.add_argument("--expected-device", default="cpu")


def _request_from_args(args: argparse.Namespace) -> InferenceRequest:
    return InferenceRequest(
        upstream_root=args.upstream_root,
        image_stem=args.image_stem,
        checkpoint=args.checkpoint,
        output_dir=args.output_dir,
        expected_device=args.expected_device,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the provenance-pinned Biohub strong baseline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="Verify source, checkpoint, image metadata, and ILP imports.")
    _add_request_arguments(verify, output_required=False)
    verify.set_defaults(output_dir=Path("verification-output"))
    verify.add_argument("--expected-commit", default=OFFICIAL_COMMIT)
    verify.add_argument("--expected-checkpoint-sha256", default=LOCAL_CHECKPOINT_SHA256)

    smoke = subparsers.add_parser("smoke-official", help="Run bounded image-only upstream inference.")
    _add_request_arguments(smoke)
    smoke.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)

    infer = subparsers.add_parser("infer-official", help="Run full image-only upstream inference.")
    _add_request_arguments(infer)

    harmonic_smoke = subparsers.add_parser(
        "smoke-harmonic",
        help="Run bounded image-only upstream inference with published harmonic association.",
    )
    _add_request_arguments(harmonic_smoke)
    harmonic_smoke.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)

    harmonic_infer = subparsers.add_parser(
        "infer-harmonic",
        help="Run full image-only inference with published harmonic association.",
    )
    _add_request_arguments(harmonic_infer)

    evaluate = subparsers.add_parser("evaluate", help="Score a prediction against GT after inference is complete.")
    evaluate.add_argument("--prediction", type=Path, required=True)
    evaluate.add_argument("--ground-truth", type=Path, required=True)
    evaluate.add_argument("--metrics", type=Path)
    evaluate.add_argument("--scale", type=float, nargs=3, default=(1.625, 0.40625, 0.40625))
    evaluate.add_argument("--max-distance", type=float, default=7.0)
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "verify":
        request = _request_from_args(args)
        _print_json(
            verify_inference_inputs(
                request,
                expected_commit=args.expected_commit,
                expected_checkpoint_sha256=args.expected_checkpoint_sha256,
            ),
        )
        return 0

    if args.command == "smoke-official":
        receipt = run_official_smoke(_request_from_args(args), max_frames=args.max_frames)
        _print_json({"prediction_path": receipt.prediction_path, "run_json": receipt.run_json_path})
        return 0

    if args.command == "infer-official":
        receipt = run_official_inference(_request_from_args(args))
        _print_json({"prediction_path": receipt.prediction_path, "run_json": receipt.run_json_path})
        return 0

    if args.command == "smoke-harmonic":
        receipt = run_harmonic_smoke(
            _request_from_args(args),
            max_frames=args.max_frames,
            reverse_weight=DEFAULT_HARMONIC_REVERSE_WEIGHT,
        )
        _print_json({"prediction_path": receipt.prediction_path, "run_json": receipt.run_json_path})
        return 0

    if args.command == "infer-harmonic":
        receipt = run_harmonic_inference(
            _request_from_args(args),
            reverse_weight=DEFAULT_HARMONIC_REVERSE_WEIGHT,
        )
        _print_json({"prediction_path": receipt.prediction_path, "run_json": receipt.run_json_path})
        return 0

    metrics = evaluate_prediction(
        args.prediction,
        args.ground_truth,
        scale=tuple(args.scale),
        max_distance=args.max_distance,
    )
    metrics_path = args.metrics or args.prediction.parent / "metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    _print_json(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
