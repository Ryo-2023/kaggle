#!/usr/bin/env python3
"""Research-only cg policy screen with immutable deck and cg P0 control.

Each candidate is a source-bound public-state variant.  The packaged cg P0
policy is the control; the Rule-v0 policy is not mixed into this screen.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from mage_ptcg.meta_specialist.cg_policy_candidate_v1 import VARIANT_IDS, materialize_variant
from scripts import build_root_cg_submission_candidate_v1 as builder
from scripts import run_root_cg_candidate_arena_v1 as arena


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROL_PACKAGE = ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package"
DEFAULT_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
SCHEMA = "meta-specialist-root-cg-policy-screen-v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)


def _sha256(path: Path) -> str:
    return arena._sha256(Path(path))


def build_variant_package(candidate_id: str, output: Path, *, smoke_seed: int) -> dict[str, object]:
    """Build one variant package without changing the production source."""
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"variant package output exists: {output}")
    with tempfile.TemporaryDirectory(prefix="cg-policy-variant-source-") as temporary:
        source_path = Path(temporary) / f"{candidate_id}.py"
        source_sha = materialize_variant(candidate_id, source_path)
        old_source = builder.SOURCE_AGENT
        old_schema = builder.SCHEMA
        try:
            builder.SOURCE_AGENT = source_path
            builder.SCHEMA = f"{SCHEMA}-{candidate_id}"
            manifest = builder.build_candidate(
                output,
                source_deck=ROOT / "deck.csv",
                candidate_id=candidate_id,
                smoke_games=2,
                smoke_seed=smoke_seed,
            )
        finally:
            builder.SOURCE_AGENT = old_source
            builder.SCHEMA = old_schema
    if manifest.get("policy_source_sha256") != source_sha:
        raise ValueError(f"variant source identity mismatch: {candidate_id}")
    return manifest


def _arm(package_root: Path, arm_id: str, policy_id: str) -> arena.ArenaArm:
    main_path = Path(package_root).resolve() / "main.py"
    return arena.ArenaArm(
        arm_id=arm_id,
        policy_id=policy_id,
        policy_sha256=_sha256(main_path),
        arm_kind="root_cg",
        candidate_package_root=Path(package_root).resolve(),
    )


def run_pair(
    *,
    candidate_package: Path,
    control_package: Path,
    output: Path,
    config: Path,
    base_seed: int,
    workers: int = 12,
    worker_recycle_games: int = 16,
    games_per_opponent_seat: int = 2,
) -> dict[str, object]:
    if workers != 12:
        raise ValueError("cg policy screen is sealed to workers=12")
    if games_per_opponent_seat not in {2, 8, 16}:
        raise ValueError("cg policy screen supports only 2, 8, or 16 games per opponent seat")
    expected_recycle = 16 if games_per_opponent_seat == 2 else 64
    if worker_recycle_games != expected_recycle:
        raise ValueError(
            f"games_per_opponent_seat={games_per_opponent_seat} requires "
            f"worker_recycle_games={expected_recycle}"
        )
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"screen output exists: {output}")
    refs = arena._read_refs(Path(config).resolve())
    candidate = _arm(candidate_package, "cg_policy_candidate", "cg-policy-candidate")
    control = _arm(control_package, "cg_policy_p0_control", "cg-policy-p0-control")
    games = list(
        arena._build_games(
            arm=candidate,
            refs=refs,
            pool_root=ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=f"{SCHEMA}-{base_seed}-candidate",
        )
    )
    games.extend(
        arena._build_games(
            arm=control,
            refs=refs,
            pool_root=ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=f"{SCHEMA}-{base_seed}-control",
        )
    )
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_package": str(Path(candidate_package).resolve()),
        "control_package": str(Path(control_package).resolve()),
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "deck_sha256": _sha256(Path(candidate_package) / "deck.csv"),
        "requested_games": len(games),
        "games_per_opponent_seat": games_per_opponent_seat,
        "base_seed": base_seed,
        "reference_ids": list(refs),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "authority": AUTHORITY_FALSE,
        "research_only": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evaluation = arena.run_parallel_cabt_evaluation(
        tuple(games),
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    rows = evaluation["rows"]
    by_arm = {
        arm_id: arena._aggregate([row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id])
        for arm_id in (candidate.arm_id, control.arm_id)
    }
    candidate_score = float(by_arm[candidate.arm_id]["score_rate"] or 0.0)
    control_score = float(by_arm[control.arm_id]["score_rate"] or 0.0)
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "arms": by_arm,
        "candidate_delta_points": (candidate_score - control_score) * 100.0,
        "requested_games": len(games),
        "evaluator_summary": evaluation["summary"],
        "authority": AUTHORITY_FALSE,
        "research_only": True,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["status"] = "COMPLETE"
    manifest["summary_sha256"] = _sha256(summary_path)
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_root": str(output), "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", choices=VARIANT_IDS, required=True)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, default=DEFAULT_CONTROL_PACKAGE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--games-per-opponent-seat", type=int, choices=(2, 8, 16), default=2)
    parser.add_argument("--worker-recycle-games", type=int, choices=(16, 64), default=None)
    args = parser.parse_args(argv)
    result = run_pair(
        candidate_package=args.candidate_package,
        control_package=args.control_package,
        output=args.output,
        config=args.config,
        base_seed=args.base_seed,
        games_per_opponent_seat=args.games_per_opponent_seat,
        worker_recycle_games=(
            args.worker_recycle_games
            if args.worker_recycle_games is not None
            else (16 if args.games_per_opponent_seat == 2 else 64)
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
