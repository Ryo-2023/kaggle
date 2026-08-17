from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import LeagueContractError, content_id
from mage_ptcg.continuous_league.evaluation_history import (
    load_checkpoint_evaluation_summary,
    record_checkpoint_evaluation,
)


def _id(value: str) -> str:
    return content_id("checkpoint-evaluation-history-test", value)


def _result(
    *,
    runtime_policy_id: str,
    benchmark_id: str,
    exposure_snapshot_id: str,
    result_id: str,
    score_rate: float,
    fault_count: int = 0,
) -> dict[str, object]:
    return {
        "evaluation_result_id": result_id,
        "runtime_policy_id": runtime_policy_id,
        "benchmark_id": benchmark_id,
        "exposure_snapshot_id": exposure_snapshot_id,
        "scheduled_games": 512,
        "completed_games": 512,
        "is_schedule_complete": True,
        "aggregate": {
            "status": "COMPLETE" if fault_count == 0 else "FAULTED",
            "fault_count": fault_count,
            "game_weighted": {
                "games": 512,
                "wins": int(score_rate * 512),
                "losses": 512 - int(score_rate * 512),
                "draws": 0,
                "score_rate": score_rate,
                "wilson_95": [0.40, 0.60],
            },
            "opponent_equal_score_rate": score_rate,
            "worst_opponent_score_rate": score_rate - 0.1,
        },
    }


def test_history_is_idempotent_and_rebuilds_complete_checkpoint_curve(
    tmp_path: Path,
) -> None:
    benchmark_id = _id("benchmark")
    exposure_snapshot_id = _id("exposure")
    first = _result(
        runtime_policy_id=_id("runtime-10000"),
        benchmark_id=benchmark_id,
        exposure_snapshot_id=exposure_snapshot_id,
        result_id=_id("result-10000"),
        score_rate=0.50,
    )
    second = _result(
        runtime_policy_id=_id("runtime-20000"),
        benchmark_id=benchmark_id,
        exposure_snapshot_id=exposure_snapshot_id,
        result_id=_id("result-20000"),
        score_rate=0.625,
    )

    recorded = record_checkpoint_evaluation(
        tmp_path,
        training_checkpoint_id=_id("checkpoint-10000"),
        training_step=10_000,
        evaluation_result=first,
    )
    duplicate = record_checkpoint_evaluation(
        tmp_path,
        training_checkpoint_id=_id("checkpoint-10000"),
        training_step=10_000,
        evaluation_result=first,
    )
    record_checkpoint_evaluation(
        tmp_path,
        training_checkpoint_id=_id("checkpoint-20000"),
        training_step=20_000,
        evaluation_result=second,
    )

    assert recorded["recorded"] is True
    assert duplicate["recorded"] is False
    history_rows = [
        json.loads(line)
        for line in (tmp_path / "evaluation_history.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(history_rows) == 2

    summary = load_checkpoint_evaluation_summary(tmp_path)
    assert [row["training_step"] for row in summary["history"]] == [10_000, 20_000]
    assert summary["latest_complete"]["training_step"] == 20_000
    assert summary["best_complete"]["training_step"] == 20_000
    assert summary["latest_complete"]["score_delta_from_previous_complete"] == 0.125


def test_history_rejects_mixed_benchmark_and_corrupt_ledger(tmp_path: Path) -> None:
    baseline = _result(
        runtime_policy_id=_id("runtime"),
        benchmark_id=_id("benchmark-a"),
        exposure_snapshot_id=_id("exposure"),
        result_id=_id("result-a"),
        score_rate=0.5,
    )
    record_checkpoint_evaluation(
        tmp_path,
        training_checkpoint_id=_id("checkpoint-a"),
        training_step=10_000,
        evaluation_result=baseline,
    )
    mixed = {
        **baseline,
        "benchmark_id": _id("benchmark-b"),
        "evaluation_result_id": _id("result-b"),
    }
    with pytest.raises(LeagueContractError, match="benchmark"):
        record_checkpoint_evaluation(
            tmp_path,
            training_checkpoint_id=_id("checkpoint-b"),
            training_step=20_000,
            evaluation_result=mixed,
        )

    (tmp_path / "evaluation_history.jsonl").write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(LeagueContractError, match="corrupt"):
        load_checkpoint_evaluation_summary(tmp_path)
