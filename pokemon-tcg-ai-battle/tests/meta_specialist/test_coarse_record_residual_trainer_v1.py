"""TDD contracts for the research-only coarse complete-action trainer."""

from __future__ import annotations

import pytest
import torch


def _rows(prefix_count: int = 2):
    from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import CoarsePrefixLogitRowV1

    bucket = "a" * 64
    actions = ("b" * 64, "c" * 64)
    return tuple(
        CoarsePrefixLogitRowV1(
            episode_id="episode-a",
            record_id="record-a",
            prefix_index=index,
            bucket_id=bucket,
            action_keys=actions,
            base_logits=(0.0, 0.0),
            target_index=0,
            signed_weight=0.8,
        )
        for index in range(prefix_count)
    )


def test_record_loss_mass_is_prefix_count_invariant():
    from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import (
        normalize_complete_action_rows_v1,
    )

    short = normalize_complete_action_rows_v1(_rows(1), mode="record_normalized")
    long = normalize_complete_action_rows_v1(_rows(4), mode="record_normalized")
    assert short.record_total_abs == pytest.approx(long.record_total_abs)
    assert sum(abs(value) for value in short.weights) == pytest.approx(
        sum(abs(value) for value in long.weights)
    )


def test_episode_normalized_rows_have_unit_episode_mass():
    from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import (
        CoarsePrefixLogitRowV1,
        normalize_complete_action_rows_v1,
    )

    rows = _rows(4) + (
        CoarsePrefixLogitRowV1("episode-b", "record-b", 0, "a" * 64, ("b" * 64, "c" * 64), (0.0, 0.0), 1, -0.2),
    )
    result = normalize_complete_action_rows_v1(rows, mode="episode_normalized")
    assert result.by_episode_abs["episode-a"] == pytest.approx(1.0)
    assert result.by_episode_abs["episode-b"] == pytest.approx(1.0)


def test_table_trainer_updates_only_residual_and_keeps_bound():
    from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import (
        CoarseResidualTableV1,
        train_coarse_record_residual_v1,
    )

    table = CoarseResidualTableV1.from_rows(_rows(2), max_abs_residual=0.25)
    before = {name: value.detach().clone() for name, value in table.state_dict().items()}
    result = train_coarse_record_residual_v1(table, _rows(2), mode="record_normalized", max_updates=1)
    assert result.optimizer_updates == 1
    assert any(not torch.equal(before[name], value) for name, value in table.state_dict().items())
    assert float(table.bounded_residuals().abs().max()) <= 0.25 + 1e-6
    assert result.performance_evidence is False


def test_trainer_rejects_nonfinite_or_unknown_bucket_rows():
    from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import (
        CoarsePrefixLogitRowV1,
        CoarseResidualTableV1,
        CoarseRecordResidualTrainerError,
    )

    with pytest.raises(CoarseRecordResidualTrainerError, match="finite"):
        CoarsePrefixLogitRowV1("e", "r", 0, "a" * 64, ("b" * 64,), (float("nan"),), 0, 0.1)
    table = CoarseResidualTableV1.from_rows(_rows(), max_abs_residual=0.25)
    with pytest.raises(CoarseRecordResidualTrainerError, match="bucket"):
        table.validate_rows((CoarsePrefixLogitRowV1("e", "r", 0, "d" * 64, ("b" * 64, "c" * 64), (0.0, 0.0), 0, 0.1),))
