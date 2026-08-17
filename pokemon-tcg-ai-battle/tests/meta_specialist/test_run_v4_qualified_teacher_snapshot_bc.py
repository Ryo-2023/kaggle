from __future__ import annotations

import pytest

from scripts.run_v4_qualified_teacher_snapshot_bc import _record_outcome_weight_v4


def test_record_outcome_weight_is_episode_constant() -> None:
    record = {"teacher": {"value_target": -1.0}}
    assert _record_outcome_weight_v4(record, outcome_weighted=True) == pytest.approx(1.0 / 3.0)
    assert _record_outcome_weight_v4(record, outcome_weighted=False) == 1.0


def test_record_outcome_weight_rejects_missing_teacher_target() -> None:
    with pytest.raises(ValueError, match="value_target"):
        _record_outcome_weight_v4({"teacher": {}}, outcome_weighted=True)
