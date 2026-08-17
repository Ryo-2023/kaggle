"""Run learner diagnostics on a deterministic synthetic fresh batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.learner_common_v1 import (  # noqa: E402
    advantage_diagnostics_v1,
    exact_policy_drift_v1,
    normalized_entropy_v1,
    vtrace_effective_kernel_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--decisions", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generator = torch.Generator().manual_seed(args.seed)
    old = torch.randn(args.decisions, 8, generator=generator)
    new = old + 0.01 * torch.randn(args.decisions, 8, generator=generator)
    mask = torch.ones_like(old, dtype=torch.bool)
    advantages = torch.randn(args.decisions, generator=generator)
    report = {
        "schema": "meta-specialist-learner-diagnostics-v3", "seed": args.seed, "decisions": args.decisions,
        "policy_drift": exact_policy_drift_v1(old, new, mask),
        "normalized_entropy_mean": float(normalized_entropy_v1(old, mask).mean().item()),
        "vtrace_kernel": vtrace_effective_kernel_v1(torch.full((args.decisions,), 0.9)),
        "advantage": advantage_diagnostics_v1(advantages),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
