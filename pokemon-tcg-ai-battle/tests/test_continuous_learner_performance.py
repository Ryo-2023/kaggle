from __future__ import annotations

from dataclasses import replace
import random
from pathlib import Path
from typing import Iterable

import pytest

from mage_ptcg.continuous_league.batching import (
    PackedReplayBatcher,
    learner_batch,
)
from mage_ptcg.continuous_league.cli import _dataclass_config, _load_mapping
from mage_ptcg.continuous_league.contracts import LeagueContractError
from mage_ptcg.continuous_league.learner_service import (
    ContinuousLearner,
    ContinuousLearnerConfig,
)
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)
from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
from mage_ptcg.policy_learning.r2d3.replay import _FenwickEpisodeSampler
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch


torch = pytest.importorskip("torch")


def test_wsl_fast_profile_avoids_gpu_resident_replay() -> None:
    """Windows host commit must not include the full GPU-resident replay."""

    configuration = _load_mapping(
        Path(__file__).parents[1]
        / "configs/continuous_league/gru256_cuda_fast.yaml"
    )
    service = _dataclass_config(
        ContinuousLearnerConfig, configuration["service"]
    )
    assert service.prepack_replay is True
    assert service.pin_memory is True
    assert service.resident_replay is False


def test_cuda_preflight_rejects_before_loading_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """壊れたWSL CUDA bridgeで巨大Replayを展開してはならない。"""

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(LeagueContractError, match="requested CUDA"):
        ContinuousLearner(
            replay_manifest_path=tmp_path / "missing-manifest.json",
            population_epoch_id="population",
            output_root=tmp_path / "output",
            deck=[1] * 60,
            service_config=ContinuousLearnerConfig(device="cuda:0"),
        )


def _transition(
    index: int,
    *,
    actions: int,
    terminal: bool = False,
    demonstration: bool = False,
) -> R2D3Transition:
    return R2D3Transition(
        public_state=tuple((index + offset) / 100.0 for offset in range(8)),
        legal_actions=tuple(
            tuple((action + offset) / 50.0 for offset in range(4))
            for action in range(actions)
        ),
        selected_action=index % actions,
        reward=1.0 if terminal else index / 10.0,
        discount=0.0 if terminal else 0.99,
        terminal=terminal,
        behavior_policy_version="behavior",
        behavior_source="test",
        opponent_policy_hash=f"opponent-{index % 3}",
        opponent_deck_hash="deck",
        opponent_source_lineage="lineage",
        opponent_family=f"family-{index % 2}",
        own_deck_hash="own",
        demonstration=demonstration,
    )


def _replay() -> PrioritizedSequenceReplay:
    replay = PrioritizedSequenceReplay(capacity=8)
    first = tuple(
        _transition(
            index,
            actions=2 + index % 3,
            terminal=index == 5,
            demonstration=index < 3,
        )
        for index in range(6)
    )
    second = tuple(
        replace(
            _transition(index + 10, actions=1 + index, terminal=index == 3),
            reward=0.5 if index == 3 else 0.0,
        )
        for index in range(4)
    )
    replay.add(
        SequenceBatch(
            burn_in=first[:2],
            learner=first[2:5],
            lookahead=first[5:],
            priority=1.0,
            sequence_id="first",
            episode_id="episode-first",
        )
    )
    replay.add(
        SequenceBatch(
            burn_in=(),
            learner=second,
            priority=2.0,
            sequence_id="second",
            episode_id="episode-second",
        )
    )
    return replay


def test_prepacked_batch_is_tensor_identical_to_reference() -> None:
    replay = _replay()
    sample = replay.sample(
        2,
        beta=0.4,
        demonstration_ratio=0.5,
        seed=17,
        episode_first=True,
    )
    expected = learner_batch(
        sample,
        torch.device("cpu"),
        n_step=3,
        opponent_classes=7,
        deck_family_classes=5,
        action_type_classes=4,
    )
    batcher = PackedReplayBatcher(
        replay,
        n_step=3,
        opponent_classes=7,
        deck_family_classes=5,
        action_type_classes=4,
    )
    actual = batcher.learner_batch(sample, torch.device("cpu"))
    assert actual.keys() == expected.keys()
    for name in actual:
        if expected[name] is None:
            assert actual[name] is None
        else:
            assert torch.equal(actual[name], expected[name]), name


def test_resident_batch_is_tensor_identical_to_reference() -> None:
    replay = _replay()
    sample = replay.sample(
        2,
        beta=0.4,
        demonstration_ratio=0.5,
        seed=23,
        episode_first=True,
    )
    expected = learner_batch(
        sample,
        torch.device("cpu"),
        n_step=3,
        opponent_classes=7,
        deck_family_classes=5,
        action_type_classes=4,
    )
    batcher = PackedReplayBatcher(
        replay,
        n_step=3,
        opponent_classes=7,
        deck_family_classes=5,
        action_type_classes=4,
    )
    device = torch.device("cpu")
    batcher.materialize_resident(device, chunk_size=1)
    actual = batcher.resident_batch(sample, device)
    assert actual.keys() == expected.keys()
    for name in actual:
        if expected[name] is None:
            assert actual[name] is None
        else:
            assert torch.equal(actual[name], expected[name]), name


@pytest.mark.parametrize("core", ["gru", "lru"])
def test_vectorized_burn_in_matches_masked_step_reference(core: str) -> None:
    torch.manual_seed(41)
    model = RecurrentDistributionalQ(
        R2D3ModelConfig(
            state_size=8,
            action_size=4,
            hidden_size=8,
            atoms=5,
            recurrent_core=core,
        )
    )
    states = torch.rand(3, 5, 8)
    mask = torch.tensor(
        [
            [False, False, False, False, False],
            [True, True, False, False, False],
            [True, True, True, True, True],
        ]
    )
    initial = torch.rand(1, 3, 8)
    hidden = initial.clone()
    encoded = model.state(states)
    for offset in range(states.shape[1]):
        _output, candidate = model.core(
            encoded[:, offset : offset + 1], hidden
        )
        hidden = torch.where(
            mask[:, offset].view(1, states.shape[0], 1),
            candidate,
            hidden,
        )
    actual = model.burn_in(states, mask, initial)
    assert torch.allclose(actual, hidden, atol=1e-6, rtol=1e-5)


def test_execution_only_prepacking_preserves_legacy_training_identity() -> None:
    baseline = ContinuousLearnerConfig()
    prepacked = ContinuousLearnerConfig(prepack_replay=True, pin_memory=False)
    assert prepacked.training_identity_payload() == (
        baseline.training_identity_payload()
    )
    cuda_baseline = ContinuousLearnerConfig(device="cuda:0")
    resident = replace(
        cuda_baseline,
        prepack_replay=True,
        resident_replay=True,
        pin_memory=True,
    )
    assert resident.training_identity_payload() == (
        cuda_baseline.training_identity_payload()
    )
    bf16 = ContinuousLearnerConfig(
        device="cuda:0",
        prepack_replay=True,
        pin_memory=True,
        mixed_precision="bf16",
        fused_optimizer=True,
        matmul_precision="high",
    )
    payload = bf16.training_identity_payload()
    assert payload["mixed_precision"] == "bf16"
    assert payload["fused_optimizer"] is True
    assert payload["matmul_precision"] == "high"


def _reference_sample(
    replay: PrioritizedSequenceReplay,
    batch_size: int,
    *,
    beta: float,
    demonstration_ratio: float,
    seed: int,
) -> tuple[tuple[int, ...], tuple[float, ...], int]:
    rng = random.Random(seed)
    weights = replay._alpha_weights()
    total = sum(weights)
    probabilities = [value / total for value in weights]
    demos, demonstration_lookup = replay._demonstration_table()
    requested = min(batch_size, round(batch_size * demonstration_ratio))

    def draw(
        candidates: Iterable[int], count: int, cache_key: str
    ) -> list[int]:
        groups = replay._episode_groups(candidates, cache_key=cache_key)
        group_weights = [
            sum(probabilities[index] for index in group) for group in groups
        ]
        picker = _FenwickEpisodeSampler(group_weights)
        selected: list[int] = []
        while len(selected) < count:
            if picker.remaining <= 0:
                picker = _FenwickEpisodeSampler(group_weights)
            group = groups[picker.pop(rng)]
            selected.append(
                rng.choices(
                    group,
                    weights=[probabilities[index] for index in group],
                    k=1,
                )[0]
            )
        return selected

    selected = draw(demos, requested, "demonstrations") if requested else []
    selected.extend(draw(range(len(replay)), batch_size - len(selected), "all"))
    correction = [
        (len(replay) * probabilities[index]) ** (-beta)
        for index in selected
    ]
    maximum = max(correction)
    return (
        tuple(selected),
        tuple(value / maximum for value in correction),
        len([index for index in selected if index in demonstration_lookup]),
    )


def test_numpy_episode_sampling_matches_python_oracle_across_updates() -> None:
    replay = PrioritizedSequenceReplay(capacity=256)
    for episode in range(64):
        for window in range(3):
            transition = _transition(
                episode + window,
                actions=1 + (episode + window) % 5,
                terminal=True,
                demonstration=episode % 11 == 0,
            )
            replay.add(
                SequenceBatch(
                    (),
                    (transition,),
                    priority=0.25 + (episode * 3 + window) / 100.0,
                    sequence_id=f"sequence-{episode}-{window}",
                    episode_id=f"episode-{episode}",
                )
            )
    optimized = replay.fork()
    reference = replay.fork()
    for step in range(25):
        beta = 0.4 + step / 100.0
        expected_indices, expected_weights, expected_demos = _reference_sample(
            reference,
            32,
            beta=beta,
            demonstration_ratio=1.0 / 32.0,
            seed=71_000 + step,
        )
        actual = optimized.sample(
            32,
            beta=beta,
            demonstration_ratio=1.0 / 32.0,
            seed=71_000 + step,
            episode_first=True,
        )
        assert actual.indices == expected_indices
        assert actual.weights == expected_weights
        assert actual.demonstrations == expected_demos
        priorities = [
            0.1 + ((index + step * 7) % 101) / 50.0
            for index in actual.indices
        ]
        optimized.update_priorities(actual.indices, priorities)
        reference.update_priorities(expected_indices, priorities)
