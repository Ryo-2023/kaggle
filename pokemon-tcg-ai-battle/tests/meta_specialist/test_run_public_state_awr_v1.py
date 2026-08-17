from __future__ import annotations

import pytest

from scripts.run_public_state_awr_v1 import (
    AWRTargetError,
    aggregate_record_advantages_v1,
    build_cross_fitted_advantage_table_v1,
    awr_weight_from_advantage_v1,
)


def _digest(hex_digit: str) -> str:
    return hex_digit * 64


def test_cross_fitted_baseline_never_uses_same_fold() -> None:
    # The first two episodes share a bucket but are deliberately in different
    # folds; the returned baseline must come only from the other fold.
    returns = {
        _digest("0"): 1.0,
        _digest("1"): -1.0,
        _digest("2"): 1.0,
        _digest("3"): -1.0,
    }
    buckets = {episode: ("bucket-x",) for episode in returns}
    table = build_cross_fitted_advantage_table_v1(
        returns, buckets, fold_count=2, advantage_clip=1.0,
    )
    assert set(table) == set(returns)
    for episode, rows in table.items():
        assert rows["bucket-x"]["baseline_source"] in {
            "bucket_external", "external_global_fallback",
        }
        assert rows["bucket-x"]["baseline_episode_ids"]
        assert episode not in rows["bucket-x"]["baseline_episode_ids"]


def test_awr_weight_is_bounded_and_filtered_is_explicit() -> None:
    positive = awr_weight_from_advantage_v1(0.5, temperature=0.25, max_weight=4.0)
    negative = awr_weight_from_advantage_v1(-0.5, temperature=0.25, max_weight=4.0)
    assert positive.raw_weight > 1.0
    assert 0.0 < positive.normalized_quality <= 1.0
    assert positive.supervision_weight == 1.0
    assert negative.supervision_weight == 1.0
    filtered = awr_weight_from_advantage_v1(
        -0.5, temperature=0.25, max_weight=4.0, filtered=True,
    )
    assert filtered.supervision_weight == 0.0
    assert filtered.normalized_quality > 0.0


def test_record_advantage_aggregation_is_prefix_count_invariant() -> None:
    episode = _digest("a")
    record = _digest("b")
    one = aggregate_record_advantages_v1([(episode, record, 0.25)])
    many = aggregate_record_advantages_v1([
        (episode, record, 0.25), (episode, record, 0.50),
        (episode, record, 0.00),
    ])
    assert one[(episode, record)] == pytest.approx(0.25)
    assert many[(episode, record)] == pytest.approx(0.25)


def test_cross_fit_rejects_same_fold_only_bucket() -> None:
    returns = {_digest("0"): 1.0, _digest("2"): -1.0, _digest("4"): 1.0}
    buckets = {episode: ("bucket-x",) for episode in returns}
    with pytest.raises(AWRTargetError, match="external baseline"):
        build_cross_fitted_advantage_table_v1(
            returns, buckets, fold_count=2, advantage_clip=1.0,
        )
