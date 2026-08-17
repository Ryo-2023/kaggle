from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.learner_common_v1 import (
    advantage_diagnostics_v1,
    exact_policy_drift_v1,
    normalized_entropy_v1,
    vtrace_effective_kernel_v1,
)


def test_entropy_is_zero_for_single_legal_action() -> None:
    assert normalized_entropy_v1(torch.tensor([[4.0]]), torch.tensor([[True]])).item() == 0.0


def test_exact_policy_drift_reports_distribution_metrics() -> None:
    old = torch.tensor([[2.0, 0.0], [0.0, 2.0]])
    new = torch.tensor([[0.0, 2.0], [0.0, 2.0]])
    metrics = exact_policy_drift_v1(old, new, torch.ones_like(old, dtype=torch.bool))
    assert metrics["forward_kl"] > 0
    assert metrics["argmax_flip_rate"] == 0.5
    assert 0 <= metrics["total_variation"] <= 1


def test_vtrace_kernel_decays_and_reports_effective_horizon() -> None:
    result = vtrace_effective_kernel_v1(torch.full((10,), 0.5), gamma=1.0)
    assert result["weights"][0] == 0.5
    assert result["weights"][4] == 0.5**5
    assert result["max_depth_weight_ge_0_01"] == 6


def test_advantage_diagnostics_is_finite_for_constant_advantage() -> None:
    result = advantage_diagnostics_v1(torch.ones(5), torch.tensor([0, 0, 1, 1, 1]))
    assert result["raw_std"] == 0
    assert result["positive_fraction"] == 1
    assert result["outcome_correlation"] == 0
