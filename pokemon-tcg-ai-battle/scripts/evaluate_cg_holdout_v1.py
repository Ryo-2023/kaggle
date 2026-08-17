#!/usr/bin/env python3
"""Run one hash-bound cg candidate/control holdout block.

The runner is deliberately separate from CEM search: it consumes one named
split only after the caller has fixed a fresh-meta manifest and writes an
immutable research-only summary.  It never promotes, trains, packages, or
submits a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import build_fresh_meta_batch_v1
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_cg_p1_cem_v1 import (
    _control_identity,
    _sha256,
    build_paired_games,
    candidate_result_from_rows,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-cg-holdout-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


def _write_new(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _candidate_config(package: Path) -> tuple[str, P1ParameterConfig]:
    manifest = package / "cg_p1_cem_candidate_manifest.json"
    if not manifest.is_file():
        raise ValueError(f"candidate manifest is missing: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "cg-p1-cem-candidate-v1":
        raise ValueError("candidate manifest schema mismatch")
    candidate_id = payload.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id is missing")
    config = P1ParameterConfig.from_mapping(payload.get("config", {}))
    if payload.get("policy_sha256") != _sha256(package / "main.py"):
        raise ValueError("candidate policy hash mismatch")
    return candidate_id, config


def _decision(summary: Mapping[str, object]) -> tuple[str, float | None, float | None]:
    candidate = summary.get("candidate")
    if not isinstance(candidate, Mapping):
        return "INVALID_FAULT", None, None
    delta = summary.get("delta_objective")
    delta_value = float(delta) if isinstance(delta, (int, float)) and not isinstance(delta, bool) else None
    seat_rates = candidate.get("seat_rates")
    seat_gap = None
    if isinstance(seat_rates, Mapping) and all(isinstance(seat_rates.get(key), (int, float)) for key in ("0", "1")):
        seat_gap = abs(float(seat_rates["0"]) - float(seat_rates["1"]))
    faults = candidate.get("faults", 0)
    if isinstance(faults, bool) or not isinstance(faults, int) or faults < 0:
        return "INVALID_FAULT", delta_value, seat_gap
    if faults != 0:
        return "INVALID_FAULT", delta_value, seat_gap
    if delta_value is not None and delta_value > 0.0 and seat_gap is not None and seat_gap <= 0.05:
        return "POSITIVE_CONTINUE", delta_value, seat_gap
    return "NOT_PROMOTABLE", delta_value, seat_gap


def run_holdout(
    *,
    candidate_package: Path | str,
    control_package: Path | str,
    split_path: Path | str,
    fresh_meta_path: Path | str,
    pool_root: Path | str,
    split_name: str,
    output_root: Path | str,
    base_seed: int,
    games_per_opponent_seat: int = 8,
    workers: int = 12,
) -> dict[str, object]:
    if split_name not in {"META_DEV", "META_FINAL"}:
        raise ValueError("holdout split must be META_DEV or META_FINAL")
    if type(base_seed) is not int or base_seed <= 0:
        raise ValueError("base_seed must be a positive integer")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    candidate = Path(candidate_package).resolve()
    control = Path(control_package).resolve()
    pool = Path(pool_root).resolve()
    split = load_weekend_split(split_path, verify_sources=True)
    fresh = build_fresh_meta_batch_v1(
        manifest_path=fresh_meta_path,
        pool_manifest_path=pool / "pool_manifest.json",
    )
    refs = split.ids(split_name)
    if not set(refs).issubset(set(fresh.reference_ids)):
        raise ValueError(f"{split_name} references are not all in fresh meta batch")
    candidate_id, config = _candidate_config(candidate)
    games = build_paired_games(
        candidate_package=candidate,
        candidate_id=candidate_id,
        config_sha256=config.config_sha256(),
        split=split,
        train_block_index=0,
        games_per_opponent_seat=games_per_opponent_seat,
        base_seed=base_seed,
        include_control=True,
        refs_override=refs,
        split_name=split_name,
        control_package=control,
        block_id=f"cg-holdout-{split_name.lower()}-{candidate_id}",
        pool_root=pool,
    )
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    evaluation = run_parallel_cabt_evaluation(
        tuple(games),
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=16,
        overwrite=False,
    )
    control_id, control_sha = _control_identity(control)
    summary = candidate_result_from_rows(
        evaluation["rows"],
        candidate_policy_id=candidate_id,
        control_policy_id=control_id,
        weights=split.weights(split_name),
        config=config,
        candidate_id=candidate_id,
    )
    decision, delta, seat_gap = _decision(summary)
    result = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "decision": decision,
        "candidate_delta": delta,
        "candidate_seat_gap": seat_gap,
        "candidate_id": candidate_id,
        "candidate_policy_sha256": _sha256(candidate / "main.py"),
        "control_policy_id": control_id,
        "control_policy_sha256": control_sha,
        "candidate": summary,
        "split_name": split_name,
        "reference_ids": list(refs),
        "fresh_meta_batch_id": fresh.batch_id,
        "fresh_meta_manifest_sha256": fresh.manifest_sha256,
        "pool_manifest_sha256": _sha256(pool / "pool_manifest.json"),
        "split_sha256": split.config_sha256,
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    _write_new(output / "summary.json", result)
    manifest = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "summary_sha256": hashlib.sha256((output / "summary.json").read_bytes()).hexdigest(),
        "evaluation": str(output / "evaluation"),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    _write_new(output / "manifest.json", manifest)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--fresh-meta", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--split-name", choices=("META_DEV", "META_FINAL"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for CABT execution")
    args = parser.parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing holdout CABT without --execute")
    result = run_holdout(
        candidate_package=args.candidate_package,
        control_package=args.control_package,
        split_path=args.split,
        fresh_meta_path=args.fresh_meta,
        pool_root=args.pool_root,
        split_name=args.split_name,
        output_root=args.output,
        base_seed=args.base_seed,
        games_per_opponent_seat=args.games_per_opponent_seat,
        workers=args.workers,
    )
    print(json.dumps({key: result[key] for key in ("status", "decision", "candidate_delta", "candidate_seat_gap", "reference_ids")}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
