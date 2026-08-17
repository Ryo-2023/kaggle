"""Promotion evidence report module.

Compiles multiple validation gates and generates human-in-the-loop decision packets.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError


class PromotionEvaluator:
    """Evaluates safety gates, latency thresholds, and outputs non-promoted sign-off checklists."""

    def __init__(self, current_champion: str = "Rule Agent v0"):
        self.current_champion = current_champion

    def evaluate_gates(
        self,
        stats: dict[str, Any],
        registry_validation_passed: bool = True,
        known_defects_count: int = 0,
        package_clean_room: str = "NOT_RUN",
        export_parity: str = "NOT_RUN",
        model_hash_consistency: str = "NOT_RUN",
        dataset_lineage: str = "NOT_RUN",
        full_regression: str = "NOT_RUN",
        critical_defects_count: int = 0,
        high_defects_count: int = 0,
    ) -> dict[str, Any]:
        """Aggregate evidence statistics and compile a human review decision packet."""
        gates = {}

        # 1. Legal Action Rate Gate
        legal_rate = stats.get("legal_action_rate", 1.0)
        gates["legal_action_rate"] = {
            "status": "PASS" if legal_rate >= 0.999 else "FAIL",
            "value": legal_rate,
            "threshold": 0.999,
        }

        # 2. Privacy Gate
        gates["privacy_violations"] = {
            "status": "PASS" if registry_validation_passed else "FAIL",
            "value": 0 if registry_validation_passed else 1,
            "threshold": 0,
        }

        # 3. Crash / Timeout / Invalid Gate
        invalid = stats.get("invalid_count", 0)
        crash = stats.get("crash_count", 0)
        timeout = stats.get("timeout_count", 0)
        safety_status = "PASS"
        if invalid > 0 or crash > 0 or timeout > 0:
            safety_status = "FAIL"
        gates["safety_violations"] = {
            "status": safety_status,
            "invalid_count": invalid,
            "crash_count": crash,
            "timeout_count": timeout,
        }

        # 4. Fallback rate gate
        fallback_rate = stats.get("fallback_rate", 0.0)
        gates["fallback_rate"] = {
            "status": "PASS" if fallback_rate <= 0.01 else "FAIL",
            "value": fallback_rate,
            "threshold": 0.01,
        }

        # 5. Screening game count
        total_games = stats.get("total_games", 0)
        gates["screening_game_count"] = {
            "status": "PASS" if total_games >= 100 else "INSUFFICIENT_EVIDENCE",
            "value": total_games,
            "threshold": 100,
        }

        # 6. Seat balance gate
        seat_breakdown = stats.get("seat_breakdown", {})
        seat_0_games = seat_breakdown.get("0", {}).get("games", 0)
        seat_1_games = seat_breakdown.get("1", {}).get("games", 0)
        seat_status = "PASS"
        if total_games > 0:
            if abs(seat_0_games - seat_1_games) > 1:
                seat_status = "FAIL"
        else:
            seat_status = "NOT_RUN"
        gates["seat_balance"] = {
            "status": seat_status,
            "seat_0_games": seat_0_games,
            "seat_1_games": seat_1_games,
        }

        # 7. Confidence interval gate (Wilson / Bootstrap interval width check)
        # Verify interval is not too wide
        wilson = stats.get("wilson_interval", [0.0, 0.0])
        interval_width = wilson[1] - wilson[0]
        gates["confidence_interval"] = {
            "status": "PASS" if interval_width <= 0.2 else "INSUFFICIENT_EVIDENCE",
            "value": interval_width,
            "threshold": 0.2,
        }

        # 8. Known Defects Gates
        defects_status = "PASS"
        if known_defects_count > 0 or critical_defects_count > 0 or high_defects_count > 0:
            defects_status = "FAIL"
        gates["known_defects"] = {
            "status": defects_status,
            "known_defects": known_defects_count,
            "critical_defects": critical_defects_count,
            "high_defects": high_defects_count,
        }

        # 9. Clean-room / Parity / Lineage / Regression input gates
        # We explicitly preserve these values as is (PASS, FAIL, NOT_RUN)
        for g_name, g_val in [
            ("package_clean_room", package_clean_room),
            ("export_parity", export_parity),
            ("model_hash_consistency", model_hash_consistency),
            ("dataset_lineage", dataset_lineage),
            ("full_regression", full_regression),
        ]:
            if g_val not in ("PASS", "FAIL", "NOT_RUN", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"):
                raise SupportContractError(f"Invalid status value '{g_val}' for gate '{g_name}'")
            gates[g_name] = {"status": g_val}

        # Warnings logic
        warnings = []
        if total_games < 100:
            warnings.append("sample-size-warning: evaluation count is too low for statistical confidence.")

        seat_0_rate = seat_breakdown.get("0", {}).get("win_rate")
        seat_1_rate = seat_breakdown.get("1", {}).get("win_rate")
        if seat_0_rate is not None and seat_1_rate is not None:
            if abs(seat_0_rate - seat_1_rate) > 0.15:
                warnings.append("seat-effect-warning: significant performance disparity detected between seat 0 and seat 1.")

        # Determine overall status
        # Priority: FAIL > NOT_RUN > INSUFFICIENT_EVIDENCE > PASS
        status_values = [g.get("status") for g in gates.values() if "status" in g]
        if "FAIL" in status_values:
            overall_result = "BLOCKED"
        elif "NOT_RUN" in status_values:
            overall_result = "INSUFFICIENT_EVIDENCE"
        elif "INSUFFICIENT_EVIDENCE" in status_values:
            overall_result = "INSUFFICIENT_EVIDENCE"
        else:
            overall_result = "REVIEW_READY"

        decision_packet = {
            "schema_version": "support-promotion-decision-v1",
            "timestamp": time.time(),
            "overall_result": overall_result,
            "promotion_status": "NO_DECISION",  # Strictly NO_DECISION, never auto-promote
            "current_champion": self.current_champion,
            "gate_results": gates,
            "warnings": warnings,
            "human_sign_off_checklist": [
                "Verify offline logs for unseen exceptions",
                "Ensure local clean-room package builds correctly",
                "Manually verify candidate policy on baseline decks",
                "Sign-off from at least two division leads"
            ],
            "recommended_next_experiment": "Execute full baseline paired evaluation with >= 500 games."
        }

        return decision_packet
