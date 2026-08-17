"""Artifact inventory classification and retention planner.

Generates recommended cleanup plans for offline training artifacts without modifying disks.
"""

from __future__ import annotations
from typing import Any

class RetentionPlanner:
    """Classifies artifacts and generates cleanup recommendations."""

    def __init__(self, artifact_inventory: list[dict[str, Any]]) -> None:
        self.inventory = artifact_inventory

    def classify_artifact(self, artifact: dict[str, Any]) -> str:
        """Classify artifact category."""
        path = str(artifact.get("path", ""))
        if path.endswith(".gz") or "/dataset" in path:
            return "dataset"
        elif "checkpoint" in path:
            return "checkpoint"
        elif "evidence" in path or path.endswith(".md"):
            return "evidence"
        elif "temp" in path or "tmp" in path:
            return "temporary"
        return "derived"

    def evaluate_retention(self, artifact: dict[str, Any]) -> str:
        """Determine retention action: KEEP, ARCHIVE, REBUILDABLE, ELIGIBLE_FOR_DELETION, PROTECTED."""
        category = self.classify_artifact(artifact)
        is_protected = bool(artifact.get("protected", False))

        if is_protected:
            return "PROTECTED"

        if category == "temporary":
            return "ELIGIBLE_FOR_DELETION"
        elif category == "checkpoint":
            return "REBUILDABLE"
        elif category == "dataset":
            return "ARCHIVE"
        return "KEEP"

    def generate_cleanup_plan(self) -> list[dict[str, Any]]:
        """Generate retention report detailing recommended actions."""
        plan = []
        for art in self.inventory:
            category = self.classify_artifact(art)
            action = self.evaluate_retention(art)

            plan.append({
                "path": art.get("path", ""),
                "category": category,
                "recommended_action": action,
                "size_bytes": art.get("size_bytes", 0),
                "rebuildable": action == "REBUILDABLE"
            })
        return plan
