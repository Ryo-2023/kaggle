"""Teacher agent reliability analysis.

Evaluates teacher agent accuracy, agreement, calibration, and fallback metrics.
"""

from __future__ import annotations
from typing import Any

def analyze_teacher_reliability(
    teacher_outputs: list[dict[str, Any]],
    true_labels: dict[str, str] = None
) -> dict[str, Any]:
    """Analyze teacher reliability indicators and produce a consensus status."""
    true_labels = true_labels or {}

    if not teacher_outputs:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "issues": ["No teacher outputs provided for analysis"]
        }

    total = len(teacher_outputs)
    failures = 0
    fallbacks = 0

    teacher_counts: dict[str, int] = {}
    teacher_correct: dict[str, int] = {}

    decision_predictions: dict[str, dict[str, str]] = {}

    for out in teacher_outputs:
        t_id = out.get("teacher_id", "unknown_teacher")
        dec_id = out.get("decision_id")

        teacher_counts[t_id] = teacher_counts.get(t_id, 0) + 1

        if out.get("failed", False):
            failures += 1
            continue
        if out.get("fallback", False):
            fallbacks += 1

        chosen = out.get("chosen_action")
        if not dec_id or not chosen:
            continue

        decision_predictions.setdefault(dec_id, {})[t_id] = str(chosen)

        # Check against ground truth label if available
        if dec_id in true_labels:
            if str(chosen) == true_labels[dec_id]:
                teacher_correct[t_id] = teacher_correct.get(t_id, 0) + 1

    # Calculate pairwise agreement
    pair_matches = 0
    pair_totals = 0
    for dec_id, preds in decision_predictions.items():
        if len(preds) < 2:
            continue
        items = list(preds.items())
        for idx in range(len(items)):
            for jdx in range(idx + 1, len(items)):
                t1, p1 = items[idx]
                t2, p2 = items[jdx]
                pair_totals += 1
                if p1 == p2:
                    pair_matches += 1

    pairwise_agreement = pair_matches / pair_totals if pair_totals > 0 else 1.0
    failure_rate = failures / total if total > 0 else 0.0
    fallback_rate = fallbacks / total if total > 0 else 0.0

    # Status determination
    status = "RELIABLE"
    issues = []
    if failure_rate > 0.05:
        status = "CONDITIONALLY_RELIABLE"
        issues.append(f"Teacher failure rate is high: {failure_rate:.2%}")
    if fallback_rate > 0.2:
        status = "CONDITIONALLY_RELIABLE"
        issues.append(f"Teacher fallback rate is high: {fallback_rate:.2%}")
    if failure_rate > 0.2:
        status = "UNRELIABLE"

    # Accuracy per teacher
    accuracies = {}
    for t_id, cnt in teacher_counts.items():
        correct = teacher_correct.get(t_id, 0)
        denom = sum(1 for out in teacher_outputs if out.get("teacher_id") == t_id and out.get("decision_id") in true_labels)
        if denom > 0:
            accuracies[t_id] = correct / denom

    return {
        "status": status,
        "total_outputs": total,
        "failure_rate": failure_rate,
        "fallback_rate": fallback_rate,
        "pairwise_agreement": pairwise_agreement,
        "teacher_accuracies": accuracies,
        "issues": issues
    }
