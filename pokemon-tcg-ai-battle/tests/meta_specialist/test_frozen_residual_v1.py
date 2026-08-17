"""Focused contract tests for the research-only frozen Wave6 residual sidecar."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistStepLogitsV1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.frozen_residual_v1 import (
    FrozenResidualPolicyV1,
    FrozenResidualSidecarV1,
    FrozenResidualError,
    ResidualCoverageSnapshotV1,
    STOP_ACTION_KEY_V1,
    build_residual_context_v1,
    frozen_residual_signed_behavior_loss_v1,
    frozen_residual_loss_v1,
)
from mage_ptcg.meta_specialist.runtime import CommittedSemanticDecisionV2
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    RuntimeDecisionEnvelope,
    greedy_decode_runtime_action_v2,
    semantic_runtime_complete_action_from_runtime_action_v2,
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
    result: SpecialistStepLogitsV1
    hidden: object
    committed: list[CommittedSemanticDecisionV2] | None = None

    def __post_init__(self) -> None:
        self.committed = []

    @property
    def next_recurrent_state_token(self) -> object:
        return self.hidden

    def logits(self, _model_input: object, _step_input: object) -> SpecialistStepLogitsV1:
        return self.result

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        assert self.committed is not None
        self.committed.append(outcome)

    def abort(self) -> None:
        return None


class _FakePolicy:
    def __init__(self, result: SpecialistStepLogitsV1) -> None:
        self.result = result
        self.sessions: list[_FakeSession] = []

    def reset(self) -> None:
        return None

    def begin_decision(self) -> _FakeSession:
        session = _FakeSession(self.result, hidden=f"hidden-{len(self.sessions)}")
        self.sessions.append(session)
        return session

    def policy_telemetry(self) -> object:
        return object()


def _known_sidecar(context: object, action_keys: tuple[str, ...], *, hidden_dim: int = 8) -> FrozenResidualSidecarV1:
    assert hasattr(context, "context_id")
    return FrozenResidualSidecarV1(
        state_feature_dim=16,
        action_feature_dim=8,
        hidden_dim=hidden_dim,
        max_abs_residual=0.25,
        known_context_ids=(context.context_id,),
        known_action_keys=action_keys,
    )


def test_zero_init_and_bounded_residual_are_explicit() -> None:
    envelope = _envelope()
    context = build_residual_context_v1(envelope._extracted.model_input, envelope.build_step_input(()))
    action_keys = context.action_keys
    sidecar = _known_sidecar(context, action_keys)

    zero = sidecar.residuals(context)
    assert torch.equal(zero.semantic, torch.zeros(len(action_keys)))
    assert zero.stop is not None
    assert float(zero.stop) == 0.0

    with torch.no_grad():
        sidecar.output.weight.fill_(100.0)
        sidecar.output.bias.fill_(100.0)
    bounded = sidecar.residuals(context)
    assert torch.all(torch.abs(bounded.semantic) <= 0.25)
    assert bounded.stop is not None and abs(float(bounded.stop)) <= 0.25


def test_zero_init_runtime_parity_and_exact_gate_coverage_are_measured() -> None:
    """A zero sidecar must be bitwise base-parity while still exposing gate coverage."""
    envelope = _envelope()
    step_input = envelope.build_step_input(())
    context = build_residual_context_v1(envelope._extracted.model_input, step_input)
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16,
        action_feature_dim=8,
        hidden_dim=8,
        max_abs_residual=0.25,
        known_context_ids=(context.context_id,),
        known_action_keys=(*context.action_keys, STOP_ACTION_KEY_V1),
    )
    base = torch.tensor([0.3 + index for index in range(len(context.action_keys))])
    base_stop = torch.tensor(0.1)
    adjusted = sidecar.adjust_logits(
        base,
        base_stop,
        context,
        action_types=tuple(item.semantic_row.option_type for item in step_input.allowed_semantic_classes),
    )
    assert torch.equal(adjusted.semantic, base)
    assert adjusted.stop is not None and torch.equal(adjusted.stop, base_stop)

    snapshot = sidecar.coverage_snapshot()
    assert isinstance(snapshot, ResidualCoverageSnapshotV1)
    assert snapshot.total_decisions == 1
    assert snapshot.valid_context_decisions == 1
    assert snapshot.exact_known_context == 1
    assert snapshot.eligible_action_slots == len(context.action_keys) + 1
    assert snapshot.known_action_slots == len(context.action_keys) + 1
    assert snapshot.residual_applied_slots == len(context.action_keys) + 1
    assert snapshot.nonzero_residual_slots == 0
    assert snapshot.top1_change_decisions == 0
    assert snapshot.ood_pass_through == 0
    assert snapshot.stop_decisions == 1
    assert snapshot.known_stop_decisions == 1
    payload = snapshot.to_dict()
    assert payload["exact_known_context_rate"] == pytest.approx(1.0)
    assert payload["known_action_rate"] == pytest.approx(1.0)
    assert payload["nonzero_residual_rate"] == pytest.approx(0.0)
    assert payload["residual_magnitude"]["count"] == len(context.action_keys) + 1


def test_heldout_unknown_context_is_counted_as_ood_pass_through() -> None:
    """A new public state must not silently look like a measured residual hit."""
    envelope = _envelope(turn=3)
    context = build_residual_context_v1(envelope._extracted.model_input, envelope.build_step_input(()))
    sidecar = FrozenResidualSidecarV1(
        state_feature_dim=16,
        action_feature_dim=8,
        hidden_dim=8,
        max_abs_residual=0.25,
        known_context_ids=("0" * 64,),
        known_action_keys=(*context.action_keys, STOP_ACTION_KEY_V1),
    )
    base = torch.arange(float(len(context.action_keys)))
    base_stop = torch.tensor(-0.5)
    adjusted = sidecar.adjust_logits(base, base_stop, context)
    assert torch.equal(adjusted.semantic, base)
    assert adjusted.stop is not None and torch.equal(adjusted.stop, base_stop)
    snapshot = sidecar.coverage_snapshot()
    assert snapshot.total_decisions == 1
    assert snapshot.valid_context_decisions == 1
    assert snapshot.exact_known_context == 0
    assert snapshot.eligible_action_slots == len(context.action_keys) + 1
    assert snapshot.known_action_slots == 0
    assert snapshot.residual_applied_slots == 0
    assert snapshot.ood_pass_through == 1
    assert snapshot.pass_through_reasons == {"unknown_context": 1}
    assert snapshot.to_dict()["ood_pass_through_rate"] == pytest.approx(1.0)


def test_coverage_delta_is_append_only_and_resettable() -> None:
    envelope = _envelope()
    context = build_residual_context_v1(envelope._extracted.model_input, envelope.build_step_input(()))
    sidecar = _known_sidecar(context, context.action_keys)
    before = sidecar.coverage_snapshot()
    base = torch.ones(len(context.action_keys))
    sidecar.adjust_logits(base, torch.tensor(0.0), context)
    after = sidecar.coverage_snapshot()
    delta = after.delta(before)
    assert delta.total_decisions == 1
    assert delta.exact_known_context == 1
    assert sidecar.reset_coverage().total_decisions == 1
    assert sidecar.coverage_snapshot().total_decisions == 0


def test_malformed_and_ood_context_fail_closed_to_zero() -> None:
    envelope = _envelope()
    step_input = envelope.build_step_input(())
    context = build_residual_context_v1(envelope._extracted.model_input, step_input)
    sidecar = _known_sidecar(context, context.action_keys)
    with torch.no_grad():
        sidecar.output.bias.fill_(1.0)
    base = torch.tensor([2.0] * len(context.action_keys))
    base_stop = torch.tensor(3.0)

    malformed = sidecar.adjust_logits(base, base_stop, None)
    assert torch.equal(malformed.semantic, base)
    assert malformed.stop is not None and torch.equal(malformed.stop, base_stop)

    ood = build_residual_context_v1(envelope._extracted.model_input, envelope.build_step_input(()))
    sidecar_ood = FrozenResidualSidecarV1(
        state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
        max_abs_residual=0.25, known_context_ids=("0" * 64,),
        known_action_keys=ood.action_keys,
    )
    adjusted = sidecar_ood.adjust_logits(base, base_stop, ood)
    assert torch.equal(adjusted.semantic, base)
    assert adjusted.stop is not None and torch.equal(adjusted.stop, base_stop)


def test_anchor_kl_and_residual_l2_keep_base_frozen() -> None:
    base = torch.tensor([[2.0, 0.0, -1.0]], requires_grad=True)
    residual = torch.tensor([[0.2, -0.1, 0.0]], requires_grad=True)
    breakdown = frozen_residual_loss_v1(
        base, residual, torch.tensor([0]), anchor_kl_weight=2.0, residual_l2_weight=0.5,
    )
    assert breakdown.total.item() > breakdown.imitation.item()
    breakdown.total.backward()
    assert base.grad is None
    assert residual.grad is not None and torch.isfinite(residual.grad).all()

    zero = frozen_residual_loss_v1(base.detach(), torch.zeros_like(residual), torch.tensor([0]))
    assert zero.anchor_kl.item() == pytest.approx(0.0, abs=1e-7)
    assert zero.residual_l2.item() == pytest.approx(0.0, abs=1e-7)


def test_signed_behavior_loss_uses_signed_log_probability_and_keeps_base_frozen() -> None:
    base = torch.tensor([[1.0, 0.0, -1.0]], requires_grad=True)
    residual = torch.tensor([[0.2, -0.1, 0.0]], requires_grad=True)
    weight = torch.tensor([0.5])
    breakdown = frozen_residual_signed_behavior_loss_v1(
        base,
        residual,
        torch.tensor([0]),
        weight,
        anchor_kl_weight=2.0,
        residual_l2_weight=0.5,
    )
    expected_signed_log_prob = -0.5 * torch.log_softmax(base.detach() + residual, dim=-1)[0, 0]
    assert breakdown.imitation.item() == pytest.approx(expected_signed_log_prob.item())
    assert breakdown.total.item() > breakdown.imitation.item()
    breakdown.total.backward()
    assert base.grad is None
    assert residual.grad is not None and torch.isfinite(residual.grad).all()

    negative = frozen_residual_signed_behavior_loss_v1(
        base.detach(), residual.detach(), torch.tensor([0]), torch.tensor([-0.5]),
    )
    expected_negative = 0.5 * torch.log_softmax(base.detach() + residual.detach(), dim=-1)[0, 0]
    assert negative.imitation.item() == pytest.approx(expected_negative.item())
    with pytest.raises(FrozenResidualError, match="signed_weight"):
        frozen_residual_signed_behavior_loss_v1(
            base.detach(), residual.detach(), torch.tensor([0]), torch.tensor([1.1]),
        )


def test_wrapper_preserves_semantic_stop_arity_and_gru_commit() -> None:
    envelope = _envelope()
    step_input = envelope.build_step_input(())
    context = build_residual_context_v1(envelope._extracted.model_input, step_input)
    base_result = SpecialistStepLogitsV1(
        semantic_logits=tuple(float(index) for index in range(len(step_input.allowed_semantic_classes))),
        stop_logit=0.0,
    )
    base = _FakePolicy(base_result)
    sidecar = _known_sidecar(context, context.action_keys)
    policy = FrozenResidualPolicyV1(base, sidecar)
    session = policy.begin_decision()
    result = session.logits(envelope._extracted.model_input, step_input)
    assert len(result.semantic_logits) == len(step_input.allowed_semantic_classes)
    assert result.stop_logit is not None
    assert result.semantic_logits == pytest.approx(base_result.semantic_logits)

    action = greedy_decode_runtime_action_v2(envelope, policy=session)
    semantic = semantic_runtime_complete_action_from_runtime_action_v2(envelope, action)
    outcome = CommittedSemanticDecisionV2(
        semantic_action=semantic,
        semantic_log_probability=0.0,
        next_recurrent_state_token=session.next_recurrent_state_token,
    )
    session.commit(outcome)
    assert base.sessions[0].committed == [outcome]
    assert session.next_recurrent_state_token == "hidden-0"


def test_sidecar_rejects_bad_topology_and_weights() -> None:
    with pytest.raises(FrozenResidualError):
        FrozenResidualSidecarV1(
            state_feature_dim=0, action_feature_dim=8, hidden_dim=8,
            max_abs_residual=0.25, known_context_ids=(), known_action_keys=(),
        )
    with pytest.raises(FrozenResidualError):
        FrozenResidualSidecarV1(
            state_feature_dim=16, action_feature_dim=8, hidden_dim=8,
            max_abs_residual=0.0, known_context_ids=(), known_action_keys=(),
        )
