#!/usr/bin/env python3
"""Independently validate previously screened, self-owned source candidates.

This is a research-only bridge between source-side CEM and a downstream policy
search.  It copies the candidate packages into a new immutable validation
root, evaluates each candidate against the fixed self-owned reference
portfolio with a disjoint seed namespace, and records only candidates that
pass both the prior screen and the fresh robust gate.  It never changes the
production opponent pool, BestKnown, Champion, submission package, or Kaggle
state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.opponent_ingest.robust_adversarial_source_cem_v1 import (  # noqa: E402
    aggregate_portfolio_source_rows_v1,
)
from scripts import run_robust_adversarial_source_cem_v1 as runner  # noqa: E402


SCHEMA_V1 = "meta-specialist-robust-source-candidate-validation-v1"


class ValidationError(ValueError):
    """Raised when a candidate validation root cannot be sealed safely."""


def _parse_candidate(value: str) -> tuple[str, Path, float]:
    """Parse ``ID=PACKAGE_ROOT[,SCREEN_MEAN]``."""

    if "=" not in value:
        raise ValidationError("candidate must be ID=PACKAGE_ROOT[,SCREEN_MEAN]")
    candidate_id, raw = value.split("=", 1)
    if "," in raw:
        raw_path, raw_mean = raw.rsplit(",", 1)
        try:
            screen_mean = float(raw_mean)
        except ValueError as exc:
            raise ValidationError(f"invalid screen mean: {raw_mean!r}") from exc
    else:
        raw_path, screen_mean = raw, 0.0
    if runner._ID.fullmatch(candidate_id) is None:
        raise ValidationError(f"invalid candidate id: {candidate_id!r}")
    package = Path(raw_path)
    if not package.is_absolute():
        package = _ROOT / package
    return candidate_id, package.resolve(), screen_mean


def _copy_candidate(source: Path, target: Path) -> dict[str, object]:
    if not source.is_dir() or source.is_symlink():
        raise ValidationError(f"candidate package is not a regular directory: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValidationError(f"candidate package contains symlink: {path}")
    if target.exists():
        raise ValidationError(f"candidate target already exists: {target}")
    shutil.copytree(source, target)
    for name in ("main.py", "deck.csv"):
        if not (target / name).is_file():
            raise ValidationError(f"candidate package is incomplete: {source}")
    return {
        "source_package": str(target.resolve()),
        "source_origin": str(source),
        "policy_sha256": runner._sha256(target / "main.py"),
        "canonical_deck_hash": runner._deck_hash(target / "deck.csv"),
    }


def _promotion_gate(*, screen_mean: float, validation: Mapping[str, object]) -> bool:
    """Require prior screen evidence plus an independent robust validation."""

    return (
        screen_mean > 0.5
        and bool(validation.get("valid"))
        and float(validation.get("mean_source_score", 0.0)) > 0.5
        and float(validation.get("min_reference_score", 0.0)) >= 0.25
        and bool(validation.get("seat_safe"))
    )


def validate_candidates(
    *,
    output_root: Path,
    candidates: Sequence[tuple[str, Path, float]],
    reference_specs: Sequence[runner.ReferenceSpec],
    base_seed: int,
    games_per_reference_seat: int,
    workers: int,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(output_root)
    if not candidates:
        raise ValidationError("at least one candidate is required")
    if games_per_reference_seat < 1:
        raise ValidationError("games_per_reference_seat must be positive")
    output_root.mkdir(parents=True)
    pool_root, reference_ids, reference_rows = runner._make_reference_pool(output_root, reference_specs)
    candidate_root = output_root / "candidate_pool"
    candidate_root.mkdir()

    rows: list[dict[str, object]] = []
    seen_policy: set[str] = set()
    for candidate_id, source, screen_mean in candidates:
        copied = _copy_candidate(source, candidate_root / candidate_id)
        policy_sha = str(copied["policy_sha256"])
        if policy_sha in seen_policy:
            raise ValidationError(f"duplicate policy SHA across candidates: {policy_sha}")
        seen_policy.add(policy_sha)
        rows.append(
            {
                "candidate_id": candidate_id,
                "screen_mean_source_score": screen_mean,
                "screen_source_cem_only": True,
                **copied,
            }
        )

    games: list[object] = []
    for index, row in enumerate(rows):
        games.extend(
            runner._candidate_games(
                package=Path(str(row["source_package"])),
                candidate_id=str(row["candidate_id"]),
                pool_root=pool_root,
                refs=reference_ids,
                base_seed=base_seed + index * 10_000,
                games_per_reference_seat=games_per_reference_seat,
                block_id=f"robust-source-independent-validation-{row['candidate_id']}",
            )
        )

    manifest = {
        "schema_version": SCHEMA_V1,
        "seed_namespace": "robust-source-independent-validation-20260820",
        "base_seed": base_seed,
        "games_per_reference_seat": games_per_reference_seat,
        "reference_ids": list(reference_ids),
        "reference_rows": reference_rows,
        "candidates": rows,
        "requested_games": len(games),
        "fresh": True,
        "unused_before_downstream_policy_run": True,
        "research_only": True,
    }
    runner._write_json_new(output_root / "validation_manifest.json", manifest)
    evaluation = runner._evaluate(games, output_root / "evaluation", workers=workers)

    result_rows: list[dict[str, object]] = []
    for row in rows:
        aggregate = aggregate_portfolio_source_rows_v1(
            evaluation["rows"],
            candidate_policy_id=str(row["candidate_id"]),
            reference_ids=reference_ids,
            seat_gap_limit=0.25,
        )
        gate = _promotion_gate(screen_mean=float(row["screen_mean_source_score"]), validation=aggregate)
        result_rows.append({**row, "validation": aggregate, "promotion_gate": gate})

    selected = [str(row["candidate_id"]) for row in result_rows if row["promotion_gate"]]
    result = {
        "schema_version": SCHEMA_V1,
        "status": "COMPLETE",
        "manifest_sha256": runner._sha256(output_root / "validation_manifest.json"),
        "evaluation_summary": evaluation["summary"],
        "candidates": result_rows,
        "selected_ids": selected,
        "freshness_basis": "screen-only-source-CEM-candidates-rechecked-on-disjoint-seed-namespace-before-downstream-policy-search",
        "fresh": True,
        "unused_before_downstream_policy_run": True,
        "research_only": True,
    }
    runner._write_json_new(output_root / "validation_result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", action="append", required=True, help="ID=PACKAGE_ROOT[,SCREEN_MEAN]")
    parser.add_argument("--reference", action="append", required=True, help="ID=PACKAGE_ROOT")
    parser.add_argument("--base-seed", type=int, default=2026082001)
    parser.add_argument("--games-per-reference-seat", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        candidates = tuple(_parse_candidate(value) for value in args.candidate)
        refs = tuple(runner._parse_reference(value) for value in args.reference)
        result = validate_candidates(
            output_root=args.output,
            candidates=candidates,
            reference_specs=refs,
            base_seed=args.base_seed,
            games_per_reference_seat=args.games_per_reference_seat,
            workers=args.workers,
        )
    except (ValidationError, runner.RobustSourceCampaignError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    summary = []
    for row in result["candidates"]:
        aggregate = row["validation"]
        summary.append(
            {
                "candidate_id": row["candidate_id"],
                "mean_source_score": aggregate["mean_source_score"],
                "min_reference_score": aggregate["min_reference_score"],
                "max_seat_gap": aggregate["max_seat_gap"],
                "valid": aggregate["valid"],
                "promotion_gate": row["promotion_gate"],
            }
        )
    print(json.dumps({"status": result["status"], "requested_games": result["evaluation_summary"]["requested_games"], "faults": result["evaluation_summary"]["faults"], "selected_ids": result["selected_ids"], "rows": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
