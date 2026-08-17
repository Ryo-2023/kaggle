"""Strict paired comparison of P1/P0 public decision telemetry.

The P1 and P0 collectors use the same game strata, but their trajectories can
diverge after the first different action.  This module therefore compares only
the common public-state prefix of each ``(game_id, seat)`` sequence.  It emits
diagnostic operation-pair statistics; it never treats an observed terminal WDL
as a counterfactual label and never authorizes a performance screen by itself.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_p1_public_hypothesis_v1 import (
    TELEMETRY_SCHEMA,
    _reject_forbidden_keys,
    _selected_operation,
    bucket_public_state_v1,
)


PAIRED_TELEMETRY_SCHEMA = "meta-specialist-cg-p1-paired-telemetry-v1"
_OUTCOMES = {"win", "loss", "draw"}


class PairedTelemetryError(ValueError):
    """Raised when either telemetry source is outside the public contract."""


@dataclass(frozen=True)
class PairedPublicDecisionV1:
    game_id: str
    seat: int
    ordinal: int
    p1_operation: str
    p0_operation: str
    p1_outcome: str
    p0_outcome: str
    public_state_key: tuple[tuple[str, object], ...]

    @property
    def operation_changed(self) -> bool:
        return self.p1_operation != self.p0_operation


def _validate_row(row: Mapping[str, object]) -> None:
    if row.get("schema_version") != TELEMETRY_SCHEMA:
        raise PairedTelemetryError("telemetry schema mismatch")
    if row.get("record_type") != "decision":
        raise PairedTelemetryError("paired input must contain decision records only")
    if not isinstance(row.get("game_id"), str) or not isinstance(row.get("seat"), int):
        raise PairedTelemetryError("decision requires game_id and integer seat")
    try:
        _reject_forbidden_keys(row)
    except ValueError as exc:
        raise PairedTelemetryError(str(exc)) from exc


def _group_rows(rows: Sequence[Mapping[str, object]]) -> dict[tuple[str, int], list[Mapping[str, object]]]:
    grouped: dict[tuple[str, int], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            raise PairedTelemetryError("telemetry rows must be mappings")
        _validate_row(row)
        grouped[(str(row["game_id"]), int(row["seat"]))].append(row)
    for sequence in grouped.values():
        sequence.sort(
            key=lambda item: (
                int(item.get("decision_index", 0)),
                int(item.get("step", 0)),
                int(item.get("turn", 0)),
                int(item.get("turn_action_count", 0)),
            )
        )
    return grouped


def _public_state_key(row: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
    bucket = bucket_public_state_v1(row)
    return tuple(sorted((key, value) for key, value in bucket.items() if key != "operation"))


def _score(outcome: str) -> float:
    if outcome == "win":
        return 1.0
    if outcome == "draw":
        return 0.5
    return 0.0


def pair_public_decisions_v1(
    p1_rows: Sequence[Mapping[str, object]],
    p0_rows: Sequence[Mapping[str, object]],
    *,
    p1_outcomes: Mapping[str, str],
    p0_outcomes: Mapping[str, str],
) -> list[PairedPublicDecisionV1]:
    """Pair only the common public-state prefix for each game and seat."""

    for outcomes in (p1_outcomes, p0_outcomes):
        if any(value not in _OUTCOMES for value in outcomes.values()):
            raise PairedTelemetryError("terminal outcomes must be win/loss/draw")
    grouped_p1 = _group_rows(p1_rows)
    grouped_p0 = _group_rows(p0_rows)
    pairs: list[PairedPublicDecisionV1] = []
    for key in sorted(set(grouped_p1) & set(grouped_p0)):
        p1_sequence = grouped_p1[key]
        p0_sequence = grouped_p0[key]
        game_id, seat = key
        if game_id not in p1_outcomes or game_id not in p0_outcomes:
            raise PairedTelemetryError(f"missing terminal WDL: {game_id}")
        for ordinal, (p1_row, p0_row) in enumerate(zip(p1_sequence, p0_sequence)):
            p1_state = _public_state_key(p1_row)
            p0_state = _public_state_key(p0_row)
            if p1_state != p0_state:
                # Once a public state differs, later records are not a paired
                # counterfactual and are intentionally excluded.
                break
            pairs.append(
                PairedPublicDecisionV1(
                    game_id=game_id,
                    seat=seat,
                    ordinal=ordinal,
                    p1_operation=_selected_operation(p1_row),
                    p0_operation=_selected_operation(p0_row),
                    p1_outcome=str(p1_outcomes[game_id]),
                    p0_outcome=str(p0_outcomes[game_id]),
                    public_state_key=p1_state,
                )
            )
    return pairs


def analyze_paired_public_telemetry_v1(
    pairs: Sequence[PairedPublicDecisionV1],
    *,
    min_support: int = 8,
    min_negative_delta: float = 0.15,
    max_candidates: int = 3,
) -> dict[str, object]:
    """Summarize paired operation changes and fail closed by default."""

    if min_support < 2 or min_negative_delta <= 0 or max_candidates < 1:
        raise ValueError("invalid paired analyzer bounds")
    changed = [pair for pair in pairs if pair.operation_changed]
    by_operation: dict[tuple[str, str], list[PairedPublicDecisionV1]] = defaultdict(list)
    for pair in changed:
        by_operation[(pair.p1_operation, pair.p0_operation)].append(pair)

    proposals: list[dict[str, object]] = []
    supported_pairs = 0
    mixed_sign_pairs = 0
    operation_stats: list[dict[str, object]] = []
    for (p1_operation, p0_operation), rows in sorted(by_operation.items()):
        p1_score = sum(_score(row.p1_outcome) for row in rows)
        p0_score = sum(_score(row.p0_outcome) for row in rows)
        delta = (p1_score - p0_score) / len(rows)
        p1_better = sum(_score(row.p1_outcome) > _score(row.p0_outcome) for row in rows)
        p0_better = sum(_score(row.p0_outcome) > _score(row.p1_outcome) for row in rows)
        mixed = p1_better > 0 and p0_better > 0
        if len(rows) >= min_support:
            supported_pairs += 1
        if mixed:
            mixed_sign_pairs += 1
        operation_stats.append(
            {
                "p1_operation": p1_operation,
                "p0_operation": p0_operation,
                "support": len(rows),
                "p1_score": p1_score,
                "p0_score": p0_score,
                "paired_delta": delta,
                "p1_better": p1_better,
                "p0_better": p0_better,
                "mixed_sign": mixed,
            }
        )
        if len(rows) >= min_support and mixed and delta <= -min_negative_delta:
            proposals.append(
                {
                    "candidate_id": f"cg-p1-paired-{p1_operation.lower()}-to-{p0_operation.lower()}-v1",
                    "hypothesis": f"prefer {p0_operation} over {p1_operation} in the shared public prefix",
                    "reference_operation": p0_operation,
                    "observed_operation": p1_operation,
                    "support": len(rows),
                    "paired_delta": delta,
                    "diagnostic_only": True,
                    "public_only": True,
                    "kill_condition": "no positive paired delta at weighted48/common24 or unsupported public prefix",
                }
            )
    proposals.sort(key=lambda item: (float(item["paired_delta"]), -int(item["support"])))
    proposals = proposals[:max_candidates]
    reasons: list[str] = []
    if len(changed) < min_support * 2:
        reasons.append("insufficient_action_differences")
    if supported_pairs < 2:
        reasons.append("insufficient_operation_pair_support")
    if mixed_sign_pairs < 2:
        reasons.append("insufficient_mixed_sign_paired_outcomes")
    if not proposals:
        reasons.append("no_bounded_paired_hypothesis")
    ready = bool(proposals and supported_pairs >= 2 and mixed_sign_pairs >= 2)
    return {
        "schema_version": PAIRED_TELEMETRY_SCHEMA,
        "paired_rows": len(pairs),
        "action_differences": len(changed),
        "operation_pairs": len(by_operation),
        "supported_operation_pairs": supported_pairs,
        "mixed_sign_operation_pairs": mixed_sign_pairs,
        "operation_stats": operation_stats,
        "candidates": proposals,
        "reasons": reasons,
        "ready_for_candidate_screen": ready,
        "diagnostic_only": True,
        "public_only": True,
        "authority": {"training": False, "promotion": False, "submission": False, "teacher": False, "longrun": False},
    }


__all__ = [
    "PAIRED_TELEMETRY_SCHEMA",
    "PairedPublicDecisionV1",
    "PairedTelemetryError",
    "analyze_paired_public_telemetry_v1",
    "pair_public_decisions_v1",
]
