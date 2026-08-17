#!/usr/bin/env python3
"""Screen public-state candidates derived from observed P1 failures.

The fixed cg P1 package is the control.  Each candidate is an immutable
source-bound overlay that only reads the public opponent active HP/maxHP and
the selected attack damage.  This runner is research-only and seals a paired
24-opponent, two-seat, two-repetition screen at workers=12.
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

from mage_ptcg.meta_specialist.cg_p1_observed_failure_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_observed_failure_variant_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402


SCHEMA = "meta-specialist-cg-p1-observed-failure-screen-v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
DEFAULT_SOURCE_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


class ObservedFailureScreenError(ValueError):
    """Raised when the observed-failure screen cannot prove its contract."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ObservedFailureScreenError(f"regular file required: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _aggregate(rows: list[dict[str, object]], *, include_seat: bool = True) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    result: dict[str, object] = {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (
            (outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)) / requested
            if requested
            else None
        ),
    }
    if include_seat:
        result["seat"] = {
            str(seat): _aggregate(
                [row for row in rows if row.get("seat") == seat], include_seat=False
            )
            for seat in (0, 1)
        }
    return result


def _build_pair_games(
    *,
    candidate_package: Path,
    control_package: Path,
    refs: tuple[str, ...],
    base_seed: int,
) -> tuple[arena.ArenaArm, arena.ArenaArm, tuple[arena.EvaluationGameV1, ...]]:
    candidate = arena.ArenaArm(
        arm_id="cg_p1_observed_candidate",
        policy_id="cg-p1-observed-failure-candidate",
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
    games = list(
        arena._build_games(
            arm=candidate,
            refs=refs,
            pool_root=_ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=2,
            block_id=f"{SCHEMA}-{base_seed}-candidate",
        )
    )
    games.extend(
        arena._build_games(
            arm=control,
            refs=refs,
            pool_root=_ROOT / "opponents",
            base_seed=base_seed,
            games_per_opponent_seat=2,
            block_id=f"{SCHEMA}-{base_seed}-control",
        )
    )
    candidate_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in games
        if game.metadata.get("arm_id") == candidate.arm_id
    }
    control_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in games
        if game.metadata.get("arm_id") == control.arm_id
    }
    if candidate_keys != control_keys or len(candidate_keys) != 96:
        raise ObservedFailureScreenError("candidate/control strata are not the same 96 cells")
    return candidate, control, tuple(games)


def run_observed_failure_screen(
    *,
    candidate_id: str,
    source_package: Path,
    output_root: Path,
    config: Path = DEFAULT_CONFIG,
    base_seed: int,
    workers: int = 12,
    worker_recycle_games: int = 16,
) -> dict[str, object]:
    if candidate_id not in VARIANT_IDS:
        raise ObservedFailureScreenError(f"unknown candidate_id: {candidate_id}")
    if workers != 12 or worker_recycle_games != 16:
        raise ObservedFailureScreenError("observed-failure screen is sealed to workers=12/recycle=16")
    source_package = Path(source_package).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"screen output exists: {output}")
    if _sha256(source_package / "main.py") != BASE_SOURCE_SHA256:
        raise ObservedFailureScreenError("control package is not the fixed P1 policy")
    refs = arena._read_refs(Path(config).resolve())
    output.mkdir(parents=True, exist_ok=False)
    candidate_package = output / "candidate-package"
    package_manifest = materialize_observed_failure_variant_v1(
        source_package=source_package,
        output_package=candidate_package,
        candidate_id=candidate_id,
    )
    candidate, control, games = _build_pair_games(
        candidate_package=candidate_package,
        control_package=source_package,
        refs=refs,
        base_seed=base_seed,
    )
    smoke = arena.run_root_cg_game_v1(games[0].to_payload())
    if smoke.get("status") != "DONE":
        raise ObservedFailureScreenError(f"candidate smoke failed: {smoke.get('status')}")
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_id": candidate_id,
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "candidate_deck_sha256": _sha256(candidate_package / "deck.csv"),
        "control_deck_sha256": _sha256(source_package / "deck.csv"),
        "candidate_package_manifest": package_manifest,
        "reference_ids": list(refs),
        "requested_games": len(games),
        "games_per_opponent_seat": 2,
        "base_seed": base_seed,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "smoke": {"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps")},
        "public_features": ["opponent.active.hp", "opponent.active.maxHp", "attack.damage"],
        "authority": AUTHORITY_FALSE,
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
    delta = (
        float(candidate_summary["score_rate"] or 0.0)
        - float(control_summary["score_rate"] or 0.0)
    ) * 100.0
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": candidate_id,
        "candidate": candidate_summary,
        "control": control_summary,
        "candidate_delta_points": delta,
        "evaluator_summary": evaluation["summary"],
        "smoke": {"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps")},
        "public_features": ["opponent.active.hp", "opponent.active.maxHp", "attack.damage"],
        "authority": AUTHORITY_FALSE,
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "next_gate": "manual review; no automatic 384/768/longrun",
    }
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest.update(
        {
            "status": "COMPLETE",
            "summary_sha256": _sha256(summary_path),
            "faults": evaluation["summary"].get("faults"),
            "completed_games": evaluation["summary"].get("completed_games"),
        }
    )
    (output / "manifest-complete.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", choices=VARIANT_IDS, required=True)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    try:
        result = run_observed_failure_screen(
            candidate_id=args.candidate_id,
            source_package=args.source_package,
            output_root=args.output,
            config=args.config,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (ObservedFailureScreenError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
