"""Experiment registry query engine.

Enables read-only filtering and sorting of experiment records.
"""

from __future__ import annotations
from typing import Any

class ExperimentQueryEngine:
    """Read-only search and Pareto filter engine for registry experiment records."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = [dict(r) for r in records]

    def query(
        self,
        filters: dict[str, Any] = None,
        sort_by: str = None,
        reverse: bool = False
    ) -> list[dict[str, Any]]:
        """Filter and sort experiment records."""
        filters = filters or {}

        filtered = []
        for r in self.records:
            match = True
            for k, val in filters.items():
                if r.get(k) != val:
                    match = False
                    break
            if match:
                filtered.append(r)

        if sort_by:
            filtered.sort(key=lambda x: (x.get(sort_by) is not None, x.get(sort_by)), reverse=reverse)

        return filtered
