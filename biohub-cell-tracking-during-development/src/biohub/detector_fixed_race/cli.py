"""Command-line orchestration for detector-fixed cache and association runs."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from biohub.benchmark_race.contracts import SampleSpec
from biohub.detector_fixed_race import panel as panel_api
from biohub.detector_fixed_race.association import ASSOCIATION_METHODS, AssociationSpec, associate_from_cache
from biohub.detector_fixed_race.cache import load_detector_cache
from biohub.detector_fixed_race.prediction import evaluate_prediction, write_prediction
from biohub.detector_fixed_race.upstream_adapter import CaptureConfig, materialize_detector_cache


def _load_predictor(upstream_root: Path) -> ModuleType:
    from biohub.detector_fixed_race.upstream_adapter import _load_upstream_predictor

    return _load_upstream_predictor(Path(upstream_root))


def _sample_spec(image_path: Path) -> SampleSpec:
    import zarr

    root = zarr.open_group(image_path, mode="r")
    array = root["0"]
    attrs = root.attrs.asdict() if hasattr(root.attrs, "asdict") else dict(root.attrs)
    shape = tuple(int(value) for value in array.shape)
    scale = panel_api._image_scale(attrs)
    quantiles = panel_api._image_quantiles(attrs)
    return SampleSpec(
        sample_id=image_path.stem,
        # SampleSpec intentionally keeps image stems relative at the
        # image-only contract boundary; materialize_detector_cache receives
        # the separately validated absolute image_path below.
        image_stem=image_path.name,
        shape=shape,
        scale=scale,
        quantiles=quantiles,
    )


def _methods(value: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in value.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("methods must not be empty")
    unknown = set(values) - set(ASSOCIATION_METHODS)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown methods: {sorted(unknown)}")
    return values


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def run_associate(
    *,
    cache: Path,
    output_root: Path,
    methods: Sequence[str],
    upstream_root: Path,
) -> list[dict[str, Any]]:
    detector_cache = load_detector_cache(cache)
    predictor = _load_predictor(upstream_root)
    graph_builder, ilp_solver = panel_api._association_components(predictor)
    records: list[dict[str, Any]] = []
    sample_id = str(detector_cache.manifest["sample_id"])
    for method_id in methods:
        association = associate_from_cache(
            detector_cache,
            AssociationSpec(method_id),
            graph_builder=graph_builder,
            ilp_solver=ilp_solver,
        )
        prediction_path = Path(output_root) / sample_id / f"{method_id}.geff"
        write_prediction(detector_cache, association, predictor, prediction_path)
        records.append(
            {
                "sample_id": sample_id,
                "method_id": method_id,
                "cache_hash": association.cache_hash,
                "prediction_path": str(prediction_path),
                "prediction_manifest_path": str(prediction_path.parent / "prediction_manifest.json"),
                "selected_edge_count": association.config["selected_edge_count"],
                "config": dict(association.config),
            }
        )
    _write_json(Path(output_root) / sample_id / "association_receipt.json", records)
    return records


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run detector-fixed Biohub association race.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze-panel", help="Freeze a score-free validation panel")
    freeze.add_argument("--train-root", type=Path, required=True)
    freeze.add_argument("--gt-root", type=Path, required=True)
    freeze.add_argument("--development-sample", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--minimum", type=int, default=3)
    freeze.add_argument("--maximum", type=int, default=5)
    freeze.add_argument("--no-division-priority", action="store_true")

    materialize = subparsers.add_parser("materialize", help="Run the pinned detector once into a GT-free cache")
    materialize.add_argument("--sample", required=True)
    materialize.add_argument("--train-root", type=Path, required=True)
    materialize.add_argument("--upstream-root", type=Path, required=True)
    materialize.add_argument("--checkpoint", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.add_argument("--device", default="cpu")
    materialize.add_argument("--max-frames", type=int)

    associate = subparsers.add_parser("associate", help="Replay association methods from an existing detector cache")
    associate.add_argument("--cache", type=Path, required=True)
    associate.add_argument("--output", type=Path, required=True)
    associate.add_argument("--upstream-root", type=Path, required=True)
    associate.add_argument("--methods", type=_methods, default=ASSOCIATION_METHODS)

    evaluate = subparsers.add_parser("evaluate", help="Evaluate prediction GEFF against GT")
    evaluate.add_argument("--prediction", type=Path, required=True)
    evaluate.add_argument("--ground-truth", type=Path, required=True)
    evaluate.add_argument("--metrics", type=Path)
    evaluate.add_argument("--scale", type=float, nargs=3, default=(1.625, 0.40625, 0.40625))
    evaluate.add_argument("--max-distance", type=float, default=7.0)

    dev = subparsers.add_parser("dev-race", help="Run all association methods and metric on one sample")
    dev.add_argument("--sample", required=True)
    dev.add_argument("--cache", type=Path, required=True)
    dev.add_argument("--output", type=Path, required=True)
    dev.add_argument("--ground-truth", type=Path, required=True)
    dev.add_argument("--upstream-root", type=Path, required=True)
    dev.add_argument("--methods", type=_methods, default=ASSOCIATION_METHODS)

    panel = subparsers.add_parser("panel", help="Run all methods over a frozen panel")
    panel.add_argument("--panel", type=Path, required=True)
    panel.add_argument("--train-root", type=Path, required=True)
    panel.add_argument("--gt-root", type=Path, required=True)
    panel.add_argument("--cache-root", type=Path, required=True)
    panel.add_argument("--output", type=Path, required=True)
    panel.add_argument("--upstream-root", type=Path, required=True)
    panel.add_argument("--methods", type=_methods, default=ASSOCIATION_METHODS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "freeze-panel":
        panel = panel_api.freeze_validation_panel(
            args.train_root,
            args.gt_root,
            args.development_sample,
            minimum=args.minimum,
            maximum=args.maximum,
            require_division_if_available=not args.no_division_priority,
        )
        _write_json(args.output, panel)
        print(json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "materialize":
        image_path = args.train_root / f"{args.sample}.zarr"
        sample = _sample_spec(image_path)
        receipt = materialize_detector_cache(
            image_path=image_path,
            upstream_root=args.upstream_root,
            checkpoint=args.checkpoint,
            output_root=args.output,
            sample=sample,
            config=CaptureConfig(),
            expected_device=args.device,
            max_frames=args.max_frames,
        )
        payload = {
            "cache_root": str(receipt.root),
            "cache_hash": receipt.cache_hash,
            "manifest_path": str(receipt.manifest_path),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "associate":
        records = run_associate(
            cache=args.cache,
            output_root=args.output,
            methods=args.methods,
            upstream_root=args.upstream_root,
        )
        print(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "evaluate":
        metrics = evaluate_prediction(
            args.prediction,
            args.ground_truth,
            {"scale": tuple(args.scale), "max_distance": args.max_distance},
        )
        target = args.metrics or args.prediction.parent / "metrics.json"
        _write_json(target, metrics)
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "dev-race":
        predictor = _load_predictor(args.upstream_root)
        records = panel_api.run_dev_race(
            sample_id=args.sample,
            cache_root=args.cache,
            output_root=args.output,
            methods=args.methods,
            gt_path=args.ground_truth,
            predictor_module=predictor,
        )
        print(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "panel":
        predictor = _load_predictor(args.upstream_root)
        result = panel_api.run_panel(
            panel_path=args.panel,
            methods=args.methods,
            output_root=args.output,
            train_root=args.train_root,
            gt_root=args.gt_root,
            cache_root=args.cache_root,
            predictor_module=predictor,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unknown command: {args.command}")


__all__ = ["_build_parser", "main", "run_associate"]
