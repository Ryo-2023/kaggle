"""Copyable R2D3 command-line entry points (small deterministic smoke tools)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .replay import PrioritizedSequenceReplay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.policy_learning r2d3")
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay-build"); replay.add_argument("--output", type=Path, required=True); replay.add_argument("--capacity", type=int, default=100000)
    train = sub.add_parser("train"); train.add_argument("--replay-manifest", type=Path, required=True); train.add_argument("--output", type=Path, required=True); train.add_argument("--device", default="cpu")
    actor = sub.add_parser("actor"); actor.add_argument("--policy", type=Path, required=True); actor.add_argument("--action-mode", choices=("greedy", "epsilon", "boltzmann"), default="greedy")
    server = sub.add_parser("inference-server"); server.add_argument("--checkpoint", type=Path, required=True); server.add_argument("--max-batch-size", type=int, default=128); server.add_argument("--max-delay-ms", type=float, default=5.0)
    evaluate = sub.add_parser("evaluate"); evaluate.add_argument("--population", type=Path, required=True); evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "replay-build":
            if args.capacity < 1: raise ValueError("capacity must be positive")
            result = {"schema": "r2d3-replay-manifest-v1", "capacity": args.capacity, "prioritized": True, "recurrent_sequence": True}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2) + "\n")
        elif args.command == "train": result = {"status": "configured", "device": args.device, "replay_manifest": str(args.replay_manifest)}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2) + "\n")
        elif args.command == "actor": result = {"status": "configured", "policy": str(args.policy), "action_mode": args.action_mode}
        elif args.command == "inference-server": result = {"status": "configured", "checkpoint": str(args.checkpoint), "max_batch_size": args.max_batch_size, "max_delay_ms": args.max_delay_ms}
        else: result = {"status": "configured", "population": str(args.population)}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, sort_keys=True)); return 0
    except (OSError, ValueError) as exc: print(json.dumps({"error": type(exc).__name__, "message": str(exc)})); return 2
