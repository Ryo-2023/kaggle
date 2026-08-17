#!/usr/bin/env python3
"""Screen one bounded P2 variant against the fixed P1 cg policy.

The candidate and control share the exact 24-opponent/seat/repetition/seed
strata.  This is research-only, workers=12, and it never promotes or submits.
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

from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_p1_variant_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402


SCHEMA = "meta-specialist-cg-p1-variant-screen-v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
DEFAULT_SOURCE_PACKAGE = _ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


class CgP1VariantScreenError(ValueError):
    """Raised when a P1 variant screen cannot prove its identity contract."""


def _validate_screen_budget(*, workers: int, worker_recycle_games: int, games_per_opponent_seat: int) -> None:
    if workers != 12:
        raise CgP1VariantScreenError("P1 variant screen is sealed to workers=12")
    if games_per_opponent_seat not in {2, 8, 16}:
        raise CgP1VariantScreenError("P1 variant screen supports only 2, 8, or 16 games per opponent seat")
    expected_recycle = 16 if games_per_opponent_seat == 2 else 64
    if worker_recycle_games != expected_recycle:
        raise CgP1VariantScreenError(
            f"games_per_opponent_seat={games_per_opponent_seat} requires "
            f"worker_recycle_games={expected_recycle}"
        )


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgP1VariantScreenError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate(rows: list[dict[str, object]], *, include_seat: bool = True) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    result: dict[str, object] = {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)) / requested if requested else None,
    }
    if include_seat:
        result["seat"] = {
            str(seat): _aggregate(
                [row for row in rows if row.get("seat") == seat], include_seat=False
            )
            for seat in (0, 1)
        }
    return result


def finalize_p1_variant_screen(output_root: Path | str) -> dict[str, object]:
    """Seal an already-completed evaluator ledger without rerunning games."""

    output = Path(output_root).resolve()
    manifest_path = output / "manifest.json"
    ledger_path = output / "evaluation" / "ledger.jsonl"
    if not manifest_path.is_file() or not ledger_path.is_file():
        raise CgP1VariantScreenError(f"screen ledger is missing: {output}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [
        dict(json.loads(line))
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    requested = int(manifest.get("requested_games", -1))
    if len(rows) != requested:
        raise CgP1VariantScreenError(
            f"ledger rows {len(rows)} do not match requested {requested}"
        )
    arms = {str(row.get("metadata", {}).get("arm_id")) for row in rows}
    expected_arms = {"cg_p1_variant_candidate", "cg_p1_fixed_control"}
    if arms != expected_arms:
        raise CgP1VariantScreenError(f"unexpected screen arms: {sorted(arms)}")
    grouped = {
        arm_id: [row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id]
        for arm_id in sorted(expected_arms)
    }
    candidate_keys = {
        (str(row.get("metadata", {}).get("pair_key")), row.get("seed"))
        for row in grouped["cg_p1_variant_candidate"]
    }
    control_keys = {
        (str(row.get("metadata", {}).get("pair_key")), row.get("seed"))
        for row in grouped["cg_p1_fixed_control"]
    }
    if candidate_keys != control_keys:
        raise CgP1VariantScreenError("candidate/control paired strata changed")
    candidate_summary = _aggregate(grouped["cg_p1_variant_candidate"])
    control_summary = _aggregate(grouped["cg_p1_fixed_control"])
    delta = (
        float(candidate_summary["score_rate"] or 0.0)
        - float(control_summary["score_rate"] or 0.0)
    ) * 100.0
    smoke = json.loads((output / "smoke.json").read_text(encoding="utf-8")) if (output / "smoke.json").is_file() else {}
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": manifest.get("candidate_id"),
        "candidate": candidate_summary,
        "control": control_summary,
        "candidate_delta_points": delta,
        "evaluator_summary": json.loads((output / "evaluation" / "summary.json").read_text(encoding="utf-8")),
        "smoke": smoke,
        "paired_strata_count": len(candidate_keys),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "next_gate": "manual review; no automatic common24/384/768",
    }
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest.update({
        "status": "COMPLETE",
        "summary_sha256": _sha256(summary_path),
        "faults": summary["evaluator_summary"].get("faults"),
        "completed_games": summary["evaluator_summary"].get("completed_games"),
    })
    (output / "manifest-complete.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary, "manifest": manifest}


def _build_pair_games(*, candidate_package: Path, control_package: Path, refs: tuple[str, ...], base_seed: int, games_per_opponent_seat: int):
    candidate = arena.ArenaArm(
        arm_id="cg_p1_variant_candidate",
        policy_id="cg-p1-variant-candidate",
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
        refs=refs,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}-candidate",
    ))
    games.extend(arena._build_games(
        arm=control,
        refs=refs,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}-control",
    ))
    candidate_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == candidate.arm_id}
    control_keys = {(str(g.metadata["pair_key"]), g.seed) for g in games if g.metadata.get("arm_id") == control.arm_id}
    if candidate_keys != control_keys:
        raise CgP1VariantScreenError("candidate/control strata differ")
    return candidate, control, tuple(games)


def run_p1_variant_screen(
    *,
    candidate_id: str,
    source_package: Path,
    output_root: Path,
    config: Path = DEFAULT_CONFIG,
    base_seed: int = 40410000,
    workers: int = 12,
    worker_recycle_games: int = 16,
    games_per_opponent_seat: int = 2,
) -> dict[str, object]:
    if candidate_id not in VARIANT_IDS:
        raise CgP1VariantScreenError(f"unknown candidate_id: {candidate_id}")
    _validate_screen_budget(
        workers=workers,
        worker_recycle_games=worker_recycle_games,
        games_per_opponent_seat=games_per_opponent_seat,
    )
    source = Path(source_package).resolve()
    control_package = source
    if _sha256(source / "main.py") != BASE_SOURCE_SHA256:
        raise CgP1VariantScreenError("control package is not the fixed P1 policy")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"screen output exists: {output}")
    refs = arena._read_refs(Path(config).resolve())
    output.mkdir(parents=True, exist_ok=False)
    candidate_package = output / "candidate-package"
    package_manifest = materialize_p1_variant_package_v1(
        source_package=source,
        output_package=candidate_package,
        candidate_id=candidate_id,
    )
    candidate, control, games = _build_pair_games(
        candidate_package=candidate_package,
        control_package=control_package,
        refs=refs,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
    )
    smoke = arena.run_root_cg_game_v1(games[0].to_payload())
    (output / "smoke.json").write_text(json.dumps({"status": smoke.get("status"), "winner": smoke.get("winner"), "steps": smoke.get("steps"), "candidate_id": candidate_id}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if smoke.get("status") != "DONE":
        raise CgP1VariantScreenError(f"candidate smoke failed: {smoke.get('status')}")
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_id": candidate_id,
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "candidate_policy_sha256": candidate.policy_sha256,
        "control_policy_sha256": control.policy_sha256,
        "candidate_deck_sha256": _sha256(candidate_package / "deck.csv"),
        "control_deck_sha256": _sha256(control_package / "deck.csv"),
        "candidate_package_manifest": package_manifest,
        "reference_ids": list(refs),
        "requested_games": len(games),
        "games_per_opponent_seat": games_per_opponent_seat,
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
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "next_gate": "manual review; no automatic common24/384/768",
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
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=40410000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--games-per-opponent-seat",
        type=int,
        choices=(2, 8, 16),
        default=2,
        help="paired games per opponent and seat; 8/16 are reserved for independent confirmation",
    )
    parser.add_argument("--worker-recycle-games", type=int, choices=(16, 64), default=None)
    args = parser.parse_args(argv)
    recycle_games = (
        args.worker_recycle_games
        if args.worker_recycle_games is not None
        else (16 if args.games_per_opponent_seat == 2 else 64)
    )
    try:
        result = run_p1_variant_screen(
            candidate_id=args.candidate_id,
            source_package=args.source_package,
            output_root=args.output,
            config=args.config,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=recycle_games,
            games_per_opponent_seat=args.games_per_opponent_seat,
        )
    except (CgP1VariantScreenError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
