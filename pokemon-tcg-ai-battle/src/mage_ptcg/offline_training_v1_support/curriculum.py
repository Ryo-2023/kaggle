"""Curriculum training scheduler and stage manager.

Slices dataset into stages (easy, medium, hard, rare, OOD) using quality and uncertainty signals.
Ensures validation/test data is never targeted.
"""

from __future__ import annotations
import math
from typing import Any

def assign_curriculum_stage(
    record: dict[str, Any],
    student_entropy: float = 0.0,
    teacher_disagreement: float = 0.0
) -> str:
    """Classify a record into a curriculum stage based on uncertainty signals."""
    is_fallback = bool(record.get("fallback", False))
    is_conflict = bool(record.get("conflict", False))
    is_ood = bool(record.get("ood", False))

    if is_ood:
        return "OOD"
    if is_conflict:
        return "conflicting"
    if is_fallback:
        return "fallback"

    if student_entropy > 1.2 or teacher_disagreement > 0.8:
        return "hard"
    elif student_entropy > 0.5 or teacher_disagreement > 0.3:
        return "medium"
    else:
        return "easy"

def plan_curriculum_batches(
    records: list[dict[str, Any]],
    student_entropies: dict[str, float] = None,
    teacher_disagreements: dict[str, float] = None,
    hard_limit_ratio: float = 0.3
) -> dict[str, list[dict[str, Any]]]:
    """Slice records into curriculum stages, enforcing a constraint on hard cases ratio."""
    student_entropies = student_entropies or {}
    teacher_disagreements = teacher_disagreements or {}

    stages: dict[str, list[dict[str, Any]]] = {
        "easy": [], "medium": [], "hard": [], "rare": [], "OOD": [],
        "fallback": [], "conflicting": [], "quarantined": []
    }

    for r in records:
        if r.get("split") in ("val", "validation", "test"):
            continue

        dec_id = r.get("decision_id", "")
        entropy = student_entropies.get(dec_id, 0.0)
        disagree = teacher_disagreements.get(dec_id, 0.0)

        stage = assign_curriculum_stage(r, entropy, disagree)

        is_rare = bool(r.get("selection_type") == "rare_select" or r.get("context_type") == "rare_context")
        if is_rare and stage in ("easy", "medium"):
            stage = "rare"

        stages[stage].append(r)

    total_easy_med = len(stages["easy"]) + len(stages["medium"])
    max_hard = int(total_easy_med * hard_limit_ratio)

    if len(stages["hard"]) > max_hard:
        excess = stages["hard"][max_hard:]
        stages["hard"] = stages["hard"][:max_hard]
        stages["medium"].extend(excess)

    return stages
