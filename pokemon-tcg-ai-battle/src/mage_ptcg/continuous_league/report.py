"""評価 report、promotion gate、sealed holdout 一回性 marker。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    LeagueContractError,
    atomic_write_bytes,
    atomic_write_json,
    content_id,
    load_json,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class PromotionGate:
    minimum_game_weighted_score_rate: float = 0.5
    minimum_worst_opponent_score_rate: float = 0.0
    minimum_baseline_delta: float = 0.0
    require_positive_bootstrap_lower_bound: bool = False


def evaluate_promotion_gate(
    evaluation_result: Mapping[str, Any],
    *,
    comparison: Mapping[str, Any] | None,
    gate: PromotionGate,
) -> dict[str, Any]:
    aggregate = evaluation_result["aggregate"]
    reasons = []
    if (
        aggregate.get("status") != "COMPLETE"
        or aggregate.get("fault_count", 0) != 0
        or not evaluation_result.get("is_schedule_complete")
    ):
        reasons.append("evaluation_incomplete_or_faulted")
    score = aggregate["game_weighted"].get("score_rate")
    worst = aggregate.get("worst_opponent_score_rate")
    if score is None or score < gate.minimum_game_weighted_score_rate:
        reasons.append("game_weighted_score_below_gate")
    if worst is None or worst < gate.minimum_worst_opponent_score_rate:
        reasons.append("worst_opponent_score_below_gate")
    if comparison is None:
        reasons.append("fixed_baseline_comparison_missing")
    else:
        if comparison["delta_score_rate"] < gate.minimum_baseline_delta:
            reasons.append("fixed_baseline_delta_below_gate")
        interval = comparison.get("block_bootstrap_95")
        if gate.require_positive_bootstrap_lower_bound and (
            interval is None or interval[0] <= 0
        ):
            reasons.append("bootstrap_lower_bound_not_positive")
    identity = {
        "evaluation_result_id": evaluation_result["evaluation_result_id"],
        "comparison": dict(comparison) if comparison is not None else None,
        "gate": {
            "minimum_game_weighted_score_rate": gate.minimum_game_weighted_score_rate,
            "minimum_worst_opponent_score_rate": gate.minimum_worst_opponent_score_rate,
            "minimum_baseline_delta": gate.minimum_baseline_delta,
            "require_positive_bootstrap_lower_bound": gate.require_positive_bootstrap_lower_bound,
        },
        "passed": not reasons,
        "reasons": reasons,
    }
    return {
        "promotion_gate_result_id": content_id("promotion-gate-result-v1", identity),
        **identity,
    }


def write_evaluation_report(
    *,
    evaluation_result: Mapping[str, Any],
    output_dir: Path,
    comparison: Mapping[str, Any] | None = None,
    calibration_forecast: Mapping[str, Any] | None = None,
    promotion_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    aggregate = evaluation_result["aggregate"]
    report = {
        "schema_version": 1,
        "evaluation_result": dict(evaluation_result),
        "fixed_baseline_comparison": (
            dict(comparison) if comparison is not None else None
        ),
        "calibration_forecast": (
            dict(calibration_forecast) if calibration_forecast is not None else None
        ),
        "promotion_gate": (
            dict(promotion_gate) if promotion_gate is not None else None
        ),
    }
    report["report_id"] = content_id("continuous-evaluation-report-v1", report)
    atomic_write_json(output_dir / "report.json", report)
    weighted = aggregate["game_weighted"]
    lines = [
        "# Offline League Evaluation",
        "",
        f"- RuntimePolicy: `{evaluation_result['runtime_policy_id']}`",
        f"- Benchmark: `{evaluation_result['benchmark_id']}`",
        f"- Status: `{aggregate['status']}`",
        f"- Games: {weighted['games']} "
        f"({weighted['wins']}-{weighted['losses']}-{weighted['draws']})",
        f"- Game-weighted score: {weighted['score_rate']}",
        f"- Opponent-equal score: {aggregate['opponent_equal_score_rate']}",
        f"- Worst opponent score: {aggregate['worst_opponent_score_rate']}",
    ]
    if comparison is not None:
        lines.extend(
            [
                f"- Fixed-baseline delta: {comparison['delta_score_rate']:+.6f}",
                f"- Block bootstrap 95%: {comparison.get('block_bootstrap_95')}",
            ]
        )
    if calibration_forecast is not None:
        lines.append(
            "- Predicted public score: "
            f"{calibration_forecast['predicted_public_score']}"
        )
    if promotion_gate is not None:
        lines.append(f"- Promotion gate: {promotion_gate['passed']}")
    lines.extend(["", "## Per opponent", ""])
    for summary in aggregate["per_opponent"].values():
        lines.append(
            f"- `{summary['asset_id']}`: "
            f"{summary['wins']}-{summary['losses']}-{summary['draws']}, "
            f"score={summary['score_rate']}, "
            f"cohort={summary['exposure_cohort']}"
        )
    atomic_write_bytes(
        output_dir / "report.md", ("\n".join(lines) + "\n").encode("utf-8")
    )
    return report


def consume_sealed_holdout(
    *,
    marker_root: Path,
    holdout_id: str,
    runtime_policy_id: str,
    benchmark_id: str,
    evaluation_result: Mapping[str, Any],
) -> dict[str, Any]:
    marker_path = Path(marker_root) / f"{holdout_id}.json"
    if marker_path.exists():
        existing = load_json(marker_path)
        raise LeagueContractError(
            "sealed holdout already consumed by "
            f"RuntimePolicy {existing.get('runtime_policy_id')}"
        )
    if evaluation_result.get("benchmark_id") != benchmark_id:
        raise LeagueContractError("sealed result benchmark mismatch")
    if evaluation_result.get("runtime_policy_id") != runtime_policy_id:
        raise LeagueContractError("sealed result RuntimePolicy mismatch")
    result_hash = content_id("sealed-evaluation-result-v1", evaluation_result)
    identity = {
        "holdout_id": holdout_id,
        "runtime_policy_id": runtime_policy_id,
        "benchmark_id": benchmark_id,
        "evaluation_result_hash": result_hash,
        "consumed": True,
    }
    marker = {
        "schema_version": 1,
        "sealed_consumption_id": content_id("sealed-consumption-v1", identity),
        **identity,
        "consumed_at": utc_now(),
    }
    atomic_write_json(marker_path, marker)
    return marker
