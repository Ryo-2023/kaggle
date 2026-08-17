"""Portfolio-robust adversarial meta-source aggregation.

The first source-side CEM optimized only against P1.  This module keeps the
same terminal-WDL-only boundary while evaluating one generated source against
several fixed reference policies.  A source is ranked by the mean/worst
reference score so a single overfit matchup cannot dominate the source pool.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from mage_ptcg.opponent_ingest.adversarial_source_cem_v1 import (
    AdversarialSourceError,
    aggregate_source_rows_v1,
)


SCHEMA_V1 = "meta-specialist-robust-adversarial-source-cem-v1"


class RobustAdversarialSourceError(ValueError):
    """Raised when portfolio source evidence is malformed."""


def aggregate_portfolio_source_rows_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_policy_id: str,
    reference_ids: Sequence[str],
    seat_gap_limit: float = 0.25,
) -> dict[str, object]:
    """Aggregate one source candidate over an exact fixed reference portfolio.

    Only terminal ``outcome``, ``opponent_id``, ``policy_id`` and ``seat`` are
    consumed.  The robust objective is ``(mean + worst) / 2 - fault_rate``;
    ``valid`` additionally requires every reference to have both seats, no
    seat collapse, no faults, and a bounded seat gap.
    """

    if not isinstance(candidate_policy_id, str) or not candidate_policy_id:
        raise RobustAdversarialSourceError("candidate_policy_id must be non-empty")
    if not isinstance(reference_ids, Sequence) or isinstance(reference_ids, (str, bytes)) or not reference_ids:
        raise RobustAdversarialSourceError("reference_ids must be a non-empty sequence")
    normalized_refs = tuple(str(item) for item in reference_ids)
    if any(not item for item in normalized_refs):
        raise RobustAdversarialSourceError("reference_ids must contain non-empty strings")
    if len(set(normalized_refs)) != len(normalized_refs):
        raise RobustAdversarialSourceError("duplicate reference id")
    if type(seat_gap_limit) not in (int, float) or isinstance(seat_gap_limit, bool):
        raise RobustAdversarialSourceError("seat_gap_limit must be finite")
    if not math.isfinite(float(seat_gap_limit)) or not 0.0 <= float(seat_gap_limit) <= 1.0:
        raise RobustAdversarialSourceError("seat_gap_limit must be in [0,1]")

    reference_results: dict[str, dict[str, object]] = {}
    for reference_id in normalized_refs:
        try:
            aggregate = aggregate_source_rows_v1(
                rows,
                candidate_policy_id=candidate_policy_id,
                opponent_id=reference_id,
                seat_gap_limit=seat_gap_limit,
            )
        except AdversarialSourceError as exc:
            if "no matching rows" in str(exc):
                raise RobustAdversarialSourceError(str(exc)) from exc
            raise RobustAdversarialSourceError(str(exc)) from exc
        reference_results[reference_id] = aggregate

    requested = sum(int(item["requested_games"]) for item in reference_results.values())
    faults = sum(int(item["faults"]) for item in reference_results.values())
    mean_score = sum(float(item["source_score"]) for item in reference_results.values()) / len(reference_results)
    worst_score = min(float(item["source_score"]) for item in reference_results.values())
    seat_gaps = [
        float(item["seat_gap"])
        for item in reference_results.values()
        if item.get("seat_gap") is not None
    ]
    max_seat_gap = max(seat_gaps) if seat_gaps else None
    robust_objective = 0.5 * (mean_score + worst_score) - (faults / requested if requested else 1.0)
    valid = all(
        bool(item["valid"]) and bool(item["seat_safe"])
        for item in reference_results.values()
    )
    return {
        "schema_version": SCHEMA_V1,
        "requested_games": requested,
        "faults": faults,
        "fault_rate": faults / requested if requested else 1.0,
        "reference_count": len(reference_results),
        "reference_ids": list(normalized_refs),
        "reference_results": reference_results,
        "mean_source_score": mean_score,
        "min_reference_score": worst_score,
        "max_seat_gap": max_seat_gap,
        "seat_gap_limit": float(seat_gap_limit),
        "seat_safe": max_seat_gap is not None and max_seat_gap <= float(seat_gap_limit),
        "robust_objective": robust_objective,
        "objective": robust_objective,
        "valid": valid,
        "action_trace_used": False,
        "private_fields_used": False,
        "teacher_labels_used": False,
    }


__all__ = [
    "SCHEMA_V1",
    "RobustAdversarialSourceError",
    "aggregate_portfolio_source_rows_v1",
]
