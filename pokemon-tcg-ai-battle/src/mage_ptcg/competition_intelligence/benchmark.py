"""Immutable fixture-scale benchmark manifests for O1-6."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .canonical import digest

BENCHMARK_SCHEMA_VERSION = "intelligence-benchmark-v1"


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    kind: str
    snapshot_hashes: tuple[str, ...]
    episode_ids: tuple[str, ...]
    opponents: tuple[str, ...]
    seat_assignments: tuple[str, ...]
    seeds: tuple[int, ...]
    evaluation_config: dict[str, Any]
    unknown_meta_allocation: float
    surrogate_versions: tuple[str, ...]
    scenario_hashes: tuple[str, ...]
    manifest_id: str
    content_hash: str

    def payload(self) -> dict[str, Any]:
        return {"schema_version": BENCHMARK_SCHEMA_VERSION, "kind": self.kind, "snapshot_hashes": list(self.snapshot_hashes),
                "episode_ids": list(self.episode_ids), "opponents": list(self.opponents), "seat_assignments": list(self.seat_assignments),
                "seeds": list(self.seeds), "evaluation_config": self.evaluation_config, "unknown_meta_allocation": self.unknown_meta_allocation,
                "surrogate_versions": list(self.surrogate_versions), "scenario_hashes": list(self.scenario_hashes)}


def build_benchmark_manifest(kind: str, *, snapshot_hashes: Iterable[str], episode_ids: Iterable[str], opponents: Iterable[str],
                             seeds: Iterable[int], evaluation_config: dict[str, Any], unknown_meta_allocation: float,
                             surrogate_versions: Iterable[str]) -> BenchmarkManifest:
    ids, opponent_ids, seed_values = tuple(sorted(set(episode_ids))), tuple(sorted(set(opponents))), tuple(sorted(set(seeds)))
    seats = ("first", "second")
    scenarios = tuple(sorted(digest({"episode_id": item, "seeds": seed_values, "seats": seats}, domain="benchmark-scenario") for item in ids))
    base = {"schema_version": BENCHMARK_SCHEMA_VERSION, "kind": kind, "snapshot_hashes": sorted(set(snapshot_hashes)), "episode_ids": list(ids),
            "opponents": list(opponent_ids), "seat_assignments": list(seats), "seeds": list(seed_values),
            "evaluation_config": evaluation_config, "unknown_meta_allocation": unknown_meta_allocation,
            "surrogate_versions": sorted(set(surrogate_versions)), "scenario_hashes": list(scenarios)}
    content_hash = digest(base, domain="benchmark-manifest")
    return BenchmarkManifest(kind=kind, snapshot_hashes=tuple(base["snapshot_hashes"]), episode_ids=ids, opponents=opponent_ids,
                             seat_assignments=seats, seeds=seed_values, evaluation_config=evaluation_config,
                             unknown_meta_allocation=unknown_meta_allocation, surrogate_versions=tuple(base["surrogate_versions"]),
                             scenario_hashes=scenarios, manifest_id="benchmark-" + content_hash[:24], content_hash=content_hash)
