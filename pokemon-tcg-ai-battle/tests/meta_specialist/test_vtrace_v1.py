"""TDD coverage for clipped IMPALA V-trace: stdlib oracle and torch parity."""

from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.vtrace_v1 import (  # noqa: E402
    VTraceRolloutStepV1,
    VTraceRolloutV1,
    VtraceV1Error,
    evaluate_vtrace_v1,
    evaluate_vtrace_v1_torch,
    trajectory_within_pool_age_window_v1,
)


def _step(
    *, behavior: float, target: float, reward: float, discount: float, value: float,
    terminal: bool = False,
) -> VTraceRolloutStepV1:
    return VTraceRolloutStepV1(
        behavior_log_probability=behavior, target_log_probability=target,
        reward=reward, discount=discount, value=value, terminal=terminal,
    )


def _rollout(steps: tuple[VTraceRolloutStepV1, ...], *, bootstrap_value: float) -> VTraceRolloutV1:
    return VTraceRolloutV1(steps=steps, bootstrap_value=bootstrap_value)


# --- Hand-checked forward math -------------------------------------------


def test_single_step_matches_hand_computation() -> None:
    # ratio = exp(-0.5 - -0.7) = exp(0.2); well below rho_bar/c_bar so no clip.
    step = _step(behavior=-0.7, target=-0.5, reward=0.3, discount=0.9, value=0.1)
    rollout = _rollout((step,), bootstrap_value=0.4)

    result = evaluate_vtrace_v1(rollout, rho_bar=1.5, c_bar=1.5)

    ratio = math.exp(-0.5 - (-0.7))
    delta = ratio * (0.3 + 0.9 * 0.4 - 0.1)
    vs = 0.1 + delta  # discount * c * (bootstrap - bootstrap) == 0
    pg = ratio * (0.3 + 0.9 * 0.4 - 0.1)

    assert result.steps[0].ratio == pytest.approx(ratio)
    assert result.steps[0].rho == pytest.approx(ratio)
    assert result.steps[0].c == pytest.approx(ratio)
    assert result.steps[0].vs == pytest.approx(vs)
    assert result.steps[0].pg_advantage == pytest.approx(pg)


def test_two_step_recursion_matches_hand_computation() -> None:
    step0 = _step(behavior=-0.6, target=-0.6, reward=0.0, discount=0.95, value=0.2)
    step1 = _step(behavior=-0.6, target=-0.6, reward=1.0, discount=0.95, value=0.3)
    rollout = _rollout((step0, step1), bootstrap_value=0.5)

    result = evaluate_vtrace_v1(rollout, rho_bar=2.0, c_bar=2.0)

    ratio = 1.0  # target == behavior everywhere
    delta1 = ratio * (1.0 + 0.95 * 0.5 - 0.3)
    vs1 = 0.3 + delta1
    delta0 = ratio * (0.0 + 0.95 * 0.3 - 0.2)
    vs0 = 0.2 + delta0 + 0.95 * ratio * (vs1 - 0.3)
    pg1 = ratio * (1.0 + 0.95 * 0.5 - 0.3)
    pg0 = ratio * (0.0 + 0.95 * vs1 - 0.2)

    assert result.steps[1].vs == pytest.approx(vs1)
    assert result.steps[0].vs == pytest.approx(vs0)
    assert result.steps[1].pg_advantage == pytest.approx(pg1)
    assert result.steps[0].pg_advantage == pytest.approx(pg0)


# --- Clipping actually binds ----------------------------------------------


def test_clipping_binds_exactly_at_the_chosen_thresholds() -> None:
    # log-ratio = -2.0 - (-5.0) = 3.0 => ratio ~= 20.09, comfortably above both bars.
    step = _step(behavior=-5.0, target=-2.0, reward=0.2, discount=0.9, value=0.1)
    rollout = _rollout((step,), bootstrap_value=0.0)

    result = evaluate_vtrace_v1(rollout, rho_bar=1.2, c_bar=1.1)

    assert result.steps[0].ratio > 1.2
    assert result.steps[0].rho == 1.2
    assert result.steps[0].c == 1.1


def test_clipping_saturates_so_a_larger_ratio_gives_an_identical_result() -> None:
    moderate = _step(behavior=-5.0, target=-2.0, reward=0.2, discount=0.9, value=0.1)
    extreme = _step(behavior=-5.0, target=-0.01, reward=0.2, discount=0.9, value=0.1)
    rollout_moderate = _rollout((moderate,), bootstrap_value=0.4)
    rollout_extreme = _rollout((extreme,), bootstrap_value=0.4)

    result_moderate = evaluate_vtrace_v1(rollout_moderate, rho_bar=1.2, c_bar=1.1)
    result_extreme = evaluate_vtrace_v1(rollout_extreme, rho_bar=1.2, c_bar=1.1)

    assert result_moderate.steps[0].ratio != pytest.approx(result_extreme.steps[0].ratio)
    assert result_moderate.steps[0].rho == result_extreme.steps[0].rho == 1.2
    assert result_moderate.steps[0].c == result_extreme.steps[0].c == 1.1
    assert result_moderate.steps[0].vs == pytest.approx(result_extreme.steps[0].vs)
    assert result_moderate.steps[0].pg_advantage == pytest.approx(result_extreme.steps[0].pg_advantage)


def test_below_threshold_ratio_is_not_clipped() -> None:
    step = _step(behavior=-0.5, target=-0.5, reward=0.0, discount=0.9, value=0.0)
    rollout = _rollout((step,), bootstrap_value=0.0)

    result = evaluate_vtrace_v1(rollout, rho_bar=5.0, c_bar=5.0)

    assert result.steps[0].ratio == pytest.approx(1.0)
    assert result.steps[0].rho == pytest.approx(1.0)
    assert result.steps[0].c == pytest.approx(1.0)


def test_rho_bar_and_c_bar_are_required_explicit_keywords() -> None:
    step = _step(behavior=-0.5, target=-0.5, reward=0.0, discount=0.9, value=0.0)
    rollout = _rollout((step,), bootstrap_value=0.0)
    with pytest.raises(TypeError):
        evaluate_vtrace_v1(rollout)  # type: ignore[call-arg]


# --- Terminal handling: must not bootstrap past the end --------------------


def test_terminal_step_is_invariant_to_bootstrap_value() -> None:
    step = _step(behavior=-0.4, target=-0.3, reward=1.0, discount=0.0, value=0.2, terminal=True)

    result_a = evaluate_vtrace_v1(_rollout((step,), bootstrap_value=0.0), rho_bar=1.5, c_bar=1.5)
    result_b = evaluate_vtrace_v1(_rollout((step,), bootstrap_value=999.0), rho_bar=1.5, c_bar=1.5)

    assert result_a.steps[0].vs == pytest.approx(result_b.steps[0].vs)
    assert result_a.steps[0].pg_advantage == pytest.approx(result_b.steps[0].pg_advantage)


def test_terminal_step_blocks_backward_propagation_across_episode_boundary() -> None:
    """A step after a mid-window terminal must not leak value into steps before it."""
    step0 = _step(behavior=-0.5, target=-0.4, reward=0.0, discount=0.95, value=0.1)
    terminal_step = _step(
        behavior=-0.3, target=-0.3, reward=1.0, discount=0.0, value=0.2, terminal=True,
    )

    def _third_step(value: float) -> VTraceRolloutStepV1:
        return _step(behavior=-0.6, target=-0.5, reward=-0.2, discount=0.9, value=value)

    result_low = evaluate_vtrace_v1(
        _rollout((step0, terminal_step, _third_step(-0.9)), bootstrap_value=0.5),
        rho_bar=1.5, c_bar=1.5,
    )
    result_high = evaluate_vtrace_v1(
        _rollout((step0, terminal_step, _third_step(0.9)), bootstrap_value=0.5),
        rho_bar=1.5, c_bar=1.5,
    )

    # Steps at/before the terminal boundary are unaffected by anything after it.
    assert result_low.steps[0].vs == pytest.approx(result_high.steps[0].vs)
    assert result_low.steps[1].vs == pytest.approx(result_high.steps[1].vs)
    # The post-terminal step itself does differ, proving the fixture is live.
    assert result_low.steps[2].vs != pytest.approx(result_high.steps[2].vs)


def test_terminal_step_also_invariant_across_different_bootstrap_with_prefix() -> None:
    step0 = _step(behavior=-0.5, target=-0.4, reward=0.0, discount=0.95, value=0.1)
    terminal_step = _step(
        behavior=-0.3, target=-0.3, reward=1.0, discount=0.0, value=0.2, terminal=True,
    )
    result_a = evaluate_vtrace_v1(
        _rollout((step0, terminal_step), bootstrap_value=-5.0), rho_bar=1.5, c_bar=1.5,
    )
    result_b = evaluate_vtrace_v1(
        _rollout((step0, terminal_step), bootstrap_value=5.0), rho_bar=1.5, c_bar=1.5,
    )
    assert result_a.steps[0].vs == pytest.approx(result_b.steps[0].vs)
    assert result_a.steps[1].vs == pytest.approx(result_b.steps[1].vs)


def test_discount_must_be_zero_exactly_when_terminal() -> None:
    with pytest.raises(VtraceV1Error, match="discount exactly 0.0"):
        _step(behavior=-0.1, target=-0.1, reward=0.0, discount=0.5, value=0.0, terminal=True)
    with pytest.raises(VtraceV1Error, match="cannot carry discount 0.0"):
        _step(behavior=-0.1, target=-0.1, reward=0.0, discount=0.0, value=0.0, terminal=False)


# --- Non-finite rejection ---------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_reward_rejected(bad: float) -> None:
    with pytest.raises(VtraceV1Error):
        _step(behavior=-0.1, target=-0.1, reward=bad, discount=0.9, value=0.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_log_probability_rejected(bad: float) -> None:
    with pytest.raises(VtraceV1Error):
        _step(behavior=bad, target=-0.1, reward=0.0, discount=0.9, value=0.0)
    with pytest.raises(VtraceV1Error):
        _step(behavior=-0.1, target=bad, reward=0.0, discount=0.9, value=0.0)


def test_positive_log_probability_rejected() -> None:
    with pytest.raises(VtraceV1Error, match="cannot be positive"):
        _step(behavior=0.5, target=-0.1, reward=0.0, discount=0.9, value=0.0)
    with pytest.raises(VtraceV1Error, match="cannot be positive"):
        _step(behavior=-0.1, target=0.5, reward=0.0, discount=0.9, value=0.0)


def test_non_finite_bootstrap_value_rejected() -> None:
    step = _step(behavior=-0.1, target=-0.1, reward=0.0, discount=0.9, value=0.0)
    with pytest.raises(VtraceV1Error):
        evaluate_vtrace_v1(
            _rollout((step,), bootstrap_value=float("nan")), rho_bar=1.5, c_bar=1.5,
        )


def test_non_positive_rho_bar_rejected() -> None:
    step = _step(behavior=-0.1, target=-0.1, reward=0.0, discount=0.9, value=0.0)
    with pytest.raises(VtraceV1Error, match="rho_bar"):
        evaluate_vtrace_v1(_rollout((step,), bootstrap_value=0.0), rho_bar=0.0, c_bar=1.0)


# --- Pool-epoch age window --------------------------------------------------


def test_age_window_admits_current_epoch() -> None:
    assert trajectory_within_pool_age_window_v1(
        current_pool_epoch=10, trajectory_pool_epoch=10, recipe_max_age=0,
    )


def test_age_window_admits_at_the_boundary() -> None:
    assert trajectory_within_pool_age_window_v1(
        current_pool_epoch=10, trajectory_pool_epoch=7, recipe_max_age=3,
    )


def test_age_window_rejects_just_outside_the_boundary() -> None:
    assert not trajectory_within_pool_age_window_v1(
        current_pool_epoch=10, trajectory_pool_epoch=6, recipe_max_age=3,
    )


def test_age_window_rejects_a_future_trajectory_epoch() -> None:
    assert not trajectory_within_pool_age_window_v1(
        current_pool_epoch=10, trajectory_pool_epoch=11, recipe_max_age=5,
    )


def test_age_window_rejects_bad_types() -> None:
    with pytest.raises(VtraceV1Error):
        trajectory_within_pool_age_window_v1(
            current_pool_epoch=10, trajectory_pool_epoch=7, recipe_max_age=-1,
        )


# --- Oracle vs. torch: forward value parity --------------------------------


def _fixture_arrays() -> tuple[list[float], list[float], list[float], list[float], list[float], list[bool]]:
    behavior = [-0.9, -0.4, -1.2, -0.3, -0.7, -0.6]
    target = [-0.7, -0.5, -0.8, -0.2, -0.9, -0.6]
    reward = [0.1, -0.2, 0.0, 0.4, -0.1, 1.0]
    discount = [0.95, 0.97, 0.9, 0.93, 0.96, 0.0]
    value = [0.05, 0.12, -0.08, 0.2, 0.15, 0.3]
    terminal = [False, False, False, False, False, True]
    return behavior, target, reward, discount, value, terminal


def _oracle_rollout(bootstrap: float) -> VTraceRolloutV1:
    behavior, target, reward, discount, value, terminal = _fixture_arrays()
    steps = tuple(
        _step(behavior=b, target=t, reward=r, discount=d, value=v, terminal=term)
        for b, t, r, d, v, term in zip(behavior, target, reward, discount, value, terminal)
    )
    return _rollout(steps, bootstrap_value=bootstrap)


def _torch_tensors(bootstrap: float, *, requires_grad: bool = False):
    behavior, target, reward, discount, value, _terminal = _fixture_arrays()
    behavior_t = torch.tensor(behavior, dtype=torch.float64)
    target_t = torch.tensor(target, dtype=torch.float64, requires_grad=requires_grad)
    reward_t = torch.tensor(reward, dtype=torch.float64)
    discount_t = torch.tensor(discount, dtype=torch.float64)
    value_t = torch.tensor(value, dtype=torch.float64, requires_grad=requires_grad)
    bootstrap_t = torch.tensor(bootstrap, dtype=torch.float64, requires_grad=requires_grad)
    return behavior_t, target_t, reward_t, discount_t, value_t, bootstrap_t


def test_oracle_and_torch_forward_values_match() -> None:
    bootstrap = 0.25
    oracle = evaluate_vtrace_v1(_oracle_rollout(bootstrap), rho_bar=1.3, c_bar=1.1)
    behavior_t, target_t, reward_t, discount_t, value_t, bootstrap_t = _torch_tensors(bootstrap)

    result = evaluate_vtrace_v1_torch(
        behavior_log_probability=behavior_t, target_log_probability=target_t,
        reward=reward_t, discount=discount_t, value=value_t, bootstrap_value=bootstrap_t,
        rho_bar=1.3, c_bar=1.1,
    )

    oracle_vs = torch.tensor([s.vs for s in oracle.steps], dtype=torch.float64)
    oracle_pg = torch.tensor([s.pg_advantage for s in oracle.steps], dtype=torch.float64)
    oracle_rho = torch.tensor([s.rho for s in oracle.steps], dtype=torch.float64)
    oracle_c = torch.tensor([s.c for s in oracle.steps], dtype=torch.float64)
    oracle_ratio = torch.tensor([s.ratio for s in oracle.steps], dtype=torch.float64)

    assert torch.allclose(result.vs, oracle_vs, rtol=1e-9, atol=1e-9)
    assert torch.allclose(result.pg_advantage, oracle_pg, rtol=1e-9, atol=1e-9)
    assert torch.allclose(result.rho, oracle_rho, rtol=1e-9, atol=1e-9)
    assert torch.allclose(result.c, oracle_c, rtol=1e-9, atol=1e-9)
    assert torch.allclose(result.ratio, oracle_ratio, rtol=1e-9, atol=1e-9)
    assert result.vs.device.type == "cpu"
    assert not result.vs.requires_grad  # leaves had requires_grad=False here


# --- Oracle vs. torch: gradient parity (no clipping active) ---------------


def _oracle_loss(
    *, target_overrides: list[float] | None, value_overrides: list[float] | None,
    bootstrap: float, weight_vs: float, weight_pg: float,
) -> float:
    behavior, target, reward, discount, value, terminal = _fixture_arrays()
    if target_overrides is not None:
        target = target_overrides
    if value_overrides is not None:
        value = value_overrides
    steps = tuple(
        _step(behavior=b, target=t, reward=r, discount=d, value=v, terminal=term)
        for b, t, r, d, v, term in zip(behavior, target, reward, discount, value, terminal)
    )
    result = evaluate_vtrace_v1(_rollout(steps, bootstrap_value=bootstrap), rho_bar=6.0, c_bar=6.0)
    return weight_vs * math.fsum(s.vs for s in result.steps) + weight_pg * math.fsum(
        s.pg_advantage for s in result.steps
    )


def test_oracle_and_torch_gradients_match_via_finite_difference() -> None:
    """rho_bar/c_bar=6.0 keeps every step's ratio well under the clip (no kink)."""
    bootstrap = 0.25
    weight_vs, weight_pg = 0.7, -0.4
    behavior_t, target_t, reward_t, discount_t, value_t, bootstrap_t = _torch_tensors(
        bootstrap, requires_grad=True,
    )

    result = evaluate_vtrace_v1_torch(
        behavior_log_probability=behavior_t, target_log_probability=target_t,
        reward=reward_t, discount=discount_t, value=value_t, bootstrap_value=bootstrap_t,
        rho_bar=6.0, c_bar=6.0,
    )
    _, target, _, _, value, _ = _fixture_arrays()
    for s in result.rho.detach().tolist():
        assert s < 6.0  # sanity: clipping is not active in this fixture
    loss = weight_vs * result.vs.sum() + weight_pg * result.pg_advantage.sum()
    loss.backward()

    assert target_t.grad is not None and value_t.grad is not None and bootstrap_t.grad is not None

    h = 1.0e-5
    for index in range(len(target)):
        perturbed_target = list(target)
        perturbed_target[index] += h
        plus = _oracle_loss(
            target_overrides=perturbed_target, value_overrides=None,
            bootstrap=bootstrap, weight_vs=weight_vs, weight_pg=weight_pg,
        )
        perturbed_target[index] -= 2 * h
        minus = _oracle_loss(
            target_overrides=perturbed_target, value_overrides=None,
            bootstrap=bootstrap, weight_vs=weight_vs, weight_pg=weight_pg,
        )
        finite_diff = (plus - minus) / (2 * h)
        assert finite_diff == pytest.approx(float(target_t.grad[index]), abs=1e-5)

    for index in range(len(value)):
        perturbed_value = list(value)
        perturbed_value[index] += h
        plus = _oracle_loss(
            target_overrides=None, value_overrides=perturbed_value,
            bootstrap=bootstrap, weight_vs=weight_vs, weight_pg=weight_pg,
        )
        perturbed_value[index] -= 2 * h
        minus = _oracle_loss(
            target_overrides=None, value_overrides=perturbed_value,
            bootstrap=bootstrap, weight_vs=weight_vs, weight_pg=weight_pg,
        )
        finite_diff = (plus - minus) / (2 * h)
        assert finite_diff == pytest.approx(float(value_t.grad[index]), abs=1e-5)

    plus = _oracle_loss(
        target_overrides=None, value_overrides=None, bootstrap=bootstrap + h,
        weight_vs=weight_vs, weight_pg=weight_pg,
    )
    minus = _oracle_loss(
        target_overrides=None, value_overrides=None, bootstrap=bootstrap - h,
        weight_vs=weight_vs, weight_pg=weight_pg,
    )
    finite_diff = (plus - minus) / (2 * h)
    assert finite_diff == pytest.approx(float(bootstrap_t.grad), abs=1e-5)


def test_clipped_target_log_probability_has_zero_local_gradient_through_rho() -> None:
    """When target is deep in the clipped region, nudging it further leaves vs unchanged."""
    behavior = torch.tensor([-5.0], dtype=torch.float64)
    target = torch.tensor([-0.01], dtype=torch.float64, requires_grad=True)
    reward = torch.tensor([0.2], dtype=torch.float64)
    discount = torch.tensor([0.9], dtype=torch.float64)
    value = torch.tensor([0.1], dtype=torch.float64)
    bootstrap = torch.tensor(0.4, dtype=torch.float64)

    result = evaluate_vtrace_v1_torch(
        behavior_log_probability=behavior, target_log_probability=target,
        reward=reward, discount=discount, value=value, bootstrap_value=bootstrap,
        rho_bar=1.2, c_bar=1.2,
    )
    assert float(result.rho[0].detach()) == pytest.approx(1.2)
    result.vs.sum().backward()
    assert float(target.grad[0]) == pytest.approx(0.0, abs=1e-9)


# --- Ratio scope: opponent fields never enter the math ----------------------


def test_vtrace_signatures_have_no_opponent_parameter() -> None:
    import inspect

    for function in (evaluate_vtrace_v1, evaluate_vtrace_v1_torch):
        parameters = set(inspect.signature(function).parameters)
        assert not any("opponent" in name for name in parameters)
    for cls in (VTraceRolloutStepV1, VTraceRolloutV1):
        import dataclasses

        field_names = {field.name for field in dataclasses.fields(cls)}
        assert not any("opponent" in name for name in field_names)
