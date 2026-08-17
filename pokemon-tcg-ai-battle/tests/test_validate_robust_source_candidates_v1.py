from __future__ import annotations

import pytest

from scripts.validate_robust_source_candidates_v1 import (
    ValidationError,
    _parse_candidate,
    _promotion_gate,
)


def _aggregate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "valid": True,
        "mean_source_score": 0.55,
        "min_reference_score": 0.25,
        "seat_safe": True,
    }
    value.update(overrides)
    return value


def test_parse_candidate_accepts_optional_screen_mean() -> None:
    candidate_id, path, screen_mean = _parse_candidate("source-a=runs/a,0.625")
    assert candidate_id == "source-a"
    assert path.name == "a"
    assert screen_mean == pytest.approx(0.625)


def test_parse_candidate_rejects_invalid_id() -> None:
    with pytest.raises(ValidationError):
        _parse_candidate("bad/id=runs/a,0.5")


def test_promotion_gate_requires_screen_and_independent_validation() -> None:
    assert _promotion_gate(screen_mean=0.51, validation=_aggregate())
    assert not _promotion_gate(screen_mean=0.50, validation=_aggregate())
    assert not _promotion_gate(screen_mean=0.7, validation=_aggregate(max_seat_gap=0.5, seat_safe=False))
    assert not _promotion_gate(screen_mean=0.7, validation=_aggregate(mean_source_score=0.5))
