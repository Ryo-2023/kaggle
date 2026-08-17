"""SP-PSRO operational CLI; expansion remains validation-gated."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .r2d3.psro import meta_strategy, should_expand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.policy_learning psro")
    sub = parser.add_subparsers(dest="command", required=True)
    payoff = sub.add_parser("payoff"); payoff.add_argument("--matrix", type=Path, required=True); payoff.add_argument("--output", type=Path, required=True); payoff.add_argument("--experimental-global-psro", action="store_true")
    response = sub.add_parser("train-response"); response.add_argument("--population", type=Path, required=True); response.add_argument("--output", type=Path, required=True)
    expand = sub.add_parser("expand"); expand.add_argument("--meta-improvement", type=float, required=True); expand.add_argument("--validation-improvement", type=float, required=True); expand.add_argument("--faults", type=int, required=True); expand.add_argument("--novel", action="store_true"); expand.add_argument("--single-opponent-overfit", action="store_true"); expand.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "payoff":
            result = {"schema": "sp-psro-meta-v1", "meta_strategy": meta_strategy(json.loads(args.matrix.read_text()), experimental_global_psro=args.experimental_global_psro)}
        elif args.command == "train-response": result = {"status": "configured", "population": str(args.population), "algorithm": "sp-psro-best-response"}
        else: result = should_expand(meta_improvement=args.meta_improvement, validation_improvement=args.validation_improvement, faults=args.faults, novel=args.novel, single_opponent_overfit=args.single_opponent_overfit)
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (OSError, ValueError, TypeError) as exc: print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False)); return 2
