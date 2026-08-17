"""Run a file-backed, research-only confirmation for one cg CEM candidate.

This bridge exists for the post-CEM gate: it compares one self-owned
parameterized package with a fixed control on identical META_TRAIN opponent,
seat, repetition, and seed strata.  It never changes Champion state and never
submits an artifact.  The module is intentionally file-backed so Python's
``multiprocessing`` spawn mode can import the main module during a heavy run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import aggregate_candidate_rows  # noqa: E402
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import WeekendSplit, load_weekend_split  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1  # noqa: E402
from scripts.run_cg_p1_cem_v1 import (  # noqa: E402
    P1_PACKAGE,
    _control_identity,
    _evaluate_games,
    build_paired_games,
    candidate_result_from_rows,
)


SCHEMA = "cg-p1-cem-candidate-confirmation-v1"
DEFAULT_SPLIT = _ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"
DEFAULT_CONTROL = _ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
DEFAULT_BASE_SEED = 480962000
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


class ConfirmationError(ValueError):
    """Raised when a confirmation cannot be bound to immutable artifacts."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ConfirmationError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_candidate_config(path: Path | str) -> P1ParameterConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ConfirmationError("candidate config must be a mapping")
    values = payload.get("config", payload.get("parameters", payload))
    if not isinstance(values, Mapping):
        raise ConfirmationError("candidate config mapping is missing")
    return P1ParameterConfig.from_mapping(values)


def build_confirmation_games(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config_sha256: str,
    split: WeekendSplit,
    control_package: Path | str,
    reference_ids: Sequence[str],
    base_seed: int,
    repetitions: int,
) -> tuple[object, ...]:
    """Build a paired candidate/control schedule with an identical seed grid."""

    if not isinstance(candidate_id, str) or not candidate_id:
        raise ConfirmationError("candidate_id must be non-empty")
    if not isinstance(config_sha256, str) or len(config_sha256) != 64:
        raise ConfirmationError("config_sha256 must be a SHA-256 hex string")
    if type(base_seed) is not int or base_seed <= 0:
        raise ConfirmationError("base_seed must be a positive integer")
    if type(repetitions) is not int or repetitions <= 0:
        raise ConfirmationError("repetitions must be a positive integer")
    refs = tuple(str(item) for item in reference_ids)
    if not refs or len(set(refs)) != len(refs):
        raise ConfirmationError("reference_ids must be non-empty and unique")
    games = build_paired_games(
        candidate_package=Path(candidate_package),
        candidate_id=candidate_id,
        config_sha256=config_sha256,
        split=split,
        train_block_index=0,
        games_per_opponent_seat=repetitions,
        base_seed=base_seed,
        include_control=True,
        refs_override=refs,
        split_name="META_TRAIN",
        control_package=Path(control_package),
        block_id=f"cg-p1-cem-confirm-{candidate_id}-{base_seed}",
    )
    candidate = [game for game in games if game.metadata.get("arm_role") == "candidate"]
    control = [game for game in games if game.metadata.get("arm_role") == "p1_control"]
    candidate_pairs = {(game.metadata.get("pair_key"), game.seed) for game in candidate}
    control_pairs = {(game.metadata.get("pair_key"), game.seed) for game in control}
    if not candidate or len(candidate) != len(control) or candidate_pairs != control_pairs:
        raise ConfirmationError("candidate/control confirmation strata differ")
    return tuple(games)


def summarize_confirmation_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_id: str,
    control_id: str,
    weights: Mapping[str, float],
    config: P1ParameterConfig,
) -> dict[str, object]:
    """Summarize a completed confirmation and fail closed on quality gates."""

    candidate_rows = [row for row in rows if row.get("policy_id") == candidate_id]
    control_rows = [row for row in rows if row.get("policy_id") == control_id]
    if not candidate_rows or len(candidate_rows) != len(control_rows):
        raise ConfirmationError("candidate/control rows are missing or unbalanced")
    candidate = aggregate_candidate_rows(candidate_rows, weights=weights)
    control = aggregate_candidate_rows(control_rows, weights=weights)
    candidate_seats = candidate.get("seat_rates", {})
    seat_gap = None
    if isinstance(candidate_seats, Mapping) and all(
        isinstance(candidate_seats.get(seat), (int, float)) for seat in ("0", "1")
    ):
        seat_gap = abs(float(candidate_seats["0"]) - float(candidate_seats["1"]))
    delta = float(candidate["objective"]) - float(control["objective"])
    faults = int(candidate.get("faults", 0)) + int(control.get("faults", 0))
    seat_safe = seat_gap is not None and seat_gap <= 0.05
    decision = "PROMISING_CONFIRMATION" if faults == 0 and seat_safe and delta > 0 else "NOT_PROMOTABLE"
    return {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "candidate_id": candidate_id,
        "control_id": control_id,
        "config_sha256": config.config_sha256(),
        "candidate": candidate,
        "control": control,
        "delta_objective": delta,
        "delta_points": delta * 100.0,
        "candidate_seat_gap": seat_gap,
        "candidate_seat_safe": seat_safe,
        "faults": faults,
        "decision": decision,
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }


def run_confirmation(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    candidate_config: P1ParameterConfig,
    output_root: Path | str,
    split_path: Path | str = DEFAULT_SPLIT,
    control_package: Path | str = DEFAULT_CONTROL,
    reference_ids: Sequence[str] | None = None,
    base_seed: int = DEFAULT_BASE_SEED,
    repetitions: int = 16,
    workers: int = 12,
) -> dict[str, object]:
    output = Path(output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output root is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    split = load_weekend_split(split_path, verify_sources=True)
    refs = tuple(reference_ids) if reference_ids is not None else split.ids("META_TRAIN")
    candidate_path = Path(candidate_package).resolve()
    control_path = Path(control_package).resolve()
    candidate_sha = _sha256(candidate_path / "main.py")
    control_id, control_sha = _control_identity(control_path)
    games = build_confirmation_games(
        candidate_package=candidate_path,
        candidate_id=candidate_id,
        config_sha256=candidate_config.config_sha256(),
        split=split,
        control_package=control_path,
        reference_ids=refs,
        base_seed=base_seed,
        repetitions=repetitions,
    )
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "research_only": True,
        "candidate_id": candidate_id,
        "candidate_package": str(candidate_path),
        "candidate_policy_sha256": candidate_sha,
        "candidate_config_sha256": candidate_config.config_sha256(),
        "control_package": str(control_path),
        "control_policy_id": control_id,
        "control_policy_sha256": control_sha,
        "split_path": str(Path(split_path).resolve()),
        "split_sha256": split.config_sha256,
        "split_name": "META_TRAIN",
        "reference_ids": list(refs),
        "base_seed": base_seed,
        "repetitions": repetitions,
        "requested_games": len(games),
        "workers": workers,
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    try:
        evaluation = _evaluate_games(games, output / "evaluation", workers)
        summary = summarize_confirmation_rows(
            evaluation["rows"],
            candidate_id=candidate_id,
            control_id=control_id,
            weights=split.weights("META_TRAIN"),
            config=candidate_config,
        )
        summary["requested_games"] = len(evaluation["rows"])
        summary["evaluator_summary"] = evaluation["summary"]
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        manifest.update(
            {
                "status": "COMPLETE",
                "decision": summary["decision"],
                "summary_sha256": _sha256(output / "summary.json"),
                "evaluation_summary_sha256": _sha256(output / "evaluation/summary.json"),
            }
        )
        (output / "manifest-complete.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return {"status": "COMPLETE", "output_root": str(output), "summary": summary}
    except Exception as exc:
        stop = {
            "schema_version": SCHEMA,
            "status": "STOPPED",
            "reason": f"{type(exc).__name__}: {exc}",
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        (output / "stop.json").write_text(
            json.dumps(stop, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-config-json", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--repetitions", type=int, default=16)
    parser.add_argument("--workers", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_confirmation(
            candidate_package=args.candidate_package,
            candidate_id=args.candidate_id,
            candidate_config=load_candidate_config(args.candidate_config_json),
            output_root=args.output,
            split_path=args.split,
            control_package=args.control_package,
            base_seed=args.base_seed,
            repetitions=args.repetitions,
            workers=args.workers,
        )
    except (ConfirmationError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
