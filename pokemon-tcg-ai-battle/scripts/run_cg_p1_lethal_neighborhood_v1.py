#!/usr/bin/env python3
"""Screen observed-failure cg-lethal neighborhood variants.

The fixed P1 package is the only control.  Each run is a fresh research root
with a 12-opponent weighted48 screen; common24/384 are separate explicit
stages and are never started implicitly by this command.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_lethal_neighborhood_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_p1_lethal_variant_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402


SCHEMA = "meta-specialist-cg-p1-lethal-neighborhood-v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
DEFAULT_SOURCE_PACKAGE = _ROOT / (
    "runs/final-sprint-autonomous/"
    "cg-policy-screen-v1-retry-safe4-20260814/candidates/"
    "cg-lethal-target-v1/package"
)
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
WEIGHTED_IDS = (
    "aman_crustleaware_fighting",
    "kokinnwakashuu_lucario_search",
    "pilkwang_lucario_alakazam",
    "kiyotah_abomasnow",
    "kiyotah_dragapult",
    "masamikobayashi_garchomp",
    "medal_0001_77a53ffc",
    "naoto714_kangaskhan",
    "prvsiyan_grimmsnarl",
    "aristophanivan_multiply",
    "itsuki9180_lucario_jp",
    "kiyotah_iono",
)


class CgP1LethalNeighborhoodError(ValueError):
    """Raised when the bounded research screen contract is not closed."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgP1LethalNeighborhoodError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_screen_contract_v1(*, workers: int, worker_recycle_games: int, stage_games: int) -> None:
    if workers != 12 or worker_recycle_games != 16 or stage_games != 48:
        raise CgP1LethalNeighborhoodError(
            "weighted48 is sealed to workers=12, recycle=16, stage_games=48"
        )


def _aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    return {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (
            outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)
        ) / requested if requested else None,
        "seat": {
            str(seat): {
                "wins": sum(row.get("outcome") == "win" for row in rows if row.get("seat") == seat),
                "draws": sum(row.get("outcome") == "draw" for row in rows if row.get("seat") == seat),
                "losses": sum(row.get("outcome") == "loss" for row in rows if row.get("seat") == seat),
                "faults": sum(row.get("outcome") == "fault" for row in rows if row.get("seat") == seat),
            }
            for seat in (0, 1)
        },
    }


def _build_pair_games(*, candidate_package: Path, control_package: Path, base_seed: int, games_per_opponent_seat: int):
    candidate = arena.ArenaArm(
        arm_id="cg_p1_lethal_neighborhood_candidate",
        policy_id="cg-p1-lethal-neighborhood-candidate",
        policy_sha256=_sha256(candidate_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=candidate_package,
    )
    control = arena.ArenaArm(
        arm_id="cg_p1_fixed_control",
        policy_id="cg-lethal-target-v1",
        policy_sha256=_sha256(control_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=control_package,
    )
    games = list(arena._build_games(
        arm=candidate,
        refs=WEIGHTED_IDS,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}-candidate",
    ))
    games.extend(arena._build_games(
        arm=control,
        refs=WEIGHTED_IDS,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}-control",
    ))
    candidate_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == candidate.arm_id}
    control_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == control.arm_id}
    if candidate_keys != control_keys or len(candidate_keys) != 48:
        raise CgP1LethalNeighborhoodError("candidate/control weighted strata differ")
    return candidate, control, tuple(games)


def run_p1_lethal_neighborhood_screen(
    *,
    candidate_id: str,
    source_package: Path,
    output_root: Path,
    base_seed: int = 40500000,
    workers: int = 12,
    worker_recycle_games: int = 16,
) -> dict[str, object]:
    if candidate_id not in VARIANT_IDS:
        raise CgP1LethalNeighborhoodError(f"unknown candidate_id: {candidate_id}")
    validate_screen_contract_v1(workers=workers, worker_recycle_games=worker_recycle_games, stage_games=48)
    source = Path(source_package).resolve()
    if _sha256(source / "main.py") != BASE_SOURCE_SHA256:
        raise CgP1LethalNeighborhoodError("control package is not immutable P1")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"screen output exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    candidate_package = output / "candidate-package"
    package_manifest = materialize_p1_lethal_variant_package_v1(
        source_package=source,
        output_package=candidate_package,
        candidate_id=candidate_id,
    )
    candidate, control, games = _build_pair_games(
        candidate_package=candidate_package,
        control_package=source,
        base_seed=base_seed,
        games_per_opponent_seat=2,
    )
    smoke = arena.run_root_cg_game_v1(games[0].to_payload())
    (output / "smoke.json").write_text(
        json.dumps({"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps"), "candidate_id": candidate_id}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if smoke.get("status") != "DONE":
        raise CgP1LethalNeighborhoodError(f"candidate smoke failed: {smoke.get('status')}")
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_id": candidate_id,
        "observed_failure": {"lethal_states": 192, "nonlethal_selected": 29, "losses": 18},
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "candidate_deck_sha256": _sha256(candidate_package / "deck.csv"),
        "control_deck_sha256": _sha256(source / "deck.csv"),
        "candidate_package_manifest": package_manifest,
        "reference_ids": list(WEIGHTED_IDS),
        "requested_games": len(games),
        "games_per_opponent_seat": 2,
        "base_seed": base_seed,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "smoke_status": smoke.get("status"),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evaluation = arena.run_parallel_cabt_evaluation(
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
    candidate_summary = _aggregate(grouped[candidate.arm_id])
    control_summary = _aggregate(grouped[control.arm_id])
    delta = (float(candidate_summary["score_rate"] or 0.0) - float(control_summary["score_rate"] or 0.0)) * 100.0
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": candidate_id,
        "candidate": candidate_summary,
        "control": control_summary,
        "candidate_delta_points": delta,
        "evaluator_summary": evaluation["summary"],
        "smoke": {"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps")},
        "paired_strata_count": 48,
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "coverage_gate": "not measured by terminal-only runner; candidate screen only",
        "next_gate": "common24 only if positive, fault0, both-seat support; no automatic promotion",
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path), "faults": evaluation["summary"].get("faults"), "completed_games": evaluation["summary"].get("completed_games")})
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", choices=VARIANT_IDS, required=True)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=40500000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    try:
        result = run_p1_lethal_neighborhood_screen(
            candidate_id=args.candidate_id,
            source_package=args.source_package,
            output_root=args.output,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (CgP1LethalNeighborhoodError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
