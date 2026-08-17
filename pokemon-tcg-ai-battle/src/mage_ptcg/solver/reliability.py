"""Bounded, privacy-safe runtime telemetry for C3 decisions."""

from __future__ import annotations

from collections import Counter, deque
import math
from typing import Any

from .bounded_search import BoundedSearchResult, KNOWN_SELECTION_TYPES


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


class SearchTelemetry:
    """Keep aggregate counters and a fixed-capacity trace window."""

    def __init__(self, *, trace_capacity: int = 4096) -> None:
        if type(trace_capacity) is not int or trace_capacity <= 0:
            raise ValueError("trace_capacity must be a positive int")
        self._traces: deque[dict[str, object]] = deque(maxlen=trace_capacity)
        self._decisions = 0
        self._fallbacks = 0
        self._timeouts = 0
        self._engine_calls = 0
        self._expansions = 0
        self._latencies_ms: deque[float] = deque(maxlen=trace_capacity)
        self._selection_counts: Counter[str] = Counter()
        self._fallback_reasons: Counter[str] = Counter()
        self._budget_reasons: Counter[str] = Counter()

    @property
    def last_trace(self) -> dict[str, object] | None:
        return dict(self._traces[-1]) if self._traces else None

    def record(self, result: BoundedSearchResult) -> None:
        payload = result.to_trace_payload()
        self._traces.append(payload)
        self._latencies_ms.append(result.elapsed_ms)
        self._decisions += 1
        self._fallbacks += int(result.fell_back)
        self._timeouts += int(result.timed_out)
        self._engine_calls += result.engine_calls
        self._expansions += result.expansions
        selection_label = (
            str(result.selection_type)
            if result.selection_type in KNOWN_SELECTION_TYPES
            else "UNKNOWN"
        )
        self._selection_counts[selection_label] += 1
        self._budget_reasons[result.budget_exhaustion_reason] += 1
        if result.fallback_reason is not None:
            self._fallback_reasons[result.fallback_reason] += 1

    def snapshot(self) -> dict[str, Any]:
        latencies = list(self._latencies_ms)
        counterexamples = [
            trace
            for trace in self._traces
            if trace["selection"] != trace["fallback_selection"]
            or trace["fallback_reason"] is not None
            or trace["budget_exhaustion_reason"] != "complete"
        ][:32]
        return {
            "schema_version": "bounded-search-telemetry-v0",
            "decisions": self._decisions,
            "fallback_count": self._fallbacks,
            "fallback_rate": self._fallbacks / self._decisions if self._decisions else None,
            "timeout_count": self._timeouts,
            "timeout_rate": self._timeouts / self._decisions if self._decisions else None,
            "engine_calls": self._engine_calls,
            "engine_calls_per_decision": (
                self._engine_calls / self._decisions if self._decisions else None
            ),
            "expansions": self._expansions,
            "latency_ms": {
                "count": len(latencies),
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "maximum": max(latencies) if latencies else None,
            },
            "selection_type_counts": dict(sorted(self._selection_counts.items())),
            "fallback_reasons": dict(sorted(self._fallback_reasons.items())),
            "budget_reasons": dict(sorted(self._budget_reasons.items())),
            "counterexamples": counterexamples,
            "retained_trace_count": len(self._traces),
        }


__all__ = ["SearchTelemetry"]
