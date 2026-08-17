"""Targeted DAgger query selection and provenance-preserving aggregation."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


class DAggerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DAggerQuery:
    decision_id: str
    episode_id: str
    priority: float
    reasons: tuple[str, ...]


def select_queries(records: Iterable[dict[str, Any]], *, budget: int) -> list[DAggerQuery]:
    """Select model-visited states without reading private opponent fields."""
    if budget < 1:
        raise DAggerError("DAgger query budget must be positive")
    queries: list[DAggerQuery] = []
    for record in records:
        decision_id, episode_id = record.get("decision_id"), record.get("episode_id")
        if not isinstance(decision_id, str) or not isinstance(episode_id, str):
            continue
        confidence = record.get("policy_confidence")
        value_error = record.get("value_error")
        disagreement = bool(record.get("teacher_disagreement"))
        unknown_actions = bool(record.get("unknown_action_composition"))
        before_loss = bool(record.get("before_loss"))
        if confidence is not None and not isinstance(confidence, (int, float)): continue
        if value_error is not None and not isinstance(value_error, (int, float)): continue
        reasons = []
        score = 0.0
        if confidence is not None:
            score += max(0.0, min(1.0, 1.0 - float(confidence)))
            if confidence < .5: reasons.append("LOW_CONFIDENCE")
        if value_error is not None:
            score += min(1.0, abs(float(value_error)))
            if abs(float(value_error)) > .5: reasons.append("VALUE_ERROR")
        if disagreement: score += 1.0; reasons.append("TEACHER_DISAGREEMENT")
        if unknown_actions: score += .5; reasons.append("UNKNOWN_ACTION_COMPOSITION")
        if before_loss: score += .5; reasons.append("PRE_LOSS")
        if reasons:
            queries.append(DAggerQuery(decision_id, episode_id, score, tuple(reasons)))
    return sorted(queries, key=lambda item: (-item.priority, item.episode_id, item.decision_id))[:budget]


def aggregate_records(*, base: Path, relabeled: Path, output: Path) -> dict[str, int]:
    """Append relabels by decision id, rejecting conflicting unlabeled data."""
    if output.exists():
        raise DAggerError("DAgger aggregate destination already exists")
    def read(path: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    base_rows, new_rows = read(base), read(relabeled)
    replacements = {str(row.get("state_fingerprint")): row for row in new_rows if row.get("state_fingerprint")}
    if len(replacements) != len(new_rows):
        raise DAggerError("every relabeled record needs a state fingerprint")
    merged = [replacements.pop(str(row.get("state_fingerprint")), row) for row in base_rows]
    merged.extend(replacements[key] for key in sorted(replacements))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged), encoding="utf-8")
    return {"base_records": len(base_rows), "relabeled_records": len(new_rows), "output_records": len(merged)}
