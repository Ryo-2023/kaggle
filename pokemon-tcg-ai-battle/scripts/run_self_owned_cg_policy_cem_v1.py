#!/usr/bin/env python3
"""Run the existing P1 CEM core against a self-owned scratch deck.

The historical ``run_cg_p1_cem_v1.py`` runner intentionally binds every
candidate to the repository/root deck.  This research-only bridge keeps its
paired CABT, independent re-evaluation, and fail-closed selection logic while
replacing only the package materializer with the self-owned deck-bound
materializer.  It never touches ``deck.csv`` at the repository root and never
updates BestKnown, Champion, or submission artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

import scripts.run_cg_p1_cem_v1 as p1_cem  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig  # noqa: E402
from mage_ptcg.meta_specialist.self_owned_cg_parameterized_package_v1 import (  # noqa: E402
    materialize_self_owned_cg_parameterized_package_v1,
)


def _install_self_owned_materializer(self_owned_deck_package: Path) -> None:
    """Install a process-local adapter without changing the P1 runner source."""

    deck_package = self_owned_deck_package.resolve()

    def materialize(*, source_package, output_package, config, candidate_id):
        return materialize_self_owned_cg_parameterized_package_v1(
            source_package=source_package,
            self_owned_deck_package=deck_package,
            output_package=output_package,
            config=config,
            candidate_id=candidate_id,
        )

    p1_cem.materialize_parameterized_package = materialize


def run_self_owned_cg_policy_cem_v1(
    *,
    output_root: str | Path,
    split_path: str | Path,
    source_package: str | Path,
    self_owned_deck_package: str | Path,
    control_package: str | Path,
    pool_root: str | Path,
    generations: int = 1,
    all_train_refs: bool = True,
    reeval_for_update: bool = True,
    reeval_repeats: int = 2,
    reeval_games_per_opponent_seat: int = 2,
    positive_delta_gate: bool = True,
    risk_aware_update: bool = True,
    campaign_seed: int = 2026084601,
    population_size: int = 8,
    elite_count: int = 2,
    initial_config: P1ParameterConfig | None = None,
    initial_scale_fraction: float | None = 0.20,
) -> dict[str, object]:
    """Execute one bounded self-owned policy CEM campaign."""

    source = Path(source_package).resolve()
    deck_package = Path(self_owned_deck_package).resolve()
    control = Path(control_package).resolve()
    if not source.is_dir() or not deck_package.is_dir() or not control.is_dir():
        raise FileNotFoundError("source, self-owned deck, and control packages must exist")
    _install_self_owned_materializer(deck_package)
    return p1_cem.run_campaign(
        output_root=Path(output_root),
        split_path=Path(split_path),
        source_package=source,
        control_package=control,
        pool_root=Path(pool_root),
        target_generations=generations,
        perform_reeval=reeval_for_update,
        all_train_refs=all_train_refs,
        reeval_for_update=reeval_for_update,
        reeval_repeats=reeval_repeats,
        reeval_games_per_opponent_seat=reeval_games_per_opponent_seat,
        positive_delta_gate=positive_delta_gate,
        risk_aware_update=risk_aware_update,
        campaign_seed=campaign_seed,
        population_size=population_size,
        elite_count=elite_count,
        initial_config=initial_config,
        initial_scale_fraction=initial_scale_fraction,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for heavy CABT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--source-package", type=Path, required=True)
    parser.add_argument("--self-owned-deck-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--campaign-seed", type=int, default=2026084601)
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--reeval-repeats", type=int, default=2)
    parser.add_argument("--reeval-games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--initial-scale-fraction", type=float, default=0.20)
    parser.add_argument("--initial-config-json", type=Path)
    parser.add_argument("--no-positive-delta-gate", action="store_true")
    parser.add_argument("--no-risk-aware-update", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        print('{"status":"BLOCKED_EXECUTE_REQUIRED","research_only":true}')
        return 2
    initial_config = p1_cem._load_initial_config(args.initial_config_json) if args.initial_config_json else None
    result = run_self_owned_cg_policy_cem_v1(
        output_root=args.output,
        split_path=args.split,
        source_package=args.source_package,
        self_owned_deck_package=args.self_owned_deck_package,
        control_package=args.control_package,
        pool_root=args.pool_root,
        generations=args.generations,
        reeval_repeats=args.reeval_repeats,
        reeval_games_per_opponent_seat=args.reeval_games_per_opponent_seat,
        positive_delta_gate=not args.no_positive_delta_gate,
        risk_aware_update=not args.no_risk_aware_update,
        campaign_seed=args.campaign_seed,
        population_size=args.population_size,
        elite_count=args.elite_count,
        initial_config=initial_config,
        initial_scale_fraction=args.initial_scale_fraction,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "results"},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
