"""complete experience chunks だけから immutable replay version を作る。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch

from .contracts import (
    LeagueContractError,
    atomic_write_json,
    content_id,
    file_sha256,
    load_json,
    utc_now,
)
from .experience import read_experience_chunk


def _transition(payload: Mapping[str, Any]) -> R2D3Transition:
    normalized = dict(payload)
    normalized["public_state"] = tuple(normalized["public_state"])
    normalized["legal_actions"] = tuple(
        tuple(action) for action in normalized["legal_actions"]
    )
    if normalized.get("hidden_state") is not None:
        normalized["hidden_state"] = tuple(normalized["hidden_state"])
    return R2D3Transition(**normalized)


def _sequence(payload: Mapping[str, Any]) -> SequenceBatch:
    return SequenceBatch(
        burn_in=tuple(_transition(item) for item in payload["burn_in"]),
        learner=tuple(_transition(item) for item in payload["learner"]),
        lookahead=tuple(_transition(item) for item in payload.get("lookahead", [])),
        priority=float(payload["priority"]),
        sequence_id=str(payload["sequence_id"]),
        episode_id=str(payload.get("episode_id", "")),
    )


@dataclass(frozen=True, slots=True)
class ReplayDatasetVersion:
    replay_dataset_version_id: str
    population_epoch_id: str
    experience_chunk_ids: tuple[str, ...]
    sequence_count: int
    replay_sha256: str
    replay_path: Path
    manifest_path: Path
    parent_replay_dataset_version_id: str | None = None


def _validate_manifest_identity(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") == 2:
        required = {
            "population_epoch_id",
            "source_replay",
            "sequence_count",
            "replay_file",
            "replay_sha256",
        }
        missing = required.difference(manifest)
        if missing:
            raise LeagueContractError(
                f"imported replay manifest misses identity fields: {sorted(missing)}"
            )
        source = manifest["source_replay"]
        if not isinstance(source, Mapping):
            raise LeagueContractError("imported replay source_replay must be an object")
        source_required = {
            "label",
            "source_manifest_sha256",
            "source_replay_sha256",
            "source_schema",
        }
        source_missing = source_required.difference(source)
        if source_missing:
            raise LeagueContractError(
                "imported replay source metadata misses "
                f"{sorted(source_missing)}"
            )
        identity = {
            "population_epoch_id": manifest["population_epoch_id"],
            "source_replay": dict(source),
            "replay_sha256": manifest["replay_sha256"],
            "sequence_count": manifest["sequence_count"],
        }
        if content_id("imported-replay-dataset-v1", identity) != manifest.get(
            "replay_dataset_version_id"
        ):
            raise LeagueContractError("imported replay manifest identity mismatch")
        return
    required = {
        "population_epoch_id",
        "parent_replay_dataset_version_id",
        "experience_chunk_ids",
        "record_hashes",
        "alpha",
        "demonstration_bonus",
        "capacity",
    }
    missing = required.difference(manifest)
    if missing:
        raise LeagueContractError(
            f"sealed replay manifest misses identity fields: {sorted(missing)}"
        )
    identity = {key: manifest[key] for key in required}
    if content_id("replay-dataset-version-v1", identity) != manifest.get(
        "replay_dataset_version_id"
    ):
        raise LeagueContractError("sealed replay manifest identity mismatch")


def _validate_imported_replay(replay: PrioritizedSequenceReplay) -> None:
    """現在の semantic R2D3 learner が読める replay だけを採用する。"""

    if not len(replay):
        raise LeagueContractError("imported replay must contain at least one sequence")
    seen_sequence_ids: set[str] = set()
    for sequence in replay.sequences():
        if not sequence.sequence_id or sequence.sequence_id in seen_sequence_ids:
            raise LeagueContractError("imported replay has duplicate or empty sequence IDs")
        seen_sequence_ids.add(sequence.sequence_id)
        transitions = (*sequence.burn_in, *sequence.learner, *sequence.lookahead)
        if not sequence.learner or not transitions:
            raise LeagueContractError("imported replay has an empty learner sequence")
        for transition in transitions:
            if len(transition.public_state) != 128:
                raise LeagueContractError("imported replay state size is not 128")
            if not transition.legal_actions:
                raise LeagueContractError("imported replay has no legal actions")
            if not 0 <= transition.selected_action < len(transition.legal_actions):
                raise LeagueContractError("imported replay selected action is illegal")
            if any(len(action) != 64 for action in transition.legal_actions):
                raise LeagueContractError("imported replay action size is not 64")
            if not math.isfinite(float(transition.reward)) or not math.isfinite(
                float(transition.discount)
            ):
                raise LeagueContractError("imported replay has non-finite transition data")
            if "kaggle" in transition.behavior_source.lower():
                raise LeagueContractError(
                    "Kaggle replay actions cannot be imported as training labels"
                )


def import_replay_dataset(
    *,
    source_replay_path: Path,
    source_manifest_path: Path,
    output_root: Path,
    population_epoch_id: str,
    source_label: str,
) -> ReplayDatasetVersion:
    """外部で凍結済みの互換 replay を、copy-once の league version として採用する。

    sequence を JSONL へ再展開しないため、長期実験で得た replay を二重保存しない。
    """

    source_replay_path = Path(source_replay_path)
    source_manifest_path = Path(source_manifest_path)
    source_manifest = load_json(source_manifest_path)
    source_schema = str(source_manifest.get("schema", ""))
    if source_schema not in {
        "r2d3-e2e-replay-manifest-v1",
        "r2d3-e2e-replay-manifest-v2",
    }:
        raise LeagueContractError("unsupported imported replay schema")
    source_replay_sha256 = file_sha256(source_replay_path)
    if source_manifest.get("replay_sha256") != source_replay_sha256:
        raise LeagueContractError("imported replay checksum differs from source manifest")
    replay = PrioritizedSequenceReplay.load(source_replay_path)
    _validate_imported_replay(replay)
    source = {
        "label": source_label,
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "source_replay_sha256": source_replay_sha256,
        "source_schema": source_schema,
    }
    identity = {
        "population_epoch_id": population_epoch_id,
        "source_replay": source,
        "replay_sha256": source_replay_sha256,
        "sequence_count": len(replay),
    }
    replay_dataset_version_id = content_id("imported-replay-dataset-v1", identity)
    version_dir = Path(output_root) / replay_dataset_version_id
    replay_path = version_dir / "replay.json"
    manifest_path = version_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        _validate_manifest_identity(manifest)
        if file_sha256(replay_path) != manifest.get("replay_sha256"):
            raise LeagueContractError("imported replay copy is corrupt")
        return ReplayDatasetVersion(
            replay_dataset_version_id=replay_dataset_version_id,
            population_epoch_id=population_epoch_id,
            experience_chunk_ids=(),
            sequence_count=int(manifest["sequence_count"]),
            replay_sha256=str(manifest["replay_sha256"]),
            replay_path=replay_path,
            manifest_path=manifest_path,
        )
    version_dir.mkdir(parents=True, exist_ok=True)
    temporary_replay = version_dir / ".replay.json.importing"
    try:
        shutil.copyfile(source_replay_path, temporary_replay)
        if file_sha256(temporary_replay) != source_replay_sha256:
            raise LeagueContractError("imported replay copy checksum mismatch")
        temporary_replay.replace(replay_path)
    finally:
        temporary_replay.unlink(missing_ok=True)
    shutil.copyfile(source_manifest_path, version_dir / "source_manifest.json")
    manifest = {
        "schema_version": 2,
        "replay_dataset_version_id": replay_dataset_version_id,
        **identity,
        "experience_chunk_ids": [],
        "parent_replay_dataset_version_id": None,
        "replay_file": replay_path.name,
        "status": "SEALED",
        "sealed_at": utc_now(),
    }
    atomic_write_json(manifest_path, manifest)
    return ReplayDatasetVersion(
        replay_dataset_version_id=replay_dataset_version_id,
        population_epoch_id=population_epoch_id,
        experience_chunk_ids=(),
        sequence_count=len(replay),
        replay_sha256=source_replay_sha256,
        replay_path=replay_path,
        manifest_path=manifest_path,
    )


def seal_replay_dataset(
    *,
    chunk_manifests: Iterable[Path],
    output_root: Path,
    population_epoch_id: str,
    capacity: int | None = None,
    alpha: float = 0.6,
    demonstration_bonus: float = 1.0,
    parent_replay_manifest: Path | None = None,
) -> ReplayDatasetVersion:
    chunks = []
    records = []
    for manifest_path in sorted(Path(path) for path in chunk_manifests):
        manifest, chunk_records = read_experience_chunk(manifest_path)
        if manifest["population_epoch_id"] != population_epoch_id:
            raise LeagueContractError("replay seal cannot cross population epochs")
        chunks.append(manifest)
        records.extend(chunk_records)
    if not chunks or not records:
        raise LeagueContractError("replay seal requires complete experience chunks")
    ordered_records = sorted(
        records,
        key=lambda record: (
            record["game_id"],
            record["sequence"]["sequence_id"],
        ),
    )
    parent_sequences: tuple[SequenceBatch, ...] = ()
    parent_version_id: str | None = None
    inherited_chunk_ids: list[str] = []
    if parent_replay_manifest is not None:
        parent_manifest = load_json(Path(parent_replay_manifest))
        if parent_manifest.get("status") != "SEALED":
            raise LeagueContractError("parent replay is not sealed")
        parent_version_id = str(parent_manifest["replay_dataset_version_id"])
        parent_sequences = load_sealed_replay(parent_replay_manifest).sequences()
        inherited_chunk_ids = list(parent_manifest.get("experience_chunk_ids", []))
    sequence_ids = [
        *(sequence.sequence_id for sequence in parent_sequences),
        *(record["sequence"]["sequence_id"] for record in ordered_records),
    ]
    if len(sequence_ids) != len(set(sequence_ids)):
        raise LeagueContractError("replay seal sees duplicate sequence IDs")
    sequence_count = len(parent_sequences) + len(ordered_records)
    effective_capacity = max(capacity or sequence_count, sequence_count)
    identity = {
        "population_epoch_id": population_epoch_id,
        "parent_replay_dataset_version_id": parent_version_id,
        "experience_chunk_ids": sorted(
            {
                *inherited_chunk_ids,
                *(manifest["experience_chunk_id"] for manifest in chunks),
            }
        ),
        "record_hashes": [
            content_id("experience-record-v1", record) for record in ordered_records
        ],
        "alpha": alpha,
        "demonstration_bonus": demonstration_bonus,
        "capacity": effective_capacity,
    }
    replay_dataset_version_id = content_id("replay-dataset-version-v1", identity)
    version_dir = Path(output_root) / replay_dataset_version_id
    replay_path = version_dir / "replay.json"
    manifest_path = version_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        _validate_manifest_identity(manifest)
        if manifest.get("replay_dataset_version_id") != replay_dataset_version_id:
            raise LeagueContractError("replay version ID collision")
        if file_sha256(replay_path) != manifest.get("replay_sha256"):
            raise LeagueContractError("sealed replay is corrupt")
        return ReplayDatasetVersion(
            replay_dataset_version_id=replay_dataset_version_id,
            population_epoch_id=population_epoch_id,
            experience_chunk_ids=tuple(identity["experience_chunk_ids"]),
            sequence_count=int(manifest["sequence_count"]),
            replay_sha256=manifest["replay_sha256"],
            replay_path=replay_path,
            manifest_path=manifest_path,
            parent_replay_dataset_version_id=parent_version_id,
        )
    version_dir.mkdir(parents=True, exist_ok=True)
    replay = PrioritizedSequenceReplay(
        effective_capacity,
        alpha=alpha,
        demonstration_bonus=demonstration_bonus,
    )
    for sequence in parent_sequences:
        replay.add(sequence, priority=sequence.priority)
    for record in ordered_records:
        sequence = _sequence(record["sequence"])
        replay.add(sequence, priority=sequence.priority)
    replay_metadata = replay.save(replay_path)
    manifest = {
        "schema_version": 1,
        "replay_dataset_version_id": replay_dataset_version_id,
        **identity,
        "sequence_count": len(replay),
        "replay_file": replay_path.name,
        "replay_sha256": replay_metadata["sha256"],
        "status": "SEALED",
        "sealed_at": utc_now(),
    }
    atomic_write_json(manifest_path, manifest)
    return ReplayDatasetVersion(
        replay_dataset_version_id=replay_dataset_version_id,
        population_epoch_id=population_epoch_id,
        experience_chunk_ids=tuple(identity["experience_chunk_ids"]),
        sequence_count=len(replay),
        replay_sha256=replay_metadata["sha256"],
        replay_path=replay_path,
        manifest_path=manifest_path,
        parent_replay_dataset_version_id=parent_version_id,
    )


def load_sealed_replay(manifest_path: Path) -> PrioritizedSequenceReplay:
    manifest = load_json(Path(manifest_path))
    if manifest.get("status") != "SEALED":
        raise LeagueContractError("learner can consume only sealed replay versions")
    _validate_manifest_identity(manifest)
    replay_path = Path(manifest_path).parent / manifest["replay_file"]
    if file_sha256(replay_path) != manifest.get("replay_sha256"):
        raise LeagueContractError("sealed replay hash mismatch")
    return PrioritizedSequenceReplay.load(replay_path)


def bootstrap_seat_coverage(
    chunk_manifests: Iterable[Path], opponent_instance_ids: Iterable[str]
) -> dict[str, list[int]]:
    coverage = {opponent_id: set() for opponent_id in opponent_instance_ids}
    for manifest_path in chunk_manifests:
        _manifest, records = read_experience_chunk(Path(manifest_path))
        for record in records:
            opponent_id = record["opponent_instance_id"]
            if opponent_id in coverage:
                coverage[opponent_id].add(int(record["candidate_seat"]))
    return {
        opponent_id: sorted(seats) for opponent_id, seats in sorted(coverage.items())
    }
