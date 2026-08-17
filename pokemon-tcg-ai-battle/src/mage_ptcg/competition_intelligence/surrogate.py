"""Smoothed empirical opponent policy; never a Student-training source."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from .canonical import digest
from .contracts import ContractError
from .meta import ANALYSIS_GRANTED

SURROGATE_SCHEMA_VERSION = "opponent-surrogate-v1"
_CONTEXT_ORDER = ("phase", "action_category", "board_bucket", "joint_fingerprint", "seat", "matchup_bucket")


@dataclass(frozen=True, slots=True)
class SurrogateObservation:
    source_id: str
    timestamp: str
    action_key: str
    context: Mapping[str, str]
    confidence: float = 1.0
    permission_status: str = ANALYSIS_GRANTED
    actor_visible: bool = True

    def __post_init__(self) -> None:
        if not self.source_id or not self.timestamp or not self.action_key:
            raise ContractError("source_id, timestamp, and action_key must be non-empty")
        if not self.actor_visible:
            raise ContractError("Opponent Surrogate accepts actor-visible observations only")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ContractError("confidence must be finite in [0, 1]")
        if any(key not in _CONTEXT_ORDER or not isinstance(value, str) for key, value in self.context.items()):
            raise ContractError("context contains an unsupported or non-string field")


def _context_key(context: Mapping[str, str], level: int) -> tuple[tuple[str, str], ...]:
    return tuple((name, context[name]) for name in _CONTEXT_ORDER[:level] if name in context)


@dataclass(frozen=True, slots=True)
class OpponentSurrogate:
    cutoff_time: str
    actions: tuple[str, ...]
    tables: Mapping[tuple[tuple[str, str], ...], Mapping[str, float]]
    support: Mapping[tuple[tuple[str, str], ...], float]
    source_ids: tuple[str, ...]
    minimum_support: float
    laplace: float
    entropy_floor: float
    artifact_id: str
    content_hash: str

    def predict(self, context: Mapping[str, str]) -> dict[str, Any]:
        if not self.actions:
            return {"distribution": {}, "fallback": "no_actions", "missing_data_flags": ["no_permitted_observations"]}
        for level, label in ((len(_CONTEXT_ORDER), "exact"), (3, "reduced"), (1, "fingerprint_or_phase"), (0, "generic")):
            key = _context_key(context, level)
            if key in self.tables and self.support.get(key, 0.0) >= self.minimum_support:
                return {"distribution": dict(self.tables[key]), "fallback": label, "missing_data_flags": []}
        return {"distribution": {action: 1 / len(self.actions) for action in self.actions}, "fallback": "deterministic_uniform", "missing_data_flags": ["insufficient_support"]}


def _entropy(distribution: Mapping[str, float]) -> float:
    return -sum(value * math.log(value) for value in distribution.values() if value > 0)


def _with_entropy_floor(distribution: dict[str, float], floor: float) -> dict[str, float]:
    if len(distribution) <= 1 or _entropy(distribution) >= floor:
        return distribution
    uniform = 1 / len(distribution)
    low, high = 0.0, 1.0
    for _ in range(40):
        mid = (low + high) / 2
        candidate = {key: (1 - mid) * value + mid * uniform for key, value in distribution.items()}
        if _entropy(candidate) < floor:
            low = mid
        else:
            high = mid
    return {key: (1 - high) * value + high * uniform for key, value in distribution.items()}


def build_opponent_surrogate(
    observations: Iterable[SurrogateObservation], *, cutoff_time: str, minimum_support: float = 2.0,
    laplace: float = 1.0, entropy_floor: float = 0.1,
) -> OpponentSurrogate:
    if minimum_support <= 0 or laplace <= 0 or entropy_floor < 0:
        raise ContractError("minimum_support/laplace must be positive and entropy_floor non-negative")
    kept = [item for item in observations if item.timestamp <= cutoff_time and item.permission_status == ANALYSIS_GRANTED]
    actions = tuple(sorted({item.action_key for item in kept}))
    counts: dict[tuple[tuple[str, str], ...], dict[str, float]] = {}
    support: dict[tuple[tuple[str, str], ...], float] = {}
    for item in kept:
        for level in (len(_CONTEXT_ORDER), 3, 1, 0):
            key = _context_key(item.context, level)
            counts.setdefault(key, {})[item.action_key] = counts.setdefault(key, {}).get(item.action_key, 0.0) + item.confidence
            support[key] = support.get(key, 0.0) + item.confidence
    tables: dict[tuple[tuple[str, str], ...], dict[str, float]] = {}
    for key, table in counts.items():
        denominator = sum(table.values()) + laplace * len(actions)
        distribution = {action: (table.get(action, 0.0) + laplace) / denominator for action in actions}
        tables[key] = _with_entropy_floor(distribution, entropy_floor)
    payload = {"schema_version": SURROGATE_SCHEMA_VERSION, "cutoff_time": cutoff_time, "actions": list(actions),
               "tables": [{"context": list(key), "distribution": value, "support": support[key]} for key, value in sorted(tables.items())],
               "source_ids": sorted({item.source_id for item in kept}), "minimum_support": minimum_support,
               "laplace": laplace, "entropy_floor": entropy_floor, "training_route": "forbidden"}
    hash_value = digest(payload, domain="opponent-surrogate")
    return OpponentSurrogate(cutoff_time=cutoff_time, actions=actions, tables=tables, support=support,
                             source_ids=tuple(payload["source_ids"]), minimum_support=minimum_support, laplace=laplace,
                             entropy_floor=entropy_floor, artifact_id="surrogate-" + hash_value[:24], content_hash=hash_value)


def evaluate_surrogate(surrogate: OpponentSurrogate, heldout: Iterable[SurrogateObservation], *, top_k: int = 3) -> dict[str, Any]:
    rows = [item for item in heldout if item.permission_status == ANALYSIS_GRANTED and item.actor_visible]
    if not rows:
        return {"count": 0, "negative_log_likelihood": None, "top_k_accuracy": None, "brier_score": None,
                "context_coverage": 0.0, "fallback_rate": 0.0, "unseen_context_rate": 0.0}
    nll = brier = 0.0
    top_hits = fallback = unseen = covered = 0
    for row in rows:
        prediction = surrogate.predict(row.context)
        distribution = prediction["distribution"]
        probability = max(distribution.get(row.action_key, 0.0), 1e-12)
        nll -= math.log(probability)
        brier += sum((value - (1.0 if action == row.action_key else 0.0)) ** 2 for action, value in distribution.items())
        if row.action_key in [action for action, _ in sorted(distribution.items(), key=lambda item: (-item[1], item[0]))[:top_k]]:
            top_hits += 1
        if prediction["fallback"] != "exact":
            fallback += 1
        if prediction["fallback"] == "deterministic_uniform":
            unseen += 1
        if not prediction["missing_data_flags"]:
            covered += 1
    return {"count": len(rows), "negative_log_likelihood": nll / len(rows), "top_k_accuracy": top_hits / len(rows),
            "brier_score": brier / len(rows), "context_coverage": covered / len(rows),
            "fallback_rate": fallback / len(rows), "unseen_context_rate": unseen / len(rows),
            "deterministic": True, "not_an_exact_clone": True}
