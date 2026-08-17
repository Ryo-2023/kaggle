from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.outcome_weighted_v4 import (
    outcome_quality_weight_v4,
    outcome_weight_summary_v4,
)


def test_outcome_quality_weight_is_max_normalized() -> None:
    assert outcome_quality_weight_v4(1.0) == 1.0
    assert outcome_quality_weight_v4(0.0) == 2.0 / 3.0
    assert outcome_quality_weight_v4(-1.0) == 1.0 / 3.0


def test_outcome_quality_weight_rejects_nonfinite_or_unknown_targets() -> None:
    with pytest.raises(ValueError):
        outcome_quality_weight_v4("win")
    with pytest.raises(ValueError):
        outcome_quality_weight_v4(float("nan"))


def test_outcome_summary_is_deterministic() -> None:
    assert outcome_weight_summary_v4([1.0, -1.0, 0.0]) == {
        "targets": {"win": 1, "draw": 1, "loss": 1},
        "weights": {"win": 1.0, "draw": 2.0 / 3.0, "loss": 1.0 / 3.0},
        "ratio_win_to_loss": 3.0,
    }
