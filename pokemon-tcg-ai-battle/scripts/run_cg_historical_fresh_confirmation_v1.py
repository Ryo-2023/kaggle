#!/usr/bin/env python3
"""Run a paired, research-only confirmation on an unused historical META_FINAL.

This runner is deliberately separate from the public-medal confirmation path.
It accepts only a hash-bound staged historical split and a fresh-meta manifest,
keeps the immutable cg P1 package as control, and grants no promotion or
submission authority.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split  # noqa: E402
from scripts import run_cg_p1_cem_v1 as cem  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import run_parallel_cabt_evaluation  # noqa: E402


SCHEMA = "cg-historical-fresh-confirmation-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


class HistoricalFreshConfirmationError(ValueError):
    """Raised when a staged historical confirmation contract is invalid."""


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise HistoricalFreshConfirmationError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate_arm_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate outcomes while retaining faults and per-seat rates."""

    selected = list(rows)
    outcomes = Counter(str(row.get("outcome", "fault")) for row in selected)
    by_seat: dict[str, dict[str, object]] = {}
    for seat in (0, 1):
        seat_rows = [row for row in selected if row.get("seat") == seat]
        seat_outcomes = Counter(str(row.get("outcome", "fault")) for row in seat_rows)
        requested = len(seat_rows)
        score = seat_outcomes["win"] + 0.5 * seat_outcomes["draw"]
        by_seat[str(seat)] = {
            "requested_games": requested,
            "wins": seat_outcomes["win"],
            "draws": seat_outcomes["draw"],
            "losses": seat_outcomes["loss"],
            "faults": seat_outcomes["fault"],
            "score_rate": score / requested if requested else None,
        }
    requested = len(selected)
    score = outcomes["win"] + 0.5 * outcomes["draw"]
    seat_rates = [by_seat[str(seat)]["score_rate"] for seat in (0, 1)]
    seat_gap = (
        abs(float(seat_rates[0]) - float(seat_rates[1]))
        if all(value is not None for value in seat_rates)
        else None
    )
    return {
        "requested_games": requested,
        "wins": outcomes["win"],
        "draws": outcomes["draw"],
        "losses": outcomes["loss"],
        "faults": outcomes["fault"],
        "score_rate": score / requested if requested else None,
        "by_seat": by_seat,
        "seat_gap": seat_gap,
    }


def _fresh_ids(fresh_meta_path: Path) -> set[str]:
    try:
        payload = json.loads(fresh_meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoricalFreshConfirmationError(f"cannot read fresh meta: {fresh_meta_path}") from exc
    references = payload.get("references") if isinstance(payload, Mapping) else None
    if not isinstance(references, list):
        raise HistoricalFreshConfirmationError("fresh meta references are missing")
    result = {str(row.get("id")) for row in references if isinstance(row, Mapping) and row.get("id")}
    if not result:
        raise HistoricalFreshConfirmationError("fresh meta references are empty")
    return result


def run_confirmation(
    *,
    output_root: Path | str,
    pool_root: Path | str,
    split_path: Path | str,
    fresh_meta_path: Path | str,
    candidate_package: Path | str,
    control_package: Path | str,
    candidate_id: str,
    config_sha256: str,
    reference_ids: Sequence[str] | None = None,
    base_seed: int = 20260926,
    repetitions: int = 16,
    workers: int = 12,
    worker_recycle_games: int = 64,
) -> dict[str, object]:
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite confirmation root: {output}")
    pool = Path(pool_root).resolve()
    split_file = Path(split_path).resolve()
    fresh_file = Path(fresh_meta_path).resolve()
    candidate = Path(candidate_package).resolve()
    control = Path(control_package).resolve()
    if repetitions <= 0 or workers <= 0 or worker_recycle_games <= 0:
        raise HistoricalFreshConfirmationError("repetitions, workers, and worker recycle must be positive")
    for package in (candidate, control):
        if not (package / "main.py").is_file() or not (package / "deck.csv").is_file():
            raise HistoricalFreshConfirmationError(f"candidate package is incomplete: {package}")
    pool_manifest = pool / "pool_manifest.json"
    if not pool_manifest.is_file():
        raise HistoricalFreshConfirmationError(f"pool manifest is missing: {pool_manifest}")
    split = load_weekend_split(split_file, verify_sources=True)
    final_ids = set(split.ids("META_FINAL"))
    refs = tuple(str(value) for value in (reference_ids or tuple(final_ids)))
    if not refs or len(refs) != len(set(refs)):
        raise HistoricalFreshConfirmationError("reference ids must be non-empty and unique")
    if not set(refs).issubset(final_ids):
        raise HistoricalFreshConfirmationError("confirmation refs must be drawn from META_FINAL")
    missing_fresh = sorted(set(refs) - _fresh_ids(fresh_file))
    if missing_fresh:
        raise HistoricalFreshConfirmationError(f"refs are absent from fresh meta: {missing_fresh}")

    output.mkdir(parents=True, exist_ok=False)
    games = cem._pair_games(
        candidate_package=candidate,
        candidate_id=candidate_id,
        config_sha256=config_sha256,
        split=split,
        refs=refs,
        games_per_opponent_seat=repetitions,
        base_seed=base_seed,
        include_control=True,
        block_id=f"{SCHEMA}-{base_seed}",
        split_name="META_FINAL",
        control_package=control,
        pool_root=pool,
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    rows = list(result["rows"])
    candidate_rows = [
        row for row in rows if isinstance(row.get("metadata"), Mapping) and row["metadata"].get("arm_role") == "candidate"
    ]
    control_rows = [
        row for row in rows if isinstance(row.get("metadata"), Mapping) and row["metadata"].get("arm_role") == "p1_control"
    ]
    candidate_stats = aggregate_arm_rows(candidate_rows)
    control_stats = aggregate_arm_rows(control_rows)
    delta = (float(candidate_stats["score_rate"] or 0.0) - float(control_stats["score_rate"] or 0.0)) * 100.0
    faults = int(candidate_stats["faults"]) + int(control_stats["faults"])
    seat_safe = candidate_stats["seat_gap"] is not None and float(candidate_stats["seat_gap"]) <= 0.05
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "candidate_id": candidate_id,
        "candidate_policy_sha256": sha256_file(candidate / "main.py"),
        "control_id": "cg-lethal-target-v1",
        "control_policy_sha256": sha256_file(control / "main.py"),
        "reference_ids": list(refs),
        "pool_root": str(pool),
        "pool_manifest_sha256": sha256_file(pool_manifest),
        "split_path": str(split_file),
        "split_sha256": sha256_file(split_file),
        "fresh_meta_path": str(fresh_file),
        "fresh_meta_sha256": sha256_file(fresh_file),
        "base_seed": base_seed,
        "games_per_opponent_seat": repetitions,
        "candidate": candidate_stats,
        "control": control_stats,
        "candidate_delta_points": delta,
        "candidate_seat_safe": seat_safe,
        "faults": faults,
        "decision": "PROMISING_CONFIRMATION" if faults == 0 and seat_safe and delta > 0.0 else "NOT_PROMOTABLE",
        "evaluator_summary": result["summary"],
        "authority": dict(AUTHORITY_FALSE),
    }
    manifest = {
        "schema_version": "cg-historical-fresh-confirmation-manifest-v1",
        "research_only": True,
        "candidate_id": candidate_id,
        "candidate_package": str(candidate),
        "control_package": str(control),
        "reference_ids": list(refs),
        "base_seed": base_seed,
        "games_per_opponent_seat": repetitions,
        "requested_games": len(games),
        "completed_games": len(rows),
        "pool_manifest_sha256": summary["pool_manifest_sha256"],
        "fresh_meta_sha256": summary["fresh_meta_sha256"],
        "split_sha256": summary["split_sha256"],
        "candidate_policy_sha256": summary["candidate_policy_sha256"],
        "control_policy_sha256": summary["control_policy_sha256"],
        "evaluator_summary": result["summary"],
        "authority": dict(AUTHORITY_FALSE),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--fresh-meta", type=Path, required=True)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--reference-id", action="append", dest="reference_ids")
    parser.add_argument("--base-seed", type=int, default=20260926)
    parser.add_argument("--repetitions", type=int, default=16)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=64)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args(argv)
    summary = run_confirmation(
        output_root=args.output,
        pool_root=args.pool_root,
        split_path=args.split,
        fresh_meta_path=args.fresh_meta,
        candidate_package=args.candidate_package,
        control_package=args.control_package,
        candidate_id=args.candidate_id,
        config_sha256=args.config_sha256,
        reference_ids=args.reference_ids,
        base_seed=args.base_seed,
        repetitions=args.repetitions,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
