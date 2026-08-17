"""Evidence-first C5 Champion/Challenger promotion gate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True, slots=True)
class PromotionConfig:
    minimum_games: int
    latency_budget_ms: float

    def __post_init__(self) -> None:
        if type(self.minimum_games) is not int or self.minimum_games < 1 or not math.isfinite(self.latency_budget_ms) or self.latency_budget_ms <= 0:
            raise ValueError("promotion config requires positive games and latency budget")


def evaluate_promotion(report: object, config: PromotionConfig) -> dict[str, object]:
    """Return PROMOTE/HOLD/REJECT/NO_DECISION without inventing missing evidence."""
    if not isinstance(report, dict):
        raise ValueError("promotion report must be an object")
    actual = report.get("source") == "actual_cabt" and report.get("synthetic") is False
    games = report.get("games")
    if not actual or type(games) is not int or games <= 0 or not report.get("environment_version"):
        return {"decision": "NO_DECISION", "reasons": ["actual_cabt_evidence_missing"], "config": asdict(config)}
    required = ("legal_action_rate", "invalid_actions", "crashes", "timeouts", "latency_ms_p95", "paired_delta_ci_low", "reproducible", "clean_submission_artifact")
    missing = [key for key in required if key not in report]
    if missing or games < config.minimum_games:
        return {"decision": "NO_DECISION", "reasons": ["required_evidence_missing_or_insufficient_games"], "missing": missing, "config": asdict(config)}
    unsafe = report["legal_action_rate"] != 1.0 or any(report[key] != 0 for key in ("invalid_actions", "crashes", "timeouts")) or report["latency_ms_p95"] > config.latency_budget_ms or not report["reproducible"] or not report["clean_submission_artifact"]
    if unsafe:
        return {"decision": "REJECT", "reasons": ["safety_or_package_gate_failed"], "config": asdict(config)}
    if not isinstance(report["paired_delta_ci_low"], (int, float)) or not math.isfinite(float(report["paired_delta_ci_low"])):
        return {"decision": "NO_DECISION", "reasons": ["paired_uncertainty_missing"], "config": asdict(config)}
    if report["paired_delta_ci_low"] > 0:
        return {"decision": "PROMOTE", "reasons": ["all_configured_gates_passed"], "config": asdict(config)}
    return {"decision": "HOLD", "reasons": ["paired_improvement_not_established"], "config": asdict(config)}


__all__ = ["PromotionConfig", "evaluate_promotion"]
