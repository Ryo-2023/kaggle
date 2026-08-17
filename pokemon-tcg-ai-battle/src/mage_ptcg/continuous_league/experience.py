"""Experience Generator が出力する complete chunk 契約。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.policy_learning.r2d3.sequence import SequenceBatch

from .contracts import (
    LeagueContractError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    content_id,
    file_sha256,
    load_json,
    utc_now,
)


def _validate_record(record: Mapping[str, Any]) -> None:
    required = {
        "game_id",
        "sequence",
        "candidate_runtime_policy_id",
        "opponent_instance_id",
        "population_epoch_id",
        "candidate_seat",
        "result",
    }
    missing = required.difference(record)
    if missing:
        raise LeagueContractError(f"experience record misses {sorted(missing)}")
    if int(record["candidate_seat"]) not in (0, 1):
        raise LeagueContractError("experience candidate_seat must be 0 or 1")
    if record["result"] not in {"win", "loss", "draw"}:
        raise LeagueContractError("experience result must be win/loss/draw")
    sequence = record["sequence"]
    if not isinstance(sequence, Mapping) or not sequence.get("sequence_id"):
        raise LeagueContractError("experience sequence is invalid")


def sequence_record(
    *,
    game_id: str,
    sequence: SequenceBatch,
    candidate_runtime_policy_id: str,
    opponent_instance_id: str,
    population_epoch_id: str,
    candidate_seat: int,
    result: str,
) -> dict[str, Any]:
    record = {
        "game_id": game_id,
        "sequence": asdict(sequence),
        "candidate_runtime_policy_id": candidate_runtime_policy_id,
        "opponent_instance_id": opponent_instance_id,
        "population_epoch_id": population_epoch_id,
        "candidate_seat": candidate_seat,
        "result": result,
    }
    _validate_record(record)
    return record


def write_experience_chunk(
    *,
    output_root: Path,
    records: Iterable[Mapping[str, Any]],
    collector_id: str,
) -> dict[str, Any]:
    ordered = sorted(
        (dict(record) for record in records),
        key=lambda record: (
            record["game_id"],
            record["sequence"]["sequence_id"],
        ),
    )
    if not ordered:
        raise LeagueContractError("experience chunk must not be empty")
    for record in ordered:
        _validate_record(record)
    sequence_ids = [record["sequence"]["sequence_id"] for record in ordered]
    if len(sequence_ids) != len(set(sequence_ids)):
        raise LeagueContractError("experience chunk sequence_id values must be unique")
    population_ids = {record["population_epoch_id"] for record in ordered}
    if len(population_ids) != 1:
        raise LeagueContractError("one chunk cannot cross population epochs")
    lines = b"".join(canonical_json_bytes(record) + b"\n" for record in ordered)
    chunk_identity = {
        "collector_id": collector_id,
        "population_epoch_id": next(iter(population_ids)),
        "record_hashes": [
            content_id("experience-record-v1", record) for record in ordered
        ],
    }
    experience_chunk_id = content_id("experience-chunk-v1", chunk_identity)
    chunk_dir = Path(output_root) / experience_chunk_id
    data_path = chunk_dir / "records.jsonl"
    manifest_path = chunk_dir / "manifest.json"
    manifest = {
        "schema_version": 1,
        "experience_chunk_id": experience_chunk_id,
        **chunk_identity,
        "record_count": len(ordered),
        "data_file": data_path.name,
        "data_sha256": __import__("hashlib").sha256(lines).hexdigest(),
        "status": "COMPLETE",
        "completed_at": utc_now(),
    }
    if manifest_path.exists():
        existing = load_json(manifest_path)
        for key in (
            "experience_chunk_id",
            "collector_id",
            "population_epoch_id",
            "record_hashes",
            "record_count",
            "data_file",
            "data_sha256",
            "status",
        ):
            if existing.get(key) != manifest[key]:
                raise LeagueContractError("experience chunk ID collision")
        if file_sha256(data_path) != manifest["data_sha256"]:
            raise LeagueContractError("existing experience chunk is corrupt")
        return existing
    chunk_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(data_path, lines)
    atomic_write_json(manifest_path, manifest)
    return manifest


def read_experience_chunk(manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = Path(manifest_path)
    manifest = load_json(manifest_path)
    if manifest.get("status") != "COMPLETE":
        raise LeagueContractError("experience chunk is not complete")
    data_path = manifest_path.parent / manifest["data_file"]
    if file_sha256(data_path) != manifest.get("data_sha256"):
        raise LeagueContractError("experience chunk data hash mismatch")
    records: list[dict[str, Any]] = []
    with data_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LeagueContractError(
                    f"corrupt experience JSONL line {line_number}: {exc}"
                ) from exc
            _validate_record(record)
            records.append(record)
    hashes = [content_id("experience-record-v1", record) for record in records]
    identity = {
        "collector_id": manifest["collector_id"],
        "population_epoch_id": manifest["population_epoch_id"],
        "record_hashes": hashes,
    }
    if (
        hashes != manifest.get("record_hashes")
        or content_id("experience-chunk-v1", identity)
        != manifest.get("experience_chunk_id")
    ):
        raise LeagueContractError("experience chunk identity mismatch")
    return manifest, records
