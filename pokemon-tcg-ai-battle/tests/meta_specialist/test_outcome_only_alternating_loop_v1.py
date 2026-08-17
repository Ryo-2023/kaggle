from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_alternating_loop_v1 import (
    AlternatingLoopError,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    DEFAULT_WORKERS_V1,
    DECK_FIXED_LONG_V1,
    POLICY_FIXED_SHORT_V1,
    build_alternating_iteration_plan_v1,
    validate_alternating_pair_v1,
)
from mage_ptcg.meta_specialist.outcome_only_alternating_runtime_v1 import (
    OutcomeOnlyCandidateSpecV1,
    _config_sha,
)


def _spec(tmp_path: Path, name: str, *, deck: str, policy: str) -> OutcomeOnlyCandidateSpecV1:
    main = tmp_path / f"{name}.py"
    deck_path = tmp_path / f"{name}.csv"
    main.write_text(policy, encoding="utf-8")
    deck_path.write_text(deck, encoding="utf-8")
    import hashlib

    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    return OutcomeOnlyCandidateSpecV1(
        candidate_id=name,
        main_path=main,
        deck_path=deck_path,
        policy_sha256=sha(main),
        deck_sha256=sha(deck_path),
        config_sha256=_config_sha({}, {}, 0.0),
        env={},
        biases={},
    )


def test_parallel_defaults_are_workers12_recycle16() -> None:
    assert DEFAULT_WORKERS_V1 == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES_V1 == 16


def test_fixed_phase_rejects_the_wrong_identity_change(tmp_path: Path) -> None:
    parent = _spec(tmp_path, "parent", deck="1\n" * 60, policy="def agent(obs):\n return []\n")
    deck_child = _spec(tmp_path, "deck-child", deck="2\n" * 60, policy="def agent(obs):\n return []\n")
    policy_child = _spec(tmp_path, "policy-child", deck="1\n" * 60, policy="def agent(obs):\n return [0]\n")
    validate_alternating_pair_v1(
        phase=POLICY_FIXED_SHORT_V1,
        candidate=deck_child,
        control=parent,
        stage_games=96,
    )
    validate_alternating_pair_v1(
        phase=DECK_FIXED_LONG_V1,
        candidate=policy_child,
        control=parent,
        stage_games=96,
    )
    with pytest.raises(AlternatingLoopError, match="policy-fixed"):
        validate_alternating_pair_v1(
            phase=POLICY_FIXED_SHORT_V1,
            candidate=policy_child,
            control=parent,
            stage_games=96,
        )
    with pytest.raises(AlternatingLoopError, match="deck-fixed"):
        validate_alternating_pair_v1(
            phase=DECK_FIXED_LONG_V1,
            candidate=deck_child,
            control=parent,
            stage_games=96,
        )


def test_iteration_plan_has_deck_then_policy_phase(tmp_path: Path) -> None:
    parent = _spec(tmp_path, "parent", deck="1\n" * 60, policy="def agent(obs):\n return []\n")
    deck_child = _spec(tmp_path, "deck-child", deck="2\n" * 60, policy="def agent(obs):\n return []\n")
    policy_child = _spec(tmp_path, "policy-child", deck="2\n" * 60, policy="def agent(obs):\n return [0]\n")
    policy_control = _spec(tmp_path, "policy-control", deck="2\n" * 60, policy="def agent(obs):\n return []\n")
    plan = build_alternating_iteration_plan_v1(
        deck_candidate=deck_child,
        native_control=parent,
        policy_candidate=policy_child,
        policy_control=policy_control,
        stage_games=96,
    )
    assert [item.phase for item in plan] == [POLICY_FIXED_SHORT_V1, DECK_FIXED_LONG_V1]
    assert plan[0].candidate.candidate_id == "deck-child"
    assert plan[1].candidate.candidate_id == "policy-child"
    assert plan[1].control.candidate_id == "policy-control"
    assert all(item.authority_false for item in plan)


def test_plan_rejects_policy_candidate_with_different_deck(tmp_path: Path) -> None:
    parent = _spec(tmp_path, "parent", deck="1\n" * 60, policy="def agent(obs):\n return []\n")
    deck_child = _spec(tmp_path, "deck-child", deck="2\n" * 60, policy="def agent(obs):\n return []\n")
    policy_child = _spec(tmp_path, "policy-child", deck="1\n" * 60, policy="def agent(obs):\n return [0]\n")
    policy_control = _spec(tmp_path, "policy-control", deck="2\n" * 60, policy="def agent(obs):\n return []\n")
    with pytest.raises(AlternatingLoopError, match="frozen deck"):
        build_alternating_iteration_plan_v1(
            deck_candidate=deck_child,
            native_control=parent,
            policy_candidate=policy_child,
            policy_control=policy_control,
            stage_games=96,
        )
