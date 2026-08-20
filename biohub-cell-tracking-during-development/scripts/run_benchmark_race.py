#!/usr/bin/env python3
"""Narrow image-only entrypoint for the benchmark-race classical lanes.

The script exposes bounded ``smoke`` and ``infer`` commands for ``blob_lap``
and ``cc_flow``.  Neither command accepts a ground-truth path or opens a GEFF
input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.benchmark_race.blob_lap import run_blob_lap  # noqa: E402
from biohub.benchmark_race.cc_flow import run_cc_flow  # noqa: E402
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec  # noqa: E402

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
        values = np.asarray(array)
        quantiles = {
            "0.001": float(np.nanquantile(values, 0.001)),
            "0.999": float(np.nanquantile(values, 0.999)),
        }
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
        choices=("blob_lap", "cc_flow"),
        default="blob_lap",
        help="Image-only detector/linker lane (default: blob_lap)",
    )
    parser.add_argument("--image-stem", type=Path, required=True, help="Relative OME-Zarr image path/stem")
    parser.add_argument("--cache-root", type=Path, default=Path("artifacts/multi_method_race/cache"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/multi_method_race"))
    parser.add_argument("--expected-device", default="cpu", choices=("cpu",))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run image-only benchmark-race classical inference.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke", help="Run a bounded two-frame image-only smoke")
    _add_common_arguments(smoke)
    smoke.add_argument("--max-frames", type=int, default=2)
    infer = subparsers.add_parser("infer", help="Run image-only blob_lap inference")
    _add_common_arguments(infer)
    infer.add_argument("--max-frames", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    max_frames = args.max_frames
    request = _build_request(args, max_frames=max_frames)
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
