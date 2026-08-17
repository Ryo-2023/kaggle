from __future__ import annotations

import json
import dataclasses
from pathlib import Path

import pytest

from scripts.parallel_cabt_evaluator_v1 import (
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    PARALLEL_CABT_EVALUATOR_SCHEMA_V1,
    EvaluationGameV1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


def _sha(char: str) -> str:
    return char * 64


def _game(index: int, *, fixture_status: str = "DONE") -> EvaluationGameV1:
    return EvaluationGameV1(
        game_id=f"block-a-game-{index:04d}",
        block_id="block-a",
        policy_id="candidate-v1",
        policy_sha256=_sha("a"),
        deck_id="archaludon-v1",
        deck_sha256=_sha("b"),
        opponent_id=f"opponent-{index % 2}",
        opponent_identity={"policy_sha256": _sha("c"), "deck_sha256": _sha("d")},
        opponent_deck_sha256=_sha("d"),
        seat=index % 2,
        seed=1000 + index,
        max_steps=100,
        timeout_seconds=2.0,
        policy_agent_name="fixture_candidate",
        opponent_agent_name="fixture_opponent",
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
        metadata={"fixture_status": fixture_status},
    )


def test_parallel_evaluator_default_workers_follow_resource_policy() -> None:
    assert DEFAULT_MAX_WORKERS_V1 == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES_V1 == 16


def test_game_spec_rejects_invalid_identity_and_seat() -> None:
    with pytest.raises(ValueError, match="seat"):
        EvaluationGameV1(
            game_id="g",
            block_id="b",
            policy_id="p",
            policy_sha256=_sha("a"),
            deck_id="d",
            deck_sha256=_sha("b"),
            opponent_id="o",
            opponent_identity={},
            opponent_deck_sha256=_sha("c"),
            seat=2,
            seed=1,
            max_steps=10,
            timeout_seconds=1.0,
        )

    with pytest.raises(ValueError, match="SHA-256"):
        EvaluationGameV1(
            game_id="g",
            block_id="b",
            policy_id="p",
            policy_sha256="not-a-sha",
            deck_id="d",
            deck_sha256=_sha("b"),
            opponent_id="o",
            opponent_identity={},
            opponent_deck_sha256=_sha("c"),
            seat=0,
            seed=1,
            max_steps=10,
            timeout_seconds=1.0,
        )


def test_serial_and_parallel_fixture_runs_have_same_ledger_contract(tmp_path: Path) -> None:
    games = (_game(0), _game(1), _game(2, fixture_status="STEP_LIMIT"), _game(3))
    serial = run_parallel_cabt_evaluation(
        games,
        output_dir=tmp_path / "serial",
        max_workers=1,
        worker_recycle_games=1,
    )
    parallel = run_parallel_cabt_evaluation(
        games,
        output_dir=tmp_path / "parallel",
        max_workers=2,
        worker_recycle_games=2,
    )

    assert serial["summary"] == parallel["summary"]
    assert serial["summary"]["requested_games"] == 4
    assert serial["summary"]["faults"] == 1
    assert serial["summary"]["score_denominator_games"] == 4
    assert serial["summary"]["score_rate"] == pytest.approx(0.75)
    assert serial["summary"]["status_distribution"]["DONE"] == 3
    assert serial["summary"]["status_distribution"]["STEP_LIMIT"] == 1

    for root in (tmp_path / "serial", tmp_path / "parallel"):
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == PARALLEL_CABT_EVALUATOR_SCHEMA_V1
        assert manifest["max_workers"] in (1, 2)
        assert manifest["worker_recycle_games"] in (1, 2)
        assert manifest["max_in_flight_games"] == manifest["max_workers"]
        assert manifest["thread_environment"]["OMP_NUM_THREADS"] == "1"
        assert manifest["thread_environment"]["MKL_NUM_THREADS"] == "1"
        assert len(list((root / "games").glob("*.json"))) == 4
        rows = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((root / "games").glob("*.json"))
        ]
        for row in rows:
            assert row["policy_sha256"] == _sha("a")
            assert row["deck_sha256"] == _sha("b")
            assert row["opponent_deck_sha256"] == _sha("d")
            assert row["evaluator_implementation_sha256"] == evaluator_implementation_sha256_v1()
            assert row["block_id"] == "block-a"
            assert row["steps"] is not None
            assert row["runtime_seconds"] is not None


def test_fault_row_is_persisted_when_runner_raises(tmp_path: Path) -> None:
    game = _game(0, fixture_status="RAISE")
    result = run_parallel_cabt_evaluation(
        (game,),
        output_dir=tmp_path / "fault",
        max_workers=1,
        worker_recycle_games=1,
    )
    row = json.loads((tmp_path / "fault" / "games" / f"{game.game_id}.json").read_text())
    assert row["status"] == "FAULT"
    assert row["outcome"] == "fault"
    assert row["fault_kind"] == "runner_exception"
    assert result["summary"]["faults"] == 1
    assert result["summary"]["score_denominator_games"] == 1
    assert result["summary"]["score_rate"] == 0.0


def test_timeout_refills_bounded_queue_and_persists_all_rows(tmp_path: Path) -> None:
    timed_out = dataclasses.replace(
        _game(0, fixture_status="BLOCK"), timeout_seconds=0.05
    )
    completed = _game(1)
    result = run_parallel_cabt_evaluation(
        (timed_out, completed),
        output_dir=tmp_path / "timeout-refill",
        max_workers=1,
        worker_recycle_games=1,
    )

    assert result["summary"]["requested_games"] == 2
    assert result["summary"]["faults"] == 1
    assert result["summary"]["completed_games"] == 1
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "timeout-refill" / "games").glob("*.json"))
    ]
    assert len(rows) == 2
    assert rows[0]["fault_kind"] in {"timeout", "parent_timeout"}
    assert rows[1]["status"] == "DONE"


def test_duplicate_game_ids_are_rejected_before_workers_start() -> None:
    game = _game(0)
    with pytest.raises(ValueError, match="duplicate game_id"):
        run_parallel_cabt_evaluation((game, game), output_dir="/tmp/parallel-cabt-duplicate")
