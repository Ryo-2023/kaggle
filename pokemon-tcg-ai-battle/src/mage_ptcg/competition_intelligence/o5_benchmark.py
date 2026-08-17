"""Versioned, immutable Benchmark manifest envelope for O5.

``o5_activation.build_benchmark_manifest`` already decides the
population-gated ``sets``/``status``; this module only adds the
run-identifying, reproducibility, and provenance envelope a *versioned*
Benchmark needs (id, seeds, game budget, commit, candidate artifact
identity, content-addressed hash). Definition and results stay separate:
this module builds the manifest only, ``o5_evaluation`` consumes it and
writes results elsewhere.

A manifest is scoped to exactly one ``benchmark_kind``: ``"performance"``
(``core_regression``/``current_meta`` only) or ``"safety"``
(``safety``/``adversarial`` only, i.e. fault-injection opponents). This
split exists so a fault-injection match (an opponent that deliberately
crashes or returns an illegal action) can never be silently merged into a
candidate's performance win rate -- the two kinds always produce distinct
``benchmark_id`` runs, distinct manifests, and distinct reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .canonical import digest
from .o5_activation import OpponentInstanceSpec, build_benchmark_manifest

O5_BENCHMARK_MANIFEST_SCHEMA_VERSION = "o5-versioned-benchmark-manifest-v2"
BENCHMARK_KINDS = ("performance", "safety")
_PERFORMANCE_SET_NAMES = ("core_regression", "current_meta")
_SAFETY_SET_NAMES = ("safety", "adversarial")
_ALL_SET_NAMES = ("core_regression", "current_meta", "adversarial", "safety")
NOT_APPLICABLE = "NOT_APPLICABLE"


class O5BenchmarkError(ValueError):
    """Raised for a malformed versioned benchmark manifest input."""


def _non_blank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise O5BenchmarkError(f"{name} must be a non-blank string")
    return value


def _sets_for_kind(base_sets: Mapping[str, Sequence[str]], benchmark_kind: str) -> dict[str, tuple[str, ...]]:
    included = _PERFORMANCE_SET_NAMES if benchmark_kind == "performance" else _SAFETY_SET_NAMES
    return {name: (tuple(base_sets.get(name, ())) if name in included else ()) for name in _ALL_SET_NAMES}


@dataclass(frozen=True, slots=True)
class VersionedBenchmarkManifest:
    schema_version: str
    benchmark_id: str
    benchmark_version: str
    benchmark_kind: str
    created_at: str
    source_snapshot_ids: tuple[str, ...]
    deck_registry_version: str
    policy_pack_version: str
    agent_family_versions: Mapping[str, str]
    ruleset_version: str
    cabt_version: str
    seed_set: tuple[int, ...]
    seat_swap_policy: str
    game_count: int
    logical_pair_count: int
    time_budget_seconds: float
    candidate_artifact_id: str
    candidate_artifact_hash: str
    baseline_artifact_ids: tuple[str, ...]
    environment: str
    commit: str
    status: str
    sets: Mapping[str, tuple[str, ...]]
    requirements: Mapping[str, int]
    config_hash: str = field(init=False)
    manifest_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_hash", digest(self._config_payload(), domain="o5-versioned-benchmark-config"))
        object.__setattr__(self, "manifest_hash", digest(self._public_payload(), domain="o5-versioned-benchmark-manifest"))

    def _config_payload(self) -> dict[str, object]:
        return {
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "benchmark_kind": self.benchmark_kind,
            "deck_registry_version": self.deck_registry_version,
            "policy_pack_version": self.policy_pack_version,
            "agent_family_versions": dict(sorted(self.agent_family_versions.items())),
            "ruleset_version": self.ruleset_version,
            "cabt_version": self.cabt_version,
            "seed_set": list(self.seed_set),
            "seat_swap_policy": self.seat_swap_policy,
            "game_count": self.game_count,
            "time_budget_seconds": self.time_budget_seconds,
            "candidate_artifact_id": self.candidate_artifact_id,
            "candidate_artifact_hash": self.candidate_artifact_hash,
            "baseline_artifact_ids": sorted(self.baseline_artifact_ids),
            "environment": self.environment,
            "commit": self.commit,
        }

    def _public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "benchmark_kind": self.benchmark_kind,
            "created_at": self.created_at,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "deck_registry_version": self.deck_registry_version,
            "policy_pack_version": self.policy_pack_version,
            "agent_family_versions": dict(sorted(self.agent_family_versions.items())),
            "ruleset_version": self.ruleset_version,
            "cabt_version": self.cabt_version,
            "seed_set": list(self.seed_set),
            "seat_swap_policy": self.seat_swap_policy,
            "game_count": self.game_count,
            "logical_pair_count": self.logical_pair_count,
            "time_budget_seconds": self.time_budget_seconds,
            "candidate_artifact_id": self.candidate_artifact_id,
            "candidate_artifact_hash": self.candidate_artifact_hash,
            "baseline_artifact_ids": list(self.baseline_artifact_ids),
            "environment": self.environment,
            "commit": self.commit,
            "status": self.status,
            "sets": {key: list(value) for key, value in self.sets.items()},
            "requirements": dict(self.requirements),
            "config_hash": self.config_hash,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._public_payload(), "manifest_hash": self.manifest_hash}


def build_versioned_benchmark_manifest(
    population: Sequence[OpponentInstanceSpec],
    *,
    benchmark_id: str,
    benchmark_version: str,
    benchmark_kind: str,
    created_at: str,
    source_snapshot_ids: Sequence[str],
    deck_registry_version: str,
    policy_pack_version: str,
    agent_family_versions: Mapping[str, str],
    ruleset_version: str,
    cabt_version: str,
    seed_set: Sequence[int],
    seat_swap_policy: str,
    game_count: int,
    time_budget_seconds: float,
    candidate_artifact_id: str,
    candidate_artifact_hash: str,
    baseline_artifact_ids: Sequence[str],
    environment: str,
    commit: str,
    active_exact_decks: int,
    runnable_families: int,
    verified_links: int,
) -> VersionedBenchmarkManifest:
    _non_blank(benchmark_id, "benchmark_id")
    _non_blank(benchmark_version, "benchmark_version")
    _non_blank(candidate_artifact_id, "candidate_artifact_id")
    _non_blank(candidate_artifact_hash, "candidate_artifact_hash")
    if benchmark_kind not in BENCHMARK_KINDS:
        raise O5BenchmarkError(f"benchmark_kind must be one of {BENCHMARK_KINDS}")
    if seat_swap_policy not in {"ALWAYS_SWAP", "NO_SWAP"}:
        raise O5BenchmarkError("seat_swap_policy must be ALWAYS_SWAP or NO_SWAP")
    if not seed_set or any(type(seed) is not int for seed in seed_set):
        raise O5BenchmarkError("seed_set must be a non-empty tuple of ints")
    if len(set(seed_set)) != len(seed_set):
        # A duplicate seed would make the Evaluation Runner replay (and
        # resume-return) the exact same completed games twice, silently
        # double-counting them in every aggregate -- reproduced and
        # confirmed during an independent audit.
        raise O5BenchmarkError("seed_set must not contain duplicate seeds")
    if type(game_count) is not int or game_count <= 0:
        raise O5BenchmarkError("game_count must be a positive integer")
    if seat_swap_policy == "ALWAYS_SWAP" and game_count % 2:
        raise O5BenchmarkError("game_count must be even when seat_swap_policy is ALWAYS_SWAP")
    base = build_benchmark_manifest(
        population, active_exact_decks=active_exact_decks, runnable_families=runnable_families, verified_links=verified_links
    )
    return VersionedBenchmarkManifest(
        schema_version=O5_BENCHMARK_MANIFEST_SCHEMA_VERSION,
        benchmark_id=benchmark_id,
        benchmark_version=benchmark_version,
        benchmark_kind=benchmark_kind,
        created_at=created_at,
        source_snapshot_ids=tuple(source_snapshot_ids),
        deck_registry_version=deck_registry_version,
        policy_pack_version=policy_pack_version,
        agent_family_versions=dict(agent_family_versions),
        ruleset_version=ruleset_version,
        cabt_version=cabt_version,
        seed_set=tuple(seed_set),
        seat_swap_policy=seat_swap_policy,
        game_count=game_count,
        logical_pair_count=game_count // 2,
        time_budget_seconds=time_budget_seconds,
        candidate_artifact_id=candidate_artifact_id,
        candidate_artifact_hash=candidate_artifact_hash,
        baseline_artifact_ids=tuple(baseline_artifact_ids),
        environment=environment,
        commit=commit,
        status=base["status"],
        sets=_sets_for_kind(base["sets"], benchmark_kind),
        requirements=dict(base["requirements"]),
    )


__all__ = [
    "BENCHMARK_KINDS",
    "NOT_APPLICABLE",
    "O5BenchmarkError",
    "O5_BENCHMARK_MANIFEST_SCHEMA_VERSION",
    "VersionedBenchmarkManifest",
    "build_versioned_benchmark_manifest",
]
