"""Research-only tests for frozen-base logit ensembles and reset ablations.

These tests intentionally avoid ``make_agent``/CABT.  The adapter is a
research boundary: semantic logits are averaged before the existing decoder,
each member owns its own recurrent state, and reset modes are explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.neural_policy_v4 import SpecialistNeuralPolicyV4
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    RuntimeDecisionEnvelope,
    greedy_decode_runtime_action_v2,
    semantic_runtime_complete_action_from_runtime_action_v2,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.research_logit_ensemble_v1 import (
    ResearchLogitEnsemblePolicyV1,
    ResearchLogitEnsembleError,
)


def _observation(*, turn: int = 2) -> dict[str, object]:
    hand = [
        {"id": 101, "serial": 1001, "playerIndex": 0},
        {"id": 102, "serial": 1002, "playerIndex": 0},
    ]
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5,
        "burned": False, "confused": False, "deckCount": 53, "discard": [],
        "hand": hand, "handCount": 2, "paralyzed": False,
        "poisoned": False, "prize": [None] * 6,
    }
    opponent = {**player, "hand": None, "handCount": 0}
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player, opponent], "result": -1, "retreated": False,
            "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
            "turn": turn, "turnActionCount": 3, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 2, "minCount": 0,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 7,
    }


def _envelope(*, turn: int = 2) -> RuntimeDecisionEnvelope:
    state = build_actor_visible_decision_state_v2(_observation(turn=turn))
    return RuntimeDecisionEnvelope.from_actor_visible_state(
        state,
        vocabulary=make_test_card_vocabulary_v1(range(1, 1_000)),
    )


@dataclass
class _FakeSession:
    policy: "_FakePolicy"
    semantic: tuple[float, ...]
    stop: float
    next_recurrent_state_token: object
    calls: int = 0
    committed: list[CommittedSemanticDecisionV2] | None = None
    aborted: int = 0

    def __post_init__(self) -> None:
        self.committed = []

    def logits(self, _model_input: object, step_input: object) -> SpecialistStepLogitsV1:
        self.calls += 1
        stop = self.stop if step_input.stop_available else None
        semantic = self.semantic
        if self.policy.resize_domain:
            semantic = tuple(
                semantic[index] if index < len(semantic) else 0.0
                for index in range(len(step_input.allowed_semantic_classes))
            )
        return SpecialistStepLogitsV1(semantic_logits=semantic, stop_logit=stop)

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        assert self.committed is not None
        self.committed.append(outcome)

    def abort(self) -> None:
        self.aborted += 1


class _FakePolicy:
    def __init__(self, semantic: tuple[float, ...], stop: float, name: str, *, resize_domain: bool = True) -> None:
        self.semantic = semantic
        self.stop = stop
        self.name = name
        self.resize_domain = resize_domain
        self.reset_calls = 0
        self.begin_calls = 0
        self.sessions: list[_FakeSession] = []

    def reset(self) -> None:
        self.reset_calls += 1

    def begin_decision(self) -> _FakeSession:
        self.begin_calls += 1
        session = _FakeSession(
            policy=self,
            semantic=self.semantic,
            stop=self.stop,
            next_recurrent_state_token=f"{self.name}-hidden-{self.begin_calls}",
        )
        self.sessions.append(session)
        return session


def _commit_decoded(envelope: RuntimeDecisionEnvelope, session: object) -> object:
    action = greedy_decode_runtime_action_v2(envelope, policy=session)
    semantic_action = semantic_runtime_complete_action_from_runtime_action_v2(envelope, action)
    outcome = CommittedSemanticDecisionV2(
        semantic_action=semantic_action,
        semantic_log_probability=0.0,
        next_recurrent_state_token=getattr(session, "next_recurrent_state_token"),
    )
    session.commit(outcome)
    return action


def test_ensemble_averages_semantic_and_stop_logits_then_commits_member_hidden() -> None:
    """The decoder sees one averaged semantic distribution, while commits stay per member."""
    left = _FakePolicy((2.0, 0.0), stop=4.0, name="left")
    right = _FakePolicy((0.0, 4.0), stop=0.0, name="right")
    policy = ResearchLogitEnsemblePolicyV1((left, right), reset_mode="normal")
    session = policy.begin_decision()
    envelope = _envelope()

    first = envelope.build_step_input(())
    averaged = session.logits(envelope._extracted.model_input, first)
    assert averaged.semantic_logits == pytest.approx((1.0, 2.0))
    assert averaged.stop_logit == pytest.approx(2.0)

    action = _commit_decoded(envelope, session)
    assert action.envelope is envelope
    assert [item.calls for item in left.sessions] == [2]
    assert [item.calls for item in right.sessions] == [2]
    assert [item.committed[0].next_recurrent_state_token for item in left.sessions] == ["left-hidden-1"]
    assert [item.committed[0].next_recurrent_state_token for item in right.sessions] == ["right-hidden-1"]
    assert session.next_recurrent_state_token == ("left-hidden-1", "right-hidden-1")


@pytest.mark.parametrize(
    ("mode", "expected_resets"),
    [("normal", 0), ("action", 3), ("turn", 1)],
)
def test_reset_modes_distinguish_normal_action_and_turn_boundaries(
    mode: str, expected_resets: int,
) -> None:
    """Action resets every action; turn resets only when the public turn changes."""
    left = _FakePolicy((1.0, 0.0), stop=0.0, name="left")
    right = _FakePolicy((1.0, 0.0), stop=0.0, name="right")
    policy = ResearchLogitEnsemblePolicyV1((left, right), reset_mode=mode)

    for turn in (2, 2, 3):
        session = policy.begin_decision()
        envelope = _envelope(turn=turn)
        _commit_decoded(envelope, session)

    assert (left.reset_calls, right.reset_calls) == (expected_resets, expected_resets)
    assert left.begin_calls == right.begin_calls == 3


def test_ensemble_rejects_member_domain_or_stop_mismatch() -> None:
    class BadPolicy(_FakePolicy):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, resize_domain=False, **kwargs)

        def begin_decision(self) -> _FakeSession:
            session = super().begin_decision()
            session.semantic = (9.0,)
            return session

    policy = ResearchLogitEnsemblePolicyV1(
        (_FakePolicy((1.0, 0.0), 0.0, "good"), BadPolicy((0.0, 1.0), 0.0, "bad")),
        reset_mode="normal",
    )
    session = policy.begin_decision()
    envelope = _envelope()
    with pytest.raises(ResearchLogitEnsembleError, match="semantic logit arity"):
        session.logits(envelope._extracted.model_input, envelope.build_step_input(()))


def test_adapter_does_not_require_or_mutate_v4_model() -> None:
    """The research adapter is a policy wrapper; frozen V4 model parameters remain untouched."""
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=8, embedding_dim=6, seed=5).eval()
    before = {name: value.detach().clone() for name, value in model.state_dict().items()}
    assert before
    after = {name: value.detach().clone() for name, value in model.state_dict().items()}
    assert all((before[name] == after[name]).all() for name in before)


def test_real_v4_members_commit_independent_tensor_hidden_states() -> None:
    """A real V4 pair reaches the decoder and commits two separate GRU tokens."""
    left = SpecialistNeuralPolicyV4(
        SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=8, embedding_dim=6, seed=11).eval(),
        policy_identity="1" * 64,
        checkpoint_lineage_id="a" * 64,
    )
    right = SpecialistNeuralPolicyV4(
        SpecialistModelV4(card_vocabulary_size=1_000, hidden_dim=8, embedding_dim=6, seed=13).eval(),
        policy_identity="2" * 64,
        checkpoint_lineage_id="b" * 64,
    )
    policy = ResearchLogitEnsemblePolicyV1((left, right), reset_mode="normal")
    envelope = _envelope()
    session = policy.begin_decision()

    action = _commit_decoded(envelope, session)
    assert action.envelope is envelope
    tokens = session.next_recurrent_state_token
    assert len(tokens) == 2
    assert all(hasattr(token, "shape") for token in tokens)
    assert tokens[0] is not tokens[1]
