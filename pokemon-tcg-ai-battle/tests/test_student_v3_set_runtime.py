from __future__ import annotations

import math
from pathlib import Path

import pytest

from mage_ptcg.decision_state import build_decision_state


DECK = [1] * 60


def _observation(*, minimum: int = 0, maximum: int = 2) -> dict[str, object]:
    card = {
        "id": 1,
        "serial": 0,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }
    player = {
        "active": [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": [card],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [player, player],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": 0,
            "maxCount": maximum,
            "minCount": minimum,
            "option": [
                {"type": 14},
                {"type": 13, "attackId": 1},
                {"type": 7, "index": 0},
            ],
            "type": 0,
        },
        "step": 7,
    }


class _FixedModel:
    def __init__(self, action_scores: list[float], count_scores: list[float]) -> None:
        self.action_scores = action_scores
        self.count_scores = count_scores

    def __call__(self, state, actions, legal_mask):
        import torch

        del state
        return (
            torch.tensor([self.action_scores[: actions.shape[1]]], device=actions.device),
            torch.tensor([self.count_scores], device=actions.device),
        )

    def eval(self):
        return self


def test_runtime_decodes_explicit_zero_variable_and_fixed_multi() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
    )

    decline = StudentV3SetCandidatePolicy(
        model=_FixedModel([3.0, 2.0, 1.0], [10.0, 0.0, 0.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    assert decline.choose(_observation(minimum=0, maximum=2)) == []

    single = StudentV3SetCandidatePolicy(
        model=_FixedModel([0.0, 4.0, 2.0], [0.0, 10.0, 0.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    assert single.choose(_observation(minimum=0, maximum=2)) == [1]

    fixed = StudentV3SetCandidatePolicy(
        model=_FixedModel([0.0, 4.0, 2.0], [100.0, 100.0, -100.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    assert fixed.choose(_observation(minimum=2, maximum=2)) == [1, 2]


def test_runtime_ties_use_stable_actionkey_then_exact_option_index() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
    )

    observation = _observation(minimum=2, maximum=2)
    state = build_decision_state(observation)
    expected = [
        action.option_index
        for action in sorted(
            state.legal_actions,
            key=lambda action: (action.action_key.digest, action.option_index),
        )[:2]
    ]
    policy = StudentV3SetCandidatePolicy(
        model=_FixedModel([1.0, 1.0, 1.0], [0.0, 0.0, 1.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    assert policy.choose(observation) == expected


def test_runtime_ordered_selection_uses_legal_counted_rule_v0_fallback() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
    )

    class _MustNotRunModel:
        def eval(self):
            return self

        def __call__(self, *_args, **_kwargs):
            raise AssertionError("ordered fallback must not invoke the set model")

    policy = StudentV3SetCandidatePolicy(
        model=_MustNotRunModel(),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    ordered = _observation(minimum=1, maximum=2)
    ordered["select"] = {**ordered["select"], "type": 5, "context": 34}

    selected = policy.choose(ordered)

    assert selected == [0]
    assert len(selected) == len(set(selected))
    assert all(0 <= index < len(ordered["select"]["option"]) for index in selected)
    assert policy.last_decision_trace == {
        "status": "rule_v0_fallback",
        "fallback_reason": "ordered_selection_requires_pointer_head",
        "selected_count": 1,
    }
    assert policy.telemetry_snapshot() == {
        "selection_decision_count": 1,
        "model_decision_count": 0,
        "fallback_count": 1,
        "fallback_reason_counts": {
            "ordered_selection_requires_pointer_head": 1,
        },
    }


def test_runtime_explicit_duplicate_action_identity_uses_rule_v0_fallback() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
    )

    class _MustNotRunModel:
        def eval(self):
            return self

        def __call__(self, *_args, **_kwargs):
            raise AssertionError("unsupported selection must not invoke the set model")

    observation = _observation(minimum=1, maximum=1)
    tool = {
        "id": 2,
        "serial": 1,
        "playerIndex": 0,
        "hp": 0,
        "maxHp": 0,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }
    host = dict(observation["current"]["players"][0]["hand"][0])
    host["tools"] = [tool]
    observation["current"]["players"][0]["active"] = [host]
    observation["select"] = {
        **observation["select"],
        "type": 2,
        "context": 26,
        "option": [
            {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
            {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        ],
    }
    policy = StudentV3SetCandidatePolicy(
        model=_MustNotRunModel(),
        device="cpu",
        deck=DECK,
        max_count=2,
    )

    assert policy.choose(observation) == [0]
    assert policy.telemetry_snapshot()["fallback_reason_counts"] == {
        "duplicate_stable_actionkey_identity": 1,
    }


def test_runtime_unknown_and_nonfinite_errors_propagate_without_fallback() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
        StudentV3SetRuntimeError,
    )

    policy = StudentV3SetCandidatePolicy(
        model=_FixedModel([1.0, 0.0, -1.0], [0.0, 1.0, 0.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )

    unknown = _observation(minimum=1, maximum=2)
    unknown["select"] = {**unknown["select"], "type": 999}
    with pytest.raises(StudentV3SetRuntimeError, match="unknown"):
        policy.choose(unknown)

    malformed_state = _observation(minimum=1, maximum=2)
    malformed_state["current"] = {
        **malformed_state["current"],
        "yourIndex": 2,
    }
    with pytest.raises(StudentV3SetRuntimeError, match="decision state construction"):
        policy.choose(malformed_state)

    bad_action = StudentV3SetCandidatePolicy(
        model=_FixedModel([1.0, math.nan, -1.0], [0.0, 1.0, 0.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    with pytest.raises(StudentV3SetRuntimeError, match="non-finite action"):
        bad_action.choose(_observation(minimum=1, maximum=2))

    bad_count = StudentV3SetCandidatePolicy(
        model=_FixedModel([1.0, 0.0, -1.0], [math.nan, math.nan, math.nan]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    with pytest.raises(StudentV3SetRuntimeError, match="non-finite count"):
        bad_count.choose(_observation(minimum=1, maximum=2))

    assert policy.telemetry_snapshot()["fallback_count"] == 0
    assert bad_action.telemetry_snapshot()["fallback_count"] == 0
    assert bad_count.telemetry_snapshot()["fallback_count"] == 0


def test_runtime_returns_bound_deck_and_rejects_invalid_constructor() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetCandidatePolicy,
        StudentV3SetRuntimeError,
    )

    policy = StudentV3SetCandidatePolicy(
        model=_FixedModel([1.0], [1.0, 0.0, 0.0]),
        device="cpu",
        deck=DECK,
        max_count=2,
    )
    assert policy.choose({"select": None}) == DECK
    assert policy.as_agent()({"select": None}, {"seed": 1}) == DECK
    with pytest.raises(StudentV3SetRuntimeError, match="60-card"):
        StudentV3SetCandidatePolicy(
            model=policy,
            device="cpu",
            deck=[1] * 59,
            max_count=2,
        )


def test_runtime_closure_is_closed_and_content_addressed() -> None:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        student_v3_set_runtime_closure_v1,
    )

    closure = student_v3_set_runtime_closure_v1()

    assert closure["schema_version"] == "student-v3-set-runtime-closure-v1"
    assert len(closure["closure_sha256"]) == 64
    assert set(closure["source_sha256s"]) == {
        "cabt_json_contract",
        "cabt_trace",
        "candidate_pilot",
        "decision_state",
        "deck_io",
        "gpu_student_v3_set",
        "rule_v0",
        "student_dataset",
        "student_features",
        "student_model",
        "student_v3_set_runtime",
    }
    assert all(len(value) == 64 for value in closure["source_sha256s"].values())
    assert all(
        Path(path).is_file() and not Path(path).is_absolute()
        for path in closure["source_paths"].values()
    )
