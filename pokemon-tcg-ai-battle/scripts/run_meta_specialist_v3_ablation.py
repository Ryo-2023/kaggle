"""Run the R2/R3 representation ablation and emit JSON evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.representation_benchmark_v3 import (  # noqa: E402
    build_gate1_input_manifest_v3,
    run_gate1_v3,
    run_representation_benchmark_v3,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--teacher-root", type=Path)
    parser.add_argument("--lane-root", action="append", default=[], metavar="LANE=PATH")
    parser.add_argument("--split-manifest", action="append", default=[], metavar="LANE=PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--max-epochs", type=int, default=8)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--build-gate-input", action="store_true")
    parser.add_argument("--limit", type=int, default=512)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    def parse_lanes(values: list[str], flag: str) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for item in values:
            lane, separator, raw_path = item.partition("=")
            if not separator or not lane or not raw_path or lane in result:
                parser.error(f"{flag} must be unique LANE=PATH values")
            result[lane] = Path(raw_path)
        return result
    lane_roots = parse_lanes(args.lane_root, "--lane-root")
    split_manifests = parse_lanes(args.split_manifest, "--split-manifest")
    if args.build_gate_input:
        if not lane_roots or split_manifests or not args.output:
            parser.error("--build-gate-input requires --lane-root values, no --split-manifest, and --output")
        inputs = {
            lane: build_gate1_input_manifest_v3(lane=lane, root=root, output_path=args.output / f"gate1-input-{lane}.json")
            for lane, root in lane_roots.items()
        }
        print(json.dumps({"schema": "meta-specialist-gate1-input-v1", "inputs": {lane: str(path) for lane, path in inputs.items()}}, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if lane_roots or split_manifests:
        if not args.output:
            parser.error("Gate 1 requires --output as its artifact directory")
        result = run_gate1_v3(
            lane_roots=lane_roots, split_manifest_paths=split_manifests,
            patience=args.patience, min_delta=args.min_delta, output_dir=args.output,
            dry_run=args.dry_run, max_epochs=args.max_epochs, device=args.device,
        )
        report = {
            "schema": "meta-specialist-gate1-v3", "status": result.status,
            "seeds": list(result.seeds), "runs": list(result.runs),
            "artifact": str(result.output_path),
            "selection_artifact": str(result.decision_path),
        }
    elif args.dry_run:
        parser.error("--dry-run is only valid with --lane-root/--split-manifest")
    else:
        if args.teacher_root:
            parser.error("--teacher-root legacy action-type benchmark is retired; build and run a Gate 1 input manifest")
        report = (
            run_representation_benchmark_v3(seed=args.seed, samples=args.samples, epochs=args.epochs)
        )
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output and not lane_roots:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
