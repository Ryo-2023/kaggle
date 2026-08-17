from __future__ import annotations

import torch

from mage_ptcg.meta_specialist.learner_awr_crr_v1 import awr_weights_v1, crr_weights_v1
from mage_ptcg.meta_specialist.learner_ppo_recurrent_v1 import ppo_recurrent_loss_v1
from mage_ptcg.meta_specialist.learner_vtrace_online_v1 import ConsumeOnceVTraceQueueV1, vtrace_targets_v1


def test_ppo_clips_ratio_and_reports_exact_kl() -> None:
    result = ppo_recurrent_loss_v1(
        new_log_probs=torch.log(torch.tensor([0.9, 0.1])),
        old_log_probs=torch.log(torch.tensor([0.5, 0.5])),
        advantages=torch.tensor([1.0, -1.0]),
        entropy=torch.tensor([0.4, 0.4]),
        reference_log_probs=torch.log(torch.tensor([0.6, 0.4])),
        clip_epsilon=0.1,
        reference_kl_coefficient=0.1,
        entropy_coefficient=0.001,
    )
    assert result.loss.ndim == 0
    assert result.exact_kl >= 0
    assert result.clip_fraction > 0


def test_consume_once_vtrace_queue_rejects_reuse() -> None:
    queue = ConsumeOnceVTraceQueueV1(max_actor_lag=1)
    queue.publish("episode-1", version=3, payload={"x": 1})
    item = queue.consume("episode-1", learner_version=4)
    assert item.payload == {"x": 1}
    try:
        queue.consume("episode-1", learner_version=4)
    except KeyError:
        pass
    else:
        raise AssertionError("consumed trajectory was reused")


def test_vtrace_targets_and_replay_weights_are_bounded() -> None:
    targets = vtrace_targets_v1(
        rewards=torch.tensor([1.0, 0.0]), values=torch.tensor([0.0, 0.0, 0.0]),
        behavior_log_probs=torch.log(torch.tensor([0.5, 0.5])),
        target_log_probs=torch.log(torch.tensor([0.5, 0.5])),
        discounts=torch.ones(2), rho_bar=2.0, c_bar=1.0,
    )
    assert torch.allclose(targets, torch.tensor([1.0, 0.0]))
    assert torch.all(awr_weights_v1(torch.tensor([-1.0, 0.0, 2.0]), temperature=1.0, max_weight=20) <= 20)
    assert torch.equal(crr_weights_v1(torch.tensor([-1.0, 0.0, 2.0])), torch.tensor([0.0, 0.0, 1.0]))
