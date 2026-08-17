"""Run a research-only CABT panel over the residual public meta.

The ordinary cg alternating runner is sealed to 24-reference successive
halving stages.  This bridge intentionally keeps that contract unchanged and
evaluates exactly three public references that were absent from the weekend
splits and v1/v2/v3 holdouts.  It is a confirmation panel, never a CEM
selection or automatic promotion source.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (  # noqa: E402
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


SCHEMA = "meta-specialist-cg-residual-panel-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/cg_unused_meta_residual_v1.json"
DEFAULT_POOL = _ROOT / "opponents"
EXPECTED_REFERENCE_COUNT = 3
EXPECTED_REPETITIONS = 16
EXPECTED_GAMES_PER_ARM = EXPECTED_REFERENCE_COUNT * 2 * EXPECTED_REPETITIONS


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_sha(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_residual_refs(config_path: Path | str) -> tuple[tuple[str, ...], int]:
    """Load and validate the fixed three-reference public residual panel."""

    path = Path(config_path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgAlternatingRuntimeError(f"invalid residual panel config: {path}") from exc
    if not isinstance(payload, Mapping):
        raise CgAlternatingRuntimeError("residual panel config must be an object")
    refs = payload.get("opponent_ids")
    if not isinstance(refs, list) or len(refs) != EXPECTED_REFERENCE_COUNT:
        raise CgAlternatingRuntimeError("residual panel requires exactly three opponent_ids")
    normalized = tuple(str(item) for item in refs)
    if len(set(normalized)) != EXPECTED_REFERENCE_COUNT or any(not item for item in normalized):
        raise CgAlternatingRuntimeError("residual panel opponent_ids must be unique and non-empty")
    contract = payload.get("evaluation_contract")
    if not isinstance(contract, Mapping):
        raise CgAlternatingRuntimeError("residual panel evaluation_contract is missing")
    repetitions = contract.get("repetitions_per_opponent_seat")
    games_per_arm = contract.get("games_per_arm")
    if repetitions != EXPECTED_REPETITIONS or games_per_arm != EXPECTED_GAMES_PER_ARM:
        raise CgAlternatingRuntimeError("residual panel contract must be 96 games/arm and 16 repetitions")
    pool_payload = json.loads((DEFAULT_POOL / "pool_manifest.json").read_text(encoding="utf-8"))
    pool_by_id = {str(item.get("id")): item for item in pool_payload if isinstance(item, Mapping)}
    for ref in normalized:
        item = pool_by_id.get(ref)
        if item is None or item.get("smoke_ok") is not True or item.get("source") != "public":
            raise CgAlternatingRuntimeError(f"residual reference is not a public smoke_ok opponent: {ref}")
    return normalized, EXPECTED_REPETITIONS


def _annotate_games(
    games: Sequence[EvaluationGameV1],
    *,
    arm_label: str,
    package: CgPackageSpecV1,
    config_sha256: str,
    panel_sha256: str,
) -> tuple[EvaluationGameV1, ...]:
    annotated: list[EvaluationGameV1] = []
    for game in games:
        metadata = {
            **dict(game.metadata),
            "residual_panel_schema": SCHEMA,
            "residual_arm": arm_label,
            "residual_config_sha256": config_sha256,
            "residual_panel_sha256": panel_sha256,
            "package_manifest_sha256": package.manifest_sha256,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        annotated.append(replace(game, metadata=metadata))
    return tuple(annotated)


def _build_residual_pair_games(
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    reference_ids: Sequence[str],
    pool_root: Path | str = DEFAULT_POOL,
    base_seed: int,
    repetitions: int = EXPECTED_REPETITIONS,
    config_sha256: str = "0" * 64,
    panel_sha256: str = "0" * 64,
) -> tuple[EvaluationGameV1, ...]:
    """Build paired residual games with identical opponent/seat/seed strata."""

    if candidate.deck_sha256 != control.deck_sha256:
        raise CgAlternatingRuntimeError("residual panel requires the same deck")
    if candidate.policy_sha256 == control.policy_sha256:
        raise CgAlternatingRuntimeError("residual panel requires different policy identities")
    refs = tuple(reference_ids)
    if len(refs) != EXPECTED_REFERENCE_COUNT or len(set(refs)) != EXPECTED_REFERENCE_COUNT:
        raise CgAlternatingRuntimeError("residual panel requires exactly three unique references")
    if type(repetitions) is not int or repetitions <= 0:
        raise CgAlternatingRuntimeError("residual repetitions must be a positive integer")
    pool = Path(pool_root).resolve()
    candidate_arm = arena.ArenaArm(
        arm_id="cg-residual-candidate",
        policy_id=candidate.candidate_id,
        policy_sha256=candidate.policy_sha256,
        arm_kind="root_cg",
        candidate_package_root=candidate.package_root,
    )
    control_arm = arena.ArenaArm(
        arm_id="cg-residual-control",
        policy_id=control.candidate_id,
        policy_sha256=control.policy_sha256,
        arm_kind="root_cg",
        candidate_package_root=control.package_root,
    )
    candidate_raw = arena._build_games(
        arm=candidate_arm,
        refs=refs,
        pool_root=pool,
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{base_seed}-candidate",
    )
    control_raw = arena._build_games(
        arm=control_arm,
        refs=refs,
        pool_root=pool,
        base_seed=base_seed,
        games_per_opponent_seat=repetitions,
        block_id=f"{SCHEMA}-{base_seed}-control",
    )
    candidate_games = _annotate_games(
        candidate_raw,
        arm_label="candidate",
        package=candidate,
        config_sha256=config_sha256,
        panel_sha256=panel_sha256,
    )
    control_games = _annotate_games(
        control_raw,
        arm_label="control",
        package=control,
        config_sha256=config_sha256,
        panel_sha256=panel_sha256,
    )
    candidate_keys = {(str(game.metadata["pair_key"]), game.seed) for game in candidate_games}
    control_keys = {(str(game.metadata["pair_key"]), game.seed) for game in control_games}
    if candidate_keys != control_keys:
        raise CgAlternatingRuntimeError("residual candidate/control strata differ")
    if len(candidate_games) != EXPECTED_GAMES_PER_ARM or len(control_games) != EXPECTED_GAMES_PER_ARM:
        raise CgAlternatingRuntimeError("residual panel game count does not match its contract")
    return candidate_games + control_games


def _seat_gap(summary: Mapping[str, object]) -> float:
    seat = summary.get("seat")
    if not isinstance(seat, Mapping):
        return 1.0
    left = seat.get("0")
    right = seat.get("1")
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return 1.0
    left_rate = left.get("score_rate")
    right_rate = right.get("score_rate")
    if type(left_rate) not in (int, float) or type(right_rate) not in (int, float):
        return 1.0
    return abs(float(left_rate) - float(right_rate))


def summarize_residual_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    stage_games_per_arm: int = EXPECTED_GAMES_PER_ARM,
    protocol_sha256: str,
) -> dict[str, object]:
    """Summarize a completed residual panel without granting promotion authority."""

    candidate_rows = [row for row in rows if row.get("metadata", {}).get("residual_arm") == "candidate"]
    control_rows = [row for row in rows if row.get("metadata", {}).get("residual_arm") == "control"]
    if len(candidate_rows) != stage_games_per_arm or len(control_rows) != stage_games_per_arm:
        raise CgAlternatingRuntimeError("residual panel arms do not cover the requested games")
    key = lambda row: (str(row.get("metadata", {}).get("pair_key")), row.get("seed"))
    candidate_keys = {key(row) for row in candidate_rows}
    control_keys = {key(row) for row in control_rows}
    if candidate_keys != control_keys:
        raise CgAlternatingRuntimeError("residual summary strata differ")
    candidate = arena._aggregate(candidate_rows)
    control = arena._aggregate(control_rows)
    candidate_score = float(candidate.get("score_rate") or 0.0)
    control_score = float(control.get("score_rate") or 0.0)
    delta = candidate_score - control_score
    faults = int(candidate.get("faults", 0)) + int(control.get("faults", 0))
    candidate_gap = _seat_gap(candidate)
    control_gap = _seat_gap(control)
    positive = faults == 0 and delta > 0.0 and candidate_gap <= 0.05 and control_gap <= 0.05
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage_games_per_arm": stage_games_per_arm,
        "protocol_sha256": protocol_sha256,
        "candidate": candidate,
        "control": control,
        "candidate_delta": delta,
        "candidate_delta_points": delta * 100.0,
        "candidate_seat_gap": candidate_gap,
        "control_seat_gap": control_gap,
        "faults": faults,
        "decision": "POSITIVE_SIGNAL" if positive else "NOT_PROMOTABLE",
        "promotion_authority": False,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    payload["summary_sha256"] = _semantic_sha(payload)
    return payload


def run_residual_panel(
    *,
    candidate_package: Path | str,
    control_package: Path | str,
    config_path: Path | str = DEFAULT_CONFIG,
    pool_root: Path | str = DEFAULT_POOL,
    output_root: Path | str,
    base_seed: int,
    workers: int = 12,
    worker_recycle_games: int = 16,
    execute: bool = False,
) -> dict[str, object]:
    """Materialize or execute one residual panel block."""

    if workers != 12 or worker_recycle_games != 16:
        raise CgAlternatingRuntimeError("residual panel is sealed to workers=12/recycle=16")
    candidate = CgPackageSpecV1.from_package(Path(candidate_package).resolve())
    control = CgPackageSpecV1.from_package(Path(control_package).resolve())
    refs, repetitions = load_residual_refs(config_path)
    config_sha = _sha256(Path(config_path).resolve())
    pool_path = Path(pool_root).resolve()
    panel_sha = _sha256(pool_path / "pool_manifest.json")
    games = _build_residual_pair_games(
        candidate=candidate,
        control=control,
        reference_ids=refs,
        pool_root=pool_path,
        base_seed=base_seed,
        repetitions=repetitions,
        config_sha256=config_sha,
        panel_sha256=panel_sha,
    )
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"residual panel output already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    protocol_sha = _semantic_sha(
        {
            "schema_version": SCHEMA,
            "pool_manifest_sha256": panel_sha,
            "config_sha256": config_sha,
            "reference_ids": list(refs),
            "repetitions": repetitions,
            "base_seed": base_seed,
            "candidate_policy_sha256": candidate.policy_sha256,
            "control_policy_sha256": control.policy_sha256,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        }
    )
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "EXECUTING" if execute else "DRY_RUN",
        "requested_games": len(games),
        "stage_games_per_arm": EXPECTED_GAMES_PER_ARM,
        "base_seed": base_seed,
        "reference_ids": list(refs),
        "repetitions_per_opponent_seat": repetitions,
        "config_path": str(Path(config_path).resolve()),
        "config_sha256": config_sha,
        "pool_manifest_sha256": panel_sha,
        "protocol_sha256": protocol_sha,
        "candidate": candidate.to_dict(),
        "control": control.to_dict(),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not execute:
        return {"status": "DRY_RUN", "output_root": str(root), "manifest": manifest}
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=root / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    summary = summarize_residual_rows(
        evaluation["rows"],
        stage_games_per_arm=EXPECTED_GAMES_PER_ARM,
        protocol_sha256=protocol_sha,
    )
    summary["evaluator_summary"] = evaluation["summary"]
    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest.update(
        {
            "status": "COMPLETE",
            "summary_sha256": _sha256(summary_path),
            "completed_games": evaluation["summary"].get("completed_games"),
            "faults": evaluation["summary"].get("faults"),
            "decision": summary["decision"],
        }
    )
    complete_path = root / "manifest-complete.json"
    complete_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(root), "summary": summary, "manifest": manifest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for CABT execution")
    args = parser.parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing residual CABT run without --execute")
    try:
        result = run_residual_panel(
            candidate_package=args.candidate_package,
            control_package=args.control_package,
            config_path=args.config,
            pool_root=args.pool_root,
            output_root=args.output,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            execute=True,
        )
    except (CgAlternatingRuntimeError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
