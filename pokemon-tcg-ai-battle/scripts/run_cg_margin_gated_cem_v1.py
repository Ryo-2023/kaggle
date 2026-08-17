#!/usr/bin/env python3
"""Run a research-only CEM campaign on the margin-gated P1 surface.

The runner keeps the P1/root-deck package as the control, pairs candidate and
control games by opponent/seat/seed, and never touches BestKnown, production
files, or the sealed split.  Candidate promotion requires positive deltas on
every independent repeat plus strict seat and opponent-seat safety.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_margin_gated_cem_v1 import (  # noqa: E402
    rank_valid_results,
    sample_population,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import aggregate_candidate_rows  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_margin_gated_renderer_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    MarginGatedConfig,
    candidate_id_for_config,
    materialize_margin_gated_package,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import (  # noqa: E402
    WeekendSplit,
    load_weekend_split,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    verify_self_owned_cg_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


SCHEMA = "cg-margin-gated-cem-campaign-v1"
CONTROL_POLICY_ID = "cg-margin-gated-p1-control"
SEAT_GAP_LIMIT = 0.05
P1_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
ROOT_DECK_PACKAGE = (
    _ROOT / "runs/cg-kaggle-kernel-meta-promoted-fresh-union4-rootdeck-v2-20260816"
    / "p1-root-control-v2"
)
DEFAULT_SPLIT = _ROOT / "runs/cg-self-owned-margin-gated-v1-20260816/promoted/cg_self_owned_weekend_split.json"
DEFAULT_OUTPUT = _ROOT / "runs/cg-self-owned-margin-gated-cem-v1-20260816"
DEFAULT_POOL = _ROOT / "opponents"


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _package_manifest_sha(package: Path) -> str:
    for name in ("self_owned_cg_package_manifest.json", "manifest.json"):
        candidate = package / name
        if candidate.is_file():
            return _sha256(candidate)
    raise ValueError(f"package manifest missing: {package}")


def _bind_game(game: EvaluationGameV1, *, role: str, split: WeekendSplit, pool_root: Path, config_sha: str) -> EvaluationGameV1:
    payload = game.to_payload()
    metadata = dict(payload["metadata"])
    metadata.update(
        {
            "schema_version": arena.SCHEMA,
            "cem_schema": SCHEMA,
            "arm_role": role,
            "config_sha256": config_sha,
            "split_sha256": split.config_sha256,
            "pool_root": str(pool_root.resolve()),
            "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
            "research_only": True,
            "training_exposure": 0,
        }
    )
    payload["metadata"] = metadata
    return EvaluationGameV1(**payload)


def _build_arm_games(
    *,
    package: Path,
    policy_id: str,
    refs: Sequence[str],
    split: WeekendSplit,
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    block_id: str,
    role: str,
    config_sha: str,
) -> tuple[EvaluationGameV1, ...]:
    package = package.resolve()
    arm = arena.ArenaArm(
        arm_id=policy_id,
        policy_id=policy_id,
        policy_sha256=_sha256(package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=package,
    )
    games = arena._build_games(
        arm=arm,
        refs=tuple(refs),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
    )
    return tuple(_bind_game(game, role=role, split=split, pool_root=pool_root, config_sha=config_sha) for game in games)


def _paired_games(
    *,
    candidate_package: Path,
    candidate_id: str,
    control_package: Path,
    split: WeekendSplit,
    refs: Sequence[str],
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    block_id: str,
    config_sha: str,
) -> tuple[EvaluationGameV1, ...]:
    candidate = _build_arm_games(
        package=candidate_package,
        policy_id=candidate_id,
        refs=refs,
        split=split,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
        role="candidate",
        config_sha=config_sha,
    )
    control = _build_arm_games(
        package=control_package,
        policy_id=CONTROL_POLICY_ID,
        refs=refs,
        split=split,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
        role="p1_control",
        config_sha=config_sha,
    )
    candidate_pairs = {str(game.metadata["pair_key"]) for game in candidate}
    control_pairs = {str(game.metadata["pair_key"]) for game in control}
    if candidate_pairs != control_pairs:
        raise ValueError("candidate/control pair strata differ")
    return candidate + control


def _aggregate_pair(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_id: str,
    config: MarginGatedConfig,
    weights: Mapping[str, float],
    block_id: str | None = None,
) -> dict[str, object]:
    if block_id is not None:
        rows = tuple(row for row in rows if row.get("block_id") == block_id)
    candidate_rows = [row for row in rows if row.get("policy_id") == candidate_id]
    control_rows = [row for row in rows if row.get("policy_id") == CONTROL_POLICY_ID]
    candidate = aggregate_candidate_rows(candidate_rows, weights=weights)
    control = aggregate_candidate_rows(control_rows, weights=weights)
    delta = float(candidate["objective"]) - float(control["objective"])
    return {
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "candidate": candidate,
        "control": control,
        "delta_objective": round(delta, 10),
        "objective": round(delta, 10),
        "faults": int(candidate.get("faults", 0)) + int(control.get("faults", 0)),
        "valid": bool(candidate.get("faults", 0) == 0 and control.get("faults", 0) == 0),
    }


def _seat_gap(summary: Mapping[str, object]) -> float | None:
    rates = summary.get("seat_rates")
    if not isinstance(rates, Mapping):
        return None
    seat0, seat1 = rates.get("0"), rates.get("1")
    if type(seat0) not in (int, float) or type(seat1) not in (int, float):
        return None
    return round(abs(float(seat0) - float(seat1)), 10)


def _opponent_seat_gaps(summary: Mapping[str, object]) -> dict[str, float]:
    raw = summary.get("opponent_seat_rates")
    result: dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return result
    for opponent_id, rates in raw.items():
        if not isinstance(rates, Mapping):
            continue
        seat0, seat1 = rates.get("0"), rates.get("1")
        if type(seat0) in (int, float) and type(seat1) in (int, float):
            result[str(opponent_id)] = round(abs(float(seat0) - float(seat1)), 10)
    return result


def _evaluate(games: Sequence[EvaluationGameV1], output: Path, *, workers: int, worker_recycle_games: int) -> dict[str, object]:
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}
    total = len(games)

    def progress(row: Mapping[str, object]) -> None:
        state["completed"] += 1
        if str(row.get("outcome", "fault")) == "fault":
            state["faults"] += 1
        now = time.monotonic()
        if now - float(state["last_emit"]) >= 10.0 or state["completed"] == total:
            print(json.dumps({"stage": "margin_gated_cem", "completed": state["completed"], "requested": total, "faults": state["faults"]}, ensure_ascii=False), file=sys.stderr, flush=True)
            state["last_emit"] = now

    return run_parallel_cabt_evaluation(
        tuple(games),
        output_dir=output,
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
        progress=progress,
    )


def _screen(
    *,
    generation: int,
    configs: Sequence[MarginGatedConfig],
    packages: Mapping[str, Path],
    split: WeekendSplit,
    pool_root: Path,
    output: Path,
    games_per_opponent_seat: int,
    seed: int,
    workers: int,
    worker_recycle_games: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    refs = split.ids("META_TRAIN")
    weights = split.weights("META_TRAIN")
    games: list[EvaluationGameV1] = []
    for index, config in enumerate(configs):
        candidate_id = candidate_id_for_config(config, generation=generation, index=index)
        games.extend(
            _paired_games(
                candidate_package=packages[candidate_id],
                candidate_id=candidate_id,
                control_package=ROOT_DECK_PACKAGE,
                split=split,
                refs=refs,
                pool_root=pool_root,
                base_seed=seed,
                games_per_opponent_seat=games_per_opponent_seat,
                block_id=f"margin-gated-screen-{candidate_id}",
                config_sha=config.config_sha256(),
            )
        )
    evaluation = _evaluate(games, output, workers=workers, worker_recycle_games=worker_recycle_games)
    rows = evaluation["rows"]
    results = [
        _aggregate_pair(
            rows,
            candidate_id=candidate_id_for_config(config, generation=generation, index=index),
            config=config,
            weights=weights,
            block_id=f"margin-gated-screen-{candidate_id_for_config(config, generation=generation, index=index)}",
        )
        for index, config in enumerate(configs)
    ]
    return results, evaluation["summary"]


def _independent(
    *,
    elites: Sequence[dict[str, object]],
    packages: Mapping[str, Path],
    configs: Mapping[str, MarginGatedConfig],
    split: WeekendSplit,
    pool_root: Path,
    output: Path,
    repeats: int,
    games_per_opponent_seat: int,
    seed: int,
    workers: int,
    worker_recycle_games: int,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    refs = split.ids("META_TRAIN")
    weights = split.weights("META_TRAIN")
    games: list[EvaluationGameV1] = []
    for repeat in range(repeats):
        for elite in elites:
            candidate_id = str(elite["candidate_id"])
            games.extend(
                _paired_games(
                    candidate_package=packages[candidate_id],
                    candidate_id=candidate_id,
                    control_package=ROOT_DECK_PACKAGE,
                    split=split,
                    refs=refs,
                    pool_root=pool_root,
                    base_seed=seed + 500_000 + repeat * 10_000,
                    games_per_opponent_seat=games_per_opponent_seat,
                    block_id=f"margin-gated-reeval-{candidate_id}-r{repeat:02d}",
                    config_sha=configs[candidate_id].config_sha256(),
                )
            )
    evaluation = _evaluate(games, output, workers=workers, worker_recycle_games=worker_recycle_games)
    rows = evaluation["rows"]
    by_candidate: dict[str, dict[str, object]] = {}
    for elite in elites:
        candidate_id = str(elite["candidate_id"])
        repeats_out: list[dict[str, object]] = []
        for repeat in range(repeats):
            block_id = f"margin-gated-reeval-{candidate_id}-r{repeat:02d}"
            block_rows = [row for row in rows if row.get("block_id") == block_id]
            repeats_out.append(_aggregate_pair(block_rows, candidate_id=candidate_id, config=configs[candidate_id], weights=weights))
        candidate_summaries = [item["candidate"] for item in repeats_out]
        deltas = [float(item["delta_objective"]) for item in repeats_out]
        seat_gaps = [_seat_gap(item) for item in candidate_summaries]
        opponent_gaps = [_opponent_seat_gaps(item) for item in candidate_summaries]
        seat_safe = all(gap is None or gap <= SEAT_GAP_LIMIT for gap in seat_gaps)
        opponent_safe = all(gap <= SEAT_GAP_LIMIT for gaps in opponent_gaps for gap in gaps.values())
        positive = all(delta > 0.0 for delta in deltas)
        fault_free = all(int(item.get("faults", 0)) == 0 for item in repeats_out)
        by_candidate[candidate_id] = {
            "candidate_id": candidate_id,
            "config": configs[candidate_id].as_dict(),
            "config_sha256": configs[candidate_id].config_sha256(),
            "repeats": repeats_out,
            "repeat_deltas": deltas,
            "min_delta_objective": min(deltas) if deltas else None,
            "mean_delta_objective": statistics.fmean(deltas) if deltas else None,
            "repeat_seat_gaps": seat_gaps,
            "repeat_opponent_seat_gaps": opponent_gaps,
            "positive_delta_gate": positive,
            "fault_free_gate": fault_free,
            "seat_safe": seat_safe,
            "opponent_seat_safe": opponent_safe,
            "research_gate_pass": bool(positive and fault_free and seat_safe and opponent_safe),
        }
    return by_candidate, evaluation["summary"]


def run_campaign(
    *,
    output_root: Path | str,
    split_path: Path | str = DEFAULT_SPLIT,
    source_package: Path | str = P1_PACKAGE,
    control_package: Path | str = ROOT_DECK_PACKAGE,
    target_generations: int = 2,
    population_size: int = 8,
    elite_count: int = 2,
    screen_games_per_opponent_seat: int = 1,
    reeval_repeats: int = 2,
    reeval_games_per_opponent_seat: int = 4,
    workers: int = 1,
    worker_recycle_games: int = 1,
    seed: int = 2026081801,
    pool_root: Path | str | None = None,
) -> dict[str, object]:
    if target_generations <= 0 or population_size <= 0 or elite_count <= 0 or elite_count > population_size:
        raise ValueError("invalid CEM dimensions")
    if reeval_repeats <= 0 or screen_games_per_opponent_seat <= 0 or reeval_games_per_opponent_seat <= 0:
        raise ValueError("game counts must be positive")
    source = Path(source_package).resolve()
    control = Path(control_package).resolve()
    if _sha256(source / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("source package is not sealed P1")
    source_manifest = verify_self_owned_cg_package_v1(control)
    if source_manifest.get("parent_policy_sha256") != BASE_SOURCE_SHA256:
        raise ValueError("control package is not P1-bound")
    split = load_weekend_split(split_path, verify_sources=True)
    if source_manifest.get("deck_file_sha256") != split.metadata["bindings"]["p1_deck_sha256"]:
        raise ValueError("control package deck does not match split P1 binding")
    if pool_root is None:
        pool_manifest_ref = split.metadata.get("sources", {}).get("pool_manifest_path")
        if not isinstance(pool_manifest_ref, str) or not pool_manifest_ref:
            raise ValueError("split does not bind a pool manifest path")
        pool_root = Path(pool_manifest_ref)
        if not pool_root.is_absolute():
            pool_root = _ROOT / pool_root
        pool_root = pool_root.resolve().parent
    else:
        pool_root = Path(pool_root).resolve()
    if not (pool_root / "pool_manifest.json").is_file():
        raise FileNotFoundError(pool_root / "pool_manifest.json")
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"output root is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    center = MarginGatedConfig.default()
    scales = None
    all_generations: list[dict[str, object]] = []
    for generation in range(target_generations):
        gen_root = root / f"generation-{generation:04d}"
        candidate_root = gen_root / "candidates"
        candidate_root.mkdir(parents=True, exist_ok=False)
        configs = sample_population(center, generation=generation, population_size=population_size, seed=seed, scales=scales)
        packages: dict[str, Path] = {}
        config_by_id: dict[str, MarginGatedConfig] = {}
        for index, config in enumerate(configs):
            candidate_id = candidate_id_for_config(config, generation=generation, index=index)
            package = candidate_root / f"candidate-{index:02d}"
            materialize_margin_gated_package(
                source_package=source,
                self_owned_deck_package=control,
                output_package=package,
                config=config,
                candidate_id=candidate_id,
            )
            packages[candidate_id] = package
            config_by_id[candidate_id] = config
        _write_json(
            gen_root / "manifest.json",
            {
                "schema_version": SCHEMA,
                "generation": generation,
                "population_size": population_size,
                "elite_count": elite_count,
                "screen_games_per_opponent_seat": screen_games_per_opponent_seat,
                "reeval_repeats": reeval_repeats,
                "reeval_games_per_opponent_seat": reeval_games_per_opponent_seat,
                "split_sha256": split.config_sha256,
                "parent_policy_sha256": BASE_SOURCE_SHA256,
                "control_policy_sha256": _sha256(control / "main.py"),
                "evaluator_sha256": evaluation_implementation_sha256_v1(),
                "research_only": True,
            },
        )
        screen_results, screen_summary = _screen(
            generation=generation,
            configs=configs,
            packages=packages,
            split=split,
            pool_root=pool_root,
            output=gen_root / "screen",
            games_per_opponent_seat=screen_games_per_opponent_seat,
            seed=seed + generation * 100_000,
            workers=workers,
            worker_recycle_games=worker_recycle_games,
        )
        ranked = []
        for item in screen_results:
            row = dict(item)
            row["objective"] = float(item["delta_objective"])
            row["valid"] = int(item["candidate"].get("faults", 0)) == 0 and int(item["control"].get("faults", 0)) == 0
            row["faults"] = int(item["faults"])
            ranked.append(row)
        try:
            elites = list(rank_valid_results(ranked, elite_count=elite_count))
            screen_selection = "screen_delta_rank"
        except ValueError:
            elites = []
            screen_selection = "screen_no_valid_elites_preserve_center"
        independent: dict[str, dict[str, object]] = {}
        if elites:
            independent, independent_summary = _independent(
                elites=elites,
                packages=packages,
                configs=config_by_id,
                split=split,
                pool_root=pool_root,
                output=gen_root / "independent",
                repeats=reeval_repeats,
                games_per_opponent_seat=reeval_games_per_opponent_seat,
                seed=seed + 500_000 + generation * 100_000,
                workers=workers,
                worker_recycle_games=worker_recycle_games,
            )
        else:
            independent_summary = None
        accepted = [
            result for result in independent.values() if result.get("research_gate_pass") is True
        ]
        if accepted:
            new_center, new_scales = update_distribution(center, accepted)
            elite_selection = "risk_aware_independent_train_x2_positive_delta_gate"
            center_changed = new_center.config_sha256() != center.config_sha256()
        else:
            new_center, new_scales = center, scales
            elite_selection = "risk_aware_independent_train_x2_positive_delta_gate_preserve_center"
            center_changed = False
        generation_result: dict[str, object] = {
            "schema_version": SCHEMA,
            "generation": generation,
            "screen_results": screen_results,
            "screen_selection": screen_selection,
            "screen_summary": screen_summary,
            "independent": independent,
            "independent_summary": independent_summary,
            "elite_selection": elite_selection,
            "accepted_count": len(accepted),
            "center": center.as_dict(),
            "new_center": new_center.as_dict(),
            "new_scales": new_scales,
            "center_changed": center_changed,
            "dev_final_consumed": False,
            "champion_changed": False,
            "best_known_updated": False,
            "research_only": True,
        }
        _write_json(gen_root / "results.json", generation_result)
        all_generations.append(generation_result)
        center, scales = new_center, new_scales
    manifest = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "output_root": str(root),
        "split_path": str(Path(split_path).resolve()),
        "split_sha256": split.config_sha256,
        "source_package": str(source),
        "control_package": str(control),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "final_center": center.as_dict(),
        "generation_count": len(all_generations),
        "generations": all_generations,
        "dev_final_consumed": False,
        "champion_changed": False,
        "best_known_updated": False,
        "research_only": True,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False},
    }
    _write_json(root / "manifest.json", manifest)
    return {"status": "COMPLETE", "output_root": str(root), "generations": len(all_generations), "best_known_updated": False}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-package", type=Path, default=P1_PACKAGE)
    parser.add_argument("--control-package", type=Path, default=ROOT_DECK_PACKAGE)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--screen-games-per-opponent-seat", type=int, default=1)
    parser.add_argument("--reeval-repeats", type=int, default=2)
    parser.add_argument("--reeval-games-per-opponent-seat", type=int, default=4)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-recycle-games", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026081801)
    parser.add_argument("--pool-root", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_campaign(
            output_root=args.output,
            split_path=args.split,
            source_package=args.source_package,
            control_package=args.control_package,
            target_generations=args.generations,
            population_size=args.population_size,
            elite_count=args.elite_count,
            screen_games_per_opponent_seat=args.screen_games_per_opponent_seat,
            reeval_repeats=args.reeval_repeats,
            reeval_games_per_opponent_seat=args.reeval_games_per_opponent_seat,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            seed=args.seed,
            pool_root=args.pool_root,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
