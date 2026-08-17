"""Reproducible self-owned Rule-v0 public rollout entrypoint.

This thin CLI intentionally delegates to the research-only screen bridge.  It
exists as the stable command for a real Tomato smoke and the follow-up
common24 screen; it never writes raw ``env.steps`` or private observations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from scripts.run_self_owned_rule_v0_public_outcome_screen_v1 import (  # noqa: E402
    DEFAULT_CONFIG,
    run_common24_rollout_v1,
    run_common24_screen_v1,
    run_rollout_smoke_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "common24-rollout", "screen96", "all"), default="smoke")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path, help="Existing smoke table for screen96")
    parser.add_argument("--broad-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--opponent-id", default="tomatomato_archaludon")
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=14_900_000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.mode == "smoke":
        result = run_rollout_smoke_v1(
            output_dir=args.output,
            opponent_id=args.opponent_id,
            games=args.games,
            base_seed=args.base_seed,
        )
    elif args.mode == "common24-rollout":
        result = run_common24_rollout_v1(
            output_dir=args.output,
            broad_config=args.broad_config,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
        )
    elif args.mode == "screen96":
        if args.table is None:
            raise SystemExit("--table is required for --mode screen96")
        result = run_common24_screen_v1(
            output_dir=args.output,
            table_path=args.table,
            broad_config=args.broad_config,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    else:
        smoke_root = args.output / "rollout-smoke"
        smoke = run_rollout_smoke_v1(
            output_dir=smoke_root,
            opponent_id=args.opponent_id,
            games=args.games,
            base_seed=args.base_seed,
        )
        common24 = run_common24_rollout_v1(
            output_dir=args.output / "common24-rollout",
            broad_config=args.broad_config,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
        )
        result = {"smoke": smoke, "common24_rollout": common24}
        if common24.get("ready_for_screen"):
            result["screen96"] = run_common24_screen_v1(
                output_dir=args.output / "screen96",
                table_path=Path(str(common24["table_path"])),
                broad_config=args.broad_config,
                games_per_seat=args.games_per_seat,
                base_seed=args.base_seed,
                workers=args.workers,
                worker_recycle_games=args.worker_recycle_games,
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
