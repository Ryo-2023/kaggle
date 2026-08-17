#!/usr/bin/env python3
"""384-game confirmation for the positive public Ursaluna P1 candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_observed_failure_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    materialize_observed_failure_variant_v1,
)
from scripts import run_cg_p1_observed_failure_screen_v1 as screen  # noqa: E402


SCHEMA = "meta-specialist-cg-p1-observed-failure-confirmation-v1"
CANDIDATE_ID = "cg-p1-ursaluna-pressure-v1"
DEFAULT_SOURCE_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DEFAULT_SOURCE_SCREEN = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-p1-observed-ursaluna-pressure-screen-96-20260814-v1"
)
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def confirmation_contract_v1() -> dict[str, object]:
    return {
        "games_per_opponent_seat": 8,
        "requested_games_per_arm": 384,
        "workers": 12,
        "worker_recycle_games": 64,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
    }


def validate_confirmation_contract_v1(*, workers: int, worker_recycle_games: int) -> dict[str, object]:
    contract = confirmation_contract_v1()
    if workers != contract["workers"] or worker_recycle_games != contract["worker_recycle_games"]:
        raise ValueError("confirmation is sealed to workers=12/recycle=64")
    return contract


def _positive_screen_summary(path: Path) -> tuple[dict[str, object], str]:
    summary_path = path / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"positive 96 screen summary missing: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("source 96 screen candidate mismatch")
    if float(summary.get("candidate_delta_points", 0.0)) <= 0.0:
        raise ValueError("source 96 screen is not positive")
    evaluator = summary.get("evaluator_summary") or {}
    if int(evaluator.get("faults", 1)) != 0:
        raise ValueError("source 96 screen has faults")
    return summary, screen._sha256(summary_path)


def run_confirmation(
    *,
    output_root: Path,
    source_package: Path = DEFAULT_SOURCE_PACKAGE,
    source_screen: Path = DEFAULT_SOURCE_SCREEN,
    config: Path = DEFAULT_CONFIG,
    base_seed: int = 40830000,
    workers: int = 12,
    worker_recycle_games: int = 64,
) -> dict[str, object]:
    contract = validate_confirmation_contract_v1(
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    source_package = Path(source_package).resolve()
    source_screen = Path(source_screen).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"confirmation output exists: {output}")
    source_summary, source_summary_sha = _positive_screen_summary(source_screen)
    if screen._sha256(source_package / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("control package is not the fixed P1 policy")
    refs = screen.arena._read_refs(Path(config).resolve())
    output.mkdir(parents=True, exist_ok=False)
    candidate_package = output / "candidate-package"
    package_manifest = materialize_observed_failure_variant_v1(
        source_package=source_package,
        output_package=candidate_package,
        candidate_id=CANDIDATE_ID,
    )
    candidate = screen.arena.ArenaArm(
        arm_id="cg_p1_observed_candidate",
        policy_id=CANDIDATE_ID,
        policy_sha256=screen._sha256(candidate_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=candidate_package,
    )
    control = screen.arena.ArenaArm(
        arm_id="cg_p1_fixed_control",
        policy_id="cg-lethal-target-v1",
        policy_sha256=screen._sha256(source_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=source_package,
    )
    games = list(
        screen.arena._build_games(
            arm=candidate,
            refs=refs,
            pool_root=_ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=8,
            block_id=f"{SCHEMA}-{base_seed}-candidate",
        )
    )
    games.extend(
        screen.arena._build_games(
            arm=control,
            refs=refs,
            pool_root=_ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=8,
            block_id=f"{SCHEMA}-{base_seed}-control",
        )
    )
    candidate_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == candidate.arm_id}
    control_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == control.arm_id}
    if candidate_keys != control_keys or len(candidate_keys) != 384:
        raise ValueError("confirmation candidate/control strata are not the same 384 cells")
    smoke = screen.arena.run_root_cg_game_v1(games[0].to_payload())
    if smoke.get("status") != "DONE":
        raise ValueError(f"confirmation smoke failed: {smoke.get('status')}")
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_id": CANDIDATE_ID,
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "candidate_deck_sha256": screen._sha256(candidate_package / "deck.csv"),
        "control_deck_sha256": screen._sha256(source_package / "deck.csv"),
        "candidate_package_manifest": package_manifest,
        "reference_ids": list(refs),
        "requested_games": len(games),
        "games_per_opponent_seat": 8,
        "base_seed": base_seed,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "source_screen_root": str(source_screen),
        "source_screen_summary_sha256": source_summary_sha,
        "source_screen_delta_points": source_summary["candidate_delta_points"],
        "smoke": {"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps")},
        "authority": dict(screen.AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evaluation = screen.arena.run_parallel_cabt_evaluation(
        games,
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    rows = [dict(row) for row in evaluation["rows"]]
    grouped = {
        candidate.arm_id: [row for row in rows if row.get("metadata", {}).get("arm_id") == candidate.arm_id],
        control.arm_id: [row for row in rows if row.get("metadata", {}).get("arm_id") == control.arm_id],
    }
    candidate_summary = screen._aggregate(grouped[candidate.arm_id])
    control_summary = screen._aggregate(grouped[control.arm_id])
    delta = (float(candidate_summary["score_rate"] or 0.0) - float(control_summary["score_rate"] or 0.0)) * 100.0
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": CANDIDATE_ID,
        "candidate": candidate_summary,
        "control": control_summary,
        "candidate_delta_points": delta,
        "evaluator_summary": evaluation["summary"],
        "source_screen_summary_sha256": source_summary_sha,
        "source_screen_delta_points": source_summary["candidate_delta_points"],
        "smoke": {"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps")},
        "authority": dict(screen.AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "next_gate": "manual review; no automatic 768/longrun",
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "summary_sha256": screen._sha256(summary_path), "faults": evaluation["summary"].get("faults"), "completed_games": evaluation["summary"].get("completed_games")})
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--source-screen", type=Path, default=DEFAULT_SOURCE_SCREEN)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--base-seed", type=int, default=40830000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        result = run_confirmation(
            output_root=args.output,
            source_package=args.source_package,
            source_screen=args.source_screen,
            config=args.config,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
