#!/usr/bin/env python3
"""Summarize a sealed two-seed V4 DAgger short-gate comparison.

This command only reads completed artifacts.  It never runs games, trains a
model, promotes a checkpoint, or changes the Wave6 baseline.  Missing candidate
evaluations are reported as ``UNMEASURED`` rather than being treated as a pass.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.v4_promotion_gate import (  # noqa: E402
    validate_v4_imitation_metrics_payload,
)


EVALUATION_SCHEMA = "meta-specialist-v4-heldout-checkpoint-strength-v1"
REPORT_SCHEMA = "meta-specialist-v4-dagger-bc-report-v1"
OPPONENTS = (
    "kiyotah_lucario",
    "sue124_alakazam",
    "skarin_dragapult",
    "ozawa_crustle_v2",
    "nihei_megalopunny",
    "yaroslav_crustleaware_lucario",
)
SEEDS = (0, 1)
EXPECTED_REQUESTED_GAMES = 192
EXPECTED_GAMES_PER_SEAT = 16
EXPECTED_BASE_SEED = 12_500_000
EXPECTED_MAX_STEPS = 2_000
_HEX = frozenset("0123456789abcdef")


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase 64-character SHA-256")
    return value


def _read_json(path: Path, expected_sha: str, *, field: str) -> dict[str, object]:
    expected = _sha(expected_sha, field=f"{field}_sha256")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{field} cannot be read: {path}") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError(f"{field} bytes do not match the supplied SHA-256")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not valid UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{field} root must be an object")
    return value


def _parse_seed_path(value: str, *, field: str) -> tuple[int, Path]:
    if "=" not in value:
        raise ValueError(f"{field} must have the form SEED=PATH")
    seed_text, path_text = value.split("=", 1)
    try:
        seed = int(seed_text)
    except ValueError as exc:
        raise ValueError(f"{field} seed is not an integer: {seed_text!r}") from exc
    if seed not in SEEDS or not path_text:
        raise ValueError(f"{field} seed/path is invalid")
    return seed, Path(path_text)


def _require_seed_map(values: Sequence[str] | None, *, field: str) -> dict[int, Path]:
    if not values:
        return {}
    result: dict[int, Path] = {}
    for value in values:
        seed, path = _parse_seed_path(value, field=field)
        if seed in result:
            raise ValueError(f"{field} contains a duplicate seed")
        result[seed] = path
    if set(result) != set(SEEDS):
        raise ValueError(f"{field} must contain exactly seeds 0 and 1")
    return result


def _require_seed_sha_map(values: Sequence[str] | None, *, field: str) -> dict[int, str]:
    if not values:
        return {}
    result: dict[int, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{field} must have the form SEED=SHA256")
        seed_text, sha = value.split("=", 1)
        try:
            seed = int(seed_text)
        except ValueError as exc:
            raise ValueError(f"{field} seed is not an integer: {seed_text!r}") from exc
        if seed not in SEEDS or seed in result:
            raise ValueError(f"{field} contains an invalid or duplicate seed")
        result[seed] = _sha(sha, field=f"{field}[{seed}]")
    if set(result) != set(SEEDS):
        raise ValueError(f"{field} must contain exactly seeds 0 and 1")
    return result


def _row(payload: Mapping[str, object], *, field: str, expected_requested: int) -> dict[str, object]:
    if type(payload) is not dict:
        raise ValueError(f"{field} must be an object")
    normalized = dict(payload)
    if "wins" in payload:
        normalized.update({
            "w": payload.get("wins"), "d": payload.get("draws"),
            "l": payload.get("losses"), "f": payload.get("faults"),
        })
    # The held-out evaluator uses ``requested_games`` for the top-level row,
    # while seat/opponent rows use ``requested``.  Some subgroup rows omit
    # ``games_played`` because it is derivable from requested minus faults.
    if "requested" not in normalized:
        normalized["requested"] = normalized.get("requested_games", expected_requested)
    if "games_played" not in normalized:
        faults = normalized.get("f")
        if type(faults) is int and faults >= 0:
            normalized["games_played"] = int(normalized["requested"]) - faults
    values: dict[str, int] = {}
    for key in ("w", "d", "l", "f", "requested", "games_played"):
        value = normalized.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"{field}.{key} must be a non-negative integer")
        values[key] = value
    if values["requested"] != expected_requested:
        raise ValueError(f"{field}.requested must be {expected_requested}")
    if values["games_played"] != values["requested"] - values["f"]:
        raise ValueError(f"{field}.games_played disagrees with faults")
    if values["w"] + values["d"] + values["l"] + values["f"] != values["requested"]:
        raise ValueError(f"{field} outcome counts do not sum to requested games")
    score_rate = normalized.get("score_rate")
    if type(score_rate) not in {int, float} or isinstance(score_rate, bool) or not math.isfinite(float(score_rate)):
        raise ValueError(f"{field}.score_rate is invalid")
    return values


def _validate_evaluation(
    payload: Mapping[str, object], *, field: str, expected_checkpoint: Mapping[str, str] | None,
    protocol_sha: str | None,
) -> dict[str, object]:
    if payload.get("schema_version") != EVALUATION_SCHEMA:
        raise ValueError(f"{field} has an unsupported schema")
    if payload.get("fixed_held_out_opponent_ids") != list(OPPONENTS) or payload.get("opponent_ids") != list(OPPONENTS):
        raise ValueError(f"{field} does not use the fixed six-opponent pool")
    if payload.get("games_per_seat") != EXPECTED_GAMES_PER_SEAT:
        raise ValueError(f"{field}.games_per_seat is not the fixed protocol")
    if payload.get("base_seed") != EXPECTED_BASE_SEED or payload.get("max_steps") != EXPECTED_MAX_STEPS:
        raise ValueError(f"{field} uses a different evaluation protocol")
    if payload.get("requested_games") != EXPECTED_REQUESTED_GAMES:
        raise ValueError(f"{field}.requested_games is not {EXPECTED_REQUESTED_GAMES}")
    current_protocol = _sha(payload.get("evaluation_protocol_sha256"), field=f"{field}.evaluation_protocol_sha256")
    if protocol_sha is not None and current_protocol != protocol_sha:
        raise ValueError(f"{field} evaluation protocol differs from the baseline")
    checkpoint = payload.get("checkpoint")
    if type(checkpoint) is not dict:
        raise ValueError(f"{field} has no checkpoint binding")
    checkpoint_file = _sha(checkpoint.get("file_sha256"), field=f"{field}.checkpoint.file_sha256")
    checkpoint_tensor = _sha(checkpoint.get("tensor_state_sha256"), field=f"{field}.checkpoint.tensor_state_sha256")
    if expected_checkpoint is not None and (
        checkpoint_file != expected_checkpoint["file_sha256"]
        or checkpoint_tensor != expected_checkpoint["tensor_state_sha256"]
    ):
        raise ValueError(f"{field} checkpoint does not match the sealed training report")
    faults = payload.get("faults")
    if type(faults) is not int or faults < 0 or faults > EXPECTED_REQUESTED_GAMES:
        raise ValueError(f"{field}.faults is invalid")
    overall = _row(payload, field=f"{field}.overall", expected_requested=EXPECTED_REQUESTED_GAMES)
    if faults != overall["f"]:
        raise ValueError(f"{field}.faults disagrees with overall row")
    seat_payload = payload.get("seat")
    if type(seat_payload) is not dict or set(seat_payload) != {"0", "1"}:
        raise ValueError(f"{field}.seat is incomplete")
    seats = {
        seat: _row(seat_payload[str(seat)], field=f"{field}.seat.{seat}", expected_requested=96)
        for seat in (0, 1)
    }
    opponent_payload = payload.get("per_opponent")
    if type(opponent_payload) is not dict or set(opponent_payload) != set(OPPONENTS):
        raise ValueError(f"{field}.per_opponent is incomplete")
    opponents = {
        opponent: _row(opponent_payload[opponent], field=f"{field}.per_opponent.{opponent}", expected_requested=32)
        for opponent in OPPONENTS
    }
    for grouping, rows in (("seat", seats), ("per_opponent", opponents)):
        for key in ("w", "d", "l", "f", "requested", "games_played"):
            if sum(row[key] for row in rows.values()) != overall[key]:
                raise ValueError(f"{field}.{grouping} {key} disagrees with overall")
    return {
        "raw": dict(payload), "protocol_sha": current_protocol, "checkpoint": {
            "file_sha256": checkpoint_file, "tensor_state_sha256": checkpoint_tensor,
        }, "overall": overall, "seat": seats, "opponent": opponents,
    }


def _sum_rows(rows: Sequence[Mapping[str, int]]) -> dict[str, int]:
    result = {key: sum(int(row[key]) for row in rows) for key in ("w", "d", "l", "f", "requested", "games_played")}
    # Keep the evaluator's public names in the aggregate as well as the
    # compact internal names used by the gate calculations.
    result.update({"wins": result["w"], "draws": result["d"], "losses": result["l"], "faults": result["f"]})
    return result


def _rate(row: Mapping[str, int]) -> float:
    return float(row["w"]) / float(row["requested"])


def _delta(candidate: Mapping[str, int], baseline: Mapping[str, int]) -> float:
    return _rate(candidate) - _rate(baseline)


def _check(status: str, **extra: object) -> dict[str, object]:
    return {"status": status, **extra}


def _inline_imitation_payload(report: Mapping[str, object]) -> dict[str, object] | None:
    """Adapt per-seed DAgger metrics to the fixed promotion-gate schema."""
    lane = report.get("lane")
    seed_results = report.get("seed_results")
    if type(lane) is not str or type(seed_results) is not dict:
        return None
    wrapped: dict[str, object] = {}
    for seed in ("0", "1"):
        row = seed_results.get(seed)
        if type(row) is not dict or type(row.get("validation_imitation_metrics")) is not dict:
            return None
        wrapped[seed] = {
            "checkpoint": {
                "file_sha256": row.get("best_checkpoint_file_sha256"),
                "tensor_state_sha256": row.get("best_checkpoint_tensor_state_sha256"),
            },
            "partitions": {
                "validation": {
                    "recurrence": {"carry": row["validation_imitation_metrics"]},
                },
            },
        }
    return {
        "schema": "meta-specialist-v4-imitation-metrics-v1",
        "lane": lane,
        "seed_results": wrapped,
    }


def _compare(
    baseline: Mapping[int, Mapping[str, object]], candidate: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    by_seed: dict[str, object] = {}
    for seed in SEEDS:
        base = baseline[seed]["overall"]
        cand = candidate[seed]["overall"]
        by_seed[str(seed)] = {
            "candidate": dict(cand), "baseline": dict(base), "delta_score_rate": _delta(cand, base),
        }
    pooled_base = _sum_rows([baseline[seed]["overall"] for seed in SEEDS])
    pooled_candidate = _sum_rows([candidate[seed]["overall"] for seed in SEEDS])
    by_seat: dict[str, object] = {}
    for seat in (0, 1):
        base = _sum_rows([baseline[seed]["seat"][seat] for seed in SEEDS])
        cand = _sum_rows([candidate[seed]["seat"][seat] for seed in SEEDS])
        by_seat[str(seat)] = {"candidate": cand, "baseline": base, "delta_score_rate": _delta(cand, base)}
    by_opponent: dict[str, object] = {}
    for opponent in OPPONENTS:
        base = _sum_rows([baseline[seed]["opponent"][opponent] for seed in SEEDS])
        cand = _sum_rows([candidate[seed]["opponent"][opponent] for seed in SEEDS])
        by_opponent[opponent] = {"candidate": cand, "baseline": base, "delta_score_rate": _delta(cand, base)}
    return {
        "pooled": {"candidate": pooled_candidate, "baseline": pooled_base, "delta_score_rate": _delta(pooled_candidate, pooled_base)},
        "by_seed": by_seed, "by_seat": by_seat, "by_opponent": by_opponent,
    }


def _short_gate(
    baseline: Mapping[int, Mapping[str, object]], candidate: Mapping[int, Mapping[str, object]],
    comparison: Mapping[str, object], action_metrics_check: Mapping[str, object],
) -> dict[str, object]:
    candidate_rows = [candidate[seed]["overall"] for seed in SEEDS]
    zero_faults = all(row["f"] == 0 for row in candidate_rows)
    seed_deltas = [float(comparison["by_seed"][str(seed)]["delta_score_rate"]) for seed in SEEDS]
    seat_deltas = [float(comparison["by_seat"][str(seat)]["delta_score_rate"]) for seat in (0, 1)]
    opponent_deltas = [float(comparison["by_opponent"][opponent]["delta_score_rate"]) for opponent in OPPONENTS]
    checks = {
        "candidate_evaluations_complete": _check("PASS"),
        "zero_faults": _check("PASS" if zero_faults else "FAIL", candidate_faults=sum(row["f"] for row in candidate_rows)),
        "seed_non_regression": _check("PASS" if all(delta >= 0.0 for delta in seed_deltas) else "FAIL", deltas=seed_deltas),
        "pooled_improvement": _check(
            "PASS" if float(comparison["pooled"]["delta_score_rate"]) >= 0.05 else "FAIL",
            delta_score_rate=float(comparison["pooled"]["delta_score_rate"]), threshold=0.05,
        ),
        "seat_non_regression": _check(
            "PASS" if all(delta >= -0.03 for delta in seat_deltas) else "FAIL",
            deltas=seat_deltas, allowed_regression=-0.03,
        ),
        "opponent_non_regression": _check(
            "PASS" if sum(delta >= 0.0 for delta in opponent_deltas) >= 4 else "FAIL",
            non_regressive_count=sum(delta >= 0.0 for delta in opponent_deltas), deltas=opponent_deltas,
        ),
        "action_metrics_complete": dict(action_metrics_check),
    }
    return {
        "status": "PASS" if all(value["status"] == "PASS" for value in checks.values()) else "FAIL",
        "checks": checks,
    }


def _markdown(summary: Mapping[str, object]) -> str:
    gate = summary["short_gate"]
    comparison = summary.get("comparison")
    lines = [
        "# Wave2 DAgger 短期ゲート集計",
        "",
        "> この文書は研究用の比較集計であり、Champion変更・提出・長時間学習を自動許可しない。",
        "",
        f"## 研究費用ゲート: {gate['status']}",
        "",
        f"- 長時間学習権限: `{summary['long_training_authority']}`",
    ]
    if comparison:
        pooled = comparison["pooled"]
        lines += [
            "",
            "| 比較 | 勝数 | 対戦数 | 勝率 |",
            "|---|---:|---:|---:|",
            f"| 候補 | {pooled['candidate']['w']} | {pooled['candidate']['requested']} | {_rate(pooled['candidate']):.4f} |",
            f"| Wave6 | {pooled['baseline']['w']} | {pooled['baseline']['requested']} | {_rate(pooled['baseline']):.4f} |",
            "",
            "### Seed別",
            "",
            "| seed | 候補勝数 | Wave6勝数 | 勝率差 |",
            "|---:|---:|---:|---:|",
        ]
        for seed in SEEDS:
            row = comparison["by_seed"][str(seed)]
            lines.append(f"| {seed} | {row['candidate']['w']} | {row['baseline']['w']} | {row['delta_score_rate']:.4f} |")
        lines += ["", "### 相手別", "", "| 相手 | 候補勝数 | Wave6勝数 | 勝率差 |", "|---|---:|---:|---:|"]
        for opponent in OPPONENTS:
            row = comparison["by_opponent"][opponent]
            lines.append(f"| {opponent} | {row['candidate']['w']} | {row['baseline']['w']} | {row['delta_score_rate']:.4f} |")
    lines += ["", "### 判定項目", "", "| 項目 | 状態 |", "|---|---|"]
    for name, check in gate["checks"].items():
        lines.append(f"| {name} | {check['status']} |")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bc-report", type=Path, required=True)
    parser.add_argument("--bc-report-sha256", required=True)
    parser.add_argument("--wave6-evaluation", action="append", required=True)
    parser.add_argument("--wave6-evaluation-sha256", action="append", required=True)
    parser.add_argument("--candidate-evaluation", action="append")
    parser.add_argument("--candidate-evaluation-sha256", action="append")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        baseline_paths = _require_seed_map(args.wave6_evaluation, field="--wave6-evaluation")
        baseline_shas = _require_seed_sha_map(args.wave6_evaluation_sha256, field="--wave6-evaluation-sha256")
        if set(baseline_paths) != set(baseline_shas):
            raise ValueError("Wave6 evaluation paths and SHA anchors have different seeds")
        candidate_paths = _require_seed_map(args.candidate_evaluation, field="--candidate-evaluation") if args.candidate_evaluation else {}
        candidate_shas = _require_seed_sha_map(args.candidate_evaluation_sha256, field="--candidate-evaluation-sha256") if args.candidate_evaluation_sha256 else {}
        if set(candidate_paths) != set(candidate_shas):
            raise ValueError("candidate evaluation paths and SHA anchors have different seeds")
        report = _read_json(args.bc_report, args.bc_report_sha256, field="bc_report")
        report_sha = _sha(args.bc_report_sha256, field="bc_report_sha256")
        if report.get("schema") != REPORT_SCHEMA or report.get("promotion_authority") is not False:
            raise ValueError("BC report is not a research-only DAgger report")
        report_seeds = report.get("seed_results")
        if type(report_seeds) is not dict or set(report_seeds) != {"0", "1"}:
            raise ValueError("BC report must contain seed 0 and seed 1 results")
        expected_checkpoints: dict[int, dict[str, str]] = {}
        for seed in SEEDS:
            item = report_seeds[str(seed)]
            if type(item) is not dict:
                raise ValueError("BC report seed result is invalid")
            expected_checkpoints[seed] = {
                "file_sha256": _sha(item.get("best_checkpoint_file_sha256"), field=f"bc_report.seed{seed}.checkpoint_file_sha256"),
                "tensor_state_sha256": _sha(item.get("best_checkpoint_tensor_state_sha256"), field=f"bc_report.seed{seed}.checkpoint_tensor_state_sha256"),
            }
        baseline: dict[int, Mapping[str, object]] = {}
        protocol_sha: str | None = None
        for seed in SEEDS:
            payload = _read_json(baseline_paths[seed], baseline_shas[seed], field=f"wave6_evaluation_seed{seed}")
            baseline[seed] = _validate_evaluation(payload, field=f"wave6_evaluation_seed{seed}", expected_checkpoint=None, protocol_sha=protocol_sha)
            protocol_sha = str(baseline[seed]["protocol_sha"])
        if not candidate_paths:
            summary: dict[str, object] = {
                "schema": "meta-specialist-v4-dagger-wave2-summary-v1",
                "status": "UNMEASURED", "bc_report": str(args.bc_report),
                "candidate_evaluations": {}, "long_training_authority": False,
                "short_gate": {"status": "UNMEASURED", "checks": {"candidate_evaluations_complete": _check("UNMEASURED")}},
            }
        else:
            candidate: dict[int, Mapping[str, object]] = {}
            for seed in SEEDS:
                payload = _read_json(candidate_paths[seed], candidate_shas[seed], field=f"candidate_evaluation_seed{seed}")
                candidate[seed] = _validate_evaluation(
                    payload, field=f"candidate_evaluation_seed{seed}", expected_checkpoint=expected_checkpoints[seed], protocol_sha=protocol_sha,
                )
            comparison = _compare(baseline, candidate)
            inline_imitation = _inline_imitation_payload(report)
            if inline_imitation is None:
                action_metrics_check = _check(
                    "UNMEASURED", reason="BC report has no per-seed validation imitation metrics",
                )
            else:
                action_details, action_reasons = validate_v4_imitation_metrics_payload(
                inline_imitation, expected_lane=str(report.get("lane")),
                    expected_checkpoints=expected_checkpoints,
                    artifact_sha256=report_sha,
                )
                action_metrics_check = _check(
                    "PASS" if not action_reasons else "FAIL",
                    reasons=action_reasons, details=action_details,
                )
            gate = _short_gate(baseline, candidate, comparison, action_metrics_check)
            summary = {
                "schema": "meta-specialist-v4-dagger-wave2-summary-v1", "status": gate["status"],
                "bc_report": str(args.bc_report), "candidate_evaluations": {str(seed): str(candidate_paths[seed]) for seed in SEEDS},
                "comparison": comparison, "short_gate": gate, "long_training_authority": False,
            }
        _atomic_write(args.json_output, json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        _atomic_write(args.markdown_output, _markdown(summary))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
