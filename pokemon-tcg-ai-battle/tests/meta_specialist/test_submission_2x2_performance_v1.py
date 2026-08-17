from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.run_submission_2x2_performance_v1 import (
    Submission2x2ArmV1,
    Submission2x2RuntimeError,
    build_submission_2x2_games_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arm(tmp_path: Path, arm_id: str, *, policy_kind: str) -> Submission2x2ArmV1:
    main = tmp_path / f"{arm_id}.py"
    deck = tmp_path / f"{arm_id}.csv"
    main.write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    deck.write_text("1\n" * 60, encoding="utf-8")
    return Submission2x2ArmV1(
        arm_id=arm_id,
        policy_kind=policy_kind,
        policy_id=arm_id,
        policy_sha256=_sha(main),
        deck_path=deck,
        deck_sha256=_sha(deck),
        policy_path=main,
        checkpoint_path=main if policy_kind == "v4_seed1" else None,
        checkpoint_tensor_sha256=("a" * 64) if policy_kind == "v4_seed1" else None,
        subject_archetype_id="archaludon",
    )


def test_build_2x2_games_keeps_same_pair_strata(tmp_path: Path) -> None:
    rule = _arm(tmp_path, "rule", policy_kind="rule_v0")
    v4 = _arm(tmp_path, "v4", policy_kind="v4_seed1")
    refs = ("official_random", "plamen06_steel")
    games = build_submission_2x2_games_v1(
        arm=rule,
        pool_root=Path("opponents"),
        reference_ids=refs,
        games_per_opponent_seat=2,
        base_seed=1200,
        block_id="rule-arch",
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
    )
    assert len(games) == 8
    assert {game.metadata["cell"] for game in games} == {"rule"}
    assert len({(game.opponent_id, game.seat, game.metadata["repetition"], game.seed) for game in games}) == 8
    assert all(game.metadata["policy_kind"] == "rule_v0" for game in games)

    other = build_submission_2x2_games_v1(
        arm=v4,
        pool_root=Path("opponents"),
        reference_ids=refs,
        games_per_opponent_seat=2,
        base_seed=1200,
        block_id="v4-root",
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
    )
    assert {
        (game.opponent_id, game.seat, game.metadata["repetition"], game.seed)
        for game in games
    } == {
        (game.opponent_id, game.seat, game.metadata["repetition"], game.seed)
        for game in other
    }


def test_build_2x2_rejects_invalid_policy_kind(tmp_path: Path) -> None:
    with pytest.raises(Submission2x2RuntimeError, match="policy_kind"):
        _arm(tmp_path, "bad", policy_kind="unknown")


def test_v4_arm_requires_checkpoint_identity(tmp_path: Path) -> None:
    with pytest.raises(Submission2x2RuntimeError, match="checkpoint"):
        Submission2x2ArmV1(
            arm_id="v4",
            policy_kind="v4_seed1",
            policy_id="v4",
            policy_sha256="a" * 64,
            deck_path=tmp_path / "missing.csv",
            deck_sha256="b" * 64,
            policy_path=tmp_path / "missing.py",
        )
