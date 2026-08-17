from __future__ import annotations

from pathlib import Path
import json

import pytest

from mage_ptcg.bootstrap_champion.contracts import BootstrapContractError
from mage_ptcg.bootstrap_champion.teacher import (
    BootstrapTeacherExample,
    collect_teacher_dataset,
    encoded_examples_from_dataset,
    load_teacher_trace,
    outcome_weight,
    split_games,
    validate_actor_visible_example,
)


def _example(*, game_id: str = "game-a", outcome: str = "win") -> BootstrapTeacherExample:
    return BootstrapTeacherExample(
        game_id=game_id,
        decision_index=0,
        public_state={"turn": 1},
        own_private_state={"hand_size": 3},
        visible_history=({"event": "start"},),
        legal_action_keys=("a", "b"),
        selected_action_key="a",
        outcome=outcome,
        behavior_weight=outcome_weight(outcome),
        teacher_candidate_id="a" * 64,
    )


def test_teacher_example_rejects_hidden_opponent_information() -> None:
    example = BootstrapTeacherExample(
        game_id="game-a",
        decision_index=0,
        public_state={"opponent_hand": [1]},
        own_private_state={},
        visible_history=(),
        legal_action_keys=("a",),
        selected_action_key="a",
        outcome="win",
        behavior_weight=1.0,
        teacher_candidate_id="a" * 64,
    )

    with pytest.raises(BootstrapContractError, match="forbidden"):
        validate_actor_visible_example(example)


def test_teacher_dataset_splits_whole_games_and_excludes_faulted_and_multi_select(
    tmp_path: Path,
) -> None:
    examples = [
        _example(game_id="good-a"),
        _example(game_id="good-a"),
        _example(game_id="good-b", outcome="loss"),
    ]
    manifest = collect_teacher_dataset(
        examples=examples,
        excluded_game_ids={"faulted"},
        skipped_multi_select_decisions=2,
        deck_hash="b" * 64,
        teacher_candidate_id="a" * 64,
        seed=7,
        output=tmp_path,
    )

    assert manifest.decision_count == 3
    assert manifest.skipped_multi_select_decisions == 2
    assert set(manifest.train_game_ids).isdisjoint(manifest.validation_game_ids)
    assert set(manifest.train_game_ids) | set(manifest.validation_game_ids) == {"good-a", "good-b"}
    assert (tmp_path / "manifest.json").is_file()


def test_outcome_weights_and_game_split_are_deterministic() -> None:
    assert [outcome_weight(value) for value in ("win", "draw", "loss")] == [1.0, 0.5, 0.25]
    assert split_games(["c", "a", "b"], seed=11) == split_games(["a", "b", "c"], seed=11)


def test_sealed_teacher_examples_can_supply_semantic_distillation_input(tmp_path: Path) -> None:
    example = BootstrapTeacherExample(
        game_id="game-a",
        decision_index=0,
        public_state={"turn": 1},
        own_private_state={"hand_size": 3},
        visible_history=(),
        legal_action_keys=("a", "b"),
        selected_action_key="b",
        outcome="win",
        behavior_weight=1.0,
        teacher_candidate_id="a" * 64,
        encoded_state=(0.1, 0.2),
        encoded_actions=((1.0, 0.0), (0.0, 1.0)),
        selected_action=1,
    )
    collect_teacher_dataset(
        examples=[example],
        excluded_game_ids=set(),
        skipped_multi_select_decisions=0,
        deck_hash="b" * 64,
        teacher_candidate_id="a" * 64,
        seed=7,
        output=tmp_path,
    )

    assert encoded_examples_from_dataset(tmp_path / "train.jsonl") == [
        {
            "state": [0.1, 0.2],
            "actions": [[1.0, 0.0], [0.0, 1.0]],
            "legal_mask": [True, True],
            "selected_action": 1,
            "behavior_weight": 1.0,
        }
    ]


def test_teacher_trace_can_select_only_the_eventual_champion(tmp_path: Path) -> None:
    root = tmp_path / "trace" / "games"
    root.mkdir(parents=True)
    for game_id, candidate in (("game-a", "a" * 64), ("game-b", "b" * 64)):
        root.joinpath(f"{game_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "bootstrap-teacher-trace-game-v1",
                    "game_id": game_id,
                    "candidate_id": candidate,
                    "status": "DONE",
                    "skipped_multi_select_decisions": 0,
                    "examples": [
                        {
                            **_example(game_id=game_id).to_dict(),
                            "teacher_candidate_id": candidate,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    examples, excluded, skipped = load_teacher_trace(tmp_path / "trace", teacher_candidate_id="a" * 64)

    assert [example.game_id for example in examples] == ["game-a"]
    assert excluded == set()
    assert skipped == 0
