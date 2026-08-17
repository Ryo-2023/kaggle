#!/usr/bin/env python3
"""Run a new near-lethal bonus-strength sweep around the P2 parent.

The previously confirmed ``+12000`` point is intentionally excluded.  This
is a bounded parameter sweep, not a blind retry of the confirmed candidate.
The underlying screen remains research-only and uses the existing P2 control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig  # noqa: E402
from scripts.run_cg_p2_context_screen_v1 import (  # noqa: E402
    DEFAULT_CONTROL,
    DEFAULT_SPLIT,
    run_screen,
)


SCHEMA = "cg-p2-near-sweep-v1"
CONFIRMED_BONUS = 12_000


def build_near_lethal_configs(values: Sequence[int]) -> tuple[P2ContextConfig, ...]:
    values = tuple(values)
    if not values:
        raise ValueError("near-lethal sweep values cannot be empty")
    if any(type(value) is not int or value <= 0 or value > 30_000 for value in values):
        raise ValueError("near-lethal sweep values must be integers in [1, 30000]")
    if len(set(values)) != len(values):
        raise ValueError("near-lethal sweep values must be unique")
    if CONFIRMED_BONUS in values:
        raise ValueError("confirmed +12000 point is excluded from this sweep")
    return tuple(
        P2ContextConfig(
            near_lethal_attack_bonus=value,
            threat_energy_attack_bonus=0,
            full_bench_attack_bonus=0,
        )
        for value in values
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_near_sweep(
    *,
    output_root: Path | str,
    values: Sequence[int] = (4000, 8000, 16000, 20000, 24000),
    split_path: Path | str = DEFAULT_SPLIT,
    control_package: Path | str = DEFAULT_CONTROL,
    base_seed: int = 48416000,
    repetitions: int = 2,
    workers: int = 12,
) -> dict[str, object]:
    configs = build_near_lethal_configs(values)
    result = run_screen(
        output_root=output_root,
        split_path=split_path,
        control_package=control_package,
        configs=configs,
        base_seed=base_seed,
        repetitions=repetitions,
        workers=workers,
    )
    output = Path(output_root).resolve()
    spec = {
        "schema_version": SCHEMA,
        "values": list(values),
        "confirmed_point_excluded": CONFIRMED_BONUS,
        "base_seed": base_seed,
        "repetitions": repetitions,
        "summary_sha256": _sha256(output / "summary.json"),
        "research_only": True,
        "promotion_authority": False,
    }
    (output / "sweep_manifest.json").write_text(
        json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result["sweep_manifest"] = spec
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--values", default="4000,8000,16000,20000,24000")
    parser.add_argument("--base-seed", type=int, default=48416000)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)
    values = tuple(int(item) for item in args.values.split(",") if item.strip())
    result = run_near_sweep(
        output_root=args.output,
        values=values,
        base_seed=args.base_seed,
        repetitions=args.repetitions,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

