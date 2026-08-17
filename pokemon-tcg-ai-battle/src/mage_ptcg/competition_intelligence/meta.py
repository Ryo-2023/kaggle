"""Deterministic, permission-aware O1-6 meta and matchup baselines."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from .canonical import digest
from .contracts import ContractError

META_SCHEMA_VERSION = "meta-snapshot-v1"
MATCHUP_SCHEMA_VERSION = "matchup-posterior-v1"
ANALYSIS_GRANTED = "ANALYSIS_GRANTED"


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ContractError(f"{name} must be finite and non-negative")
    return result


def _probabilities(values: Mapping[str, float], unknown_mass: float, name: str) -> None:
    total = unknown_mass + sum(values.values())
    if not math.isfinite(total) or abs(total - 1.0) > 1e-9:
        raise ContractError(f"{name} plus unknown_mass must sum to 1.0")
    if any(not math.isfinite(v) or v < 0 or v > 1 for v in values.values()):
        raise ContractError(f"{name} values must be finite probabilities")
    if not math.isfinite(unknown_mass) or not 0 <= unknown_mass <= 1:
        raise ContractError("unknown_mass must be a finite probability")


@dataclass(frozen=True, slots=True)
class WeightedStrategyObservation:
    observation_id: str
    source_id: str
    source_kind: str
    episode_id: str
    joint_fingerprint_id: str | None
    archetype_posterior: Mapping[str, float]
    unknown_mass: float
    timestamp: str
    source_weight: float
    freshness_weight: float
    duplicate_discount: float
    confidence: float
    population_bucket: str | None
    lineage_version_group: str | None
    permission_status: str
    analysis_version: str

    def __post_init__(self) -> None:
        for name in ("observation_id", "source_id", "source_kind", "episode_id", "timestamp", "permission_status", "analysis_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractError(f"{name} must be non-empty")
        _probabilities(self.archetype_posterior, self.unknown_mass, "archetype_posterior")
        for name in ("source_weight", "freshness_weight", "duplicate_discount", "confidence"):
            _finite_nonnegative(getattr(self, name), name)

    @property
    def effective_weight(self) -> float:
        return self.source_weight * self.freshness_weight * self.duplicate_discount * self.confidence

    def content_payload(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id, "source_id": self.source_id, "source_kind": self.source_kind,
            "episode_id": self.episode_id, "joint_fingerprint_id": self.joint_fingerprint_id,
            "archetype_posterior": dict(sorted(self.archetype_posterior.items())), "unknown_mass": self.unknown_mass,
            "timestamp": self.timestamp, "source_weight": self.source_weight, "freshness_weight": self.freshness_weight,
            "duplicate_discount": self.duplicate_discount, "confidence": self.confidence,
            "effective_weight": self.effective_weight, "population_bucket": self.population_bucket,
            "lineage_version_group": self.lineage_version_group, "permission_status": self.permission_status,
            "analysis_version": self.analysis_version,
        }


@dataclass(frozen=True, slots=True)
class MetaSnapshot:
    cutoff_time: str
    prior: Mapping[str, float]
    posterior_mean: Mapping[str, float]
    intervals: Mapping[str, tuple[float, float]]
    scenarios: Mapping[str, Mapping[str, float]]
    effective_sample_size: float
    source_composition: Mapping[str, float]
    included_observation_ids: tuple[str, ...]
    excluded_observation_ids: Mapping[str, str]
    meta_snapshot_id: str
    meta_snapshot_sha256: str

    def content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": META_SCHEMA_VERSION, "cutoff_time": self.cutoff_time,
            "prior": dict(sorted(self.prior.items())), "posterior_mean": dict(sorted(self.posterior_mean.items())),
            "intervals": {k: list(v) for k, v in sorted(self.intervals.items())},
            "scenarios": {k: dict(sorted(v.items())) for k, v in sorted(self.scenarios.items())},
            "effective_sample_size": self.effective_sample_size,
            "source_composition": dict(sorted(self.source_composition.items())),
            "included_observation_ids": list(self.included_observation_ids),
            "excluded_observation_ids": dict(sorted(self.excluded_observation_ids.items())),
        }


def build_meta_snapshot(
    observations: Iterable[WeightedStrategyObservation], *, cutoff_time: str, prior: Mapping[str, float] | None = None
) -> MetaSnapshot:
    included: list[WeightedStrategyObservation] = []
    excluded: dict[str, str] = {}
    for observation in sorted(observations, key=lambda item: item.observation_id):
        if observation.timestamp > cutoff_time:
            excluded[observation.observation_id] = "after_cutoff"
        elif observation.permission_status != ANALYSIS_GRANTED:
            excluded[observation.observation_id] = "analysis_permission_not_granted"
        else:
            included.append(observation)
    labels = sorted({label for item in included for label in item.archetype_posterior} | set((prior or {}).keys()))
    base_prior = {label: _finite_nonnegative((prior or {}).get(label, 1.0), f"prior[{label}]") for label in labels}
    base_prior["unknown"] = _finite_nonnegative((prior or {}).get("unknown", 1.0), "prior[unknown]")
    alpha = dict(base_prior)
    source_weights: dict[str, float] = {}
    weights: list[float] = []
    for item in included:
        weight = item.effective_weight
        weights.append(weight)
        source_weights[item.source_kind] = source_weights.get(item.source_kind, 0.0) + weight
        for label, mass in item.archetype_posterior.items():
            alpha[label] = alpha.get(label, 0.0) + weight * mass
        alpha["unknown"] += weight * item.unknown_mass
    total = sum(alpha.values())
    mean = {label: value / total for label, value in sorted(alpha.items())}
    intervals = {
        label: (max(0.0, probability - 1.96 * math.sqrt(probability * (1 - probability) / (total + 1))),
                min(1.0, probability + 1.96 * math.sqrt(probability * (1 - probability) / (total + 1))))
        for label, probability in mean.items()
    }
    weight_sum = sum(weights)
    ess = weight_sum * weight_sum / sum(weight * weight for weight in weights) if weights and sum(w * w for w in weights) else 0.0
    composition = {kind: value / weight_sum for kind, value in sorted(source_weights.items())} if weight_sum else {}
    payload = {
        "schema_version": META_SCHEMA_VERSION, "cutoff_time": cutoff_time, "prior": base_prior, "posterior_mean": mean,
        "intervals": {key: list(value) for key, value in intervals.items()},
        "scenarios": {"lower": {key: value[0] for key, value in intervals.items()}, "upper": {key: value[1] for key, value in intervals.items()}},
        "effective_sample_size": ess, "source_composition": composition,
        "included_observation_ids": [item.observation_id for item in included], "excluded_observation_ids": excluded,
    }
    sha = digest(payload, domain="meta-snapshot")
    return MetaSnapshot(cutoff_time=cutoff_time, prior=base_prior, posterior_mean=mean, intervals=intervals,
                        scenarios={"lower": {key: value[0] for key, value in intervals.items()},
                                   "upper": {key: value[1] for key, value in intervals.items()}},
                        effective_sample_size=ess, source_composition=composition,
                        included_observation_ids=tuple(payload["included_observation_ids"]), excluded_observation_ids=excluded,
                        meta_snapshot_id="meta-" + sha[:24], meta_snapshot_sha256=sha)


def detect_drift(previous: MetaSnapshot, current: MetaSnapshot) -> dict[str, Any]:
    labels = sorted(set(previous.posterior_mean) | set(current.posterior_mean))
    p = [previous.posterior_mean.get(label, 0.0) for label in labels]
    q = [current.posterior_mean.get(label, 0.0) for label in labels]
    tv = 0.5 * sum(abs(left - right) for left, right in zip(p, q))
    midpoint = [(left + right) / 2 for left, right in zip(p, q)]
    def kl(values: list[float], target: list[float]) -> float:
        return sum(value * math.log2(value / other) for value, other in zip(values, target) if value and other)
    js = 0.5 * kl(p, midpoint) + 0.5 * kl(q, midpoint)
    unknown_change = current.posterior_mean.get("unknown", 0.0) - previous.posterior_mean.get("unknown", 0.0)
    new_cluster_support = sum(current.posterior_mean.get(label, 0.0) for label in labels if label not in previous.posterior_mean and label != "unknown")
    ess_change = current.effective_sample_size - previous.effective_sample_size
    verdict = "NO_SIGNIFICANT_DRIFT"
    if tv >= 0.20 or js >= 0.08 or new_cluster_support >= 0.10:
        verdict = "BENCHMARK_REFRESH_RECOMMENDED"
    elif tv >= 0.05 or abs(unknown_change) >= 0.05 or abs(ess_change) >= 5:
        verdict = "REVIEW_RECOMMENDED"
    return {"total_variation_distance": tv, "jensen_shannon_divergence": js,
            "unknown_mass_change": unknown_change, "new_cluster_support": new_cluster_support,
            "effective_sample_size_change": ess_change, "verdict": verdict}


@dataclass(frozen=True, slots=True)
class MatchupObservation:
    own_joint_strategy: str
    opponent_strategy: str
    seat: str
    result: str
    source_kind: str
    timestamp: str
    weight: float = 1.0
    confidence: float = 1.0
    unknown_mass: float = 0.0
    permission_status: str = ANALYSIS_GRANTED


def build_matchup_posterior(observations: Iterable[MatchupObservation], *, cutoff_time: str) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[MatchupObservation]] = {}
    for item in observations:
        if item.timestamp <= cutoff_time and item.permission_status == ANALYSIS_GRANTED:
            groups.setdefault((item.own_joint_strategy, item.opponent_strategy, item.seat), []).append(item)
    result = []
    for key, rows in sorted(groups.items()):
        wins = sum(item.weight * item.confidence for item in rows if item.result == "win")
        losses = sum(item.weight * item.confidence for item in rows if item.result == "loss")
        alpha, beta = 1 + wins, 1 + losses
        mean = alpha / (alpha + beta)
        interval = (max(0.0, mean - 1.96 * math.sqrt(mean * (1 - mean) / (alpha + beta + 1))),
                    min(1.0, mean + 1.96 * math.sqrt(mean * (1 - mean) / (alpha + beta + 1))))
        total_weight = sum(item.weight * item.confidence for item in rows)
        composition: dict[str, float] = {}
        for item in rows:
            composition[item.source_kind] = composition.get(item.source_kind, 0.0) + item.weight * item.confidence
        result.append({"own_joint_strategy": key[0], "opponent_strategy": key[1], "seat": key[2], "games": len(rows),
                       "effective_sample_size": total_weight, "win_posterior": mean, "interval": list(interval),
                       "source_composition": composition, "cutoff_time": cutoff_time,
                       "confidence": min(1.0, total_weight / 20),
                       "unknown_mass": sum(item.unknown_mass for item in rows) / len(rows)})
    payload = {"schema_version": MATCHUP_SCHEMA_VERSION, "cutoff_time": cutoff_time, "groups": result}
    return {**payload, "content_hash": digest(payload, domain="matchup-posterior")}
