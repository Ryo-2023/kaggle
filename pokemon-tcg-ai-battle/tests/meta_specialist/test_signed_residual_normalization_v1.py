"""Research-only tests for complete-action/episode normalization contracts."""

from __future__ import annotations

import pytest


def _rows(*, long: bool = False):
    from mage_ptcg.meta_specialist.signed_residual_normalization_v1 import SignedPrefixWeightV1

    return tuple(
        [
            SignedPrefixWeightV1("episode-a", "record-a", index, 0.8)
            for index in range(4 if long else 1)
        ]
        + [SignedPrefixWeightV1("episode-b", "record-b", 0, -0.2)]
    )


def test_record_normalization_is_invariant_to_prefix_count():
    from mage_ptcg.meta_specialist.signed_residual_normalization_v1 import (
        normalize_signed_prefix_weights_v1,
    )

    short = normalize_signed_prefix_weights_v1(_rows(long=False), mode="record_normalized")
    long = normalize_signed_prefix_weights_v1(_rows(long=True), mode="record_normalized")
    assert short.record_total_abs == pytest.approx(long.record_total_abs)
    assert short.by_record_abs["record-a"] == pytest.approx(long.by_record_abs["record-a"])
    assert sum(short.weights) == pytest.approx(sum(long.weights))


def test_episode_normalization_equalizes_episode_total_abs_mass():
    from mage_ptcg.meta_specialist.signed_residual_normalization_v1 import (
        normalize_signed_prefix_weights_v1,
    )

    result = normalize_signed_prefix_weights_v1(_rows(long=True), mode="episode_normalized")
    assert result.by_episode_abs["episode-a"] == pytest.approx(1.0)
    assert result.by_episode_abs["episode-b"] == pytest.approx(1.0)
    assert result.episode_total_abs == pytest.approx(2.0)


def test_normalization_rejects_noncontiguous_or_unknown_modes():
    from mage_ptcg.meta_specialist.signed_residual_normalization_v1 import (
        SignedPrefixWeightV1,
        SignedResidualNormalizationError,
        normalize_signed_prefix_weights_v1,
    )

    with pytest.raises(SignedResidualNormalizationError, match="mode"):
        normalize_signed_prefix_weights_v1(_rows(), mode="prefix")
    with pytest.raises(SignedResidualNormalizationError, match="contiguous"):
        normalize_signed_prefix_weights_v1(
            (SignedPrefixWeightV1("episode-a", "record-a", 1, 1.0),),
            mode="record_normalized",
        )
