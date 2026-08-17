"""Resource budget tracking and safe degradation scheduler.

Monitors execution metrics against soft/hard resource limits.
"""

from __future__ import annotations
from typing import Any

class ResourceBudgetTracker:
    """Tracks and limits consumption of wall-time, queries, disk and RAM."""

    def __init__(self, limits: dict[str, float]) -> None:
        self.limits = limits
        self.consumption: dict[str, float] = {}

    def consume(self, resource: str, amount: float) -> None:
        """Add consumed resource units."""
        self.consumption[resource] = self.consumption.get(resource, 0.0) + amount

    def check_limit(self, resource: str) -> str:
        """Compare consumption to limits and return status (OK, SOFT_LIMIT, HARD_LIMIT)."""
        limit = self.limits.get(resource)
        if limit is None:
            return "OK"

        consumed = self.consumption.get(resource, 0.0)
        if consumed >= limit:
            return "HARD_LIMIT"
        elif consumed >= limit * 0.8:
            return "SOFT_LIMIT"
        return "OK"

    def get_degraded_parameters(self) -> dict[str, Any]:
        """Determine adjusted parameters based on soft/hard budget statuses."""
        params = {
            "skip_extended_fuzz": False,
            "scale_records_cap": 100000,
            "bootstrap_samples": 1000,
            "allow_new_sweep": True
        }

        time_status = self.check_limit("wall_time")
        if time_status == "HARD_LIMIT":
            params["skip_extended_fuzz"] = True
            params["scale_records_cap"] = 1000
            params["bootstrap_samples"] = 50
            params["allow_new_sweep"] = False
        elif time_status == "SOFT_LIMIT":
            params["bootstrap_samples"] = 200
            params["scale_records_cap"] = 10000

        query_status = self.check_limit("teacher_queries")
        if query_status == "HARD_LIMIT":
            params["allow_new_sweep"] = False

        return params
