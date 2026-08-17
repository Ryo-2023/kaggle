#!/usr/bin/env python3
"""Run one hash-bound cg stage against the META_TRAIN population.

This wrapper is intentionally bounded.  It consumes a pre-materialized
``CgPopulationScheduleV1`` rather than the older fixed broad-pool config,
reuses the existing cg alternating evaluator, and records the population
identity beside the normal stage sidecars.  ``--execute`` is the only switch
that starts CABT; no training, promotion, submission, or unbounded loop is
performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (
    CG_DECK_FIXED_LONG_V1,
    CG_POLICY_FIXED_SHORT_V1,
    CG_STAGE_GAMES_V1,
    DEFAULT_WORKERS_V1,
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
    load_cg_alternating_stage_v1,
    run_cg_alternating_stage_v1,
)
from mage_ptcg.meta_specialist.cg_population_alternating_v1 import (
    CgPopulationScheduleError,
    CgPopulationScheduleV1,
    load_cg_population_schedule_v1,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_ROOT = ROOT / "opponents"
AUTHORITY_FALSE_V1 = {
    "training": False,
    "promotion": False,
    "submission": False,
    "behavior_collection": False,
    "teacher_labels": False,
}


def validate_population_stage_contract_v1(
    *, stage_games: int, workers: int, worker_recycle_games: int
) -> None:
    """Validate the resource and successive-halving contract before a run."""

    if stage_games not in CG_STAGE_GAMES_V1:
        raise CgAlternatingRuntimeError(
            f"stage_games must be one of {CG_STAGE_GAMES_V1}, got {stage_games}"
        )
    if workers != DEFAULT_WORKERS_V1:
        raise CgAlternatingRuntimeError("population cg runner is sealed to workers=12")
    expected_recycle = 16 if stage_games == 96 else 64
    if worker_recycle_games != expected_recycle:
        raise CgAlternatingRuntimeError(
            f"stage_games={stage_games} requires worker_recycle_games={expected_recycle}"
        )


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgAlternatingRuntimeError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_no_clobber(path: Path, payload: Mapping[str, object]) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite sidecar: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with open(fd, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
        os.link(temporary, path)
        Path(temporary).unlink()
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def _population_sidecar(
    *,
    schedule: CgPopulationScheduleV1,
    schedule_path: Path,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    phase: str,
    stage_games: int,
    base_seed: int,
    block_id: str,
    workers: int,
    worker_recycle_games: int,
    stage_manifest_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": "meta-specialist-cg-population-stage-v1",
        "schedule_path": str(schedule_path.resolve()),
        "schedule_sha256": _sha256(schedule_path),
        "schedule_manifest_sha256": schedule.manifest_sha256,
        "pool_manifest_sha256": schedule.pool_manifest_sha256,
        "split": schedule.split,
        "selection_rule": schedule.selection_rule,
        "reference_ids": list(schedule.reference_ids),
        "weights": {key: float(schedule.weights[key]) for key in schedule.reference_ids},
        "usage_boundaries": {
            key: schedule.usage_boundaries[key] for key in schedule.reference_ids
        },
        "evaluation_only": True,
        "behavior_allowed": False,
        "teacher_labels_saved": False,
        "phase": phase,
        "stage_games": stage_games,
        "base_seed": base_seed,
        "block_id": block_id,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "candidate_id": candidate.candidate_id,
        "control_id": control.candidate_id,
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "candidate_deck_sha256": candidate.deck_sha256,
        "control_deck_sha256": control.deck_sha256,
        "stage_manifest_sha256": stage_manifest_sha256,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


def run_population_stage_v1(
    *,
    schedule_path: Path | str,
    candidate_package: Path | str,
    control_package: Path | str,
    phase: str,
    pool_root: Path | str,
    stage_games: int,
    base_seed: int,
    block_id: str,
    output_root: Path | str,
    execute: bool,
    workers: int = DEFAULT_WORKERS_V1,
    worker_recycle_games: int | None = None,
) -> dict[str, object]:
    schedule_file = Path(schedule_path).resolve()
    schedule = load_cg_population_schedule_v1(schedule_file, verify_sources=True)
    candidate = CgPackageSpecV1.from_package(candidate_package)
    control = CgPackageSpecV1.from_package(control_package)
    recycle = worker_recycle_games or (16 if stage_games == 96 else 64)
    validate_population_stage_contract_v1(
        stage_games=stage_games,
        workers=workers,
        worker_recycle_games=recycle,
    )
    result = run_cg_alternating_stage_v1(
        candidate=candidate,
        control=control,
        phase=phase,
        reference_ids=schedule.reference_ids,
        pool_root=pool_root,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id=block_id,
        output_root=output_root,
        execute=execute,
        workers=workers,
        worker_recycle_games=recycle,
    )
    root = Path(output_root).resolve()
    stage_manifest_path = root / (
        "manifest-complete.json" if execute else "manifest.json"
    )
    stage_manifest_sha = _sha256(stage_manifest_path)
    population = _population_sidecar(
        schedule=schedule,
        schedule_path=schedule_file,
        candidate=candidate,
        control=control,
        phase=phase,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id=block_id,
        workers=workers,
        worker_recycle_games=recycle,
        stage_manifest_sha256=stage_manifest_sha,
    )
    population_sha = _write_no_clobber(root / "population-manifest.json", population)
    result = {**result, "population_manifest_sha256": population_sha}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=(CG_POLICY_FIXED_SHORT_V1, CG_DECK_FIXED_LONG_V1),
        required=True,
    )
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--block-id", required=True)
    parser.add_argument("--stage-games", type=int, choices=CG_STAGE_GAMES_V1, default=96)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, choices=(16, 64), default=None)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_population_stage_v1(
            schedule_path=args.schedule,
            candidate_package=args.candidate_package,
            control_package=args.control_package,
            phase=args.phase,
            pool_root=args.pool_root,
            stage_games=args.stage_games,
            base_seed=args.base_seed,
            block_id=args.block_id,
            output_root=args.output,
            execute=args.execute,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (CgAlternatingRuntimeError, CgPopulationScheduleError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
