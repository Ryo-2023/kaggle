"""Label consensus engine.

Aggregates predictions from multiple teachers using majority vote, confidence/reliability weights.
Provides Stable ActionKey tie-breaks and provenance tracking.
"""

from __future__ import annotations
from typing import Any

def compute_label_consensus(
    decision_id: str,
    predictions: list[dict[str, Any]],
    teacher_weights: dict[str, float] = None,
    method: str = "majority"
) -> dict[str, Any]:
    """Compute consensus label from multiple teacher predictions for a single decision."""
    teacher_weights = teacher_weights or {}

    valid_preds = []
    for p in predictions:
        if p.get("failed", False):
            continue
        if not p.get("chosen_action"):
            continue
        valid_preds.append(p)

    if not valid_preds:
        return {
            "decision_id": decision_id,
            "consensus_action": None,
            "confidence": 0.0,
            "provenance": [],
            "status": "ABSTAIN"
        }

    action_scores: dict[str, float] = {}
    provenance = []

    for p in valid_preds:
        teacher_id = p.get("teacher_id", "unknown")
        action = str(p["chosen_action"])
        conf = float(p.get("confidence", 1.0))
        weight = float(teacher_weights.get(teacher_id, 1.0))

        provenance.append({
            "teacher_id": teacher_id,
            "chosen_action": action,
            "confidence": conf
        })

        if method == "majority":
            action_scores[action] = action_scores.get(action, 0.0) + 1.0
        elif method == "confidence_weighted":
            action_scores[action] = action_scores.get(action, 0.0) + conf
        elif method == "reliability_weighted":
            action_scores[action] = action_scores.get(action, 0.0) + conf * weight
        else:
            action_scores[action] = action_scores.get(action, 0.0) + 1.0

    max_score = max(action_scores.values())
    candidates = [act for act, score in action_scores.items() if score == max_score]

    # Deterministic lexicographical tie-break (Stable ActionKey)
    candidates.sort()
    consensus_action = candidates[0]

    total_score = sum(action_scores.values())
    consensus_confidence = max_score / total_score if total_score > 0 else 0.0

    status = "SUCCESS"
    if len(action_scores) > 1 and max_score / total_score < 0.6:
        status = "QUARANTINE"

    return {
        "decision_id": decision_id,
        "consensus_action": consensus_action,
        "confidence": consensus_confidence,
        "provenance": provenance,
        "status": status
    }
