"""Fixed research-only episode-outcome weighting for V4 recurrent BC."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence


RESEARCH_ONLY_OUTCOME_WEIGHTED_V4 = "RESEARCH_ONLY_OUTCOME_WEIGHTED_V4"

_WEIGHTS_V4 = {1.0: 1.0, 0.0: 2.0 / 3.0, -1.0: 1.0 / 3.0}
_LABELS_V4 = {1.0: "win", 0.0: "draw", -1.0: "loss"}


def _target_v4(value_target: object) -> float:
    if type(value_target) is bool or type(value_target) not in {int, float}:
        raise ValueError("outcome value target must be a finite numeric value")
    target = float(value_target)
    if not math.isfinite(target) or target not in _WEIGHTS_V4:
        raise ValueError("outcome value target must be exactly -1, 0, or 1")
    return target


def outcome_quality_weight_v4(value_target: object) -> float:
    """Return the fixed max-normalized episode weight for one outcome."""
    return _WEIGHTS_V4[_target_v4(value_target)]


def outcome_weight_summary_v4(targets: Sequence[float]) -> dict[str, object]:
    """Return deterministic counts and the pre-registered fixed mapping."""
    counts = Counter(_LABELS_V4[_target_v4(value)] for value in targets)
    return {
        "targets": {label: int(counts.get(label, 0)) for label in ("win", "draw", "loss")},
        "weights": {"win": 1.0, "draw": 2.0 / 3.0, "loss": 1.0 / 3.0},
        "ratio_win_to_loss": 3.0,
    }


__all__ = [
    "RESEARCH_ONLY_OUTCOME_WEIGHTED_V4",
    "outcome_quality_weight_v4",
    "outcome_weight_summary_v4",
]
