"""checkpoint ごとの可視 benchmark 成績を永続化して時系列へ集約する。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    LeagueContractError,
    append_jsonl_once,
    atomic_write_json,
    content_id,
    require_sha256,
)

_HISTORY_FILENAME = "evaluation_history.jsonl"
_SUMMARY_FILENAME = "evaluation_summary.json"


def _read_history(history_root: Path) -> list[dict[str, Any]]:
    path = Path(history_root) / _HISTORY_FILENAME
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LeagueContractError(
                    f"corrupt checkpoint evaluation history {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise LeagueContractError(
                    f"corrupt checkpoint evaluation history {path}:{line_number}: row is not an object"
                )
            evaluation_id = row.get("checkpoint_evaluation_id")
            if not isinstance(evaluation_id, str) or evaluation_id in seen:
                raise LeagueContractError(
                    f"corrupt checkpoint evaluation history {path}:{line_number}: invalid or duplicate ID"
                )
            seen.add(evaluation_id)
            rows.append(row)
    return rows


def _score_rate(row: Mapping[str, Any]) -> float | None:
    value = row["game_weighted_score_rate"]
    return float(value) if value is not None else None


def _is_complete(row: Mapping[str, Any]) -> bool:
    return (
        row["aggregate_status"] == "COMPLETE"
        and row["fault_count"] == 0
        and row["is_schedule_complete"] is True
        and _score_rate(row) is not None
    )


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (int(row["training_step"]), row["checkpoint_evaluation_id"]))
    previous_complete: float | None = None
    history: list[dict[str, Any]] = []
    complete: list[dict[str, Any]] = []
    for raw in ordered:
        row = dict(raw)
        score = _score_rate(row)
        if _is_complete(row):
            row["score_delta_from_previous_complete"] = (
                None if previous_complete is None else score - previous_complete
            )
            previous_complete = score
            complete.append(row)
        else:
            row["score_delta_from_previous_complete"] = None
        history.append(row)
    latest_complete = complete[-1] if complete else None
    best_complete = (
        max(
            complete,
            key=lambda row: (
                _score_rate(row),
                -int(row["training_step"]),
                row["checkpoint_evaluation_id"],
            ),
        )
        if complete
        else None
    )
    return {
        "schema_version": 1,
        "benchmark_id": ordered[0]["benchmark_id"] if ordered else None,
        "exposure_snapshot_id": (
            ordered[0]["exposure_snapshot_id"] if ordered else None
        ),
        "history": history,
        "latest_complete": latest_complete,
        "best_complete": best_complete,
    }


def load_checkpoint_evaluation_summary(history_root: Path) -> dict[str, Any]:
    return _summary(_read_history(Path(history_root)))


def _evaluation_row(
    *,
    training_checkpoint_id: str,
    training_step: int,
    evaluation_result: Mapping[str, Any],
) -> dict[str, Any]:
    if type(training_step) is not int or training_step < 0:
        raise LeagueContractError("training_step must be a non-negative integer")
    require_sha256(training_checkpoint_id, "training_checkpoint_id")
    required = {
        "evaluation_result_id",
        "runtime_policy_id",
        "benchmark_id",
        "exposure_snapshot_id",
        "scheduled_games",
        "completed_games",
        "is_schedule_complete",
        "aggregate",
    }
    missing = required.difference(evaluation_result)
    if missing:
        raise LeagueContractError(
            f"evaluation result missing fields: {sorted(missing)}"
        )
    aggregate = evaluation_result["aggregate"]
    if not isinstance(aggregate, Mapping):
        raise LeagueContractError("evaluation result aggregate must be an object")
    weighted = aggregate.get("game_weighted")
    if not isinstance(weighted, Mapping):
        raise LeagueContractError("evaluation result game_weighted must be an object")
    for field in (
        "evaluation_result_id",
        "runtime_policy_id",
        "benchmark_id",
        "exposure_snapshot_id",
    ):
        require_sha256(str(evaluation_result[field]), field)
    identity = {
        "training_checkpoint_id": training_checkpoint_id,
        "training_step": training_step,
        "evaluation_result_id": str(evaluation_result["evaluation_result_id"]),
        "runtime_policy_id": str(evaluation_result["runtime_policy_id"]),
        "benchmark_id": str(evaluation_result["benchmark_id"]),
        "exposure_snapshot_id": str(evaluation_result["exposure_snapshot_id"]),
    }
    return {
        "schema_version": 1,
        "checkpoint_evaluation_id": content_id(
            "checkpoint-evaluation-history-v1", identity
        ),
        **identity,
        "scheduled_games": int(evaluation_result["scheduled_games"]),
        "completed_games": int(evaluation_result["completed_games"]),
        "is_schedule_complete": bool(evaluation_result["is_schedule_complete"]),
        "aggregate_status": str(aggregate.get("status")),
        "fault_count": int(aggregate.get("fault_count", 0)),
        "game_weighted_score_rate": weighted.get("score_rate"),
        "game_weighted_wilson_95": weighted.get("wilson_95"),
        "opponent_equal_score_rate": aggregate.get("opponent_equal_score_rate"),
        "worst_opponent_score_rate": aggregate.get("worst_opponent_score_rate"),
    }


def record_checkpoint_evaluation(
    history_root: Path,
    *,
    training_checkpoint_id: str,
    training_step: int,
    evaluation_result: Mapping[str, Any],
) -> dict[str, Any]:
    """評価結果を一度だけ記録し、summary を JSONL 正本から再構成する。"""

    history_root = Path(history_root)
    row = _evaluation_row(
        training_checkpoint_id=training_checkpoint_id,
        training_step=training_step,
        evaluation_result=evaluation_result,
    )
    existing = _read_history(history_root)
    for previous in existing:
        if previous["benchmark_id"] != row["benchmark_id"]:
            raise LeagueContractError("checkpoint evaluation history benchmark mismatch")
        if previous["exposure_snapshot_id"] != row["exposure_snapshot_id"]:
            raise LeagueContractError(
                "checkpoint evaluation history exposure snapshot mismatch"
            )
    recorded = append_jsonl_once(
        history_root / _HISTORY_FILENAME, row, "checkpoint_evaluation_id"
    )
    summary = _summary(existing + ([row] if recorded else []))
    atomic_write_json(history_root / _SUMMARY_FILENAME, summary)
    return {**row, "recorded": recorded}
