"""Run the research-only cg P1 CEM campaign.

This bridge owns the heavy-run lease.  It evaluates self-owned parameterized
packages against the immutable weekend split, keeps P1 as a control, and
persists every generation/checkpoint without touching the submission branch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import statistics
import tempfile
import time
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_cem_v1 import (  # noqa: E402
    CemCampaignConfig,
    CemState,
    aggregate_candidate_rows,
    load_latest_checkpoint,
    rank_valid_results,
    sample_population,
    save_checkpoint,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    P1ParameterConfig,
    candidate_id_for_config,
    materialize_parameterized_package,
)
from mage_ptcg.meta_specialist.self_owned_cg_parameterized_package_v1 import (  # noqa: E402
    materialize_self_owned_cg_parameterized_package_v1,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import WeekendSplit, load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor  # noqa: E402
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


SCHEMA = "cg-p1-cem-campaign-v1"
P1_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DEFAULT_SPLIT = _ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
DEFAULT_OUTPUT = _ROOT / "runs/final-sprint-autonomous/cg-p1-cem-weekend-v1"
DEFAULT_CAMPAIGN_SEED = 20260815
CONTROL_POLICY_ID = "cg-lethal-target-v1-control"
RISK_AWARE_SEAT_GAP_LIMIT = 0.05


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scales_from_fraction(fraction: float) -> dict[str, float]:
    """Build a bounded local-search scale from each parameter's declared span."""

    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ValueError("initial scale fraction must be numeric")
    fraction = float(fraction)
    if not 0.0 < fraction <= 1.0:
        raise ValueError("initial scale fraction must be in (0, 1]")
    return {
        name: max(1.0, float(upper - lower) * fraction)
        for name, (lower, upper) in PARAMETER_BOUNDS.items()
    }


def _load_initial_config(path: Path | str) -> P1ParameterConfig:
    """Load a CEM center from a candidate manifest or raw parameter mapping."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("initial config payload must be a mapping")
    values: object = payload.get("config", payload.get("parameters", payload))
    if not isinstance(values, Mapping):
        raise ValueError("initial config must contain a parameter mapping")
    return P1ParameterConfig.from_mapping(values)


def _control_identity(control_package: Path | str) -> tuple[str, str]:
    """Return a stable row policy id and main.py hash for a control package."""

    package = Path(control_package).resolve()
    policy_sha = _sha256(package / "main.py")
    if package == P1_PACKAGE.resolve() and policy_sha == BASE_SOURCE_SHA256:
        return CONTROL_POLICY_ID, policy_sha
    return f"cg-control-{policy_sha[:12]}", policy_sha


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _package_manifest_sha(package: Path) -> str:
    for name in (
        "cg_p1_cem_candidate_manifest.json",
        "self_owned_cg_package_manifest.json",
        "manifest.json",
        "kaggle-package-manifest.json",
    ):
        path = package / name
        if path.is_file():
            return _sha256(path)
    return "0" * 64


def _materialize_cem_candidate(
    *,
    source_package: Path | str,
    output_package: Path | str,
    config: P1ParameterConfig,
    candidate_id: str,
    deck_binding_package: Path | str | None = None,
) -> dict[str, object]:
    """Materialize a CEM candidate with an explicit deck/fallback binding.

    The immutable P1 source can carry a different historical ``ROOT_DECK``
    literal than the deck file used by the current CEM split.  When the
    control is a verified self-owned package, reuse its existing materializer
    so the candidate's initial ``select=None`` fallback and ``deck.csv`` are
    rebound together.  Legacy controls keep the original materializer and the
    downstream static smoke remains the final contract gate.
    """

    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    binding = Path(deck_binding_package).resolve() if deck_binding_package is not None else None
    if binding is None or not (binding / "self_owned_cg_package_manifest.json").is_file():
        return materialize_parameterized_package(
            source_package=source,
            output_package=target,
            config=config,
            candidate_id=candidate_id,
        )
    source_deck = source / "deck.csv"
    binding_deck = binding / "deck.csv"
    if not source_deck.is_file() or not binding_deck.is_file():
        raise ValueError("deck binding requires source and control deck.csv")
    if _sha256(source_deck) != _sha256(binding_deck):
        raise ValueError("deck binding package does not match source package deck")
    package_manifest = materialize_self_owned_cg_parameterized_package_v1(
        source_package=source,
        self_owned_deck_package=binding,
        output_package=target,
        config=config,
        candidate_id=candidate_id,
    )
    binding_manifest = binding / "self_owned_cg_package_manifest.json"
    candidate_manifest = {
        "schema_version": "cg-p1-cem-candidate-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": str(package_manifest["policy_sha256"]),
        "deck_sha256": _sha256(target / "deck.csv"),
        "root_deck_bound": True,
        "deck_binding_mode": "SELF_OWNED_CONTROL_PACKAGE",
        "deck_binding_package": str(binding),
        "deck_binding_package_manifest_sha256": _sha256(binding_manifest),
        "self_owned_package_manifest_sha256": _sha256(
            target / "self_owned_cg_package_manifest.json"
        ),
        "research_only": True,
        "submission_branch_modified": False,
    }
    _write_new_json(target / "cg_p1_cem_candidate_manifest.json", candidate_manifest)
    return candidate_manifest


def _bind_game(
    game: EvaluationGameV1,
    *,
    split_name: str,
    split_sha256: str,
    config_sha256: str,
    arm_role: str,
    package_manifest_sha256: str,
    pool_root: Path,
) -> EvaluationGameV1:
    payload = game.to_payload()
    metadata = dict(payload["metadata"])
    metadata.update(
        {
            # The existing arena worker owns this field; CEM provenance gets
            # a separate marker so run_root_cg_game_v1 still accepts it.
            "schema_version": arena.SCHEMA,
            "cem_schema": "cg-p1-cem-game-v1",
            "split": split_name,
            "split_sha256": split_sha256,
            "config_sha256": config_sha256,
            "arm_role": arm_role,
            "package_manifest_sha256": package_manifest_sha256,
            "parent_policy_sha256": BASE_SOURCE_SHA256,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
            "pool_root": str(pool_root.resolve()),
            "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
            "research_only": True,
            "training_exposure": 0,
        }
    )
    payload["metadata"] = metadata
    return EvaluationGameV1(**payload)


def _pair_games(
    *,
    candidate_package: Path,
    candidate_id: str,
    config_sha256: str,
    split: WeekendSplit,
    refs: Sequence[str],
    games_per_opponent_seat: int,
    base_seed: int,
    include_control: bool,
    block_id: str,
    split_name: str,
    control_package: Path | str = P1_PACKAGE,
    pool_root: Path | str = _ROOT / "opponents",
) -> tuple[EvaluationGameV1, ...]:
    candidate_package = Path(candidate_package).resolve()
    pool_root = Path(pool_root).resolve()
    candidate_policy_sha = _sha256(candidate_package / "main.py")
    candidate_arm = arena.ArenaArm(
        arm_id=candidate_id,
        policy_id=candidate_id,
        policy_sha256=candidate_policy_sha,
        arm_kind="root_cg",
        candidate_package_root=candidate_package,
    )
    candidate_games = arena._build_games(
        arm=candidate_arm,
        refs=tuple(refs),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
    )
    result = [
        _bind_game(
            game,
            split_name=split_name,
            split_sha256=split.config_sha256,
            config_sha256=config_sha256,
            arm_role="candidate",
            package_manifest_sha256=_package_manifest_sha(candidate_package),
            pool_root=pool_root,
        )
        for game in candidate_games
    ]
    if include_control:
        control_package = Path(control_package).resolve()
        control_policy_id, control_policy_sha = _control_identity(control_package)
        control_arm = arena.ArenaArm(
            arm_id="p1-control" if control_policy_id == CONTROL_POLICY_ID else f"{control_policy_id}-arm",
            policy_id=control_policy_id,
            policy_sha256=control_policy_sha,
            arm_kind="root_cg",
            candidate_package_root=control_package,
        )
        control_games = arena._build_games(
            arm=control_arm,
            refs=tuple(refs),
            pool_root=pool_root,
            base_seed=base_seed,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=block_id,
        )
        result.extend(
            _bind_game(
                game,
                split_name=split_name,
                split_sha256=split.config_sha256,
                config_sha256=config_sha256,
                arm_role="p1_control",
                package_manifest_sha256=_package_manifest_sha(control_package),
                pool_root=pool_root,
            )
            for game in control_games
        )
        candidate_pairs = {game.metadata["pair_key"] for game in result if game.metadata["arm_role"] == "candidate"}
        control_pairs = {game.metadata["pair_key"] for game in result if game.metadata["arm_role"] == "p1_control"}
        if candidate_pairs != control_pairs:
            raise ValueError("candidate/control pair strata differ")
    return tuple(result)


def build_paired_games(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config_sha256: str,
    split: WeekendSplit,
    train_block_index: int,
    games_per_opponent_seat: int,
    base_seed: int,
    include_control: bool = True,
    refs_override: Sequence[str] | None = None,
    split_name: str = "META_TRAIN",
    control_package: Path | str = P1_PACKAGE,
    block_id: str | None = None,
    pool_root: Path | str = _ROOT / "opponents",
) -> tuple[EvaluationGameV1, ...]:
    if train_block_index < 0 or train_block_index >= len(split.train_blocks):
        raise ValueError("train_block_index is out of range")
    refs = tuple(refs_override) if refs_override is not None else split.train_blocks[train_block_index]
    if not refs or len(set(refs)) != len(refs):
        raise ValueError("evaluation refs must be non-empty and unique")
    resolved_block_id = block_id or f"cg-p1-cem-{candidate_id}"
    if not isinstance(resolved_block_id, str) or not resolved_block_id:
        raise ValueError("block_id must be a non-empty string")
    return _pair_games(
        candidate_package=Path(candidate_package),
        candidate_id=candidate_id,
        config_sha256=config_sha256,
        split=split,
        refs=refs,
        games_per_opponent_seat=games_per_opponent_seat,
        base_seed=base_seed,
        include_control=include_control,
        block_id=resolved_block_id,
        split_name=split_name,
        control_package=control_package,
        pool_root=pool_root,
    )


def _search_plan(
    split: WeekendSplit,
    *,
    generation: int,
    all_train_refs: bool = False,
    include_dev_refs: bool = False,
) -> tuple[tuple[str, ...], int, str]:
    """Return the fixed-size CEM search stratum for one generation.

    The original weekend pilot rotates three four-opponent blocks.  The
    robust mode keeps the 48-game budget unchanged while covering all twelve
    META_TRAIN references with two repetitions per seat.
    """

    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if type(all_train_refs) is not bool:
        raise ValueError("all_train_refs must be a boolean")
    if type(include_dev_refs) is not bool:
        raise ValueError("include_dev_refs must be a boolean")
    if include_dev_refs and not all_train_refs:
        raise ValueError("include_dev_refs requires all_train_refs")
    if all_train_refs:
        if include_dev_refs:
            return tuple(split.ids("META_TRAIN") + split.ids("META_DEV")), 2, "META_TRAIN_PLUS_DEV"
        return tuple(split.ids("META_TRAIN")), 2, "META_TRAIN_ALL"
    block_index = generation % len(split.train_blocks)
    return tuple(split.train_blocks[block_index]), 6, f"META_TRAIN_BLOCK_{block_index}"


def candidate_result_from_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_policy_id: str,
    control_policy_id: str,
    weights: Mapping[str, float],
    config: P1ParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    candidate_rows = [row for row in rows if row.get("policy_id") == candidate_policy_id]
    control_rows = [row for row in rows if row.get("policy_id") == control_policy_id]
    candidate = aggregate_candidate_rows(candidate_rows, weights=weights)
    control = aggregate_candidate_rows(control_rows, weights=weights)
    return {
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "candidate": candidate,
        "control": control,
        "delta_objective": float(candidate["objective"]) - float(control["objective"]),
        "policy_sha256": str(candidate_rows[0].get("policy_sha256")) if candidate_rows else None,
    }


def _bind_repeat_control(
    repeat_result: Mapping[str, object],
    control: Mapping[str, object],
    *,
    control_block_id: str,
) -> dict[str, object]:
    """Bind the shared per-repeat control before calculating a delta.

    Re-evaluation saves work by running one control block per repeat and
    reusing it for every screen elite.  A candidate's own block therefore has
    no control rows unless it is the first elite; binding the shared aggregate
    here prevents an empty-control ``objective=-1`` from becoming a bogus
    positive delta.
    """

    candidate = repeat_result.get("candidate")
    objective = candidate.get("objective") if isinstance(candidate, Mapping) else None
    control_objective = control.get("objective")
    if type(objective) not in (int, float) or type(control_objective) not in (int, float):
        raise ValueError("repeat result/control objective must be numeric")
    bound = dict(repeat_result)
    bound["control"] = dict(control)
    bound["delta_objective"] = round(float(objective) - float(control_objective), 10)
    bound["control_block_id"] = control_block_id
    bound["control_reused"] = True
    return bound


def _risk_aware_reevaluation(
    repeats: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Project repeated re-evaluations onto a conservative lower-tail score."""

    if not repeats:
        raise ValueError("repeated re-evaluation results cannot be empty")
    candidates: list[Mapping[str, object]] = []
    deltas: list[float] = []
    seat_gaps: list[float | None] = []
    opponent_seat_gaps: list[dict[str, float]] = []
    for repeat in repeats:
        candidate = repeat.get("candidate")
        if not isinstance(candidate, Mapping):
            raise ValueError("repeated re-evaluation is missing candidate diagnostics")
        objective = candidate.get("objective")
        if type(objective) not in (int, float):
            raise ValueError("repeated re-evaluation candidate objective is invalid")
        delta = repeat.get("delta_objective")
        if type(delta) not in (int, float):
            raise ValueError("repeated re-evaluation delta objective is invalid")
        candidates.append(candidate)
        deltas.append(float(delta))
        seat_rates = candidate.get("seat_rates")
        if isinstance(seat_rates, Mapping) and all(
            type(seat_rates.get(seat)) in (int, float) for seat in ("0", "1")
        ):
            seat_gaps.append(round(abs(float(seat_rates["0"]) - float(seat_rates["1"])), 10))
        else:
            seat_gaps.append(None)
        raw_opponent_rates = candidate.get("opponent_seat_rates")
        repeat_opponent_gaps: dict[str, float] = {}
        if isinstance(raw_opponent_rates, Mapping):
            for opponent_id, raw_rates in raw_opponent_rates.items():
                if not isinstance(raw_rates, Mapping):
                    continue
                seat0 = raw_rates.get("0")
                seat1 = raw_rates.get("1")
                if type(seat0) in (int, float) and type(seat1) in (int, float):
                    repeat_opponent_gaps[str(opponent_id)] = round(abs(float(seat0) - float(seat1)), 10)
        opponent_seat_gaps.append(repeat_opponent_gaps)
    objectives = [float(candidate["objective"]) for candidate in candidates]
    worst_candidate = dict(candidates[objectives.index(min(objectives))])
    seat_safe = all(gap is None or gap <= RISK_AWARE_SEAT_GAP_LIMIT for gap in seat_gaps)
    seat_gap_penalty = max(
        0.0,
        max(
            (gap - RISK_AWARE_SEAT_GAP_LIMIT for gap in seat_gaps if gap is not None),
            default=0.0,
        ),
    )
    opponent_seat_safe = all(
        gap <= RISK_AWARE_SEAT_GAP_LIMIT
        for gaps in opponent_seat_gaps
        for gap in gaps.values()
    )
    opponent_seat_gap_penalty = round(
        max(
            0.0,
            max(
                (
                    gap - RISK_AWARE_SEAT_GAP_LIMIT
                    for gaps in opponent_seat_gaps
                    for gap in gaps.values()
                ),
                default=0.0,
            ),
        ),
        10,
    )
    combined_gap_penalty = max(seat_gap_penalty, opponent_seat_gap_penalty)
    worst_candidate.update(
        {
            "objective": round(min(objectives) - combined_gap_penalty, 10),
            "valid": all(candidate.get("valid") is True for candidate in candidates),
            "faults": sum(int(candidate.get("faults", 0)) for candidate in candidates),
            "seat_collapse": any(bool(candidate.get("seat_collapse", False)) for candidate in candidates),
        }
    )
    return {
        "candidate": worst_candidate,
        "control": repeats[0].get("control", {}),
        "delta_objective": min(deltas),
        "min_delta_objective": min(deltas),
        "mean_delta_objective": statistics.fmean(deltas),
        "repeat_objectives": objectives,
        "repeat_deltas": deltas,
        "repeat_seat_gaps": seat_gaps,
        "seat_safe": seat_safe,
        "seat_gap_penalty": seat_gap_penalty,
        "repeat_opponent_seat_gaps": opponent_seat_gaps,
        "opponent_seat_safe": opponent_seat_safe,
        "opponent_seat_gap_penalty": opponent_seat_gap_penalty,
        "repeat_count": len(repeats),
    }


def _elite_update_rows(
    results: Sequence[Mapping[str, object]],
    *,
    use_reeval: bool,
    risk_aware: bool = False,
    positive_delta_gate: bool = False,
) -> tuple[dict[str, object], ...]:
    """Project candidate results onto the objective used for CEM updating."""

    if risk_aware and not use_reeval:
        raise ValueError("risk-aware update requires independent re-evaluation")
    if type(positive_delta_gate) is not bool:
        raise ValueError("positive_delta_gate must be a boolean")
    rows: list[dict[str, object]] = []
    for result in results:
        source: Mapping[str, object] = result
        if use_reeval:
            reeval = result.get("independent_train96")
            if not isinstance(reeval, Mapping):
                continue
            source = reeval.get("risk_aware", reeval) if risk_aware else reeval
            if not isinstance(source, Mapping):
                continue
            if positive_delta_gate:
                delta = source.get("delta_objective")
                if type(delta) not in (int, float) or float(delta) <= 0.0:
                    continue
        candidate = source.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        rows.append(
            {
                "config": result.get("config"),
                "objective": candidate.get("objective"),
                "valid": candidate.get("valid"),
                "faults": candidate.get("faults", 0),
                "candidate_id": result.get("candidate_id"),
            }
        )
    return tuple(rows)


def _select_initial_elites(
    rows: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
    center: P1ParameterConfig,
    scales: Mapping[str, float] | None,
) -> tuple[tuple[dict[str, object], ...], str, dict[str, float], int]:
    """Rank a screen, preserving the incumbent when no valid elite exists.

    ``_elite_update_rows`` intentionally keeps invalid diagnostics so the
    generation artifact explains why candidates were rejected.  Therefore a
    non-empty row list is not sufficient to call ``rank_valid_results``: all
    rows can still be invalid (for example, a pilot seat-collapse gate).  This
    helper turns that measured condition into a no-update result while
    allowing unrelated schema/configuration errors to fail closed.
    """

    valid_count = 0
    for raw in rows:
        config_raw = raw.get("config")
        try:
            if isinstance(config_raw, P1ParameterConfig):
                config_raw.validate()
            elif isinstance(config_raw, Mapping):
                P1ParameterConfig.from_mapping(config_raw)
            else:
                continue
        except (TypeError, ValueError):
            continue
        objective = raw.get("objective")
        faults = raw.get("faults", 0)
        if type(faults) is not int or faults != 0:
            continue
        if type(objective) not in (int, float) or not math.isfinite(float(objective)):
            continue
        if raw.get("valid") is False:
            continue
        valid_count += 1
    try:
        elites = rank_valid_results(rows, elite_count=elite_count)
    except ValueError as exc:
        if not str(exc).startswith("not enough valid candidates for elite update:"):
            raise
        preserved_scales = dict(scales) if scales is not None else update_distribution(
            center,
            ({"config": center.as_dict()},),
        )[1]
        return (
            (),
            "screen_valid_candidates_below_elite_count_preserve_center",
            preserved_scales,
            valid_count,
        )
    preserved_scales = dict(scales) if scales is not None else update_distribution(
        center,
        ({"config": center.as_dict()},),
    )[1]
    return elites, "search", preserved_scales, valid_count


def _select_update_elites(
    rows: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
    center: P1ParameterConfig,
    selection_label: str = "independent_train96",
) -> tuple[tuple[dict[str, object], ...], str]:
    """Rank update rows, preserving the center when the gate has too few valid rows.

    Screen rows already use a fail-closed center-preservation path.  The same
    boundary is required after independent re-evaluation: a positive row can
    still be invalid because of a seat collapse or another hard gate.  Calling
    ``rank_valid_results`` directly in that case used to abort the campaign
    instead of recording a no-update generation.
    """

    try:
        elites = rank_valid_results(rows, elite_count=elite_count)
    except ValueError as exc:
        if not str(exc).startswith("not enough valid candidates for elite update:"):
            raise
        preserved = tuple(
            {
                "candidate_id": "incumbent-center",
                "config": center.as_dict(),
                "objective": 0.0,
                "valid": True,
                "faults": 0,
            }
            for _ in range(elite_count)
        )
        return preserved, f"{selection_label}_valid_candidates_below_elite_count_preserve_center"
    return elites, selection_label


def _resource_gate(governor: ResourceGovernor) -> dict[str, object]:
    decision = governor.decide(task_cap=12, gpu_required=False)
    payload = decision.to_dict()
    if decision.state != "normal" or decision.recommended_workers < 12:
        raise RuntimeError(f"ResourceGovernor fail-closed: {payload}")
    return payload


def _static_smoke(candidate_package: Path, control_package: Path) -> None:
    """Run the native package contract outside the CEM coordinator process."""
    candidate_package = Path(candidate_package).resolve()
    control_package = Path(control_package).resolve()
    with tempfile.TemporaryDirectory(prefix="cg-static-smoke-") as temporary:
        output = Path(temporary)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        env.update(
            {
                "PYTHONPATH": os.pathsep.join((str(_ROOT), str(_ROOT / "src"))),
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
        )
        command = [
            sys.executable,
            str(_ROOT / "scripts" / "run_cg_static_smoke_v1.py"),
            "--candidate-package",
            str(candidate_package),
            "--control-package",
            str(control_package),
            "--output",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(_ROOT),
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=120.0,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("cg static smoke subprocess timed out after 120 seconds") from exc
        report_path = output / "static_smoke_report.json"
        if not report_path.is_file():
            stderr = (completed.stderr or "")[-8192:]
            raise RuntimeError(
                "cg static smoke produced no report: "
                f"returncode={completed.returncode}; stderr={stderr}"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("cg static smoke report is not valid JSON") from exc
        expected_candidate_sha = _sha256(candidate_package / "main.py")
        expected_control_sha = _sha256(control_package / "main.py")
        if (
            completed.returncode != 0
            or not isinstance(report, Mapping)
            or report.get("status") != "PASS"
            or report.get("candidate_main_sha256") != expected_candidate_sha
            or report.get("control_main_sha256") != expected_control_sha
            or report.get("candidate_agent_contract") != "PASS"
        ):
            stderr = (completed.stderr or "")[-8192:]
            raise RuntimeError(
                "cg static smoke failed: "
                f"returncode={completed.returncode}; report={report}; stderr={stderr}"
            )


def _evaluate_games(games: Sequence[EvaluationGameV1], output_dir: Path, workers: int) -> dict[str, object]:
    total = len(games)
    bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            bar = tqdm(total=total, desc="cg CEM", unit="game", dynamic_ncols=True)
        except Exception:  # pragma: no cover - only exercised without tqdm
            bar = None
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}

    def progress(row: Mapping[str, object]) -> None:
        state["completed"] += 1
        if str(row.get("outcome", "fault")) == "fault":
            state["faults"] += 1
        if bar is not None:
            bar.update(1)
            bar.set_postfix(faults=state["faults"])
            return
        now = time.monotonic()
        if now - state["last_emit"] >= 10.0 or state["completed"] == total:
            print(
                json.dumps(
                    {
                        "stage": "cg_cem",
                        "completed": state["completed"],
                        "requested": total,
                        "faults": state["faults"],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            state["last_emit"] = now

    try:
        return run_parallel_cabt_evaluation(
            tuple(games),
            output_dir=output_dir,
            max_workers=workers,
            worker_recycle_games=16,
            overwrite=False,
            progress=progress,
        )
    finally:
        if bar is not None:
            bar.close()


def run_generation(
    *,
    campaign_root: Path,
    split: WeekendSplit,
    source_package: Path,
    center: P1ParameterConfig,
    scales: Mapping[str, float] | None,
    generation: int,
    campaign_config: CemCampaignConfig,
    governor: ResourceGovernor,
    perform_reeval: bool = True,
    all_train_refs: bool = False,
    control_package: Path | str = P1_PACKAGE,
    reeval_for_update: bool = False,
    reeval_repeats: int = 1,
    reeval_games_per_opponent_seat: int = 4,
    positive_delta_gate: bool = False,
    include_dev_refs: bool = False,
    risk_aware_update: bool = False,
    pool_root: Path | str = _ROOT / "opponents",
) -> dict[str, object]:
    if reeval_for_update and not perform_reeval:
        raise ValueError("reeval_for_update requires perform_reeval")
    if type(reeval_repeats) is not int or reeval_repeats <= 0:
        raise ValueError("reeval_repeats must be a positive integer")
    if type(reeval_games_per_opponent_seat) is not int or reeval_games_per_opponent_seat <= 0:
        raise ValueError("reeval_games_per_opponent_seat must be a positive integer")
    if type(positive_delta_gate) is not bool:
        raise ValueError("positive_delta_gate must be a boolean")
    if positive_delta_gate and not reeval_for_update:
        raise ValueError("positive_delta_gate requires reeval_for_update")
    if risk_aware_update and not reeval_for_update:
        raise ValueError("risk_aware_update requires reeval_for_update")
    if risk_aware_update and reeval_repeats < 2:
        raise ValueError("risk_aware_update requires at least two re-evaluation repeats")
    if include_dev_refs and not all_train_refs:
        raise ValueError("include_dev_refs requires all_train_refs")
    resource = _resource_gate(governor)
    gen_root = campaign_root / f"generation-{generation:04d}"
    candidate_root = gen_root / "candidates"
    configs = sample_population(
        center,
        generation=generation,
        population_size=campaign_config.population_size,
        seed=campaign_config.seed,
        scales=scales,
    )
    search_refs, search_repetitions, search_mode = _search_plan(
        split,
        generation=generation,
        all_train_refs=all_train_refs,
        include_dev_refs=include_dev_refs,
    )
    search_weights = split.weights("META_TRAIN")
    if include_dev_refs:
        search_weights.update(split.weights("META_DEV"))
    package_by_id: dict[str, Path] = {}
    config_by_id: dict[str, P1ParameterConfig] = {}
    games: list[EvaluationGameV1] = []
    for index, config in enumerate(configs):
        candidate_id = candidate_id_for_config(config, generation=generation, index=index)
        package = candidate_root / f"candidate-{index:02d}"
        _materialize_cem_candidate(
            source_package=source_package,
            output_package=package,
            config=config,
            candidate_id=candidate_id,
            deck_binding_package=control_package,
        )
        _static_smoke(package, Path(control_package).resolve())
        package_by_id[candidate_id] = package
        config_by_id[candidate_id] = config
        games.extend(
            build_paired_games(
                candidate_package=package,
                candidate_id=candidate_id,
                config_sha256=config.config_sha256(),
                split=split,
                train_block_index=generation % len(split.train_blocks),
                games_per_opponent_seat=search_repetitions,
                base_seed=campaign_config.seed + generation * 100_000,
                include_control=index == 0,
                refs_override=search_refs,
                split_name="META_TRAIN_PLUS_DEV" if include_dev_refs else "META_TRAIN",
                control_package=control_package,
                pool_root=pool_root,
            )
        )
    _write_new_json(
        gen_root / "manifest.json",
        {
            "schema_version": SCHEMA,
            "generation": generation,
            "candidate_count": len(configs),
            "requested_games": len(games),
            "search_mode": search_mode,
            "search_refs": list(search_refs),
            "search_games_per_opponent_seat": search_repetitions,
            "campaign_seed": campaign_config.seed,
            "population_size": campaign_config.population_size,
            "elite_count": campaign_config.elite_count,
            "reeval_games_per_opponent_seat": reeval_games_per_opponent_seat,
            "positive_delta_gate": positive_delta_gate,
            "split_sha256": split.config_sha256,
            "parent_policy_sha256": _sha256(Path(control_package).resolve() / "main.py"),
            "resource_decision": resource,
            "research_only": True,
        },
    )
    evaluation = _evaluate_games(games, gen_root / "evaluation", resource["recommended_workers"])
    rows = evaluation["rows"]
    results: list[dict[str, object]] = []
    for candidate_id, config in config_by_id.items():
        results.append(
            candidate_result_from_rows(
                rows,
                candidate_policy_id=candidate_id,
                control_policy_id=_control_identity(control_package)[0],
                weights=search_weights,
                config=config,
                candidate_id=candidate_id,
            )
        )
    initial_elite_rows = _elite_update_rows(results, use_reeval=False)
    initial_elites, initial_selection, preserved_scales, valid_screen_candidates = _select_initial_elites(
        initial_elite_rows,
        elite_count=campaign_config.elite_count,
        center=center,
        scales=scales,
    )
    if not initial_elites:
        # Small or highly noisy fresh pools can leave fewer valid candidates
        # than the requested elite budget.  Treat that as a measured
        # no-update generation rather than crashing after spending CABT
        # budget; the incumbent center and its existing scales remain intact.
        _write_new_json(
            gen_root / "results.json",
            {
                "schema_version": SCHEMA,
                "generation": generation,
                "results": results,
                "elites": [],
                "initial_elites": [],
                "elite_selection": initial_selection,
                "new_center": center.as_dict(),
                "new_scales": preserved_scales,
                "dev": None,
                "valid_screen_candidates": valid_screen_candidates,
                "research_only": True,
            },
        )
        return {
            "generation": generation,
            "results": results,
            "elites": [],
            "initial_elites": [],
            "elite_selection": initial_selection,
            "center": center,
            "scales": preserved_scales,
            "dev": None,
            "resource_decision": resource,
        }
    reeval: list[dict[str, object]] = []
    if perform_reeval:
        for repeat in range(reeval_repeats):
            for index, elite in enumerate(initial_elites):
                candidate_id = str(elite["candidate_id"])
                package = package_by_id[candidate_id]
                reeval.extend(
                    build_paired_games(
                        candidate_package=package,
                        candidate_id=candidate_id,
                        config_sha256=config_by_id[candidate_id].config_sha256(),
                        split=split,
                        train_block_index=0,
                        games_per_opponent_seat=reeval_games_per_opponent_seat,
                        base_seed=campaign_config.seed
                        + 500_000
                        + generation * 100_000
                        + repeat * 10_000,
                        include_control=index == 0,
                        refs_override=search_refs,
                        split_name="META_TRAIN_PLUS_DEV" if include_dev_refs else "META_TRAIN",
                        control_package=control_package,
                        block_id=f"cg-p1-cem-{candidate_id}-reeval-r{repeat:02d}",
                        pool_root=pool_root,
                    )
                )
        reeval_result = _evaluate_games(reeval, gen_root / "reevaluation", resource["recommended_workers"])
        reeval_rows = reeval_result["rows"]
        reeval_ids = {str(item["candidate_id"]) for item in initial_elites}
        control_policy_id = _control_identity(control_package)[0]
        control_rows_by_block: dict[str, list[Mapping[str, object]]] = {}
        for row in reeval_rows:
            if row.get("policy_id") != control_policy_id:
                continue
            block_id = row.get("block_id")
            if not isinstance(block_id, str) or not block_id:
                raise ValueError("re-evaluation control row is missing block_id")
            control_rows_by_block.setdefault(block_id, []).append(row)
        if len(control_rows_by_block) != reeval_repeats:
            raise ValueError(
                f"expected {reeval_repeats} shared control blocks, "
                f"found {len(control_rows_by_block)}"
            )
        control_block_ids = tuple(sorted(control_rows_by_block))
        control_results_by_block = {
            block_id: aggregate_candidate_rows(rows, weights=search_weights)
            for block_id, rows in control_rows_by_block.items()
        }
        for result in results:
            if str(result["candidate_id"]) in reeval_ids:
                candidate_id = str(result["candidate_id"])
                independent = candidate_result_from_rows(
                    reeval_rows,
                    candidate_policy_id=candidate_id,
                    control_policy_id=control_policy_id,
                    weights=search_weights,
                    config=config_by_id[candidate_id],
                    candidate_id=candidate_id,
                )
                repeat_rows_by_block: dict[str, list[Mapping[str, object]]] = {}
                for row in reeval_rows:
                    if row.get("policy_id") != candidate_id:
                        continue
                    block_id = row.get("block_id")
                    if not isinstance(block_id, str) or not block_id:
                        raise ValueError("re-evaluation row is missing block_id")
                    repeat_rows_by_block.setdefault(block_id, []).append(row)
                if len(repeat_rows_by_block) != reeval_repeats:
                    raise ValueError(
                        f"expected {reeval_repeats} re-evaluation blocks for {candidate_id}, "
                        f"found {len(repeat_rows_by_block)}"
                    )
                candidate_block_ids = tuple(sorted(repeat_rows_by_block))
                repeat_results_list: list[dict[str, object]] = []
                for candidate_block_id, control_block_id in zip(candidate_block_ids, control_block_ids):
                    repeat_result = candidate_result_from_rows(
                        repeat_rows_by_block[candidate_block_id],
                        candidate_policy_id=candidate_id,
                        control_policy_id=control_policy_id,
                        weights=search_weights,
                        config=config_by_id[candidate_id],
                        candidate_id=candidate_id,
                    )
                    repeat_results_list.append(
                        _bind_repeat_control(
                            repeat_result,
                            control_results_by_block[control_block_id],
                            control_block_id=control_block_id,
                        )
                    )
                repeat_results = tuple(repeat_results_list)
                independent["reeval_repeats"] = reeval_repeats
                independent["reeval_requested_games_per_candidate"] = int(
                    independent["candidate"].get("requested_games", 0)
                )
                independent["reevaluation_blocks"] = list(repeat_results)
                if risk_aware_update:
                    independent["risk_aware"] = _risk_aware_reevaluation(repeat_results)
                result["independent_train96"] = independent
    if reeval_for_update:
        update_rows = _elite_update_rows(
            results,
            use_reeval=True,
            risk_aware=risk_aware_update,
            positive_delta_gate=positive_delta_gate,
        )
        prefix = "risk_aware_independent_train96" if risk_aware_update else "independent_train96"
        selection_label = f"{prefix}_x{reeval_repeats}"
        if positive_delta_gate and not update_rows:
            # Fail closed: no independently positive candidate means the
            # incumbent center remains the research parent for the next run.
            elites = tuple(
                {
                    "candidate_id": "incumbent-center",
                    "config": center.as_dict(),
                    "objective": 0.0,
                    "valid": True,
                    "faults": 0,
                }
                for _ in range(campaign_config.elite_count)
            )
            elite_selection = f"{selection_label}_positive_delta_gate_preserve_center"
        else:
            elites, elite_selection = _select_update_elites(
                update_rows,
                elite_count=campaign_config.elite_count,
                center=center,
                selection_label=selection_label,
            )
    else:
        elites = initial_elites
        elite_selection = "search"
    new_center, new_scales = update_distribution(center, elites)
    dev_result = None
    if generation % 2 == 1:
        incumbent_id = f"cg-p1-cem-incumbent-g{generation:02d}-{new_center.config_sha256()[:12]}"
        incumbent_package = gen_root / "incumbent" / "package"
        _materialize_cem_candidate(
            source_package=source_package,
            output_package=incumbent_package,
            config=new_center,
            candidate_id=incumbent_id,
            deck_binding_package=control_package,
        )
        _static_smoke(incumbent_package, Path(control_package).resolve())
        validation_split = "META_FINAL" if include_dev_refs else "META_DEV"
        dev_games = build_paired_games(
            candidate_package=incumbent_package,
            candidate_id=incumbent_id,
            config_sha256=new_center.config_sha256(),
            split=split,
            train_block_index=0,
            games_per_opponent_seat=8,
            base_seed=campaign_config.seed + 800_000 + generation * 100_000,
                    include_control=True,
                    refs_override=split.ids(validation_split),
                    split_name=validation_split,
                    control_package=control_package,
                    pool_root=pool_root,
                )
        dev_eval = _evaluate_games(dev_games, gen_root / "dev", resource["recommended_workers"])
        dev_result = candidate_result_from_rows(
            dev_eval["rows"],
            candidate_policy_id=incumbent_id,
            control_policy_id=_control_identity(control_package)[0],
            weights=split.weights(validation_split),
            config=new_center,
            candidate_id=incumbent_id,
        )
    _write_new_json(
        gen_root / "results.json",
        {
            "schema_version": SCHEMA,
            "generation": generation,
            "results": results,
            "elites": [str(item["candidate_id"]) for item in elites],
            "initial_elites": [str(item["candidate_id"]) for item in initial_elites],
            "elite_selection": elite_selection,
            "new_center": new_center.as_dict(),
            "new_scales": new_scales,
            "dev": dev_result,
            "research_only": True,
        },
    )
    return {
        "generation": generation,
        "results": results,
        "elites": [dict(item) for item in elites],
        "initial_elites": [dict(item) for item in initial_elites],
        "elite_selection": elite_selection,
        "center": new_center,
        "scales": new_scales,
        "dev": dev_result,
        "resource_decision": resource,
    }


def run_campaign(
    *,
    output_root: Path | str,
    split_path: Path | str = DEFAULT_SPLIT,
    source_package: Path | str = P1_PACKAGE,
    target_generations: int = 1,
    resume: bool = False,
    perform_reeval: bool = True,
    all_train_refs: bool = False,
    control_package: Path | str = P1_PACKAGE,
    initial_config: P1ParameterConfig | None = None,
    reeval_for_update: bool = False,
    reeval_repeats: int = 1,
    reeval_games_per_opponent_seat: int = 4,
    positive_delta_gate: bool = False,
    include_dev_refs: bool = False,
    risk_aware_update: bool = False,
    initial_scale_fraction: float | None = None,
    campaign_seed: int = DEFAULT_CAMPAIGN_SEED,
    pool_root: Path | str = _ROOT / "opponents",
    population_size: int = 24,
    elite_count: int = 6,
) -> dict[str, object]:
    if type(target_generations) is not int or target_generations <= 0 or target_generations > 6:
        raise ValueError("target_generations must be in [1, 6]")
    if type(reeval_repeats) is not int or reeval_repeats <= 0:
        raise ValueError("reeval_repeats must be a positive integer")
    if type(reeval_games_per_opponent_seat) is not int or reeval_games_per_opponent_seat <= 0:
        raise ValueError("reeval_games_per_opponent_seat must be a positive integer")
    if type(positive_delta_gate) is not bool:
        raise ValueError("positive_delta_gate must be a boolean")
    if positive_delta_gate and not reeval_for_update:
        raise ValueError("positive_delta_gate requires reeval_for_update")
    if risk_aware_update and not reeval_for_update:
        raise ValueError("risk_aware_update requires reeval_for_update")
    if risk_aware_update and reeval_repeats < 2:
        raise ValueError("risk_aware_update requires at least two re-evaluation repeats")
    if include_dev_refs and not all_train_refs:
        raise ValueError("include_dev_refs requires all_train_refs")
    if initial_scale_fraction is not None:
        _scales_from_fraction(initial_scale_fraction)
    if type(campaign_seed) is not int or campaign_seed <= 0:
        raise ValueError("campaign_seed must be a positive integer")
    if type(population_size) is not int or population_size <= 0:
        raise ValueError("population_size must be a positive integer")
    if type(elite_count) is not int or elite_count <= 0 or elite_count > population_size:
        raise ValueError("elite_count must be in [1, population_size]")
    campaign_root = Path(output_root).resolve()
    pool_root = Path(pool_root).resolve()
    if not (pool_root / "pool_manifest.json").is_file():
        raise FileNotFoundError(f"pool manifest missing: {pool_root / 'pool_manifest.json'}")
    split = load_weekend_split(split_path, verify_sources=True)
    source = Path(source_package).resolve()
    if _sha256(source / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("source package is not the immutable P1 parent")
    if _sha256(source / "deck.csv") != split.metadata["bindings"]["p1_deck_sha256"]:
        raise ValueError("source package deck does not match split binding")
    control = Path(control_package).resolve()
    control_policy_id, control_policy_sha = _control_identity(control)
    if _sha256(control / "deck.csv") != split.metadata["bindings"]["p1_deck_sha256"]:
        raise ValueError("control package deck does not match split binding")
    if initial_config is None:
        initial_config = P1ParameterConfig.default()
    initial_config.validate()
    campaign_config = CemCampaignConfig(
        generations=target_generations,
        population_size=population_size,
        elite_count=elite_count,
        seed=campaign_seed,
    )
    campaign_config.validate()
    if include_dev_refs:
        campaign_search_mode = "META_TRAIN_PLUS_DEV"
    else:
        campaign_search_mode = "META_TRAIN_ALL" if all_train_refs else "ROTATING_BLOCKS"
    governor = ResourceGovernor(ResourceBudget.from_json(DEFAULT_BUDGET))
    if resume:
        manifest_path = campaign_root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"campaign manifest missing: {manifest_path}")
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing_manifest.get("search_mode") != campaign_search_mode:
            raise ValueError(
                "resume search mode mismatch: "
                f"{existing_manifest.get('search_mode')} != {campaign_search_mode}"
            )
        if existing_manifest.get("control_policy_sha256", control_policy_sha) != control_policy_sha:
            raise ValueError("resume control package mismatch")
        if existing_manifest.get("reeval_for_update", reeval_for_update) != reeval_for_update:
            raise ValueError("resume elite selection mode mismatch")
        if existing_manifest.get("reeval_repeats", reeval_repeats) != reeval_repeats:
            raise ValueError("resume re-evaluation repeat count mismatch")
        if existing_manifest.get("reeval_games_per_opponent_seat", 4) != reeval_games_per_opponent_seat:
            raise ValueError("resume re-evaluation games budget mismatch")
        if existing_manifest.get("positive_delta_gate", False) != positive_delta_gate:
            raise ValueError("resume positive-delta gate mismatch")
        if existing_manifest.get("risk_aware_update", risk_aware_update) != risk_aware_update:
            raise ValueError("resume risk-aware update mode mismatch")
        if existing_manifest.get("include_dev_refs", include_dev_refs) != include_dev_refs:
            raise ValueError("resume search reference expansion mismatch")
        if existing_manifest.get("initial_scale_fraction", initial_scale_fraction) != initial_scale_fraction:
            raise ValueError("resume initial scale fraction mismatch")
        if existing_manifest.get("campaign_seed", DEFAULT_CAMPAIGN_SEED) != campaign_seed:
            raise ValueError("resume campaign seed mismatch")
        if existing_manifest.get("pool_root", str(pool_root)) != str(pool_root):
            raise ValueError("resume pool root mismatch")
        if existing_manifest.get("population_size", population_size) != population_size:
            raise ValueError("resume population size mismatch")
        if existing_manifest.get("elite_count", elite_count) != elite_count:
            raise ValueError("resume elite count mismatch")
        state = load_latest_checkpoint(campaign_root)
        start_generation = state.generation + 1
        center = state.center
        scales = state.scales
        all_results = list(state.evaluated)
    else:
        if campaign_root.exists() and any(campaign_root.iterdir()):
            raise FileExistsError(f"campaign output is not empty: {campaign_root}")
        campaign_root.mkdir(parents=True, exist_ok=True)
        start_generation = 0
        center = initial_config
        scales = _scales_from_fraction(initial_scale_fraction) if initial_scale_fraction is not None else None
        all_results = []
        resource = _resource_gate(governor)
        _write_new_json(
            campaign_root / "manifest.json",
            {
                "schema_version": SCHEMA,
                "status": "RUNNING",
                "split_path": str(Path(split_path).resolve()),
                "split_sha256": split.config_sha256,
                "parent_policy_sha256": BASE_SOURCE_SHA256,
                "control_policy_id": control_policy_id,
                "control_policy_sha256": control_policy_sha,
                "control_package": str(control),
                "initial_config_sha256": initial_config.config_sha256(),
                "reeval_for_update": reeval_for_update,
                "reeval_repeats": reeval_repeats,
                "reeval_games_per_opponent_seat": reeval_games_per_opponent_seat,
                "positive_delta_gate": positive_delta_gate,
                "risk_aware_update": risk_aware_update,
                "include_dev_refs": include_dev_refs,
                "initial_scale_fraction": initial_scale_fraction,
                "campaign_seed": campaign_seed,
                "population_size": population_size,
                "elite_count": elite_count,
                "pool_root": str(pool_root),
                "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
                "parent_deck_sha256": split.metadata["bindings"]["p1_deck_sha256"],
                "evaluator_sha256": evaluation_implementation_sha256_v1(),
                "resource_decision": resource,
                "target_generations": target_generations,
                "search_mode": campaign_search_mode,
                "research_only": True,
                "champion_changed": False,
                "submission_sent": False,
            },
        )
        if target_generations == 0:
            return {"status": "READY", "output_root": str(campaign_root)}
    if start_generation >= target_generations:
        return {"status": "COMPLETE", "output_root": str(campaign_root), "generations": start_generation}
    try:
        for generation in range(start_generation, target_generations):
            outcome = run_generation(
                campaign_root=campaign_root,
                split=split,
                source_package=source,
                center=center,
                scales=scales,
                generation=generation,
                campaign_config=campaign_config,
                governor=governor,
                perform_reeval=perform_reeval,
                all_train_refs=all_train_refs,
                control_package=control,
                reeval_for_update=reeval_for_update,
                reeval_repeats=reeval_repeats,
                reeval_games_per_opponent_seat=reeval_games_per_opponent_seat,
                positive_delta_gate=positive_delta_gate,
                include_dev_refs=include_dev_refs,
                risk_aware_update=risk_aware_update,
                pool_root=pool_root,
            )
            all_results.extend(outcome["results"])
            state = CemState(
                generation=generation,
                center=outcome["center"],
                scales=outcome["scales"],
                next_candidate_index=campaign_config.population_size,
                evaluated=all_results,
                campaign_identity={
                    "schema_version": SCHEMA,
                    "split_sha256": split.config_sha256,
                    "parent_policy_sha256": BASE_SOURCE_SHA256,
                    "control_policy_id": control_policy_id,
                    "control_policy_sha256": control_policy_sha,
                    "reeval_for_update": reeval_for_update,
                    "reeval_repeats": reeval_repeats,
                    "reeval_games_per_opponent_seat": reeval_games_per_opponent_seat,
                    "positive_delta_gate": positive_delta_gate,
                    "risk_aware_update": risk_aware_update,
                    "include_dev_refs": include_dev_refs,
                    "initial_scale_fraction": initial_scale_fraction,
                    "campaign_seed": campaign_seed,
                    "population_size": population_size,
                    "elite_count": elite_count,
                    "evaluator_sha256": evaluation_implementation_sha256_v1(),
                    "search_mode": campaign_search_mode,
                    "pool_root": str(pool_root),
                    "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
                },
            )
            save_checkpoint(campaign_root, state)
            center, scales = outcome["center"], outcome["scales"]
        manifest_path = campaign_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"status": "COMPLETE", "completed_generations": target_generations})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return {"status": "COMPLETE", "output_root": str(campaign_root), "generations": target_generations, "results": all_results}
    except Exception as exc:
        stop = {
            "schema_version": SCHEMA,
            "status": "STOPPED",
            "reason": f"{type(exc).__name__}: {exc}",
            "research_only": True,
            "champion_changed": False,
            "submission_sent": False,
        }
        if not (campaign_root / "stop.json").exists():
            _write_new_json(campaign_root / "stop.json", stop)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--source-package", type=Path, default=P1_PACKAGE)
    parser.add_argument(
        "--control-package",
        type=Path,
        default=P1_PACKAGE,
        help="package used as the paired control; defaults to immutable P1",
    )
    parser.add_argument(
        "--pool-root",
        type=Path,
        default=_ROOT / "opponents",
        help="hash-bound staged opponent pool; defaults to the repository pool",
    )
    parser.add_argument(
        "--initial-config-json",
        type=Path,
        help="candidate manifest or raw parameter mapping used as the initial CEM center",
    )
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-reeval", action="store_true")
    parser.add_argument(
        "--reeval-for-update",
        action="store_true",
        help="rank the elite set by its independent re-evaluation before updating CEM",
    )
    parser.add_argument(
        "--reeval-repeats",
        type=int,
        default=1,
        help="number of independent META_TRAIN re-evaluation blocks per screen elite",
    )
    parser.add_argument(
        "--reeval-games-per-opponent-seat",
        type=int,
        default=4,
        help="games per opponent and seat in each independent re-evaluation block",
    )
    parser.add_argument(
        "--positive-delta-gate",
        action="store_true",
        help="preserve the current center unless enough independent candidates beat control",
    )
    parser.add_argument(
        "--risk-aware-update",
        action="store_true",
        help="rank re-evaluated elites by their worst independent block (requires two repeats)",
    )
    parser.add_argument(
        "--all-train-refs",
        action="store_true",
        help="use all META_TRAIN refs with two repetitions per seat in each CEM generation",
    )
    parser.add_argument(
        "--include-dev-refs",
        action="store_true",
        help="with --all-train-refs, expand CEM search to META_TRAIN + META_DEV and validate on META_FINAL",
    )
    parser.add_argument(
        "--initial-scale-fraction",
        type=float,
        help="local-search Gaussian scale as a fraction of each parameter span (0, 1]; omitted keeps the broad default",
    )
    parser.add_argument(
        "--campaign-seed",
        type=int,
        default=DEFAULT_CAMPAIGN_SEED,
        help="independent CEM population/agent seed recorded in the campaign manifest",
    )
    parser.add_argument(
        "--population-size",
        type=int,
        default=24,
        help="CEM candidates per generation (explicitly recorded; default 24)",
    )
    parser.add_argument(
        "--elite-count",
        type=int,
        default=6,
        help="maximum independent elites per generation (must be <= population size)",
    )
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for heavy CABT execution")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing heavy CEM run without --execute")
    initial_config = _load_initial_config(args.initial_config_json) if args.initial_config_json else None
    result = run_campaign(
        output_root=args.output,
        split_path=args.split,
        source_package=args.source_package,
        control_package=args.control_package,
        initial_config=initial_config,
        target_generations=args.generations,
        resume=args.resume,
        perform_reeval=not args.no_reeval,
        all_train_refs=args.all_train_refs,
        reeval_for_update=args.reeval_for_update,
        reeval_repeats=args.reeval_repeats,
        reeval_games_per_opponent_seat=args.reeval_games_per_opponent_seat,
        positive_delta_gate=args.positive_delta_gate,
        include_dev_refs=args.include_dev_refs,
        risk_aware_update=args.risk_aware_update,
        initial_scale_fraction=args.initial_scale_fraction,
        campaign_seed=args.campaign_seed,
        pool_root=args.pool_root,
        population_size=args.population_size,
        elite_count=args.elite_count,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "results"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
