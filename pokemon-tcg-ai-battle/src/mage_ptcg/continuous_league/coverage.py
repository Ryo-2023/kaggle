"""封印済み Replay に実際に含まれる対戦相手の被覆率。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.policy_learning.r2d3.sequence import SequenceBatch

from .contracts import LeagueContractError, content_id, load_json
from .replay_sealer import load_sealed_replay


@dataclass(frozen=True, slots=True)
class ReplayPairCoverage:
    """一つの opponent policy/deck 組に対する学習可能な Replay の量。"""

    policy_hash: str
    deck_hash: str
    source_lineage: str
    family: str
    sequence_count: int
    learner_transition_count: int
    episode_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReplayCoverage:
    replay_dataset_version_id: str
    population_epoch_id: str
    pairs: tuple[ReplayPairCoverage, ...]
    coverage_id: str

    @classmethod
    def from_sequences(
        cls,
        *,
        replay_dataset_version_id: str,
        population_epoch_id: str,
        sequences: Iterable[SequenceBatch],
    ) -> "ReplayCoverage":
        aggregates: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for sequence in sequences:
            learner = sequence.learner
            if not learner:
                raise LeagueContractError("replay coverage requires learner transitions")
            first = learner[0]
            identity = (
                first.opponent_policy_hash,
                first.opponent_deck_hash,
                first.opponent_source_lineage,
                first.opponent_family,
            )
            if not all(identity == (
                step.opponent_policy_hash,
                step.opponent_deck_hash,
                step.opponent_source_lineage,
                step.opponent_family,
            ) for step in learner):
                raise LeagueContractError(
                    "a learner sequence cannot contain multiple opponent identities"
                )
            if not all(identity[:2]):
                raise LeagueContractError("replay coverage found an empty opponent hash")
            aggregate = aggregates.setdefault(
                identity,
                {"sequence_count": 0, "learner_transition_count": 0, "episodes": set()},
            )
            aggregate["sequence_count"] += 1
            aggregate["learner_transition_count"] += len(learner)
            aggregate["episodes"].add(sequence.episode_id or sequence.sequence_id)
        pairs = tuple(
            ReplayPairCoverage(
                policy_hash=identity[0],
                deck_hash=identity[1],
                source_lineage=identity[2],
                family=identity[3],
                sequence_count=int(values["sequence_count"]),
                learner_transition_count=int(values["learner_transition_count"]),
                episode_count=len(values["episodes"]),
            )
            for identity, values in sorted(aggregates.items())
        )
        if not pairs:
            raise LeagueContractError("replay coverage requires at least one sequence")
        identity = {
            "replay_dataset_version_id": replay_dataset_version_id,
            "population_epoch_id": population_epoch_id,
            "pairs": [pair.to_dict() for pair in pairs],
        }
        return cls(
            replay_dataset_version_id=replay_dataset_version_id,
            population_epoch_id=population_epoch_id,
            pairs=pairs,
            coverage_id=content_id("replay-coverage-v1", identity),
        )

    @classmethod
    def from_replay_manifest(cls, manifest_path: Path) -> "ReplayCoverage":
        manifest = load_json(manifest_path)
        return cls.from_sequences(
            replay_dataset_version_id=str(manifest["replay_dataset_version_id"]),
            population_epoch_id=str(manifest["population_epoch_id"]),
            sequences=load_sealed_replay(manifest_path).sequences(),
        )

    @property
    def opponent_pairs(self) -> frozenset[tuple[str, str]]:
        return frozenset((pair.policy_hash, pair.deck_hash) for pair in self.pairs)

    @property
    def policy_hashes(self) -> frozenset[str]:
        return frozenset(pair.policy_hash for pair in self.pairs)

    @property
    def deck_hashes(self) -> frozenset[str]:
        return frozenset(pair.deck_hash for pair in self.pairs)

    @property
    def source_lineages(self) -> frozenset[str]:
        return frozenset(pair.source_lineage for pair in self.pairs)

    @property
    def families(self) -> frozenset[str]:
        return frozenset(pair.family for pair in self.pairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "replay_dataset_version_id": self.replay_dataset_version_id,
            "population_epoch_id": self.population_epoch_id,
            "pairs": [pair.to_dict() for pair in self.pairs],
            "coverage_id": self.coverage_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReplayCoverage":
        pairs = tuple(
            ReplayPairCoverage(**dict(item)) for item in payload["pairs"]
        )
        identity = {
            "replay_dataset_version_id": str(payload["replay_dataset_version_id"]),
            "population_epoch_id": str(payload["population_epoch_id"]),
            "pairs": [pair.to_dict() for pair in pairs],
        }
        expected = content_id("replay-coverage-v1", identity)
        if payload.get("coverage_id") != expected:
            raise LeagueContractError("replay coverage hash mismatch")
        return cls(
            replay_dataset_version_id=identity["replay_dataset_version_id"],
            population_epoch_id=identity["population_epoch_id"],
            pairs=pairs,
            coverage_id=expected,
        )
