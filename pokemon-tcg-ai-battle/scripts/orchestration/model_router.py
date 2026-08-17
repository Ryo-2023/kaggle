"""Deterministic model routing that tasks cannot weaken or override."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .overnight_plan import ModelRoute


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    reasoning_effort: str
    reasons: tuple[str, ...]
    fallback: str | None = None


def route_task(
    routing: Mapping[str, ModelRoute],
    *,
    complexity: str,
    risk: str,
    control_plane: bool = False,
    repair: bool = False,
    review: bool = False,
    large_diff: bool = False,
) -> RoutingDecision:
    """Select a tier from trusted facts, then use that tier's configured profile."""

    reasons: list[str] = []
    if control_plane or risk == "high":
        tier = "deep"
        reasons.append("control-plane-or-high-risk")
    elif complexity in {"complex", "algorithm"} or large_diff:
        tier = "deep"
        reasons.append("complexity-or-large-diff")
    elif complexity == "simple" and risk == "low":
        tier = "economy"
        reasons.append("simple-low-risk")
    else:
        tier = "standard"
        reasons.append("normal-work")
    if repair:
        tier = {"economy": "standard", "standard": "deep", "deep": "deep"}[tier]
        reasons.append("repair-escalation")
    if review:
        reasons.append("independent-review")
    route = routing[tier]
    return RoutingDecision(
        tier=tier,
        model=route.model,
        reasoning_effort=route.reasoning_effort,
        reasons=tuple(reasons),
        fallback=None,
    )
