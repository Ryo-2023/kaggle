#!/usr/bin/env python3
"""Screen a bounded public-state P2 contextual attack surface.

The screen compares every candidate with one shared P2 control on identical
opponent, seat, repetition, and seed strata.  It is research-only: no
Champion mutation, submission, or fresh-meta claim is made here.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    P2ContextConfig,
    candidate_id_for_config,
    materialize_context_package,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import WeekendSplit, load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor  # noqa: E402
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import aggregate_candidate_rows  # noqa: E402


SCHEMA = "cg-p2-context-screen-v1"
DEFAULT_SPLIT = _ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
DEFAULT_CONTROL = _ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
CONTROL_ID = "cg-p2-context-control"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_screen_configs() -> tuple[P2ContextConfig, ...]:
    """Return a fixed, small grid around the identity point."""

    one = 12_000
    return tuple(
        P2ContextConfig(*values)
        for values in (
            (0, 0, 0),
            (one, 0, 0),
            (0, one, 0),
            (0, 0, one),
            (one, one, 0),
            (one, 0, one),
            (0, one, one),
            (one, one, one),
        )
    )


def _bind_games(
    games: Sequence[EvaluationGameV1],
    *,
    arm_role: str,
    config_sha256: str,
    candidate_id: str,
) -> tuple[EvaluationGameV1, ...]:
    bound: list[EvaluationGameV1] = []
    for game in games:
        metadata = dict(game.metadata)
        metadata.update(
            {
                "schema_version": arena.SCHEMA,
                "context_schema": SCHEMA,
                "context_config_sha256": config_sha256,
                "context_candidate_id": candidate_id,
                "arm_role": arm_role,
                "split": "META_TRAIN",
                "evaluator_sha256": evaluation_implementation_sha256_v1(),
                "parent_policy_sha256": BASE_SOURCE_SHA256,
                "authority": dict(AUTHORITY_FALSE),
                "research_only": True,
                "training_exposure": 0,
            }
        )
        bound.append(replace(game, metadata=metadata))
    return tuple(bound)


def build_context_paired_games(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config_sha256: str,
    split: WeekendSplit,
    control_package: Path | str,
    reference_ids: Sequence[str],
    base_seed: int,
    repetitions: int,
) -> tuple[EvaluationGameV1, ...]:
    """Build candidate/control games with exactly shared seed strata."""

    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be non-empty")
    if type(config_sha256) is not str or len(config_sha256) != 64:
        raise ValueError("config_sha256 must be a SHA-256 hex string")
    refs = tuple(str(item) for item in reference_ids)
    if not refs or len(set(refs)) != len(refs):
        raise ValueError("reference_ids must be non-empty and unique")
    if type(base_seed) is not int or base_seed <= 0:
        raise ValueError("base_seed must be a positive integer")
    if type(repetitions) is not int or repetitions <= 0:
        raise ValueError("repetitions must be a positive integer")
    candidate_path = Path(candidate_package).resolve()
    control_path = Path(control_package).resolve()
    candidate_main_sha = _sha256(candidate_path / "main.py")
    control_main_sha = _sha256(control_path / "main.py")
    candidate_arm = arena.ArenaArm(
        arm_id=candidate_id,
        policy_id=candidate_id,
        policy_sha256=candidate_main_sha,
        arm_kind="root_cg",
        candidate_package_root=candidate_path,
    )
    control_arm = arena.ArenaArm(
        arm_id="p2-control",
        policy_id=CONTROL_ID,
        policy_sha256=control_main_sha,
        arm_kind="root_cg",
        candidate_package_root=control_path,
    )
    candidate_games = arena._build_games(
        arm=candidate_arm,
        refs=refs,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{candidate_id}-{base_seed}-candidate",
    )
    control_games = arena._build_games(
        arm=control_arm,
        refs=refs,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{candidate_id}-{base_seed}-control",
    )
    candidate_bound = _bind_games(
        candidate_games,
        arm_role="candidate",
        config_sha256=config_sha256,
        candidate_id=candidate_id,
    )
    control_bound = _bind_games(
        control_games,
        arm_role="p2_control",
        config_sha256=config_sha256,
        candidate_id=candidate_id,
    )
    candidate_keys = {(game.metadata["pair_key"], game.seed) for game in candidate_bound}
    control_keys = {(game.metadata["pair_key"], game.seed) for game in control_bound}
    if candidate_keys != control_keys:
        raise ValueError("candidate/control pair strata differ")
    return candidate_bound + control_bound


def summarize_context_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_id: str,
    control_id: str,
    weights: Mapping[str, float],
    config: P2ContextConfig,
) -> dict[str, object]:
    candidate_rows = [row for row in rows if row.get("policy_id") == candidate_id]
    control_rows = [row for row in rows if row.get("policy_id") == control_id]
    if not candidate_rows or not control_rows:
        raise ValueError("candidate/control rows are missing")
    candidate = aggregate_candidate_rows(candidate_rows, weights=weights)
    control = aggregate_candidate_rows(control_rows, weights=weights)
    seat_rates = candidate.get("seat_rates", {})
    seat_gap = None
    if isinstance(seat_rates, Mapping) and all(
        isinstance(seat_rates.get(seat), (int, float)) for seat in ("0", "1")
    ):
        seat_gap = abs(float(seat_rates["0"]) - float(seat_rates["1"]))
    faults = int(candidate.get("faults", 0)) + int(control.get("faults", 0))
    delta = float(candidate["objective"]) - float(control["objective"])
    seat_safe = seat_gap is not None and seat_gap <= 0.05
    decision = "PROMISING_SCREEN" if faults == 0 and seat_safe and delta > 0 else "NOT_PROMOTABLE"
    return {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "candidate_id": candidate_id,
        "control_id": control_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "candidate": candidate,
        "control": control,
        "delta_objective": delta,
        "delta_points": delta * 100.0,
        "candidate_seat_gap": seat_gap,
        "candidate_seat_safe": seat_safe,
        "faults": faults,
        "decision": decision,
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }


def _evaluate_games(games: Sequence[EvaluationGameV1], output: Path, workers: int) -> dict[str, object]:
    total = len(games)
    progress_bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            progress_bar = tqdm(total=total, desc="cg P2 context", unit="game", dynamic_ncols=True)
        except Exception:  # pragma: no cover
            progress_bar = None
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}

    def progress(row: Mapping[str, object]) -> None:
        state["completed"] += 1
        if str(row.get("outcome", "fault")) == "fault":
            state["faults"] += 1
        if progress_bar is not None:
            progress_bar.update(1)
            progress_bar.set_postfix(faults=state["faults"])
            return
        now = time.monotonic()
        if now - state["last_emit"] >= 10.0 or state["completed"] == total:
            print(
                json.dumps(
                    {"stage": "cg_p2_context", "completed": state["completed"], "requested": total, "faults": state["faults"]},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            state["last_emit"] = now

    try:
        return run_parallel_cabt_evaluation(
            tuple(games),
            output_dir=output,
            max_workers=workers,
            worker_recycle_games=16,
            overwrite=False,
            progress=progress,
        )
    finally:
        if progress_bar is not None:
            progress_bar.close()


def run_screen(
    *,
    output_root: Path | str,
    split_path: Path | str = DEFAULT_SPLIT,
    control_package: Path | str = DEFAULT_CONTROL,
    configs: Sequence[P2ContextConfig] | None = None,
    candidate_generation: int = 0,
    base_seed: int = 48316000,
    repetitions: int = 2,
    workers: int = 12,
    config_limit: int | None = None,
) -> dict[str, object]:
    if workers != 12:
        raise ValueError("P2 context screen is sealed to workers=12")
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    if type(candidate_generation) is not int or candidate_generation < 0:
        raise ValueError("candidate_generation must be a non-negative integer")
    output = Path(output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output root is not empty: {output}")
    split = load_weekend_split(split_path, verify_sources=True)
    control = Path(control_package).resolve()
    if _sha256(control / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("control package is not the immutable P2 parent")
    chosen = tuple(configs or default_screen_configs())
    if config_limit is not None:
        if type(config_limit) is not int or config_limit <= 0:
            raise ValueError("config_limit must be a positive integer")
        chosen = chosen[:config_limit]
    if not chosen or len({config.config_sha256() for config in chosen}) != len(chosen):
        raise ValueError("context screen configs must be non-empty and unique")
    governor = ResourceGovernor(ResourceBudget.from_json(DEFAULT_BUDGET))
    decision = governor.decide(task_cap=workers, gpu_required=False)
    if decision.state != "normal" or decision.recommended_workers < workers:
        raise RuntimeError(f"ResourceGovernor fail-closed: {decision.to_dict()}")
    output.mkdir(parents=True, exist_ok=True)
    candidate_root = output / "candidates"
    candidate_root.mkdir()
    packages: list[tuple[str, P2ContextConfig, Path]] = []
    for index, config in enumerate(chosen):
        config.validate()
        candidate_id = candidate_id_for_config(config, generation=candidate_generation, index=index)
        root = candidate_root / f"candidate-{index:02d}"
        materialize_context_package(
            source_package=control,
            output_root=root,
            config=config,
            candidate_id=candidate_id,
            smoke_games=1,
            smoke_seed=base_seed + index,
        )
        packages.append((candidate_id, config, root / "package"))
    refs = tuple(split.ids("META_TRAIN"))
    games: list[EvaluationGameV1] = []
    shared_control: tuple[EvaluationGameV1, ...] | None = None
    for candidate_id, config, package in packages:
        paired = build_context_paired_games(
            candidate_package=package,
            candidate_id=candidate_id,
            config_sha256=config.config_sha256(),
            split=split,
            control_package=control,
            reference_ids=refs,
            base_seed=base_seed,
            repetitions=repetitions,
        )
        games.extend(game for game in paired if game.metadata["arm_role"] == "candidate")
        if shared_control is None:
            shared_control = tuple(game for game in paired if game.metadata["arm_role"] == "p2_control")
    if shared_control is None:
        raise ValueError("context screen produced no shared control")
    games.extend(shared_control)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "control_package": str(control),
        "control_policy_sha256": _sha256(control / "main.py"),
        "split_sha256": split.config_sha256,
        "split_name": "META_TRAIN",
        "reference_ids": list(refs),
        "candidate_count": len(packages),
        "candidate_generation": candidate_generation,
        "candidate_ids": [candidate_id for candidate_id, _config, _package in packages],
        "configs": [config.as_dict() for _candidate_id, config, _package in packages],
        "base_seed": base_seed,
        "repetitions": repetitions,
        "requested_games": len(games),
        "workers": workers,
        "worker_recycle_games": 16,
        "resource_decision": decision.to_dict(),
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "unused_meta_confirmation": "NOT_AVAILABLE_LOCAL_POOL",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    evaluation = _evaluate_games(games, output / "evaluation", workers)
    rows = evaluation["rows"]
    control_rows = [row for row in rows if row.get("policy_id") == CONTROL_ID]
    results = []
    for candidate_id, config, _package in packages:
        result = summarize_context_rows(
            rows,
            candidate_id=candidate_id,
            control_id=CONTROL_ID,
            weights=split.weights("META_TRAIN"),
            config=config,
        )
        results.append(result)
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "control": aggregate_candidate_rows(control_rows, weights=split.weights("META_TRAIN")),
        "results": results,
        "evaluator_summary": evaluation["summary"],
        "fresh_unused_meta_confirmation": "BLOCKED_NO_LOCAL_UNUSED_META",
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path)})
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--base-seed", type=int, default=48316000)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--config-limit", type=int, default=None)
    args = parser.parse_args(argv)
    result = run_screen(
        output_root=args.output,
        control_package=args.control_package,
        base_seed=args.base_seed,
        repetitions=args.repetitions,
        workers=args.workers,
        config_limit=args.config_limit,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
