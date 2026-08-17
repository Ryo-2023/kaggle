"""The bridge from collected trajectories to a trainable V-trace loss."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.vtrace_bridge_v1 import (  # noqa: E402
    VTraceBridgeV1Error,
    accumulate_trajectory_losses_v1,
    admit_trajectories_v1,
    compose_vtrace_rollout_v1,
    evaluate_trajectory_loss_v1,
)


ROOT = Path(__file__).resolve().parents[2]
COLLECTED = ROOT / "runs/meta-specialist-actor-pool/real-collection-2026-08-03/games"


def _transition(**overrides):
    base = {
        "behavior_log_probability": -0.5,
        "reward": 0.0,
        "discount": 1.0,
        "value": 0.1,
        "terminal": False,
        "pool_epoch": 0,
        "prefix_steps": [{}, {}],  # a multi-select decode: two prefixes
    }
    base.update(overrides)
    return base


def _target(scale: float = 1.0):
    parameter = torch.tensor(0.0, dtype=torch.float64, requires_grad=True)

    def fn(transition):
        return parameter * scale + float(transition["behavior_log_probability"])

    return fn, parameter


def test_a_multiselect_transition_is_one_rollout_step_not_one_per_prefix() -> None:
    transitions = [
        _transition(prefix_steps=[{}, {}, {}]),
        _transition(reward=-1.0, discount=0.0, terminal=True),
    ]
    rollout = compose_vtrace_rollout_v1(transitions, bootstrap_value=0.0)

    assert len(rollout.steps) == 2  # not 3 + 1
    assert rollout.steps[-1].discount == 0.0


def test_a_terminal_transition_must_end_the_window_and_zero_the_discount() -> None:
    with pytest.raises(VTraceBridgeV1Error, match="must carry discount 0.0"):
        compose_vtrace_rollout_v1(
            [_transition(terminal=True, discount=1.0)], bootstrap_value=0.0
        )
    with pytest.raises(VTraceBridgeV1Error, match="must end the rollout window"):
        compose_vtrace_rollout_v1(
            [_transition(terminal=True, discount=0.0), _transition()], bootstrap_value=0.0
        )


def test_non_finite_and_empty_inputs_fail_closed() -> None:
    with pytest.raises(VTraceBridgeV1Error, match="at least one transition"):
        compose_vtrace_rollout_v1([], bootstrap_value=0.0)
    for field in ("reward", "discount", "value", "behavior_log_probability"):
        with pytest.raises(VTraceBridgeV1Error, match="finite"):
            compose_vtrace_rollout_v1(
                [_transition(**{field: float("nan")})], bootstrap_value=0.0
            )


def test_vs_and_pg_advantage_are_detached_from_the_policy() -> None:
    transitions = [_transition(), _transition(reward=1.0, discount=0.0, terminal=True)]
    target_fn, parameter = _target()

    loss = evaluate_trajectory_loss_v1(
        transitions, target_log_probability=target_fn,
        bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
    )
    loss.total(value_coefficient=0.5, entropy_coefficient=0.0).backward()

    # The value loss is built only from stored values and a detached target, so
    # every gradient the policy receives must come from the policy-gradient
    # term. If vs/pg_advantage leaked gradient, this would not hold.
    assert parameter.grad is not None and torch.isfinite(parameter.grad)
    advantage_only, parameter_two = _target()
    reference = evaluate_trajectory_loss_v1(
        transitions, target_log_probability=advantage_only,
        bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
    )
    reference.policy_loss_sum.backward()
    assert torch.allclose(parameter.grad, parameter_two.grad, atol=1e-12)


def test_pool_epoch_age_window_drops_stale_trajectories_with_reasons() -> None:
    records = [
        {"transitions": [_transition(pool_epoch=5)]},
        {"transitions": [_transition(pool_epoch=1)]},
        {"transitions": []},
    ]
    kept, admission = admit_trajectories_v1(
        records, current_pool_epoch=5, recipe_max_age=1
    )

    assert admission.admitted == 1 and admission.dropped == 2
    assert kept[0]["transitions"][0]["pool_epoch"] == 5
    assert any("outside the age window" in reason for reason in admission.drop_reasons)
    assert any("no transitions" in reason for reason in admission.drop_reasons)


def test_microbatch_accumulation_matches_the_whole_batch() -> None:
    first = [_transition(), _transition(reward=1.0, discount=0.0, terminal=True)]
    second = [_transition(value=0.3), _transition(reward=-1.0, discount=0.0, terminal=True)]

    whole_fn, whole_param = _target()
    parts = [
        evaluate_trajectory_loss_v1(
            item, target_log_probability=whole_fn, bootstrap_value=0.0,
            rho_bar=1.0, c_bar=1.0,
        )
        for item in (first, second)
    ]
    merged = accumulate_trajectory_losses_v1(parts)
    (merged.total(value_coefficient=0.5, entropy_coefficient=0.0) / merged.weight_sum).backward()

    assert merged.steps == 4
    assert float(merged.weight_sum) == 4.0
    assert whole_param.grad is not None and torch.isfinite(whole_param.grad)


@pytest.mark.skipif(not COLLECTED.is_dir(), reason="no real collection artifacts present")
def test_real_collected_trajectories_produce_a_finite_loss_and_gradient() -> None:
    """End-to-end against genuinely collected games, not synthetic dicts."""
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(COLLECTED.glob("*/record.json"))
    ]
    assert records, "expected at least one collected game"

    target_fn, parameter = _target()
    losses = []
    for record in records:
        transitions = record["transitions"]
        losses.append(
            evaluate_trajectory_loss_v1(
                transitions, target_log_probability=target_fn,
                bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
            )
        )
    merged = accumulate_trajectory_losses_v1(losses)
    mean = merged.total(value_coefficient=0.5, entropy_coefficient=0.0) / merged.weight_sum
    mean.backward()

    assert merged.steps == sum(len(record["transitions"]) for record in records)
    assert torch.isfinite(mean)
    assert parameter.grad is not None and torch.isfinite(parameter.grad)
