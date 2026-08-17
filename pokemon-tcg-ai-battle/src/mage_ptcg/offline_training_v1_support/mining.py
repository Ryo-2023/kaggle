"""Hard-state mining module.

Scans decision diagnostic records to discover hard states, calculate priority
scores, and produce structured summaries.
"""

from __future__ import annotations

from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    digest,
)

REASON_WEIGHTS = {
    "RULE_STUDENT_DISAGREEMENT": 1.5,
    "TEACHER_STUDENT_DISAGREEMENT": 2.0,
    "LOW_MARGIN": 1.0,
    "HIGH_ENTROPY": 0.8,
    "SEARCH_OVERRIDE": 1.2,
    "HELDOUT_ERROR": 3.0,
    "SEAT_FAILURE": 1.5,
    "OOD_FEATURE": 1.0,
    "RUNTIME_FALLBACK": 2.5,
    "RARE_SELECTION_TYPE": 0.5,
    "RARE_CONTEXT_TYPE": 0.5,
}


def mine_hard_states(
    records: Iterable[dict[str, Any]],
    margin_threshold: float = 0.1,
    entropy_threshold: float = 1.5,
) -> list[dict[str, Any]]:
    """Scan decision records and extract hard states based on performance indicators."""
    hard_states = []

    for r in records:
        # Schema checks
        required = {"episode_id", "decision_id", "state_digest"}
        if not required.issubset(r):
            continue

        reasons = []
        contribs = {}

        ep_id = r["episode_id"]
        dec_id = r["decision_id"]
        state_dig = r["state_digest"]

        teacher_act = r.get("teacher_action_key")
        student_act = r.get("student_action_key")

        # 1. Teacher Student Disagreement
        if teacher_act and student_act and teacher_act != student_act:
            reasons.append("TEACHER_STUDENT_DISAGREEMENT")
            contribs["TEACHER_STUDENT_DISAGREEMENT"] = REASON_WEIGHTS["TEACHER_STUDENT_DISAGREEMENT"]

        # 2. Rule Student Disagreement (If metadata or other fields flag rule agent as teacher)
        # Or if explicitly marked or teacher_id is rule-agent-v0
        teacher_id = r.get("metadata", {}).get("teacher_id", "")
        if "rule" in teacher_id.lower() and teacher_act and student_act and teacher_act != student_act:
            reasons.append("RULE_STUDENT_DISAGREEMENT")
            contribs["RULE_STUDENT_DISAGREEMENT"] = REASON_WEIGHTS["RULE_STUDENT_DISAGREEMENT"]

        # 3. Low Margin
        margin = r.get("student_margin")
        if margin is not None and isinstance(margin, (int, float)) and margin < margin_threshold:
            reasons.append("LOW_MARGIN")
            contribs["LOW_MARGIN"] = REASON_WEIGHTS["LOW_MARGIN"]

        # 4. High Entropy
        entropy = r.get("student_entropy")
        if entropy is not None and isinstance(entropy, (int, float)) and entropy > entropy_threshold:
            reasons.append("HIGH_ENTROPY")
            contribs["HIGH_ENTROPY"] = REASON_WEIGHTS["HIGH_ENTROPY"]

        # 5. Held-out error
        if bool(r.get("is_error", False)):
            reasons.append("HELDOUT_ERROR")
            contribs["HELDOUT_ERROR"] = REASON_WEIGHTS["HELDOUT_ERROR"]

        # 6. Runtime Fallback
        if bool(r.get("fallback_used", False)):
            reasons.append("RUNTIME_FALLBACK")
            contribs["RUNTIME_FALLBACK"] = REASON_WEIGHTS["RUNTIME_FALLBACK"]

        # 7. Rare Selection / Context Types
        sel_type = r.get("selection_type")
        if sel_type in ("rare_select", "special_select"):
            reasons.append("RARE_SELECTION_TYPE")
            contribs["RARE_SELECTION_TYPE"] = REASON_WEIGHTS["RARE_SELECTION_TYPE"]

        ctx_type = r.get("context_type")
        if ctx_type in ("rare_context", "special_context"):
            reasons.append("RARE_CONTEXT_TYPE")
            contribs["RARE_CONTEXT_TYPE"] = REASON_WEIGHTS["RARE_CONTEXT_TYPE"]

        # Search override flag
        if bool(r.get("search_override", False)):
            reasons.append("SEARCH_OVERRIDE")
            contribs["SEARCH_OVERRIDE"] = REASON_WEIGHTS["SEARCH_OVERRIDE"]

        if not reasons:
            continue

        priority_score = sum(contribs.values())

        # Construct safe summary (excluding private observation/hand data)
        safe_summary = {
            "seat": r.get("seat", 0),
            "selection_type": sel_type,
            "context_type": ctx_type,
            "fallback_used": bool(r.get("fallback_used", False)),
            "is_error": bool(r.get("is_error", False)),
        }

        # Deduplication key based on state and teacher option key
        dedup_key = digest({
            "state_digest": state_dig,
            "selection_type": sel_type,
            "teacher_action_key": teacher_act
        }, domain="hard-state-dedup")

        hs_record = {
            "schema_version": "support-hard-state-v1",
            "hard_state_id": f"hs_{digest(dedup_key)[:16]}",
            "source_record_reference": {
                "episode_id": ep_id,
                "decision_id": dec_id,
            },
            "reason_codes": sorted(reasons),
            "priority_score": priority_score,
            "priority_contributions": contribs,
            "dedup_key": dedup_key,
            "conflict_status": "NONE",
            "safe_summary": safe_summary,
        }
        hard_states.append(hs_record)

    # Sort by priority score descending
    hard_states.sort(key=lambda x: x["priority_score"], reverse=True)
    return hard_states
