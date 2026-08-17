"""offline benchmark から public score への observation/calibration registry。"""

from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    LeagueContractError,
    append_jsonl_once,
    atomic_write_json,
    content_id,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    runtime_policy_id: str
    benchmark_id: str
    evaluation_result_id: str
    offline_score_rate: float
    public_score: float
    submission_reference: str
    observed_at: str
    observation_id: str

    @classmethod
    def build(
        cls,
        *,
        runtime_policy_id: str,
        benchmark_id: str,
        evaluation_result_id: str,
        offline_score_rate: float,
        public_score: float,
        submission_reference: str,
    ) -> "CalibrationObservation":
        if not all(
            math.isfinite(value) for value in (offline_score_rate, public_score)
        ):
            raise LeagueContractError("calibration scores must be finite")
        if not 0 <= offline_score_rate <= 1:
            raise LeagueContractError("offline score rate must be in [0, 1]")
        identity = {
            "runtime_policy_id": runtime_policy_id,
            "benchmark_id": benchmark_id,
            "evaluation_result_id": evaluation_result_id,
            "offline_score_rate": offline_score_rate,
            "public_score": public_score,
            "submission_reference": submission_reference,
        }
        return cls(
            **identity,
            observed_at=utc_now(),
            observation_id=content_id("calibration-observation-v1", identity),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_policy_id": self.runtime_policy_id,
            "benchmark_id": self.benchmark_id,
            "evaluation_result_id": self.evaluation_result_id,
            "offline_score_rate": self.offline_score_rate,
            "public_score": self.public_score,
            "submission_reference": self.submission_reference,
            "observed_at": self.observed_at,
            "observation_id": self.observation_id,
        }


def register_observation(path: Path, observation: CalibrationObservation) -> bool:
    return append_jsonl_once(path, observation.to_dict(), "observation_id")


def load_observations(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    values = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LeagueContractError(
                    f"corrupt calibration registry line {line_number}: {exc}"
                ) from exc
    return values


def _predict_thresholds(x: list[float], y: list[float], value: float) -> float:
    if value <= x[0]:
        return y[0]
    if value >= x[-1]:
        return y[-1]
    right = bisect.bisect_right(x, value)
    left = right - 1
    if x[right] == x[left]:
        return y[right]
    weight = (value - x[left]) / (x[right] - x[left])
    return y[left] + weight * (y[right] - y[left])


def fit_calibration(
    observations: Iterable[Mapping[str, Any]],
    *,
    output_path: Path,
    minimum_independent_policies: int = 30,
) -> dict[str, Any]:
    rows = [dict(row) for row in observations]
    policy_ids = sorted({row["runtime_policy_id"] for row in rows})
    if len(policy_ids) < minimum_independent_policies:
        artifact = {
            "schema_version": 1,
            "status": "OBSERVATION_ONLY",
            "independent_runtime_policies": len(policy_ids),
            "minimum_independent_policies": minimum_independent_policies,
            "forecast_available": False,
        }
        atomic_write_json(output_path, artifact)
        return artifact
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError as exc:
        raise LeagueContractError("scikit-learn is required for calibration") from exc
    x = [float(row["offline_score_rate"]) for row in rows]
    y = [float(row["public_score"]) for row in rows]
    model = IsotonicRegression(out_of_bounds="clip").fit(x, y)
    thresholds_x = [float(value) for value in model.X_thresholds_]
    thresholds_y = [float(value) for value in model.y_thresholds_]

    residuals = []
    for policy_id in policy_ids:
        training = [row for row in rows if row["runtime_policy_id"] != policy_id]
        held_out = [row for row in rows if row["runtime_policy_id"] == policy_id]
        if len({row["runtime_policy_id"] for row in training}) < 2:
            continue
        fold = IsotonicRegression(out_of_bounds="clip").fit(
            [float(row["offline_score_rate"]) for row in training],
            [float(row["public_score"]) for row in training],
        )
        for row in held_out:
            prediction = float(fold.predict([float(row["offline_score_rate"])])[0])
            residuals.append(float(row["public_score"]) - prediction)
    absolute = sorted(abs(value) for value in residuals)
    residual_95 = (
        absolute[min(len(absolute) - 1, int(0.95 * (len(absolute) - 1)))]
        if absolute
        else None
    )
    identity = {
        "runtime_policy_ids": policy_ids,
        "observation_ids": sorted(row["observation_id"] for row in rows),
        "x_thresholds": thresholds_x,
        "y_thresholds": thresholds_y,
        "validation": "leave-one-runtime-policy-out",
    }
    artifact = {
        "schema_version": 1,
        "calibration_id": content_id("offline-public-calibration-v1", identity),
        "status": "AVAILABLE",
        "forecast_available": True,
        "independent_runtime_policies": len(policy_ids),
        "observations": len(rows),
        **identity,
        "lopo_mae": (
            sum(abs(value) for value in residuals) / len(residuals)
            if residuals
            else None
        ),
        "absolute_residual_95": residual_95,
        "created_at": utc_now(),
    }
    atomic_write_json(output_path, artifact)
    return artifact


def forecast_public_score(
    calibration: Mapping[str, Any], offline_score_rate: float
) -> dict[str, Any]:
    if calibration.get("status") != "AVAILABLE":
        raise LeagueContractError("calibration forecast is not available")
    x_thresholds = list(calibration["x_thresholds"])
    if not x_thresholds[0] <= offline_score_rate <= x_thresholds[-1]:
        raise LeagueContractError(
            "offline score is outside the calibrated range; extrapolation is disabled"
        )
    prediction = _predict_thresholds(
        x_thresholds, list(calibration["y_thresholds"]), offline_score_rate
    )
    radius = calibration.get("absolute_residual_95")
    return {
        "calibration_id": calibration["calibration_id"],
        "offline_score_rate": offline_score_rate,
        "predicted_public_score": prediction,
        "empirical_95": (
            [prediction - radius, prediction + radius]
            if radius is not None
            else None
        ),
    }
