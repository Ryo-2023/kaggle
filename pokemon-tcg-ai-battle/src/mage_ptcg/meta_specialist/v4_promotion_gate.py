"""Fixed, fail-closed promotion gate for V4 held-out experiments.

This module does not run games or choose a checkpoint.  It only compares
already sealed candidate/baseline result artifacts under one evaluation
identity and applies the same overall, seat, matchup, and offline action
thresholds every time.  A missing artifact, identity drift, fault, or missing
diagnostic is a ``NO_GO`` rather than an implicit pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1


V4_PROMOTION_GATE_SCHEMA_V1 = "meta-specialist-v4-promotion-gate-v1"
EXPECTED_HELDOUT_SCHEMAS = frozenset({
    "meta-specialist-v4-heldout-checkpoint-strength-v1",
    "meta-specialist-v2-fixed-heldout-checkpoint-strength-v1",
})
EXPECTED_IMITATION_SCHEMA = "meta-specialist-v4-imitation-metrics-v1"

# These are fixed guardrails, not tunable per-run thresholds.  The action-type
# floors are the Wave5 pilot mean minus three percentage points for common
# action types; brittle types use a collapse floor only.
ACTION_COMPLETE_MIN_PER_SEED = 0.68
ACTION_COMPLETE_MIN_MEAN = 0.70
ACTION_ROOT_MIN_MEAN = 0.71
ACTION_STOP_MIN_PER_SEED = 0.80
ACTION_COMMON_MIN_MEAN = {"3": 0.7363, "7": 0.6557, "8": 0.6881, "13": 0.6293}
# Wave6 carry validation floors for the explicitly targeted action families.
# These are deliberately below the current measured values, but well above
# the old collapse-only floor so a candidate cannot hide a real END/EVOLVE/
# ATTACK regression behind strong frequent-action metrics.
ACTION_FOCUS_MIN_PER_SEED = {"9": 0.60, "13": 0.60, "14": 0.50}
ACTION_BRITTLE_COLLAPSE_FLOOR = 0.05
OVERALL_MIN_MEAN_DELTA = 0.05
MATCHUP_MIN_NONNEGATIVE = 4
SEAT_MIN_NONNEGATIVE_CELLS = 3
MATCHUP_MAX_DROP = -0.25
_HEX = frozenset("0123456789abcdef")


class V4PromotionGateError(ValueError):
    """Raised for malformed direct API input; artifact failures become NO_GO."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise V4PromotionGateError(f"{field} must be a lowercase SHA-256 string")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise V4PromotionGateError(f"{name} must be a finite number")
    return float(value)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise V4PromotionGateError(f"{name} must be a non-negative integer")
    return value


def _load_json(path: str | Path) -> tuple[dict[str, Any], str]:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise V4PromotionGateError(f"artifact is not a regular file: {candidate}")
    raw = candidate.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise V4PromotionGateError(f"artifact root is not an object: {candidate}")
    return value, digest


def _row_score(row: Mapping[str, object], *, name: str) -> float:
    requested = _integer(row.get("requested"), f"{name}.requested")
    wins = _integer(row.get("w"), f"{name}.w")
    draws = _integer(row.get("d"), f"{name}.d")
    losses = _integer(row.get("l"), f"{name}.l")
    faults = _integer(row.get("f"), f"{name}.f")
    if requested <= 0 or wins + draws + losses + faults != requested:
        raise V4PromotionGateError(f"{name} outcome counts do not close")
    if faults:
        raise V4PromotionGateError(f"{name} contains faults")
    return (wins + 0.5 * draws) / requested


def _fingerprints(value: object) -> str:
    if not isinstance(value, list):
        raise V4PromotionGateError("opponent_fingerprints must be a list")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _identity(report: Mapping[str, object]) -> dict[str, object]:
    if report.get("schema_version") not in EXPECTED_HELDOUT_SCHEMAS:
        raise V4PromotionGateError("held-out schema is not a supported fixed strength schema")
    ids = report.get("fixed_held_out_opponent_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids):
        raise V4PromotionGateError("fixed opponent list is invalid")
    opponent_ids = report.get("opponent_ids")
    if opponent_ids != ids:
        raise V4PromotionGateError("opponent_ids do not match fixed list")
    return {
        "base_seed": _integer(report.get("base_seed"), "base_seed"),
        "subject_archetype_id": report.get("subject_archetype_id"),
        "subject_deck_file_sha256": report.get("subject_deck_file_sha256"),
        "fixed_held_out_opponent_ids": tuple(ids),
        "games_per_seat": _integer(report.get("games_per_seat"), "games_per_seat"),
        "max_steps": _integer(report.get("max_steps"), "max_steps"),
        "evaluation_protocol_sha256": report.get("evaluation_protocol_sha256"),
        "opponent_fingerprints": _fingerprints(report.get("opponent_fingerprints")),
    }


def _validate_heldout(report: Mapping[str, object], *, label: str) -> dict[str, object]:
    identity = _identity(report)
    checkpoint = report.get("checkpoint")
    if type(checkpoint) is not dict:
        raise V4PromotionGateError(f"{label}.checkpoint is missing")
    checkpoint_binding = {"file_sha256": _sha(checkpoint.get("file_sha256"), field=f"{label}.checkpoint.file_sha256")}
    tensor_state_sha256 = checkpoint.get("tensor_state_sha256")
    if tensor_state_sha256 is not None:
        checkpoint_binding["tensor_state_sha256"] = _sha(
            tensor_state_sha256, field=f"{label}.checkpoint.tensor_state_sha256",
        )
    elif report.get("schema_version") != "meta-specialist-v2-fixed-heldout-checkpoint-strength-v1":
        raise V4PromotionGateError(f"{label}.checkpoint.tensor_state_sha256 is missing")
    if identity["evaluation_protocol_sha256"] != heldout_protocol_sha256_v1():
        raise V4PromotionGateError(f"{label}.evaluation_protocol_sha256 is missing or not the shared protocol")
    if report.get("comparison_status") != "valid":
        raise V4PromotionGateError(f"{label}.comparison_status is not valid")
    requested = _integer(report.get("requested_games"), f"{label}.requested_games")
    played = _integer(report.get("games_played"), f"{label}.games_played")
    faults = _integer(report.get("faults"), f"{label}.faults")
    if requested != 96 or played != requested or faults != 0:
        raise V4PromotionGateError(f"{label} is incomplete or contains faults")
    if not isinstance(report.get("per_opponent"), dict) or set(report["per_opponent"]) != set(identity["fixed_held_out_opponent_ids"]):
        raise V4PromotionGateError(f"{label}.per_opponent does not cover the fixed pool")
    if not isinstance(report.get("seat"), dict) or set(report["seat"]) != {"0", "1"}:
        raise V4PromotionGateError(f"{label}.seat does not cover both seats")
    overall = _row_score(
        {
            "requested": requested,
            "w": _integer(report.get("wins"), f"{label}.wins"),
            "d": _integer(report.get("draws"), f"{label}.draws"),
            "l": _integer(report.get("losses"), f"{label}.losses"),
            "f": faults,
        },
        name=f"{label}.overall",
    )
    per_opponent = {str(key): _row_score(value, name=f"{label}.per_opponent.{key}") for key, value in report["per_opponent"].items()}
    seat = {str(key): _row_score(value, name=f"{label}.seat.{key}") for key, value in report["seat"].items()}
    return {
        "identity": identity, "checkpoint": checkpoint_binding,
        "overall": overall, "per_opponent": per_opponent, "seat": seat,
    }


def _same_identity(reports: Sequence[dict[str, object]]) -> bool:
    if not reports:
        return False
    return all(report["identity"] == reports[0]["identity"] for report in reports[1:])


def _metric_number(mapping: Mapping[str, object], key: str, name: str) -> float:
    value = mapping.get(key)
    return _finite_number(value, name)


def _action_checks_payload(
    payload: Mapping[str, object], *, artifact_sha256: str,
    expected_identity: Mapping[str, object], expected_seeds: int,
    expected_checkpoints: Mapping[int, Mapping[str, str]],
) -> tuple[dict[str, object], list[str]]:
    reasons: list[str] = []
    if payload.get("schema") != EXPECTED_IMITATION_SCHEMA:
        reasons.append("imitation_schema_mismatch")
    if payload.get("lane") != expected_identity["subject_archetype_id"]:
        reasons.append("imitation_lane_mismatch")
    seeds = payload.get("seed_results")
    if not isinstance(seeds, dict) or set(seeds) != {str(index) for index in range(expected_seeds)}:
        reasons.append("imitation_seed_set_mismatch")
        return {"artifact_sha256": artifact_sha256}, reasons
    rows: list[dict[str, object]] = []
    for seed in sorted(seeds, key=int):
        try:
            checkpoint = seeds[seed].get("checkpoint")
            expected_checkpoint = expected_checkpoints.get(int(seed))
            if type(checkpoint) is not dict or expected_checkpoint is None:
                raise V4PromotionGateError("imitation checkpoint binding is missing")
            if (
                _sha(checkpoint.get("file_sha256"), field=f"imitation.{seed}.checkpoint.file_sha256") != expected_checkpoint["file_sha256"]
                or _sha(checkpoint.get("tensor_state_sha256"), field=f"imitation.{seed}.checkpoint.tensor_state_sha256") != expected_checkpoint["tensor_state_sha256"]
            ):
                raise V4PromotionGateError("imitation checkpoint binding does not match held-out candidate")
            validation = seeds[seed]["partitions"]["validation"]["recurrence"]["carry"]
            if (
                type(validation) is not dict
                or validation.get("schema") != EXPECTED_IMITATION_SCHEMA
                or validation.get("partition") != "validation"
                or validation.get("recurrence") != "carry"
            ):
                raise V4PromotionGateError("nested validation imitation schema is invalid")
            complete = validation["complete_action"]
            root = validation["root"]
            action_type = validation["action_type"]
            complete_top1 = _metric_number(complete, "top1", f"imitation.{seed}.complete_action.top1")
            root_top1 = _metric_number(root, "top1", f"imitation.{seed}.root.top1")
            forced = _integer(complete.get("forced_domain_size1_rows"), f"imitation.{seed}.forced_domain_size1_rows")
            eligible = _integer(complete.get("eligible_rows"), f"imitation.{seed}.eligible_rows")
            if eligible <= 0 or forced >= eligible:
                raise V4PromotionGateError("complete-action metric has no non-forced rows")
            stop = _metric_number(action_type["STOP"], "top1", f"imitation.{seed}.STOP.top1")
            action_values = {key: _metric_number(action_type[key], "top1", f"imitation.{seed}.action_type.{key}.top1") for key in (*ACTION_COMMON_MIN_MEAN, "9", "12", "14")}
            rows.append({"seed": int(seed), "complete_top1": complete_top1, "root_top1": root_top1, "stop_top1": stop, "action_type": action_values})
        except (KeyError, TypeError, V4PromotionGateError) as exc:
            reasons.append(f"imitation_metric_invalid_seed_{seed}:{exc}")
    if len(rows) != expected_seeds:
        return {"artifact_sha256": artifact_sha256, "seed_results": rows}, reasons
    complete_values = [row["complete_top1"] for row in rows]
    root_values = [row["root_top1"] for row in rows]
    stop_values = [row["stop_top1"] for row in rows]
    if any(value < ACTION_COMPLETE_MIN_PER_SEED for value in complete_values):
        reasons.append("action_complete_seed_threshold")
    if sum(complete_values) / len(complete_values) < ACTION_COMPLETE_MIN_MEAN:
        reasons.append("action_complete_mean_threshold")
    if sum(root_values) / len(root_values) < ACTION_ROOT_MIN_MEAN:
        reasons.append("action_root_mean_threshold")
    if any(value < ACTION_STOP_MIN_PER_SEED for value in stop_values):
        reasons.append("action_stop_seed_threshold")
    action_means: dict[str, float] = {}
    for action_type in (*ACTION_COMMON_MIN_MEAN, "9", "12", "14"):
        values = [row["action_type"][action_type] for row in rows]
        action_means[action_type] = sum(values) / len(values)
        floor = ACTION_COMMON_MIN_MEAN.get(action_type, ACTION_BRITTLE_COLLAPSE_FLOOR)
        focus_floor = ACTION_FOCUS_MIN_PER_SEED.get(action_type)
        if focus_floor is not None and any(value < focus_floor for value in values):
            reasons.append(f"action_type_{action_type}_focus_threshold")
        if action_means[action_type] < floor:
            reasons.append(f"action_type_{action_type}_threshold")
        if any(value < ACTION_BRITTLE_COLLAPSE_FLOOR for value in values):
            reasons.append(f"action_type_{action_type}_collapse")
    return {
        "artifact_sha256": artifact_sha256,
        "seed_results": rows,
        "complete_action_mean": sum(complete_values) / len(complete_values),
        "root_mean": sum(root_values) / len(root_values),
        "stop_values": stop_values,
        "action_type_means": action_means,
        "focus_action_type_floors": dict(ACTION_FOCUS_MIN_PER_SEED),
    }, reasons


def _action_checks(
    path: str | Path, *, expected_identity: dict[str, object], expected_seeds: int,
    expected_checkpoints: Mapping[int, Mapping[str, str]],
) -> tuple[dict[str, object], list[str]]:
    payload, digest = _load_json(path)
    return _action_checks_payload(
        payload, artifact_sha256=digest, expected_identity=expected_identity,
        expected_seeds=expected_seeds, expected_checkpoints=expected_checkpoints,
    )


def validate_v4_imitation_metrics_payload(
    payload: Mapping[str, object], *, expected_lane: str, expected_seeds: int = 2,
    expected_checkpoints: Mapping[int, Mapping[str, str]], artifact_sha256: str = "inline",
) -> tuple[dict[str, object], list[str]]:
    """Validate inline or externally sealed V4 action metrics identically.

    DAgger reports embed the carry validation metrics beside each checkpoint.
    This public wrapper lets short-gate summarizers apply the promotion-gate
    thresholds without reimplementing or weakening them.
    """
    if type(expected_lane) is not str or not expected_lane:
        raise V4PromotionGateError("expected_lane must be a non-empty string")
    expected_identity = {"subject_archetype_id": expected_lane}
    return _action_checks_payload(
        payload, artifact_sha256=artifact_sha256,
        expected_identity=expected_identity, expected_seeds=expected_seeds,
        expected_checkpoints=expected_checkpoints,
    )


def evaluate_v4_promotion_gate(
    candidate_paths: Sequence[str | Path], baseline_paths: Sequence[str | Path], *, imitation_path: str | Path | None = None,
) -> dict[str, object]:
    """Evaluate exactly two candidate and two baseline seed artifacts.

    The function is intentionally pure with respect to model selection: it
    returns a report and never copies, promotes, or deletes a checkpoint.
    """
    if len(candidate_paths) != 2 or len(baseline_paths) != 2:
        raise V4PromotionGateError("exactly two candidate and two baseline seed artifacts are required")
    reasons: list[str] = []
    candidate_rows: list[dict[str, object]] = []
    baseline_rows: list[dict[str, object]] = []
    artifact_hashes: dict[str, str] = {}
    for role, paths, target in (("candidate", candidate_paths, candidate_rows), ("baseline", baseline_paths, baseline_rows)):
        for index, path in enumerate(paths):
            label = f"{role}_{index}"
            try:
                payload, digest = _load_json(path)
                artifact_hashes[label] = digest
                target.append(_validate_heldout(payload, label=label))
            except (OSError, json.JSONDecodeError, V4PromotionGateError, UnicodeDecodeError) as exc:
                reasons.append(f"{label}_invalid:{exc}")
    if len(candidate_rows) == 2 and len(baseline_rows) == 2:
        all_rows = candidate_rows + baseline_rows
        if not _same_identity(all_rows):
            reasons.append("identity_mismatch")
        candidate_scores = [row["overall"] for row in candidate_rows]
        baseline_scores = [row["overall"] for row in baseline_rows]
        seed_deltas = [candidate_scores[index] - baseline_scores[index] for index in range(2)]
        mean_delta = sum(seed_deltas) / 2.0
        if any(delta <= 0.0 for delta in seed_deltas):
            reasons.append("overall_seed_not_better")
        if mean_delta < OVERALL_MIN_MEAN_DELTA:
            reasons.append("overall_mean_delta_threshold")
        candidate_seat = {seat: sum(row["seat"][seat] for row in candidate_rows) / 2.0 for seat in ("0", "1")}
        baseline_seat = {seat: sum(row["seat"][seat] for row in baseline_rows) / 2.0 for seat in ("0", "1")}
        seat_deltas = {seat: candidate_seat[seat] - baseline_seat[seat] for seat in ("0", "1")}
        seat_cells = [candidate_rows[index]["seat"][seat] - baseline_rows[index]["seat"][seat] for index in range(2) for seat in ("0", "1")]
        if any(value < 0.0 for value in seat_deltas.values()):
            reasons.append("seat_mean_negative")
        if sum(value >= 0.0 for value in seat_cells) < SEAT_MIN_NONNEGATIVE_CELLS:
            reasons.append("seat_cell_coverage")
        opponent_ids = candidate_rows[0]["identity"]["fixed_held_out_opponent_ids"]
        candidate_matchup = {opponent: sum(row["per_opponent"][opponent] for row in candidate_rows) / 2.0 for opponent in opponent_ids}
        baseline_matchup = {opponent: sum(row["per_opponent"][opponent] for row in baseline_rows) / 2.0 for opponent in opponent_ids}
        matchup_deltas = {opponent: candidate_matchup[opponent] - baseline_matchup[opponent] for opponent in opponent_ids}
        if sum(value >= 0.0 for value in matchup_deltas.values()) < MATCHUP_MIN_NONNEGATIVE:
            reasons.append("matchup_nonnegative_coverage")
        if min(matchup_deltas.values(), default=0.0) < MATCHUP_MAX_DROP:
            reasons.append("matchup_collapse")
        checks: dict[str, object] = {
            "overall": {"candidate_scores": candidate_scores, "baseline_scores": baseline_scores, "seed_deltas": seed_deltas, "mean_delta": mean_delta},
            "seat": {"candidate_mean": candidate_seat, "baseline_mean": baseline_seat, "deltas": seat_deltas, "cell_deltas": seat_cells},
            "matchup": {"candidate_mean": candidate_matchup, "baseline_mean": baseline_matchup, "deltas": matchup_deltas, "nonnegative_count": sum(value >= 0.0 for value in matchup_deltas.values())},
        }
        expected_identity = candidate_rows[0]["identity"]
    else:
        checks = {}
        expected_identity = None
    action_checks: dict[str, object] = {}
    if imitation_path is None:
        reasons.append("missing_imitation_metrics")
    elif expected_identity is None:
        reasons.append("action_metrics_not_evaluable")
    else:
        try:
            expected_checkpoints = {
                index: candidate_rows[index]["checkpoint"] for index in range(2)
            }
            action_checks, action_reasons = _action_checks(
                imitation_path, expected_identity=expected_identity, expected_seeds=2,
                expected_checkpoints=expected_checkpoints,
            )
            reasons.extend(action_reasons)
        except (OSError, json.JSONDecodeError, V4PromotionGateError, UnicodeDecodeError) as exc:
            reasons.append(f"imitation_invalid:{exc}")
    return {
        "schema": V4_PROMOTION_GATE_SCHEMA_V1,
        "decision": "PROMOTION_READY" if not reasons else "NO_GO",
        "reasons": sorted(set(reasons)),
        "artifact_sha256": artifact_hashes,
        "checks": checks,
        "action_checks": action_checks,
        "identity": expected_identity,
    }


__all__ = [
    "V4_PROMOTION_GATE_SCHEMA_V1", "V4PromotionGateError", "evaluate_v4_promotion_gate",
    "validate_v4_imitation_metrics_payload",
]
