#!/usr/bin/env python3
"""Generate a research-only self-owned action-adapter source package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.opponent_ingest.self_owned_adapter_v1 import generate_self_owned_adapter_v1  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--salt", required=True)
    parser.add_argument("--perturbation-rate", type=float, default=0.12)
    args = parser.parse_args(argv)
    result = generate_self_owned_adapter_v1(
        base_candidate_root=args.base_candidate_root,
        output_root=args.output,
        adapter_id=args.adapter_id,
        salt=args.salt,
        perturbation_rate=args.perturbation_rate,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
