"""固定ベンチマーク、exposure cohort、決定的対戦表。"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .catalog import CatalogEntry, CatalogSnapshot
from .contracts import LeagueContractError, content_id, require_sha256
from .coverage import ReplayCoverage


class ExposureCohort(StrEnum):
    EXACT_KNOWN = "EXACT_KNOWN"
    KNOWN_DECK_NOVEL_POLICY = "KNOWN_DECK_NOVEL_POLICY"
    NOVEL_DECK_KNOWN_POLICY = "NOVEL_DECK_KNOWN_POLICY"
    NOVEL_DECK_KNOWN_ARCHETYPE = "NOVEL_DECK_KNOWN_ARCHETYPE"
    NOVEL_ARCHETYPE = "NOVEL_ARCHETYPE"
    FULLY_UNTOUCHED = "FULLY_UNTOUCHED"


@dataclass(frozen=True, slots=True)
class ExposureSnapshot:
    replay_dataset_version_id: str
    population_epoch_id: str
    opponent_instance_ids: frozenset[str]
    policy_ids: frozenset[str]
    deck_ids: frozenset[str]
    archetype_ids: frozenset[str]
    source_ids: frozenset[str]
    observed_policy_hashes: frozenset[str]
    observed_deck_hashes: frozenset[str]
    observed_opponent_pairs: frozenset[tuple[str, str]]
    exposure_snapshot_id: str

    @classmethod
    def build(
        cls,
        *,
        replay_dataset_version_id: str,
        population_epoch_id: str,
        entries: Iterable[CatalogEntry],
    ) -> "ExposureSnapshot":
        ordered = tuple(sorted(entries, key=lambda entry: entry.asset_id))
        identity = {
            "replay_dataset_version_id": replay_dataset_version_id,
            "population_epoch_id": population_epoch_id,
            "opponent_instance_ids": sorted(
                {entry.opponent_instance_id for entry in ordered}
            ),
            "policy_ids": sorted({entry.policy_id for entry in ordered}),
            "deck_ids": sorted({entry.deck_id for entry in ordered}),
            "archetype_ids": sorted(
                {entry.effective_archetype_id for entry in ordered}
            ),
            "source_ids": sorted({entry.source_id for entry in ordered}),
            "observed_policy_hashes": sorted(
                {entry.policy_hash for entry in ordered}
            ),
            "observed_deck_hashes": sorted({entry.deck_hash for entry in ordered}),
            "observed_opponent_pairs": sorted(
                {(entry.policy_hash, entry.deck_hash) for entry in ordered}
            ),
        }
        return cls(
            replay_dataset_version_id=replay_dataset_version_id,
            population_epoch_id=population_epoch_id,
            opponent_instance_ids=frozenset(identity["opponent_instance_ids"]),
            policy_ids=frozenset(identity["policy_ids"]),
            deck_ids=frozenset(identity["deck_ids"]),
            archetype_ids=frozenset(identity["archetype_ids"]),
            source_ids=frozenset(identity["source_ids"]),
            observed_policy_hashes=frozenset(identity["observed_policy_hashes"]),
            observed_deck_hashes=frozenset(identity["observed_deck_hashes"]),
            observed_opponent_pairs=frozenset(
                tuple(value) for value in identity["observed_opponent_pairs"]
            ),
            exposure_snapshot_id=content_id("exposure-snapshot-v2", identity),
        )

    @classmethod
    def from_replay_coverage(
        cls,
        *,
        coverage: ReplayCoverage,
        catalog: CatalogSnapshot,
    ) -> "ExposureSnapshot":
        """実測 Replay だけを evidence とする Exposure snapshot を構築する。"""

        catalog_pairs = {
            (entry.policy_hash, entry.deck_hash): entry for entry in catalog.entries
        }
        observed_entries = [
            catalog_pairs[pair] for pair in coverage.opponent_pairs if pair in catalog_pairs
        ]
        identity = {
            "replay_dataset_version_id": coverage.replay_dataset_version_id,
            "population_epoch_id": coverage.population_epoch_id,
            "opponent_instance_ids": sorted(
                entry.opponent_instance_id for entry in observed_entries
            ),
            "policy_ids": sorted(entry.policy_id for entry in observed_entries),
            "deck_ids": sorted(entry.deck_id for entry in observed_entries),
            "archetype_ids": sorted(coverage.families),
            "source_ids": sorted(coverage.source_lineages),
            "observed_policy_hashes": sorted(coverage.policy_hashes),
            "observed_deck_hashes": sorted(coverage.deck_hashes),
            "observed_opponent_pairs": [list(pair) for pair in sorted(coverage.opponent_pairs)],
        }
        return cls(
            replay_dataset_version_id=coverage.replay_dataset_version_id,
            population_epoch_id=coverage.population_epoch_id,
            opponent_instance_ids=frozenset(identity["opponent_instance_ids"]),
            policy_ids=frozenset(identity["policy_ids"]),
            deck_ids=frozenset(identity["deck_ids"]),
            archetype_ids=frozenset(identity["archetype_ids"]),
            source_ids=frozenset(identity["source_ids"]),
            observed_policy_hashes=frozenset(identity["observed_policy_hashes"]),
            observed_deck_hashes=frozenset(identity["observed_deck_hashes"]),
            observed_opponent_pairs=frozenset(
                tuple(value) for value in identity["observed_opponent_pairs"]
            ),
            exposure_snapshot_id=content_id("exposure-snapshot-v2", identity),
        )

    def classify(self, entry: CatalogEntry) -> ExposureCohort:
        if (entry.policy_hash, entry.deck_hash) in self.observed_opponent_pairs:
            return ExposureCohort.EXACT_KNOWN
        if entry.deck_hash in self.observed_deck_hashes:
            return ExposureCohort.KNOWN_DECK_NOVEL_POLICY
        if entry.policy_hash in self.observed_policy_hashes:
            return ExposureCohort.NOVEL_DECK_KNOWN_POLICY
        if entry.effective_archetype_id in self.archetype_ids:
            return ExposureCohort.NOVEL_DECK_KNOWN_ARCHETYPE
        if entry.source_id in self.source_ids:
            return ExposureCohort.NOVEL_ARCHETYPE
        return ExposureCohort.FULLY_UNTOUCHED

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_dataset_version_id": self.replay_dataset_version_id,
            "population_epoch_id": self.population_epoch_id,
            "opponent_instance_ids": sorted(self.opponent_instance_ids),
            "policy_ids": sorted(self.policy_ids),
            "deck_ids": sorted(self.deck_ids),
            "archetype_ids": sorted(self.archetype_ids),
            "source_ids": sorted(self.source_ids),
            "observed_policy_hashes": sorted(self.observed_policy_hashes),
            "observed_deck_hashes": sorted(self.observed_deck_hashes),
            "observed_opponent_pairs": [
                list(pair) for pair in sorted(self.observed_opponent_pairs)
            ],
            "exposure_snapshot_id": self.exposure_snapshot_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExposureSnapshot":
        legacy = "observed_opponent_pairs" not in payload
        identity = {
            "replay_dataset_version_id": str(
                payload["replay_dataset_version_id"]
            ),
            "population_epoch_id": str(payload["population_epoch_id"]),
            "opponent_instance_ids": sorted(payload["opponent_instance_ids"]),
            "policy_ids": sorted(payload["policy_ids"]),
            "deck_ids": sorted(payload["deck_ids"]),
            "archetype_ids": sorted(payload["archetype_ids"]),
            "source_ids": sorted(payload["source_ids"]),
        }
        if legacy:
            identity.update(
                {
                    "observed_policy_hashes": [],
                    "observed_deck_hashes": [],
                    "observed_opponent_pairs": [],
                }
            )
            expected = content_id("exposure-snapshot-v1", {
                key: identity[key]
                for key in (
                    "replay_dataset_version_id", "population_epoch_id",
                    "opponent_instance_ids", "policy_ids", "deck_ids",
                    "archetype_ids", "source_ids",
                )
            })
        else:
            identity.update(
                {
                    "observed_policy_hashes": sorted(payload["observed_policy_hashes"]),
                    "observed_deck_hashes": sorted(payload["observed_deck_hashes"]),
                    "observed_opponent_pairs": sorted(
                        [list(pair) for pair in payload["observed_opponent_pairs"]]
                    ),
                }
            )
            expected = content_id("exposure-snapshot-v2", identity)
        if payload.get("exposure_snapshot_id") != expected:
            raise LeagueContractError("exposure snapshot hash mismatch")
        return cls(
            replay_dataset_version_id=identity["replay_dataset_version_id"],
            population_epoch_id=identity["population_epoch_id"],
            opponent_instance_ids=frozenset(identity["opponent_instance_ids"]),
            policy_ids=frozenset(identity["policy_ids"]),
            deck_ids=frozenset(identity["deck_ids"]),
            archetype_ids=frozenset(identity["archetype_ids"]),
            source_ids=frozenset(identity["source_ids"]),
            observed_policy_hashes=frozenset(identity["observed_policy_hashes"]),
            observed_deck_hashes=frozenset(identity["observed_deck_hashes"]),
            observed_opponent_pairs=frozenset(
                tuple(value) for value in identity["observed_opponent_pairs"]
            ),
            exposure_snapshot_id=expected,
        )


@dataclass(frozen=True, slots=True)
class SubjectDeck:
    deck_id: str
    deck_path: str
    deck_hash: str

    def __post_init__(self) -> None:
        if not self.deck_id or not self.deck_path:
            raise LeagueContractError("subject deck identity and path are required")
        require_sha256(self.deck_hash, "subject_deck.deck_hash")


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    schema_version: int
    name: str
    catalog_snapshot_id: str
    subject_decks: tuple[SubjectDeck, ...]
    opponent_instance_ids: tuple[str, ...]
    repetitions: int
    execution_blocks: tuple[str, ...]
    base_seed: int
    sealed: bool
    benchmark_id: str

    @classmethod
    def build(
        cls,
        *,
        name: str,
        catalog: CatalogSnapshot,
        subject_decks: Iterable[SubjectDeck],
        opponent_instance_ids: Iterable[str],
        repetitions: int,
        base_seed: int,
        execution_blocks: Iterable[str] = ("main",),
        sealed: bool = False,
    ) -> "BenchmarkManifest":
        decks = tuple(sorted(subject_decks, key=lambda deck: deck.deck_id))
        opponents = tuple(sorted(set(opponent_instance_ids)))
        blocks = tuple(execution_blocks)
        if not name or not decks or not opponents or not blocks:
            raise LeagueContractError(
                "benchmark requires name, decks, opponents, and execution blocks"
            )
        if repetitions <= 0:
            raise LeagueContractError("benchmark repetitions must be positive")
        if len({deck.deck_id for deck in decks}) != len(decks):
            raise LeagueContractError("subject deck_id values must be unique")
        for opponent_id in opponents:
            entry = catalog.get_instance(opponent_id)
            allowed_roles = (
                {"BENCHMARK_SEALED"}
                if sealed
                else {
                    "TRAINING_ACTIVE",
                    "TRAINING_RESERVE",
                    "BENCHMARK_VISIBLE",
                }
            )
            if entry.role not in allowed_roles:
                raise LeagueContractError(
                    f"opponent {entry.asset_id} is not eligible for "
                    f"{'sealed' if sealed else 'visible'} benchmark"
                )
        identity = {
            "schema_version": 1,
            "name": name,
            "catalog_snapshot_id": catalog.catalog_snapshot_id,
            "subject_decks": [asdict(deck) for deck in decks],
            "opponent_instance_ids": list(opponents),
            "repetitions": repetitions,
            "execution_blocks": list(blocks),
            "base_seed": base_seed,
            "sealed": sealed,
        }
        return cls(
            schema_version=1,
            name=name,
            catalog_snapshot_id=catalog.catalog_snapshot_id,
            subject_decks=decks,
            opponent_instance_ids=opponents,
            repetitions=repetitions,
            execution_blocks=blocks,
            base_seed=base_seed,
            sealed=sealed,
            benchmark_id=content_id("benchmark-v1", identity),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "catalog_snapshot_id": self.catalog_snapshot_id,
            "subject_decks": [asdict(deck) for deck in self.subject_decks],
            "opponent_instance_ids": list(self.opponent_instance_ids),
            "repetitions": self.repetitions,
            "execution_blocks": list(self.execution_blocks),
            "base_seed": self.base_seed,
            "sealed": self.sealed,
            "benchmark_id": self.benchmark_id,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], catalog: CatalogSnapshot
    ) -> "BenchmarkManifest":
        rebuilt = cls.build(
            name=str(payload["name"]),
            catalog=catalog,
            subject_decks=(
                SubjectDeck(**deck_payload)
                for deck_payload in payload["subject_decks"]
            ),
            opponent_instance_ids=payload["opponent_instance_ids"],
            repetitions=int(payload["repetitions"]),
            execution_blocks=payload["execution_blocks"],
            base_seed=int(payload["base_seed"]),
            sealed=bool(payload.get("sealed", False)),
        )
        if rebuilt.benchmark_id != payload.get("benchmark_id"):
            raise LeagueContractError("benchmark manifest hash mismatch")
        return rebuilt


@dataclass(frozen=True, slots=True)
class ScheduledGame:
    benchmark_id: str
    runtime_policy_id: str
    subject_deck_id: str
    opponent_instance_id: str
    seat: str
    repetition_index: int
    execution_block: str
    env_seed: int
    game_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_schedule(
    benchmark: BenchmarkManifest, runtime_policy_id: str
) -> tuple[ScheduledGame, ...]:
    """seat pair を保ったまま順序だけ seed で決定する。"""

    require_sha256(runtime_policy_id, "runtime_policy_id")
    pairs: list[tuple[ScheduledGame, ScheduledGame]] = []
    for deck in benchmark.subject_decks:
        for opponent_id in benchmark.opponent_instance_ids:
            for block in benchmark.execution_blocks:
                for repetition_index in range(benchmark.repetitions):
                    games: list[ScheduledGame] = []
                    for seat in ("subject_first", "subject_second"):
                        identity = {
                            "benchmark_id": benchmark.benchmark_id,
                            "runtime_policy_id": runtime_policy_id,
                            "subject_deck_id": deck.deck_id,
                            "opponent_instance_id": opponent_id,
                            "seat": seat,
                            "repetition_index": repetition_index,
                            "execution_block": block,
                        }
                        game_key = content_id("benchmark-game-v1", identity)
                        pairing_identity = {
                            key: value
                            for key, value in identity.items()
                            if key != "runtime_policy_id"
                        }
                        env_seed = (
                            int(
                                content_id(
                                    "benchmark-agent-seed-v1", pairing_identity
                                )[:16],
                                16,
                            )
                            % (2**31 - 1)
                        )
                        games.append(
                            ScheduledGame(
                                **identity,
                                env_seed=env_seed,
                                game_key=game_key,
                            )
                        )
                    pairs.append((games[0], games[1]))
    random.Random(benchmark.base_seed).shuffle(pairs)
    return tuple(game for pair in pairs for game in pair)


def coverage_matrix(
    benchmark: BenchmarkManifest,
    catalog: CatalogSnapshot,
    exposure: ExposureSnapshot,
) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for opponent_id in benchmark.opponent_instance_ids:
        entry = catalog.get_instance(opponent_id)
        cohort = exposure.classify(entry).value
        row = matrix.setdefault(entry.effective_archetype_id, {})
        row[cohort] = row.get(cohort, 0) + 1
    return matrix
