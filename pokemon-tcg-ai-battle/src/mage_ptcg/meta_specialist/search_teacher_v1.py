"""Public-belief search teacher target construction."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class SearchTargetV1:
    probabilities: dict[str, float]
    confidence: float


def soft_search_target_v1(
    values: dict[str, float], *, standard_errors: dict[str, float], current_policy: dict[str, float],
    temperature: float = 1.0,
) -> SearchTargetV1:
    keys = tuple(sorted(values))
    if not keys or set(standard_errors) != set(keys) or set(current_policy) != set(keys) or temperature <= 0:
        raise ValueError("search target domains are invalid")
    if any(not math.isfinite(values[key]) or standard_errors[key] < 0 for key in keys):
        raise ValueError("search values/standard errors are invalid")
    policy_sum = sum(current_policy.values())
    if policy_sum <= 0 or any(current_policy[key] < 0 for key in keys):
        raise ValueError("current policy must be nonnegative")
    current = {key: current_policy[key] / policy_sum for key in keys}
    maximum = max(values.values())
    exponentials = {key: math.exp((values[key] - maximum) / temperature) for key in keys}
    total = sum(exponentials.values())
    search = {key: exponentials[key] / total for key in keys}
    confidence = math.exp(-sum(standard_errors.values()) / len(keys))
    blended = {key: confidence * search[key] + (1.0 - confidence) * current[key] for key in keys}
    return SearchTargetV1(probabilities=blended, confidence=confidence)


__all__ = ["SearchTargetV1", "soft_search_target_v1"]
