#!/usr/bin/env python3
"""Run one bounded public-seat-conditioned CEM campaign.

This is a research-only bridge over a fresh, smoke-promoted self-owned source
pool.  It never updates BestKnown, Champion, production, or submission state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_seat_conditioned_renderer_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    SeatConditionedConfig,
    candidate_id_for_config,
    materialize_seat_conditioned_package,
)
from mage_ptcg.meta_specialist.cg_seat_conditioned_cem_v1 import (  # noqa: E402
    rank_valid_results,
    sample_population,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    materialize_self_owned_cg_package_v1,
    verify_self_owned_cg_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


SCHEMA = "cg-seat-conditioned-cem-campaign-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
SEAT_GAP_LIMIT = 0.05


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_detailed(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate scores with both seat and opponent×seat diagnostics."""

    result = arena._aggregate(rows)
    opponent_seat_rates: dict[str, dict[str, float]] = {}
    opponent_ids = sorted({str(row.get("opponent_id")) for row in rows})
    for opponent_id in opponent_ids:
        opponent_rows = [row for row in rows if str(row.get("opponent_id")) == opponent_id]
        by_seat: dict[str, float] = {}
        for seat in (0, 1):
            seat_rows = [row for row in opponent_rows if row.get("seat") == seat]
            aggregate = arena._aggregate(seat_rows, include_seat=False)
            score = aggregate.get("score_rate")
            if isinstance(score, (int, float)):
                by_seat[str(seat)] = float(score)
        if len(by_seat) == 2:
            opponent_seat_rates[opponent_id] = by_seat
    result["opponent_seat_rates"] = opponent_seat_rates
    return result


def _seat_gap(summary: Mapping[str, object]) -> float | None:
    seat = summary.get("seat")
    if not isinstance(seat, Mapping):
        return None
    rates = [
        float(value["score_rate"])
        for value in seat.values()
        if isinstance(value, Mapping) and isinstance(value.get("score_rate"), (int, float))
    ]
    return abs(rates[0] - rates[1]) if len(rates) == 2 else None


def _opponent_seat_gaps(summary: Mapping[str, object]) -> dict[str, float]:
    raw = summary.get("opponent_seat_rates")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, float] = {}
    for opponent_id, rates in raw.items():
        if not isinstance(rates, Mapping):
            continue
        seat0 = rates.get("0")
        seat1 = rates.get("1")
        if isinstance(seat0, (int, float)) and isinstance(seat1, (int, float)):
            result[str(opponent_id)] = abs(float(seat0) - float(seat1))
    return result


def passes_research_gate(result: Mapping[str, object]) -> bool:
    """Require positive independent repeats and both seat variance gates."""

    if result.get("faults") != 0:
        return False
    deltas = result.get("repeat_deltas")
    seat_gaps = result.get("repeat_seat_gaps")
    opponent_gaps = result.get("repeat_opponent_seat_gaps")
    if not isinstance(deltas, Sequence) or not deltas:
        return False
    if not isinstance(seat_gaps, Sequence) or len(seat_gaps) != len(deltas):
        return False
    if not isinstance(opponent_gaps, Sequence) or len(opponent_gaps) != len(deltas):
        return False
    if not all(isinstance(delta, (int, float)) and float(delta) > 0.0 for delta in deltas):
        return False
    if not all(isinstance(gap, (int, float)) and float(gap) <= SEAT_GAP_LIMIT for gap in seat_gaps):
        return False
    return all(
        isinstance(gaps, Mapping)
        and all(isinstance(gap, (int, float)) and float(gap) <= SEAT_GAP_LIMIT for gap in gaps.values())
        for gaps in opponent_gaps
    )


def _aggregate_arm(rows: Sequence[Mapping[str, object]], arm_id: str) -> dict[str, object]:
    return _aggregate_detailed(
        [row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id]
    )


def _build_arm_games(
    *,
    package: Path,
    arm_id: str,
    refs: Sequence[str],
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    block_id: str,
):
    arm = arena.ArenaArm(
        arm_id=arm_id,
        policy_id=arm_id,
        policy_sha256=_sha256(package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=package.resolve(),
    )
    return arena._build_games(
        arm=arm,
        refs=tuple(refs),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
    )


def _evaluate_pair(
    *,
    candidate: Path,
    control: Path,
    refs: Sequence[str],
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    workers: int,
    output_dir: Path,
    candidate_arm_id: str,
    control_arm_id: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    candidate_games = _build_arm_games(
        package=candidate,
        arm_id=candidate_arm_id,
        refs=refs,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=output_dir.name,
    )
    control_games = _build_arm_games(
        package=control,
        arm_id=control_arm_id,
        refs=refs,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=output_dir.name,
    )
    candidate_pairs = {game.metadata["pair_key"] for game in candidate_games}
    control_pairs = {game.metadata["pair_key"] for game in control_games}
    if candidate_pairs != control_pairs:
        raise ValueError("candidate/control pair strata differ")
    evaluation = run_parallel_cabt_evaluation(
        tuple(candidate_games) + tuple(control_games),
        output_dir=output_dir,
        max_workers=workers,
        worker_recycle_games=16,
        overwrite=False,
    )
    candidate_summary = _aggregate_arm(evaluation["rows"], candidate_arm_id)
    control_summary = _aggregate_arm(evaluation["rows"], control_arm_id)
    candidate_score = float(candidate_summary.get("score_rate") or 0.0)
    control_score = float(control_summary.get("score_rate") or 0.0)
    detail = {
        "requested_games": evaluation["summary"]["requested_games"],
        "evaluator_summary": evaluation["summary"],
        "candidate_arm_id": candidate_arm_id,
        "control_arm_id": control_arm_id,
        "delta_points": round((candidate_score - control_score) * 100.0, 6),
        "candidate_seat_gap": _seat_gap(candidate_summary),
        "control_seat_gap": _seat_gap(control_summary),
        "candidate_opponent_seat_gaps": _opponent_seat_gaps(candidate_summary),
    }
    return candidate_summary, control_summary, detail


def _independent_train(
    *,
    candidate: Path,
    control: Path,
    refs: Sequence[str],
    pool_root: Path,
    seed: int,
    repeats: int,
    games_per_opponent_seat: int,
    workers: int,
    output_dir: Path,
    candidate_id: str,
    control_arm_id: str,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    repeat_deltas: list[float] = []
    repeat_seat_gaps: list[float | None] = []
    repeat_opponent_gaps: list[dict[str, float]] = []
    total_faults = 0
    for repeat in range(repeats):
        candidate_summary, control_summary, detail = _evaluate_pair(
            candidate=candidate,
            control=control,
            refs=refs,
            pool_root=pool_root,
            base_seed=seed + repeat * 10_000,
            games_per_opponent_seat=games_per_opponent_seat,
            workers=workers,
            output_dir=output_dir / f"repeat-{repeat:02d}",
            candidate_arm_id=f"{candidate_id}-train-r{repeat:02d}",
            control_arm_id=f"{control_arm_id}-train-r{repeat:02d}",
        )
        candidate_faults = int(candidate_summary.get("faults", 0))
        control_faults = int(control_summary.get("faults", 0))
        total_faults += candidate_faults + control_faults
        repeat_deltas.append(float(detail["delta_points"]) / 100.0)
        repeat_seat_gaps.append(detail["candidate_seat_gap"])
        repeat_opponent_gaps.append(dict(detail["candidate_opponent_seat_gaps"]))
        rows.append({"candidate": candidate_summary, "control": control_summary, **detail})
    result: dict[str, object] = {
        "repeat_count": repeats,
        "repeat_deltas": repeat_deltas,
        "repeat_seat_gaps": repeat_seat_gaps,
        "repeat_opponent_seat_gaps": repeat_opponent_gaps,
        "faults": total_faults,
        "repeats": rows,
    }
    result["gate_pass"] = passes_research_gate(result)
    return result


def run_campaign(
    *,
    output_root: str | Path,
    split_path: str | Path,
    pool_root: str | Path,
    source_package: str | Path,
    deck_package: str | Path,
    population_size: int = 8,
    elite_count: int = 2,
    games_per_opponent_seat: int = 2,
    validation_games_per_opponent_seat: int = 4,
    independent_repeats: int = 2,
    workers: int = 1,
    seed: int = 2026081699,
    execute: bool = False,
) -> dict[str, object]:
    if not execute:
        raise PermissionError("--execute is required before CABT")
    if type(population_size) is not int or population_size < 2:
        raise ValueError("population_size must be at least 2")
    if type(elite_count) is not int or elite_count <= 0 or elite_count > population_size:
        raise ValueError("elite_count must be in [1, population_size]")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    if type(validation_games_per_opponent_seat) is not int or validation_games_per_opponent_seat <= 0:
        raise ValueError("validation_games_per_opponent_seat must be positive")
    if type(independent_repeats) is not int or independent_repeats <= 0:
        raise ValueError("independent_repeats must be positive")
    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be positive")
    if type(seed) is not int or seed <= 0:
        raise ValueError("seed must be positive")

    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    split = load_weekend_split(Path(split_path).resolve(), verify_sources=True)
    pool = Path(pool_root).resolve()
    pool_manifest = pool / "pool_manifest.json"
    if not pool_manifest.is_file():
        raise FileNotFoundError(pool_manifest)
    source = Path(source_package).resolve()
    deck = Path(deck_package).resolve()
    if _sha256(source / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("source package is not the sealed P1 parent")
    deck_manifest = verify_self_owned_cg_package_v1(deck)
    if deck_manifest.get("parent_policy_sha256") != BASE_SOURCE_SHA256:
        raise ValueError("deck package is not P1-bound")
    if not (source / "cg").is_dir():
        raise ValueError("source package lacks cg runtime")
    refs = tuple(split.ids("META_TRAIN"))
    if not refs:
        raise ValueError("META_TRAIN source split is empty")

    output.mkdir(parents=True, exist_ok=False)
    control = output / "control" / "package"
    materialize_self_owned_cg_package_v1(
        source_package=source,
        candidate_deck=deck / "deck.csv",
        output_package=control,
        candidate_id="seat-conditioned-cem-p1-control",
    )

    center = SeatConditionedConfig.default()
    configs = sample_population(center, generation=0, population_size=population_size, seed=seed)
    candidates_root = output / "generation-0000" / "candidates"
    records: list[dict[str, object]] = []
    all_games_dir = output / "generation-0000" / "evaluation"
    games = []
    candidate_paths: dict[str, Path] = {}
    control_arm_id = "seat-cem-p1-control"
    for index, config in enumerate(configs):
        candidate_id = candidate_id_for_config(config, generation=0, index=index)
        package = candidates_root / f"c{index:02d}" / "package"
        materialize_seat_conditioned_package(
            source_package=source,
            self_owned_deck_package=deck,
            output_package=package,
            config=config,
            candidate_id=candidate_id,
        )
        candidate_paths[candidate_id] = package
        arm_id = f"seat-cem-g00-c{index:02d}"
        block_id = f"seat-cem-g00-c{index:02d}"
        games.extend(_build_arm_games(
            package=package,
            arm_id=arm_id,
            refs=refs,
            pool_root=pool,
            base_seed=seed + index * 1000,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=block_id,
        ))
        games.extend(_build_arm_games(
            package=control,
            arm_id=control_arm_id,
            refs=refs,
            pool_root=pool,
            base_seed=seed + index * 1000,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=block_id,
        ))

    evaluation = run_parallel_cabt_evaluation(
        tuple(games),
        output_dir=all_games_dir,
        max_workers=workers,
        worker_recycle_games=16,
        overwrite=False,
    )
    for index, config in enumerate(configs):
        candidate_id = candidate_id_for_config(config, generation=0, index=index)
        arm_id = f"seat-cem-g00-c{index:02d}"
        candidate_summary = _aggregate_arm(evaluation["rows"], arm_id)
        control_rows = [
            row for row in evaluation["rows"]
            if row.get("metadata", {}).get("arm_id") == control_arm_id
            and row.get("block_id") == f"seat-cem-g00-c{index:02d}"
        ]
        control_summary = _aggregate_detailed(control_rows)
        delta = float(candidate_summary.get("score_rate") or 0.0) - float(control_summary.get("score_rate") or 0.0)
        faults = int(candidate_summary.get("faults", 0)) + int(control_summary.get("faults", 0))
        records.append({
            "candidate_id": candidate_id,
            "arm_id": arm_id,
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
            "policy_sha256": _sha256(candidate_paths[candidate_id] / "main.py"),
            "deck_sha256": _sha256(candidate_paths[candidate_id] / "deck.csv"),
            "candidate": candidate_summary,
            "control": control_summary,
            "delta_points": round(delta * 100.0, 6),
            "candidate_seat_gap": _seat_gap(candidate_summary),
            "candidate_opponent_seat_gaps": _opponent_seat_gaps(candidate_summary),
            "faults": faults,
            "objective": delta,
            "valid": faults == 0,
        })

    elites = rank_valid_results(records, elite_count=elite_count)
    next_center, next_scales = update_distribution(center, elites)
    best = elites[0]
    best_id = str(best["candidate_id"])
    best_package = candidate_paths[best_id]
    independent = _independent_train(
        candidate=best_package,
        control=control,
        refs=refs,
        pool_root=pool,
        seed=seed + 50_000,
        repeats=independent_repeats,
        games_per_opponent_seat=validation_games_per_opponent_seat,
        workers=workers,
        output_dir=output / "validation" / "independent_train",
        candidate_id=best_id,
        control_arm_id=control_arm_id,
    )
    validations: dict[str, object] = {}
    for split_name, offset in (("META_DEV", 100_000), ("META_FINAL", 200_000)):
        refs_for_validation = tuple(split.ids(split_name))
        candidate_summary, control_summary, detail = _evaluate_pair(
            candidate=best_package,
            control=control,
            refs=refs_for_validation,
            pool_root=pool,
            base_seed=seed + offset,
            games_per_opponent_seat=validation_games_per_opponent_seat,
            workers=workers,
            output_dir=output / "validation" / split_name.lower(),
            candidate_arm_id=f"{best_id}-{split_name.lower()}",
            control_arm_id=f"{control_arm_id}-{split_name.lower()}",
        )
        validations[split_name] = {
            "candidate": candidate_summary,
            "control": control_summary,
            **detail,
            "reference_ids": list(refs_for_validation),
        }

    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "generation": 0,
        "center_before": center.as_dict(),
        "center_after": next_center.as_dict(),
        "scales_after": next_scales,
        "source_package": str(source),
        "deck_package": str(deck),
        "deck_sha256": _sha256(deck / "deck.csv"),
        "pool_root": str(pool),
        "pool_manifest_sha256": _sha256(pool_manifest),
        "split_sha256": split.config_sha256,
        "train_reference_ids": list(refs),
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "train_evaluator_summary": evaluation["summary"],
        "candidates": records,
        "elites": [
            {
                **dict(item),
                "config": item["config"].as_dict()
                if isinstance(item.get("config"), SeatConditionedConfig)
                else item.get("config"),
            }
            for item in elites
        ],
        "best_candidate_id": best_id,
        "best_candidate_package": str(best_package),
        "independent_train": independent,
        "validation": validations,
        "promotion": {
            "allowed": False,
            "research_gate_pass": bool(independent.get("gate_pass")),
            "bestknown_updated": False,
        },
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output / "campaign_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--source-package", type=Path, required=True)
    parser.add_argument("--deck-package", type=Path, required=True)
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--validation-games-per-opponent-seat", type=int, default=4)
    parser.add_argument("--independent-repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026081699)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_campaign(
            output_root=args.output,
            split_path=args.split,
            pool_root=args.pool_root,
            source_package=args.source_package,
            deck_package=args.deck_package,
            population_size=args.population_size,
            elite_count=args.elite_count,
            games_per_opponent_seat=args.games_per_opponent_seat,
            validation_games_per_opponent_seat=args.validation_games_per_opponent_seat,
            independent_repeats=args.independent_repeats,
            workers=args.workers,
            seed=args.seed,
            execute=True,
        )
    except (PermissionError, FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output), "best_candidate_id": result["best_candidate_id"], "research_gate_pass": result["promotion"]["research_gate_pass"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
