"""Data repair planning utility.

Generates recommended repair actions for corrupted or leaky datasets.
Does not directly alter workspace files.
"""

from __future__ import annotations
from typing import Any

class DataRepairPlanner:
    """Creates plans to repair datasets with quality or integrity issues."""

    def __init__(self, quality_profile: dict[str, Any], leakage_report: dict[str, Any] = None) -> None:
        self.profile = quality_profile
        self.leakage = leakage_report or {}

    def generate_plan(self) -> list[dict[str, Any]]:
        """Generate a list of recommended repair action steps."""
        plan = []

        # Check duplicate issues
        dup_rate = self.profile.get("duplicate_rate", 0.0)
        if dup_rate > 0.0:
            plan.append({
                "issue": "DUPLICATE_RECORDS",
                "affected_artifact": "dataset_records",
                "safe_summary": f"Dataset contains {dup_rate:.2%} duplicate records.",
                "recommended_action": "Apply deduplication using unique record digests (dedup.py).",
                "automatic_safe": True,
                "manual_review": False,
                "expected_output": "A dataset with duplicate count reduced to 0.",
                "risk": "Negligible risk; duplicate removal is safe."
            })

        # Check conflicting label issues
        conflict_rate = self.profile.get("conflicting_label_rate", 0.0)
        if conflict_rate > 0.0:
            plan.append({
                "issue": "CONFLICTING_LABELS",
                "affected_artifact": "dataset_labels",
                "safe_summary": f"Same decision_id has {conflict_rate:.2%} conflicting chosen_actions.",
                "recommended_action": "Resolve via label consensus methods (label_consensus.py) or quarantine.",
                "automatic_safe": False,
                "manual_review": True,
                "expected_output": "One consensus label per decision_id.",
                "risk": "Moderate risk; incorrect consensus might drop valid expert diversity."
            })

        # Check leakages
        if self.leakage.get("leakage_detected", False):
            plan.append({
                "issue": "SPLIT_LEAKAGE",
                "affected_artifact": "train_validation_splits",
                "safe_summary": "Train and validation splits share identical episodes/decisions.",
                "recommended_action": "Regenerate dataset splits strictly grouped by episode_id or game_id.",
                "automatic_safe": True,
                "manual_review": False,
                "expected_output": "Zero overlap of episode_ids across splits.",
                "risk": "Low risk; split logic adjustment will resolve contamination."
            })

        return plan

    def simulate_repair(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Simulate deduplication on records (safe local operation)."""
        seen = set()
        deduplicated = []
        for r in records:
            # Drop private keys before serializing
            clean = {k: v for k, v in r.items() if k not in ("token", "api_key", "password")}
            sig = str(sorted(clean.items()))
            if sig not in seen:
                seen.add(sig)
                deduplicated.append(r)
        return deduplicated
