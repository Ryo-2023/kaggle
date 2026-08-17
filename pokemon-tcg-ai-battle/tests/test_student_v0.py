"""Focused C4 Student v0 contracts: identity, safety, export, and fidelity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from main import make_rule_agent, make_student_agent
from scripts.build_student_submission import build_student_submission, verify_student_submission
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.student.dataset import DatasetValidationError, build_rule_bc_example, load_dataset, split_examples, split_examples_from_assignments, write_dataset
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import ModelValidationError, StudentV0Model, train_model


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _player(card_id: int) -> dict[str, object]:
    return {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card_id)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}


def _observation(options: list[object], *, minimum: int = 1, maximum: int = 1, select_type: int = 0, context: int = 0) -> dict[str, object]:
    return {
        "current": {"energyAttached": False, "firstPlayer": 0, "players": [_player(100), _player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0},
        "logs": ["not persisted"],
        "search_begin_input": "not persisted",
        "select": {"context": context, "maxCount": maximum, "minCount": minimum, "option": options, "type": select_type},
        "step": 7,
    }


def _examples() -> list:
    deck = [1] * 60
    return [
        build_rule_bc_example(_observation([{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}]), deck=deck, source_id=f"episode-{index}", source_revision="test")
        for index in range(12)
    ]


def test_dataset_builder_preserves_only_actor_information_and_stable_targets() -> None:
    example = _examples()[0]
    assert example.target_action_digests
    assert "hand_card_ids" in example.own_private_state
    assert "hand_card_ids" not in example.public_state["opponent"]
    assert "logs" not in json.dumps(example.to_dict())
    assert example.source_id.startswith("sha256:")


def test_dataset_validation_rejects_forbidden_and_nonlegal_target() -> None:
    example = _examples()[0]
    bad = example.to_dict()
    bad["public_state"]["logs"] = ["secret"]
    with pytest.raises(DatasetValidationError, match="forbidden"):
        type(example).from_dict(bad)
    bad = example.to_dict()
    bad["target_action_digests"] = ["not-legal"]
    with pytest.raises(DatasetValidationError, match="not a legal"):
        type(example).from_dict(bad)


def test_group_split_and_model_export_are_deterministic(tmp_path: Path) -> None:
    examples = _examples()
    train, validation = split_examples(examples)
    assert {item.source_id for item in train}.isdisjoint(item.source_id for item in validation)
    model = train_model(train, epochs=80)
    model_path = tmp_path / "student.json"
    model.export(model_path)
    assert StudentV0Model.load(model_path) == model
    result = evaluate_model(model, validation, repeats=2)
    assert result["teacher_top1_fidelity"] == 1.0
    assert result["legal_action_rate"] == 1.0


def test_attested_split_manifest_is_applied_without_internal_resplit() -> None:
    examples = _examples()
    assignments = {example.source_id: ("validation" if index < 2 else "train") for index, example in enumerate(examples)}
    train, validation = split_examples_from_assignments(examples, assignments)
    assert {example.source_id for example in validation} == set(list(assignments)[:2])
    assert {example.source_id for example in train}.isdisjoint(example.source_id for example in validation)
    with pytest.raises(DatasetValidationError, match="empty partition"):
        split_examples_from_assignments(examples, {example.source_id: "train" for example in examples})
    with pytest.raises(DatasetValidationError, match="cover exactly"):
        split_examples_from_assignments(examples, dict(list(assignments.items())[1:]))


def test_dataset_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    assert write_dataset(path, _examples()) == 12
    assert load_dataset(path) == _examples()


def test_student_choice_is_candidate_order_invariant_and_uses_rule_fallback(tmp_path: Path) -> None:
    examples = _examples()
    model = train_model(examples, epochs=80)
    path = tmp_path / "student.json"
    model.export(path)
    first = _observation([{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}])
    second = _observation([{"type": 7, "index": 0}, {"type": 14}, {"type": 13, "attackId": 1}])
    agent = make_student_agent(deck=[1] * 60, model_path=path)
    first_key = build_decision_state(first).legal_actions[agent(first)[0]].action_key.digest
    second_key = build_decision_state(second).legal_actions[agent(second)[0]].action_key.digest
    assert first_key == second_key
    trace = agent.student_policy.last_decision_trace  # type: ignore[attr-defined]
    assert trace["student"]["status"] == "selected"  # type: ignore[index]
    assert "hand_card_ids" not in json.dumps(trace)
    missing = make_student_agent(deck=[1] * 60, model_path=tmp_path / "missing.json")
    assert missing(first) == make_rule_agent(deck=[1] * 60)(first)


def _public_trace_digest(action_key: object) -> str:
    payload = action_key.to_public_trace_payload()  # type: ignore[attr-defined]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_selected_trace_digest_redacts_private_action_key_core(tmp_path: Path) -> None:
    model_path = tmp_path / "student.json"
    train_model(_examples(), epochs=80).export(model_path)
    first = _observation([{"type": 7, "index": 0}])
    second = _observation([{"type": 7, "index": 0}])
    second["current"]["players"][0] = _player(999)  # type: ignore[index]
    agent = make_student_agent(deck=[1] * 60, model_path=model_path)

    assert agent(first) == [0]
    first_trace = agent.student_policy.last_decision_trace  # type: ignore[attr-defined]
    assert agent(second) == [0]
    second_trace = agent.student_policy.last_decision_trace  # type: ignore[attr-defined]
    first_key = build_decision_state(first).legal_actions[0].action_key
    second_key = build_decision_state(second).legal_actions[0].action_key
    first_digest = first_trace["student"]["selected_action_key_digests"][0]  # type: ignore[index]
    second_digest = second_trace["student"]["selected_action_key_digests"][0]  # type: ignore[index]

    assert first_key.card_id == 100
    assert second_key.card_id == 999
    assert first_digest == _public_trace_digest(first_key)
    assert second_digest == _public_trace_digest(second_key) == first_digest
    assert first_key.digest != second_key.digest
    assert first_key.digest not in json.dumps(first_trace)
    assert second_key.digest not in json.dumps(second_trace)
    assert "999" not in json.dumps(second_trace)

    # A core-digest dictionary attack distinguishes all candidate card IDs;
    # the selected public digest matches none and is identical for every guess.
    guessed_core_digests: dict[int, str] = {}
    guessed_public_digests: set[str] = set()
    for card_id in range(990, 1_001):
        guessed = _observation([{"type": 7, "index": 0}])
        guessed["current"]["players"][0] = _player(card_id)  # type: ignore[index]
        guessed_key = build_decision_state(guessed).legal_actions[0].action_key
        guessed_core_digests[card_id] = guessed_key.digest
        guessed_public_digests.add(_public_trace_digest(guessed_key))
    assert len(set(guessed_core_digests.values())) == len(guessed_core_digests)
    assert second_digest not in set(guessed_core_digests.values())
    assert guessed_public_digests == {second_digest}


def test_model_schema_and_nan_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"model_schema_version":"wrong"}', encoding="utf-8")
    with pytest.raises(ModelValidationError):
        StudentV0Model.load(path)
    with pytest.raises(ModelValidationError, match="non-finite"):
        StudentV0Model((float("nan"),) * 96)


def test_student_submission_is_clean_room_importable(tmp_path: Path) -> None:
    model_path = tmp_path / "student.json"
    train_model(_examples(), epochs=80).export(model_path)
    artifact = tmp_path / "artifact"
    manifest = build_student_submission(model_path, artifact)
    assert manifest["agent_identity"] == "student-v0-rule-v0-fallback"
    assert verify_student_submission(artifact)["model_bytes"] == model_path.stat().st_size
