"""O3 evaluation metadata and a non-promoting report around existing O2 execution."""

from __future__ import annotations

from typing import Any, Mapping

from mage_ptcg.o2_training_loop.core import promotion_report


def build_o3_promotion_report(evaluation: Mapping[str, Any], *, minimum_logical_pairs: int = 100) -> dict[str, Any]:
    """Never call O2's result a paired statistical estimate when CABT seeds are unavailable."""
    completed = int(evaluation.get("seat_matched_logical_pairs", 0) or 0)
    o2_input = {
        "paired_games": completed,
        "legality_failures": int(evaluation.get("legality_failures", 0) or 0),
        "timeouts": int(evaluation.get("timeouts", 0) or 0),
        "fallbacks": int(evaluation.get("fallbacks", 0) or 0),
        "failed_matches": int(evaluation.get("failed_matches", 0) or 0),
        # No paired CI is supplied: engine outcomes cannot be paired.
        "confidence_interval_95": None,
    }
    report = promotion_report(o2_input, minimum_pairs=minimum_logical_pairs)
    report.update({
        "engine_seed_supported": False,
        "pairing_mode": "seat_matched_unseeded",
        "exact_paired_inference": False,
        "automatic_champion_change": False,
        "champion_before": "Rule Agent v0",
        "champion_after": "Rule Agent v0",
    })
    return report


__all__ = ["build_o3_promotion_report"]
