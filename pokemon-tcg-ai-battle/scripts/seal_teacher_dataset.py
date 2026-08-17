"""収集した teacher records を封印済み training snapshot にする。

`collect_teacher_records_v1` が書いた局ごとの JSONL を dataset chunk へまとめ、
chunk ごとに manifest を作り、`iter_training_example_envelopes_v2` を通して
train/dev/test に分割した snapshot を出力する。

split は episode_id_hash / near_duplicate_id の連結成分単位で割り当てられるため
(training_snapshot_v1 の docstring)、同一局や近似重複が split を跨がない。ただし
全局に現れる位置 (開幕の決定など) は leak ではなく課題の定数であるため、連結には
使わない。連結に使うと 1 成分が corpus の約半分を占め、train の割合が抽選になる
(実測: 同一手法の 2 レーンで 67.8% と 15.4%)。

配分は既定で train 0.70 / development 0.15 / test 0.15 とし、snapshot へ
`split_weights` として記録する。正典 §9.3 の重複 cap も封印時に適用し、
`duplicate_cap` と各 example の `pre_cap_quality_weight` から検証できる。

## メモリを corpus サイズに比例させない

3000 局の corpus は 8.6 GiB (実測 t1-alakazam) あり、素朴に扱うと 2 箇所で落ちる。

1. 全 record を dict として保持すると **35.4 GB** になる (実測 3.7 倍)。よって
   record を貯めず、chunk 書き出しと manifest 生成の両方を 1 行ずつ流す。
2. `read_exact_regular_file` は読んだ file 全体を bytes で保持し、上限も
   4 GiB である。よって corpus を `--chunk-max-bytes` 以下の chunk 列として持つ。

chunk の切り方は split にも重複 cap にも影響しない。どちらも corpus 全体で決まる。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _atomic_write_canonical_json(path: Path, payload: dict) -> None:
    """Publish canonical bytes atomically for artifacts consumed as exact JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes_v2(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    build_local_dataset_manifest_streaming_v2,
    build_trusted_permission_set_v1,
    canonical_json_bytes_v2,
    validate_source_permission_manifest_v1,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    _snapshot_content_hash,
    _snapshot_identity,
    atomic_write_training_snapshot_v1,
    build_training_snapshot_v1,
    read_training_snapshot_v1,
    seal_sharded_corpus_v1,
)

# 1 chunk は read_exact_regular_file が丸ごと RAM に載せる。4 GiB の上限に対して
# 余裕を取り、封印中の peak RSS を chunk 1 個分に抑える。
DEFAULT_CHUNK_MAX_BYTES = 1024 * 1024 * 1024
COLLECTION_MANIFEST_SCHEMA_V2 = "specialist-teacher-dataset-manifest-v2"
COLLECTION_CONTRACT_SCHEMA_V2 = "specialist-teacher-collection-contract-v2"
GAME_RESULT_SCHEMA_V2 = "specialist-teacher-collection-game-result-v2"
_GAME_RESULT_KEYS_V2 = frozenset({
    "schema_version", "game_index", "seed", "seat", "opponent_id",
    "episode_id_hash", "status", "outcome", "record_path", "record_sha256",
    "record_count", "unlabelled", "omissions", "detail",
    "subject_deck_sha256", "teacher_policy_sha256", "permission_manifest_id",
})


class CollectionSealPreflightV2Error(ValueError):
    """Raised when collector-v2 provenance is not complete and immutable."""


@dataclass(frozen=True, slots=True)
class CollectionSealPreflightV2:
    collection: dict
    contract: dict
    permission: dict
    source_kind: str
    permission_content_hash: str
    provenance_artifacts: tuple[dict[str, str], ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CollectionSealPreflightV2Error(f"required file is unreadable: {path}") from exc
    return digest.hexdigest()


def _read_json_object(path: Path, *, field: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionSealPreflightV2Error(f"{field} is unreadable or invalid: {path}") from exc
    if type(value) is not dict:
        raise CollectionSealPreflightV2Error(f"{field} must be a JSON object: {path}")
    return value


def _resolve_declared_path(run_dir: Path, value: object, *, field: str) -> Path:
    if type(value) is not str or not value:
        raise CollectionSealPreflightV2Error(f"{field} must name a file")
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path.resolve()


def _require_same(value: object, expected: object, *, field: str) -> None:
    if value != expected:
        raise CollectionSealPreflightV2Error(
            f"collector v2 manifest/contract mismatch for {field}"
        )


def _permission_trusted_bytes(permission: dict) -> bytes:
    # The permission is trusted out-of-band at seal time.  Canonical bytes are
    # the exact representation accepted by build_trusted_permission_set_v1.
    return canonical_json_bytes_v2(permission)


def _preflight_collection_v2(
    run_dir: Path, *, expected_archetype_id: str
) -> CollectionSealPreflightV2:
    """Fail closed unless every requested collector-v2 game is DONE and bound.

    The raw record directory is not authoritative.  A record is sealable only
    when its current per-game sidecar names the exact bytes and an immutable
    attempt ledger entry records that attempt.  This prevents a partial JSONL
    left by a killed worker from being promoted on resume or seal.
    """
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "teacher_dataset_manifest.json"
    collection = _read_json_object(manifest_path, field="collection manifest")
    if collection.get("schema_version") != COLLECTION_MANIFEST_SCHEMA_V2:
        raise CollectionSealPreflightV2Error(
            "only specialist-teacher-dataset-manifest-v2 may be sealed"
        )
    _require_same(collection.get("archetype_id"), expected_archetype_id,
                  field="archetype_id")

    contract_path = _resolve_declared_path(
        run_dir, collection.get("collection_contract_path"),
        field="collection_contract_path",
    )
    if contract_path != (run_dir / "collection_contract.json").resolve():
        raise CollectionSealPreflightV2Error("collection contract must be inside its run directory")
    contract_sha = _sha256_file(contract_path)
    _require_same(collection.get("collection_contract_sha256"), contract_sha,
                  field="collection_contract_sha256")
    contract = _read_json_object(contract_path, field="collection contract")
    if contract.get("schema_version") != COLLECTION_CONTRACT_SCHEMA_V2:
        raise CollectionSealPreflightV2Error("collection contract is not v2")
    collector_source_path = _resolve_declared_path(
        run_dir, collection.get("collector_source_snapshot_path"),
        field="collector_source_snapshot_path",
    )
    if collector_source_path != (run_dir / "collector_source_snapshot.py").resolve():
        raise CollectionSealPreflightV2Error(
            "collector source snapshot must be inside its run directory"
        )
    _require_same(contract.get("collector_source_snapshot_path"),
                  collection.get("collector_source_snapshot_path"),
                  field="collector_source_snapshot_path")
    collector_source_sha = _sha256_file(collector_source_path)
    _require_same(collection.get("collector_source_sha256"), collector_source_sha,
                  field="manifest collector_source_sha256")
    _require_same(contract.get("collector_source_sha256"), collector_source_sha,
                  field="contract collector_source_sha256")

    cross_fields = (
        "run_name", "archetype_id", "subject_deck_csv_path",
        "subject_deck_file_sha256", "base_seed", "max_steps", "source_commit",
        "opponent_ids", "games_requested", "matchup_cap_fraction",
    )
    for field in cross_fields:
        _require_same(collection.get(field), contract.get(field), field=field)
    teacher = contract.get("teacher")
    if type(teacher) is not dict:
        raise CollectionSealPreflightV2Error("contract teacher asset is missing")
    _require_same(collection.get("teacher_id"), teacher.get("opponent_id"),
                  field="teacher_id")
    _require_same(collection.get("teacher_policy_hash"), teacher.get("policy_sha256"),
                  field="teacher_policy_hash")
    _require_same(collection.get("teacher_deck_file_sha256"),
                  teacher.get("deck_file_sha256"), field="teacher_deck_file_sha256")
    _require_same(collection.get("teacher_source_kind"),
                  contract.get("teacher_source_kind"), field="teacher_source_kind")
    source_kind = collection.get("teacher_source_kind")
    if source_kind not in {
        "pooled_external_submission_agent", "team_internal_agent",
    }:
        raise CollectionSealPreflightV2Error("unsupported collector-v2 teacher source_kind")

    subject_path = _resolve_declared_path(
        run_dir, collection.get("subject_deck_csv_path"), field="subject_deck_csv_path"
    )
    _require_same(_sha256_file(subject_path), collection.get("subject_deck_file_sha256"),
                  field="subject deck bytes")

    pool_root = _resolve_declared_path(run_dir, contract.get("pool_root"), field="pool_root")
    pool_manifest_path = pool_root / "pool_manifest.json"
    _require_same(_sha256_file(pool_manifest_path), contract.get("pool_manifest_sha256"),
                  field="pool_manifest_sha256")
    try:
        pool_rows = json.loads(pool_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionSealPreflightV2Error("pool manifest is invalid") from exc
    if type(pool_rows) is dict:
        pool_rows = pool_rows.get("opponents", pool_rows)
        if type(pool_rows) is dict:
            pool_rows = list(pool_rows.values())
    if type(pool_rows) is not list:
        raise CollectionSealPreflightV2Error("pool manifest has no opponent list")
    teacher_rows = [row for row in pool_rows if type(row) is dict
                    and row.get("id") == collection.get("teacher_id")]
    if len(teacher_rows) != 1:
        raise CollectionSealPreflightV2Error("teacher is not uniquely registered in pool")
    teacher_row = teacher_rows[0]
    expected_source = "public" if source_kind == "pooled_external_submission_agent" else "internal"
    _require_same(teacher_row.get("source"), expected_source, field="teacher source class")
    teacher_dir = pool_root / str(collection["teacher_id"])
    _require_same(_sha256_file(teacher_dir / "main.py"), collection.get("teacher_policy_hash"),
                  field="teacher policy bytes")
    _require_same(_sha256_file(teacher_dir / "deck.csv"),
                  collection.get("teacher_deck_file_sha256"), field="teacher deck bytes")

    permission = collection.get("permission_manifest")
    if type(permission) is not dict:
        raise CollectionSealPreflightV2Error("permission_manifest is missing")
    try:
        permission = validate_source_permission_manifest_v1(permission)
    except Exception as exc:
        raise CollectionSealPreflightV2Error("permission manifest identity does not verify") from exc
    _require_same(permission.get("permission_manifest_id"),
                  contract.get("permission_manifest_id"), field="permission_manifest_id")
    _require_same(permission.get("artifact_sha256"), collection.get("teacher_policy_hash"),
                  field="permission artifact_sha256")
    _require_same(permission.get("source_kind"), source_kind,
                  field="permission source_kind")
    if "training-local" not in permission.get("allowed_usages", []):
        raise CollectionSealPreflightV2Error("permission does not allow training-local")
    _require_same(permission.get("allowed_usages"), contract.get("allowed_usages"),
                  field="permission allowed_usages")
    permission_bytes = _permission_trusted_bytes(permission)
    permission_sha = hashlib.sha256(permission_bytes).hexdigest()
    _require_same(contract.get("permission_content_hash"), permission.get("content_hash"),
                  field="contract permission content hash")
    _require_same(collection.get("permission_content_hash"), permission.get("content_hash"),
                  field="manifest permission content hash")
    _require_same(contract.get("permission_trusted_bytes_sha256"), permission_sha,
                  field="contract permission trusted bytes SHA")
    _require_same(collection.get("permission_trusted_bytes_sha256"), permission_sha,
                  field="manifest permission trusted bytes SHA")

    omissions_path = _resolve_declared_path(
        run_dir, collection.get("omissions_path"), field="omissions_path"
    )
    if omissions_path != (run_dir / "omissions.jsonl").resolve():
        raise CollectionSealPreflightV2Error("omissions ledger must be inside its run directory")
    omissions_sha = _sha256_file(omissions_path)
    _require_same(collection.get("omissions_sha256"), omissions_sha,
                  field="omissions_sha256")
    omission_rows: list[dict] = []
    try:
        with omissions_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if type(row) is not dict:
                    raise ValueError("omission is not an object")
                omission_rows.append(row)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CollectionSealPreflightV2Error("omissions ledger is invalid") from exc
    _require_same(collection.get("decisions_unlabelled"), len(omission_rows),
                  field="decisions_unlabelled")

    games_requested = collection.get("games_requested")
    if type(games_requested) is not int or games_requested <= 0:
        raise CollectionSealPreflightV2Error("games_requested must be a positive int")
    records_dir = _resolve_declared_path(run_dir, collection.get("records_dir"),
                                         field="records_dir")
    if records_dir != (run_dir / "records").resolve():
        raise CollectionSealPreflightV2Error("records directory must be inside its run directory")
    sidecar_dir = run_dir / "game-results"
    sidecars = sorted(sidecar_dir.glob("game-*.result.json"))
    if len(sidecars) != games_requested:
        raise CollectionSealPreflightV2Error("one current game-result sidecar is required per game")
    _require_same(collection.get("game_result_sidecars"), len(sidecars),
                  field="game_result_sidecars")

    record_count_total = 0
    omission_count_total = 0
    outcome_counts: dict[str, int] = {}
    seat_counts = {"subject_first": 0, "subject_second": 0}
    matchup_record_counts: dict[str, int] = {}
    sidecar_omissions: list[dict] = []
    sidecar_indices: set[int] = set()
    opponent_ids = contract.get("opponent_ids")
    if type(opponent_ids) is not list or not opponent_ids or any(
        type(item) is not str or not item for item in opponent_ids
    ):
        raise CollectionSealPreflightV2Error("contract opponent_ids are invalid")
    for index in range(games_requested):
        path = sidecar_dir / f"game-{index:06d}.result.json"
        sidecar = _read_json_object(path, field="game result sidecar")
        if set(sidecar) != _GAME_RESULT_KEYS_V2:
            raise CollectionSealPreflightV2Error("game sidecar schema is not closed")
        if sidecar.get("schema_version") != GAME_RESULT_SCHEMA_V2:
            raise CollectionSealPreflightV2Error("game sidecar is not v2")
        _require_same(sidecar.get("game_index"), index, field="game sidecar index")
        if index in sidecar_indices:
            raise CollectionSealPreflightV2Error("duplicate game sidecar index")
        sidecar_indices.add(index)
        _require_same(sidecar.get("status"), "DONE", field="game sidecar status")
        if sidecar.get("outcome") not in {"win", "draw", "loss"}:
            raise CollectionSealPreflightV2Error("DONE game sidecar lacks terminal outcome")
        expected_seed = int(contract["base_seed"]) + index
        expected_seat = (index // len(opponent_ids)) % 2
        expected_opponent = opponent_ids[index % len(opponent_ids)]
        expected_episode = hashlib.sha256(
            (
                f"mage_ptcg:teacher-episode:v1\0{contract['run_name']}\0"
                f"{index}\0{expected_seed}"
            ).encode("utf-8")
        ).hexdigest()
        _require_same(sidecar.get("seed"), expected_seed, field="game sidecar seed")
        _require_same(sidecar.get("seat"), expected_seat, field="game sidecar seat")
        _require_same(sidecar.get("opponent_id"), expected_opponent,
                      field="game sidecar opponent")
        _require_same(sidecar.get("episode_id_hash"), expected_episode,
                      field="game sidecar episode identity")
        _require_same(sidecar.get("subject_deck_sha256"),
                      collection.get("subject_deck_file_sha256"),
                      field="sidecar subject deck SHA")
        _require_same(sidecar.get("teacher_policy_sha256"),
                      collection.get("teacher_policy_hash"),
                      field="sidecar teacher policy SHA")
        _require_same(sidecar.get("permission_manifest_id"),
                      permission.get("permission_manifest_id"),
                      field="sidecar permission ID")
        record_path = records_dir / f"game-{index:06d}.jsonl"
        _require_same(Path(str(sidecar.get("record_path"))).resolve(), record_path.resolve(),
                      field="sidecar record path")
        count = sidecar.get("record_count")
        if type(count) is not int or count <= 0:
            raise CollectionSealPreflightV2Error("DONE game sidecar needs nonempty records")
        _require_same(sidecar.get("record_sha256"), _sha256_file(record_path),
                      field="sidecar record SHA")
        try:
            actual_count = sum(1 for line in record_path.open(encoding="utf-8") if line.strip())
        except OSError as exc:
            raise CollectionSealPreflightV2Error("record JSONL is unreadable") from exc
        _require_same(count, actual_count, field="sidecar record count")
        omissions = sidecar.get("omissions")
        if type(omissions) is not list or any(type(row) is not dict for row in omissions):
            raise CollectionSealPreflightV2Error("sidecar omissions are invalid")
        _require_same(sidecar.get("unlabelled"), len(omissions),
                      field="sidecar unlabelled count")
        record_count_total += count
        omission_count_total += len(omissions)
        sidecar_omissions.extend(omissions)
        outcome = str(sidecar["outcome"])
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        matchup_record_counts[expected_opponent] = (
            matchup_record_counts.get(expected_opponent, 0) + count
        )
        seat_key = "subject_first" if expected_seat == 0 else "subject_second"
        seat_counts[seat_key] += 1

        attempts = sorted((run_dir / "game-attempts").glob(
            f"game-{index:06d}-attempt-*.json"
        ))
        if not attempts:
            raise CollectionSealPreflightV2Error("every game needs an immutable attempt ledger")
        final_attempt = _read_json_object(attempts[-1], field="game attempt ledger")
        attempt_core = {key: value for key, value in final_attempt.items()
                        if key != "attempt_ordinal"}
        if attempt_core != sidecar:
            raise CollectionSealPreflightV2Error("latest game attempt does not match current sidecar")

    all_attempts = sorted((run_dir / "game-attempts").glob("game-*-attempt-*.json"))
    if not all_attempts:
        raise CollectionSealPreflightV2Error("game attempt ledger is empty")
    _require_same(collection.get("game_attempts_total"), len(all_attempts),
                  field="game_attempts_total")
    non_done_attempts = 0
    attempts_by_game: dict[int, int] = {}
    for attempt_path in all_attempts:
        attempt = _read_json_object(attempt_path, field="game attempt ledger")
        game_index = attempt.get("game_index")
        ordinal = attempt.get("attempt_ordinal")
        if type(game_index) is not int or not 0 <= game_index < games_requested:
            raise CollectionSealPreflightV2Error("game attempt has an invalid index")
        if type(ordinal) is not int or ordinal <= 0:
            raise CollectionSealPreflightV2Error("game attempt has an invalid ordinal")
        expected_ordinal = attempts_by_game.get(game_index, 0) + 1
        if ordinal != expected_ordinal or attempt_path.name != (
            f"game-{game_index:06d}-attempt-{ordinal:04d}.json"
        ):
            raise CollectionSealPreflightV2Error("game attempt ledger is not contiguous")
        attempts_by_game[game_index] = ordinal
        if attempt.get("status") != "DONE":
            non_done_attempts += 1
    _require_same(collection.get("game_attempts_non_done"), non_done_attempts,
                  field="game_attempts_non_done")
    _require_same(collection.get("records_written"), record_count_total,
                  field="records_written")
    _require_same(collection.get("decisions_unlabelled"), omission_count_total,
                  field="sidecar omission aggregate")
    canonical_omissions = lambda rows: sorted(canonical_json_bytes_v2(row) for row in rows)
    _require_same(canonical_omissions(omission_rows), canonical_omissions(sidecar_omissions),
                  field="omissions ledger content")
    _require_same(collection.get("games_completed"), games_requested,
                  field="games_completed")
    _require_same(collection.get("games_faulted"), 0, field="games_faulted")
    _require_same(collection.get("games_other_status"), [], field="games_other_status")
    _require_same(collection.get("outcome_counts"), outcome_counts, field="outcome_counts")
    _require_same(collection.get("seat_counts"), seat_counts, field="seat_counts")
    _require_same(collection.get("matchup_record_counts"),
                  dict(sorted(matchup_record_counts.items())),
                  field="matchup_record_counts")

    artifacts = (
        {"kind": "teacher_collection_manifest_v2",
         "artifact_sha256": _sha256_file(manifest_path)},
        {"kind": "teacher_collection_contract_v2", "artifact_sha256": contract_sha},
        {"kind": "teacher_collection_omissions_v2", "artifact_sha256": omissions_sha},
        {"kind": "teacher_collector_source_snapshot_v2",
         "artifact_sha256": collector_source_sha},
        {"kind": "teacher_permission_trusted_bytes_v1", "artifact_sha256": permission_sha},
        {"kind": f"teacher_source_kind:{source_kind}",
         "artifact_sha256": hashlib.sha256(source_kind.encode("utf-8")).hexdigest()},
    )
    return CollectionSealPreflightV2(
        collection=collection, contract=contract, permission=permission,
        source_kind=source_kind,
        permission_content_hash=str(permission["content_hash"]),
        provenance_artifacts=artifacts,
    )


def _bind_collection_provenance_to_snapshot_v2(
    snapshot: dict, provenance_artifacts: tuple[dict[str, str], ...]
) -> dict:
    """Add collection closure as regular source artifacts, preserving v1 readers."""
    bound = copy.deepcopy(snapshot)
    by_key = {
        (row["kind"], row["artifact_sha256"]): dict(row)
        for row in (*bound["source_artifacts"], *provenance_artifacts)
    }
    bound["source_artifacts"] = [by_key[key] for key in sorted(by_key)]
    bound["snapshot_id"] = _snapshot_identity(bound)
    bound["content_hash"] = _snapshot_content_hash(bound)
    return bound


def _bind_collection_provenance_to_sharded_output_v2(
    output_dir: Path, index: dict, provenance_artifacts: tuple[dict[str, str], ...]
) -> dict:
    """Re-seal each shard and rewrite index IDs after provenance binding."""
    output_dir = Path(output_dir)
    rebound_rows: list[dict] = []
    for row in index["shards"]:
        path = output_dir / row["path"]
        shard = read_training_snapshot_v1(path)
        bound = _bind_collection_provenance_to_snapshot_v2(shard, provenance_artifacts)
        atomic_write_training_snapshot_v1(path, bound)
        rebound_rows.append({**row, "snapshot_id": bound["snapshot_id"]})
    rebound = copy.deepcopy(index)
    by_key = {
        (row["kind"], row["artifact_sha256"]): dict(row)
        for row in (*rebound["source_artifacts"], *provenance_artifacts)
    }
    rebound["source_artifacts"] = [by_key[key] for key in sorted(by_key)]
    rebound["shards"] = rebound_rows
    _atomic_write_canonical_json(output_dir / "snapshot_index.json", rebound)
    return rebound


def _iter_records_in(path: Path):
    """1 file の record を 1 行ずつ流す。全件を保持しない。"""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _write_chunks(records_dir: Path, output_dir: Path, chunk_max_bytes: int) -> list[Path]:
    """局ごとの record を canonical bytes へ直し、上限以下の chunk 列へ書く。"""
    paths: list[Path] = []
    handle = None
    written = 0
    try:
        for jsonl in sorted(records_dir.glob("*.jsonl")):
            for record in _iter_records_in(jsonl):
                body = canonical_json_bytes_v2(record) + b"\n"
                if handle is None or written + len(body) > chunk_max_bytes:
                    if handle is not None:
                        handle.close()
                    path = output_dir / f"dataset-{len(paths):04d}.jsonl"
                    paths.append(path)
                    handle = open(path, "wb")
                    written = 0
                handle.write(body)
                written += len(body)
    finally:
        if handle is not None:
            handle.close()
    if not paths:
        raise SystemExit(f"no records under {records_dir}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection-run-dir", required=True)
    parser.add_argument("--archetype-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--environment-version", default="cabt-local-v1")
    parser.add_argument(
        "--shard-max-examples", type=int, default=0,
        help="0 で単一 snapshot。正の値で shard へ分割する。1 corpus が単一 JSON の "
             "byte 上限やメモリに収まらない場合に使う。split は shard ごとではなく "
             "corpus 全体で決まるので、同じ episode が shard を跨いで train/test へ "
             "分かれることはない",
    )
    parser.add_argument(
        "--progress-path", default="",
        help="進捗を atomic に書く JSON。並列 supervisor はこれを読んで描画する",
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help="chunk を同時に処理するプロセス数。0 で min(chunk 数, コア数)。"
             "chunk の導出は互いに独立なのでそのまま台数分速くなる。同時に載る "
             "メモリはおおむね workers x --chunk-max-bytes になる",
    )
    parser.add_argument(
        "--chunk-max-bytes", type=int, default=DEFAULT_CHUNK_MAX_BYTES,
        help="dataset chunk 1 個の上限 byte 数。封印中の peak RSS はおおむねこの値で "
             "決まる。chunk の切り方は split にも重複 cap にも影響しない",
    )
    args = parser.parse_args()

    run_dir = Path(args.collection_run_dir)
    preflight = _preflight_collection_v2(
        run_dir, expected_archetype_id=args.archetype_id
    )
    collection = preflight.collection
    permission = preflight.permission

    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[seal] dataset chunk を書き出しています...", flush=True)
    chunk_paths = _write_chunks(run_dir / "records", output_dir, args.chunk_max_bytes)
    total_bytes = sum(path.stat().st_size for path in chunk_paths)
    print(f"[seal] chunks={len(chunk_paths)} bytes={total_bytes / 2 ** 30:.2f}GiB", flush=True)

    qualification_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.shard_max_examples:
        started = time.time()
        progress_path = Path(args.progress_path) if args.progress_path else None

        def on_progress(event: dict) -> None:
            print(f"[seal] {event} elapsed={time.time() - started:.0f}s", flush=True)
            if progress_path is None:
                return
            # 封印は「chunk 導出 → shard 書き出し」の 2 段階で、総量が分かるのは
            # chunk 段階だけなので、そこを進捗の分母にする。
            done = int(event.get("done", 0) or 0)
            total = int(event.get("chunks", 0) or 0) or len(chunk_paths)
            if event.get("stage") != "chunk":
                done = total
            elapsed = max(1e-6, time.time() - started)
            _atomic_write_json(progress_path, {
                "completed": done, "total": total,
                "rate_per_second": done / elapsed,
                "eta_seconds": (total - done) / (done / elapsed) if done else None,
                "fields": {"stage": str(event.get("stage", "")),
                           "examples": int(event.get("examples", 0) or 0),
                           "shards": int(event.get("shards", 0) or 0)},
            })

        index = seal_sharded_corpus_v1(
            chunk_paths,
            archetype_id=args.archetype_id,
            deck_csv_path=collection["subject_deck_csv_path"],
            source_commit=collection["source_commit"],
            permission_manifest=permission,
            environment_version=args.environment_version,
            qualification_time_utc=qualification_time,
            output_dir=output_dir,
            shard_max_examples=args.shard_max_examples,
            workers=args.workers or min(len(chunk_paths), os.cpu_count() or 1),
            on_progress=on_progress,
        )
        index = _bind_collection_provenance_to_sharded_output_v2(
            output_dir, index, preflight.provenance_artifacts
        )
        print(json.dumps({
            "snapshot_index": str(output_dir / "snapshot_index.json"),
            "dataset_chunks": len(chunk_paths),
            "shards": len(index["shards"]),
            "examples": index["examples_total"],
            "split_counts": index["split_counts"],
            "elapsed_seconds": round(time.time() - started, 1),
        }, ensure_ascii=False, indent=2), flush=True)
        return 0

    if len(chunk_paths) != 1:
        raise SystemExit(
            f"corpus は {len(chunk_paths)} chunk に分かれており、単一 snapshot には"
            "収まりません。\n"
            "  --shard-max-examples を指定してください (例: --shard-max-examples 20000)"
        )
    trusted = build_trusted_permission_set_v1((canonical_json_bytes_v2(permission),))
    qualified, _deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=args.archetype_id,
        deck_csv_path=Path(collection["subject_deck_csv_path"]),
        source_commit=collection["source_commit"],
    )
    chunk_manifest = build_local_dataset_manifest_streaming_v2(
        records=_iter_records_in(chunk_paths[0]),
        environment_version=args.environment_version,
        # `deck_identity` は "deck-<20hex>" の短縮表記であり manifest が要求する
        # 64-hex ではない。デッキ実体を一意に指す 64-hex は raw file の sha256 である。
        deck_fingerprint=qualified.deck_file_sha256,
        trusted_permissions=trusted,
    )
    snapshot = build_training_snapshot_v1(
        chunk_paths[0],
        manifest=chunk_manifest,
        vocabulary=vocabulary,
        trusted_permissions=trusted,
        qualification_time_utc=qualification_time,
    )
    snapshot = _bind_collection_provenance_to_snapshot_v2(
        snapshot, preflight.provenance_artifacts
    )
    out = atomic_write_training_snapshot_v1(Path(args.output), snapshot)
    counts: dict[str, int] = {}
    for example in snapshot["examples"]:
        counts[example["split"]] = counts.get(example["split"], 0) + 1
    print(json.dumps({
        "snapshot_path": str(out),
        "examples": len(snapshot["examples"]),
        "split_counts": counts,
        "snapshot_id": snapshot.get("snapshot_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
