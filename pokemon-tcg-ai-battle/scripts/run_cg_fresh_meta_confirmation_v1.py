#!/usr/bin/env python3
"""Confirm the P2 research parent on a newly added public medal holdout.

This runner is intentionally separate from the weekend split.  The 24 medal
opponents are public, smoke-ready, local-evaluation-only assets added to the
working pool after the P2 campaigns and frozen by the caller's freshness
audit.  The candidate is compared with the immutable P1 package on exactly
the same opponent, seat, repetition, and seed strata.  No promotion,
training, packaging, or submission authority is granted by this script.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402


SCHEMA = "cg-fresh-medal-meta-confirmation-v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
P1_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
P2_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-p1-cem-robust-v1"
    / "generation-0001/incumbent/package"
)
META_MANIFEST = _ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_ROOT = _ROOT / "opponents"

# Frozen at the freshness audit.  medal_0001 and medal_0004 already appeared
# in the earlier unused-meta dry run and are deliberately reserved/excluded.
FRESH_MEDAL_IDS = (
    "medal_0006_07bedfff",
    "medal_0007_dd63244c",
    "medal_0009_25393c12",
    "medal_0010_4bf59ca5",
    "medal_0014_f50fa3a2",
    "medal_0015_5e60b8c7",
    "medal_0016_706fa912",
    "medal_0018_053b4950",
    "medal_0019_df6f7443",
    "medal_0020_d6c573dd",
    "medal_0022_e40278fd",
    "medal_0190_f06bd3d5",
    "medal_0236_f7e1adfe",
    "medal_0282_78fc59fb",
    "medal_0312_a3079bb2",
    "medal_0346_5b509bae",
    "medal_0362_dae58a68",
    "medal_0378_7bcec45f",
    "medal_0427_3300b0c3",
    "medal_0460_3e769b3b",
    "medal_0509_203002de",
    "medal_0590_ff157aaa",
    "medal_2844_04dbbd93",
    "medal_2845_67cf83ea",
)

# Frozen reserve from the same public medal source.  These rows were outside
# the completed 24-opponent batch and remain available for a separate seed.
RESERVED_MEDAL_IDS = (
    "medal_2849_bd32b8f7",
    "medal_2850_952f9507",
    "medal_2851_8543bee4",
    "medal_2852_b31a602e",
    "medal_2855_fba1f87c",
    "medal_2856_458f87a5",
    "medal_2857_0c1054dc",
    "medal_2858_6644aa14",
    "medal_2859_02ea57ae",
    "medal_2862_65040fb4",
)
ALLOWED_FRESH_MEDAL_IDS = FRESH_MEDAL_IDS + RESERVED_MEDAL_IDS


class FreshMetaConfirmationError(ValueError):
    """Raised when the frozen fresh-meta contract cannot be proven."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FreshMetaConfirmationError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _meta_rows() -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(META_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreshMetaConfirmationError(f"cannot read meta manifest: {META_MANIFEST}") from exc
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise FreshMetaConfirmationError("meta manifest rows are missing")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("opponent_id"), str):
            result[str(row["opponent_id"])] = dict(row)
    return result


def validate_fresh_meta_ids(refs: Sequence[str] = FRESH_MEDAL_IDS) -> dict[str, object]:
    """Validate public/local-only/smoke-ready identity for the frozen refs."""

    refs = tuple(str(ref) for ref in refs)
    if not refs or len(refs) != len(set(refs)):
        raise FreshMetaConfirmationError("fresh meta refs must be non-empty and unique")
    if not set(refs).issubset(set(ALLOWED_FRESH_MEDAL_IDS)):
        raise FreshMetaConfirmationError("refs must be drawn from the frozen fresh/reserve medal holdout")
    pool = load_opponent_pool_v1(POOL_ROOT)
    meta = _meta_rows()
    missing = [ref for ref in refs if ref not in pool or ref not in meta]
    if missing:
        raise FreshMetaConfirmationError(f"fresh meta refs are not resolvable: {missing}")
    for ref in refs:
        pool_row = pool[ref]
        meta_row = meta[ref]
        if pool_row.source != "public" or pool_row.usage_boundary != "local_eval_only":
            raise FreshMetaConfirmationError(f"{ref} is not a public local-eval-only asset")
        if meta_row.get("source") != "public" or meta_row.get("evaluation_allowed") is not True:
            raise FreshMetaConfirmationError(f"{ref} is not evaluation-allowed public meta")
        if meta_row.get("training_allowed") is True or meta_row.get("submission_allowed") is True:
            raise FreshMetaConfirmationError(f"{ref} crosses a training/submission boundary")
        if meta_row.get("policy_sha256") != pool_row.policy_hash:
            raise FreshMetaConfirmationError(f"{ref} policy identity differs between manifests")
    return {
        "status": "PASS",
        "count": len(refs),
        "ids": list(refs),
        "pool_manifest_sha256": _sha256(POOL_ROOT / "pool_manifest.json"),
        "meta_manifest_sha256": _sha256(META_MANIFEST),
        "freshness_basis": "not present in cg-p2-* artifact audit at freeze",
    }


def _aggregate_arm(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    score = outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)
    by_seat: dict[str, dict[str, object]] = {}
    for seat in (0, 1):
        seat_rows = [row for row in rows if row.get("seat") == seat]
        seat_outcomes = Counter(str(row.get("outcome", "fault")) for row in seat_rows)
        seat_requested = len(seat_rows)
        seat_score = seat_outcomes.get("win", 0) + 0.5 * seat_outcomes.get("draw", 0)
        by_seat[str(seat)] = {
            "requested_games": seat_requested,
            "wins": seat_outcomes.get("win", 0),
            "draws": seat_outcomes.get("draw", 0),
            "losses": seat_outcomes.get("loss", 0),
            "faults": seat_outcomes.get("fault", 0),
            "score_rate": seat_score / seat_requested if seat_requested else None,
        }
    seat_rates = [by_seat[str(seat)]["score_rate"] for seat in (0, 1)]
    seat_gap = abs(float(seat_rates[0]) - float(seat_rates[1])) if all(value is not None for value in seat_rates) else None
    return {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": score / requested if requested else None,
        "seat": by_seat,
        "seat_gap": seat_gap,
    }


def build_fresh_meta_games(
    *,
    candidate_package: Path | str,
    control_package: Path | str,
    refs: Sequence[str] = FRESH_MEDAL_IDS,
    base_seed: int = 50100000,
    repetitions: int = 8,
) -> tuple:
    if type(repetitions) is not int or repetitions <= 0:
        raise FreshMetaConfirmationError("repetitions must be a positive integer")
    freshness = validate_fresh_meta_ids(refs)
    candidate_package = Path(candidate_package).resolve()
    control_package = Path(control_package).resolve()
    candidate = arena.ArenaArm(
        arm_id="p2_candidate",
        policy_id="cg-p2-research-parent-g01",
        policy_sha256=_sha256(candidate_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=candidate_package,
    )
    control = arena.ArenaArm(
        arm_id="p1_control",
        policy_id="cg-lethal-target-v1",
        policy_sha256=_sha256(control_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=control_package,
    )
    games = list(arena._build_games(
        arm=candidate,
        refs=tuple(refs),
        pool_root=POOL_ROOT,
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{base_seed}-candidate",
    ))
    games.extend(arena._build_games(
        arm=control,
        refs=tuple(refs),
        pool_root=POOL_ROOT,
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{base_seed}-control",
    ))
    for game in games:
        game.metadata["meta_provenance"] = "fresh_unused"  # type: ignore[index]
        game.metadata["fresh_meta_audit"] = freshness  # type: ignore[index]
    candidate_keys = {(game.metadata["pair_key"], game.seed) for game in games if game.metadata.get("arm_id") == candidate.arm_id}
    control_keys = {(game.metadata["pair_key"], game.seed) for game in games if game.metadata.get("arm_id") == control.arm_id}
    if candidate_keys != control_keys:
        raise FreshMetaConfirmationError("candidate/control strata are not paired")
    return tuple(games)


def summarize_rows(rows: Sequence[Mapping[str, object]], *, candidate_id: str, control_id: str) -> dict[str, object]:
    candidate_rows = [row for row in rows if row.get("metadata", {}).get("arm_id") == "p2_candidate"]
    control_rows = [row for row in rows if row.get("metadata", {}).get("arm_id") == "p1_control"]
    candidate = _aggregate_arm(candidate_rows)
    control = _aggregate_arm(control_rows)
    delta = (float(candidate["score_rate"] or 0.0) - float(control["score_rate"] or 0.0)) * 100.0
    faults = int(candidate["faults"]) + int(control["faults"])
    seat_safe = candidate["seat_gap"] is not None and float(candidate["seat_gap"]) <= 0.05
    decision = "PROMISING_CONFIRMATION" if faults == 0 and seat_safe and delta > 0.0 else "NOT_PROMOTABLE"
    return {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "candidate_id": candidate_id,
        "control_id": control_id,
        "meta_provenance": "fresh_unused",
        "candidate": candidate,
        "control": control,
        "candidate_delta_points": delta,
        "candidate_seat_safe": seat_safe,
        "faults": faults,
        "decision": decision,
        "authority": dict(AUTHORITY_FALSE),
    }


def run_confirmation(
    *,
    output_root: Path | str,
    candidate_package: Path | str = P2_PACKAGE,
    control_package: Path | str = P1_PACKAGE,
    base_seed: int = 50100000,
    repetitions: int = 8,
    workers: int = 12,
    worker_recycle_games: int = 64,
    refs: Sequence[str] = FRESH_MEDAL_IDS,
) -> dict[str, object]:
    if workers != 12 or worker_recycle_games != 64:
        raise FreshMetaConfirmationError("fresh-meta confirmation is sealed to workers=12/recycle=64")
    output = Path(output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output root is not empty: {output}")
    candidate_package = Path(candidate_package).resolve()
    control_package = Path(control_package).resolve()
    if _sha256(candidate_package / "deck.csv") != _sha256(control_package / "deck.csv"):
        raise FreshMetaConfirmationError("candidate/control deck identity differs")
    games = build_fresh_meta_games(
        candidate_package=candidate_package,
        control_package=control_package,
        refs=refs,
        base_seed=base_seed,
        repetitions=repetitions,
    )
    smoke_candidate = arena.run_root_cg_game_v1(games[0].to_payload())
    smoke_control = arena.run_root_cg_game_v1(next(game for game in games if game.metadata.get("arm_id") == "p1_control").to_payload())
    if smoke_candidate.get("status") != "DONE" or smoke_control.get("status") != "DONE":
        raise FreshMetaConfirmationError("candidate/control smoke failed")
    output.mkdir(parents=True, exist_ok=False)
    freshness = validate_fresh_meta_ids(refs)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_package": str(candidate_package),
        "candidate_policy_sha256": _sha256(candidate_package / "main.py"),
        "control_package": str(control_package),
        "control_policy_sha256": _sha256(control_package / "main.py"),
        "deck_sha256": _sha256(candidate_package / "deck.csv"),
        "reference_ids": list(refs),
        "meta_provenance": "fresh_unused",
        "freshness_audit": freshness,
        "base_seed": base_seed,
        "repetitions": repetitions,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "requested_games": len(games),
        "smoke": {"candidate": smoke_candidate.get("status"), "control": smoke_control.get("status")},
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    summary = summarize_rows(
        evaluation["rows"],
        candidate_id="cg-p2-research-parent-g01",
        control_id="cg-lethal-target-v1",
    )
    summary["requested_games"] = len(evaluation["rows"])
    summary["evaluator_summary"] = evaluation["summary"]
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "decision": summary["decision"], "summary_sha256": _sha256(summary_path)})
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-package", type=Path, default=P2_PACKAGE)
    parser.add_argument("--control-package", type=Path, default=P1_PACKAGE)
    parser.add_argument("--base-seed", type=int, default=50100000)
    parser.add_argument("--repetitions", type=int, default=8)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=64)
    parser.add_argument(
        "--refs",
        nargs="+",
        default=None,
        help="optional frozen fresh/reserve medal IDs; defaults to the completed 24-ID batch",
    )
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for heavy CABT execution")
    args = parser.parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing heavy fresh-meta confirmation without --execute")
    result = run_confirmation(
        output_root=args.output,
        candidate_package=args.candidate_package,
        control_package=args.control_package,
        base_seed=args.base_seed,
        repetitions=args.repetitions,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        refs=tuple(args.refs) if args.refs else FRESH_MEDAL_IDS,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
