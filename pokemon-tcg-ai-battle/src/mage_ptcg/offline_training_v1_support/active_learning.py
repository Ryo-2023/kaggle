"""Active learning query planner.

Scores records for annotation requests based on uncertainty, disagreement, and rarity.
"""

from __future__ import annotations
import random
from typing import Any

def plan_active_learning_queries(
    records: list[dict[str, Any]],
    uncertainties: dict[str, float],
    query_budget: int = 10,
    seed: int = 42
) -> dict[str, Any]:
    """Score records and select query budget capacity without leaking private states."""
    rng = random.Random(seed)

    scored_candidates = []
    seen_digests = set()

    for r in records:
        dec_id = r.get("decision_id")
        if not dec_id:
            continue

        clean = {k: v for k, v in r.items() if k not in ("token", "api_key", "password", "game_id")}
        sig = str(sorted(clean.items()))
        if sig in seen_digests:
            continue
        seen_digests.add(sig)

        score = uncertainties.get(dec_id, 0.0)

        if r.get("selection_type") == "rare_select" or r.get("context_type") == "rare_context":
            score += 0.3
        if r.get("fallback", False):
            score += 0.2

        reasons = []
        if score > 0.8:
            reasons.append("HIGH_UNCERTAINTY")
        if r.get("fallback", False):
            reasons.append("RUNTIME_FALLBACK")
        if r.get("selection_type") == "rare_select" or r.get("context_type") == "rare_context":
            reasons.append("RARE_FEATURE")

        if not reasons:
            reasons.append("LOW_CONFIDENCE_DEFAULT")

        scored_candidates.append({
            "decision_id": dec_id,
            "score": score,
            "reasons": reasons,
            "selection_type": r.get("selection_type", "normal"),
            "context_type": r.get("context_type", "normal")
        })

    scored_candidates.sort(key=lambda x: (-x["score"], x["decision_id"]))
    queries = scored_candidates[:query_budget]

    return {
        "queries": queries,
        "total_scored": len(scored_candidates),
        "budget": query_budget,
        "query_cost": len(queries) * 1.0
    }
