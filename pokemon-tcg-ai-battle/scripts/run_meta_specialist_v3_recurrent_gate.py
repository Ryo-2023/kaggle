"""Run the sealed full-corpus recurrent R3 Gate without any promotion side effect."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.recurrent_gate_v3 import (  # noqa: E402
    RecurrentGateLaneInputV3,
    run_recurrent_gate_v3,
)


def _parse_lane_inputs(values: list[str], parser: argparse.ArgumentParser) -> dict[str, RecurrentGateLaneInputV3]:
    result: dict[str, RecurrentGateLaneInputV3] = {}
    for item in values:
        lane, separator, remainder = item.partition("=")
        path_text, sha_separator, digest = remainder.rpartition("@")
        if (not separator or not lane or not sha_separator or not path_text or lane in result):
            parser.error("--lane-input must be unique LANE=PATH@LOWERCASE_SHA256 values")
        try:
            result[lane] = RecurrentGateLaneInputV3(Path(path_text), digest)
        except ValueError as exc:
            parser.error(str(exc))
    if set(result) != {"alakazam", "archaludon"}:
        parser.error("--lane-input must name exactly alakazam and archaludon")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-input", action="append", default=[], metavar="LANE=PATH@SHA256")
    parser.add_argument("--max-epochs", type=int, required=True)
    parser.add_argument("--patience", type=int, required=True)
    parser.add_argument("--min-delta", type=float, default=0.0001)
    parser.add_argument("--burn-in", type=int, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lane_inputs = _parse_lane_inputs(args.lane_input, parser)
    result = run_recurrent_gate_v3(
        lane_inputs=lane_inputs, max_epochs=args.max_epochs, patience=args.patience,
        min_delta=args.min_delta, burn_in=args.burn_in,
        device=torch.device(args.device), output_dir=args.output,
    )
    selection_payload = json.loads(result.decision_path.read_text(encoding="utf-8"))["selection"]
    summary = {
        "schema": "meta-specialist-recurrent-gate-run-v2", "status": result.status,
        "result": str(result.output_path), "selection": str(result.decision_path),
        "result_sha256": result.result_sha256,
        "result_file_sha256": hashlib.sha256(result.output_path.read_bytes()).hexdigest(),
        "selection_file_sha256": hashlib.sha256(result.decision_path.read_bytes()).hexdigest(),
        "promotion_authority": selection_payload["promotion_authority"],
        "lane_statuses": {lane: value["status"] for lane, value in selection_payload["lanes"].items()},
        "lane_preferred": {lane: value["preferred"] for lane, value in selection_payload["lanes"].items()},
        "runtime_gate": "not-run-by-recurrent-gate-v2",
        "learning_rate": 0.0001,
        "optimizer_update_unit": "physical-sequence",
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
