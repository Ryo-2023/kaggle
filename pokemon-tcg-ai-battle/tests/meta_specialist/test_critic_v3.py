"""Tests for the seed-independent outcome-distribution critic."""

from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.critic_v3 import (
    OutcomeCriticV3,
    calibration_metrics_v3,
    episode_balanced_cross_entropy_v3,
)


def test_initial_critic_is_uniform_and_value_is_zero() -> None:
    critic = OutcomeCriticV3(hidden_dim=8, seed=3)
    features = torch.randn(4, 8)
    output = critic(features, provenance={"game_seed": 11})
    assert torch.allclose(output.probabilities, torch.full((4, 3), 1 / 3), atol=1e-6)
    assert torch.allclose(output.value, torch.zeros(4), atol=1e-6)
    assert torch.all((output.value >= -1) & (output.value <= 1))


def test_game_seed_provenance_is_not_a_critic_input() -> None:
    critic = OutcomeCriticV3(hidden_dim=8, seed=3)
    features = torch.randn(2, 8)
    first = critic(features, provenance={"game_seed": 1, "opponent_instance_id": "a"})
    second = critic(features, provenance={"game_seed": 999, "opponent_instance_id": "b"})
    assert torch.allclose(first.logits, second.logits)


def test_critic_checkpoint_round_trip_preserves_distribution() -> None:
    critic = OutcomeCriticV3(hidden_dim=8, seed=3)
    features = torch.randn(2, 8)
    critic.load_state_dict({key: value + 0.01 for key, value in critic.state_dict().items()})
    expected = critic(features).probabilities
    restored = OutcomeCriticV3(hidden_dim=8, seed=99)
    restored.load_state_dict(critic.state_dict())
    assert torch.allclose(expected, restored(features).probabilities)


def test_episode_balancing_prevents_long_games_dominating() -> None:
    first = torch.tensor([[0.0, 2.0, 0.0]])
    second = torch.tensor([[0.0, 0.0, 2.0]] * 3)
    labels = (torch.tensor([2]), torch.tensor([0, 0, 0]))
    loss = episode_balanced_cross_entropy_v3((first, second), labels)
    expected = (torch.nn.functional.cross_entropy(first, labels[0]) + torch.nn.functional.cross_entropy(second, labels[1])) / 2
    assert torch.allclose(loss, expected)


def test_calibration_metrics_include_uniform_baseline_and_bounded_value() -> None:
    probabilities = torch.tensor([[1 / 3, 1 / 3, 1 / 3], [0.1, 0.2, 0.7]])
    labels = torch.tensor([1, 2])
    metrics = calibration_metrics_v3(probabilities, labels)
    assert metrics["cross_entropy"] > 0
    assert 0 <= metrics["brier"] <= 2
    assert 0 <= metrics["ece"] <= 1
    assert -1 <= metrics["value_min"] <= metrics["value_max"] <= 1
    assert metrics["uniform_brier"] >= 0
