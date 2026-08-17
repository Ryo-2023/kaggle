"""Run a deterministic critic warm-up/calibration smoke."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.critic_v3 import OutcomeCriticV3  # noqa: E402
from mage_ptcg.meta_specialist.critic_warmup_v3 import warmup_critic_v3  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--episodes", type=int, default=64)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.episodes < 2 or args.steps < 1:
        raise SystemExit("episodes must be >=2 and steps must be positive")
    generator = torch.Generator().manual_seed(args.seed)
    critic = OutcomeCriticV3(hidden_dim=16, seed=args.seed)
    episodes = tuple(
        (torch.randn(args.steps, 16, generator=generator), torch.full((args.steps,), index % 3, dtype=torch.long))
        for index in range(args.episodes)
    )
    report = warmup_critic_v3(critic, episodes, epochs=args.epochs, learning_rate=2e-3)
    report.update({"schema": "critic-warmup-v3", "seed": args.seed, "episodes": args.episodes, "steps": args.steps})
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
