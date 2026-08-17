"""Closed catalog for teacher-derived weights, separate from evaluation splits.

This catalog records the limited user decision that six named local agents may
contribute *derived weights* to a locally trained policy.  It deliberately
does not change the status of their copied code or decks: both remain
``local_eval_only`` and cannot enter a submission bundle.  The catalog also
does not grant training, promotion, or submission authority; a downstream
runner must acquire those authorities independently.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    build_local_dataset_manifest_streaming_v2,
    build_trusted_permission_set_v1,
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
    validate_source_permission_manifest_v1,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    TrainingSnapshotV1Error,
    corpus_dataset_sha256_v1,
    read_training_snapshot_v1,
)


SCHEMA_V1 = "meta-specialist-derived-teacher-catalog-v2"
DECISION_RELATIVE_PATH = "docs/decisions/2026-08-05-archaludon-teacher-derivation.md"
DATASET_ENVIRONMENT_VERSION_V1 = "cabt-local-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "decision", "derived_weights_allowed", "allowed_usages",
    "training_authority", "promotion_authority", "submission_authority",
    "teachers", "notes", "catalog_sha256",
})
_TEACHER_KEYS = frozenset({
    "teacher_id", "archetype", "source_kind", "teacher_usage_boundary",
    "derived_weights_allowed", "allowed_usages", "teacher_code_submission_allowed",
    "deck_submission_allowed", "promotion_authority", "submission_authority",
    "policy", "deck", "collection",
})
_ASSET_KEYS = frozenset({"path", "sha256"})
_COLLECTION_KEYS = frozenset({"status", "dataset_manifest", "snapshot_index", "game_counts", "seat_counts"})
_SEALED_MANIFEST_KEYS = frozenset({
    "path", "file_sha256", "teacher_policy_sha256", "permission_manifest_id",
})
_SNAPSHOT_INDEX_KEYS = frozenset({"path", "file_sha256", "schema_version", "examples_total", "split_counts"})
_GAME_COUNT_KEYS = frozenset({"requested", "completed", "faulted", "unlabelled", "other_status_count"})
_SEAT_COUNT_KEYS = frozenset({"subject_first", "subject_second"})
_COLLECTION_MANIFEST_V2_KEYS = frozenset({
    "schema_version", "run_name", "archetype_id", "subject_deck_csv_path",
    "subject_deck_file_sha256", "base_seed", "max_steps", "source_commit",
    "teacher_id", "teacher_policy_hash", "teacher_deck_file_sha256",
    "teacher_source_kind", "teacher_usage_boundary", "permission_manifest",
    "derivation_decision_ref", "opponent_ids", "games_requested",
    "games_completed", "games_faulted", "games_other_status", "records_written",
    "decisions_unlabelled", "outcome_counts", "seat_counts", "records_dir",
    "matchup_record_counts", "matchup_cap_fraction", "omissions_path",
    "omissions_sha256", "collection_contract_path", "collection_contract_sha256",
    "collector_source_snapshot_path", "collector_source_sha256",
    "permission_trusted_bytes_sha256", "permission_content_hash",
    "game_result_sidecars", "game_attempts_total", "game_attempts_non_done",
})
_COLLECTION_CONTRACT_V2_KEYS = frozenset({
    "schema_version", "run_name", "archetype_id", "subject_deck_csv_path",
    "subject_deck_file_sha256", "teacher", "teacher_source_kind", "opponent_ids",
    "opponents", "games_requested", "base_seed", "max_steps", "source_commit",
    "decision_ref", "permission_manifest_id", "permission_content_hash",
    "permission_trusted_bytes_sha256", "allowed_usages", "pool_root",
    "pool_manifest_sha256", "engine_entry_point", "engine_source_sha256",
    "feature_schema_hash", "vocabulary_manifest", "collector_source_sha256",
    "collector_source_snapshot_path", "seat_schedule", "opponent_schedule",
    "matchup_cap_fraction",
})
_COLLECTION_ASSET_KEYS = frozenset({
    "opponent_id", "policy_sha256", "deck_file_sha256", "canonical_deck_hash",
    "source", "usage_boundary",
})
_SNAPSHOT_INDEX_V1_KEYS = frozenset({
    "schema_version", "dataset_snapshot_sha256", "manifest_id", "dataset_chunks",
    "source_artifacts", "examples_total", "split_names", "split_weights",
    "split_counts", "duplicate_cap", "shards",
})
_SNAPSHOT_CHUNK_KEYS = frozenset({
    "path", "dataset_snapshot_sha256", "manifest_id", "manifest_content_hash",
})
_SNAPSHOT_SHARD_KEYS = frozenset({"path", "snapshot_id", "examples", "split_counts"})
_GAME_SIDECAR_V2_KEYS = frozenset({
    "schema_version", "game_index", "seed", "seat", "opponent_id",
    "episode_id_hash", "status", "outcome", "record_path", "record_sha256",
    "record_count", "unlabelled", "omissions", "detail", "subject_deck_sha256",
    "teacher_policy_sha256", "permission_manifest_id",
})
_GAME_ATTEMPT_V2_KEYS = _GAME_SIDECAR_V2_KEYS | {"attempt_ordinal"}


class DerivedTeacherCatalogError(ValueError):
    """Raised when a derived-teacher catalog is incomplete or tampered with."""


_TEACHERS: tuple[dict[str, str], ...] = (
    {
        "teacher_id": "tomatomato_archaludon",
        "archetype": "archaludon",
        "source_kind": "pooled_external_submission_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96-autonomous-v2b-20260813/snapshot_index.json",
    },
    {
        "teacher_id": "lucifer19_battlecore",
        "archetype": "archaludon",
        "source_kind": "pooled_external_submission_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-96-autonomous-v2b-20260813/snapshot_index.json",
    },
    {
        "teacher_id": "plamen06_steel",
        "archetype": "archaludon",
        "source_kind": "pooled_external_submission_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/archaludon-teacher-plamen06-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/archaludon-teacher-plamen06-96-autonomous-v2b-20260813/snapshot_index.json",
    },
    {
        "teacher_id": "ozawa_grimmsnarl_v2",
        "archetype": "grimmsnarl_froslass_munkidori",
        "source_kind": "team_internal_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/grimmsnarl-teacher-ozawa-v2-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/grimmsnarl-teacher-ozawa-v2-96-autonomous-v2b-20260813/snapshot_index.json",
    },
    {
        "teacher_id": "ozawa_rocket_v2",
        "archetype": "rocket_mewtwo_spidops",
        "source_kind": "team_internal_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/rocket-teacher-ozawa-v2-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/rocket-teacher-ozawa-v2-96-autonomous-v2b-20260813/snapshot_index.json",
    },
    {
        "teacher_id": "nihei_alakazam",
        "archetype": "alakazam",
        "source_kind": "team_internal_agent",
        "collection_manifest": "runs/meta-specialist-teacher-records/alakazam-teacher-nihei-96-autonomous-v2b-20260813/teacher_dataset_manifest.json",
        "snapshot_index": "runs/meta-specialist-teacher-records/alakazam-teacher-nihei-96-autonomous-v2b-20260813/snapshot_index.json",
    },
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DerivedTeacherCatalogError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise DerivedTeacherCatalogError(f"non-finite JSON value: {value}")


def _strict_catalog_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DerivedTeacherCatalogError("catalog is unreadable strict JSON") from exc
    if type(payload) is not dict:
        raise DerivedTeacherCatalogError("catalog must be a JSON object")
    if _canonical(payload) != raw:
        raise DerivedTeacherCatalogError("catalog must be canonical JSON")
    return payload


def _manifest_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs,
                             parse_constant=_reject_nonfinite_json)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DerivedTeacherCatalogError("sealed teacher dataset manifest is unreadable strict JSON") from exc
    if type(payload) is not dict:
        raise DerivedTeacherCatalogError("sealed teacher dataset manifest must be an object")
    return payload


def _closed(value: object, keys: frozenset[str], *, field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise DerivedTeacherCatalogError(f"{field} has an invalid closed schema")
    return value


def _strict_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DerivedTeacherCatalogError(f"{field} must be an integer >= {minimum}")
    return value


def _exact_path_in_directory(
    root: Path, value: object, directory: Path, *, field: str, name: str | None = None,
) -> Path:
    path = _artifact_path(root, value, field=field)
    if path.parent != directory or (name is not None and path.name != name):
        raise DerivedTeacherCatalogError(f"{field} must be inside the expected collection root")
    return path


def _artifact_path(
    root: Path,
    value: object,
    *,
    field: str,
    base: Path | None = None,
) -> Path:
    """Resolve a collector/index path without weakening catalog path syntax."""
    if type(value) is not str or not value:
        raise DerivedTeacherCatalogError(f"{field} must be a non-empty path")
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [root / raw]
    if not raw.is_absolute() and base is not None:
        candidates.append(base / raw)
    resolved_candidates = [candidate.resolve() for candidate in candidates]
    resolved = next(
        (candidate for candidate in resolved_candidates if candidate.exists()),
        resolved_candidates[0],
    )
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise DerivedTeacherCatalogError(f"{field} escapes repository root") from exc
    return resolved


def _strict_jsonl_rows(path: Path, *, field: str) -> list[dict[str, Any]]:
    return list(_iter_strict_jsonl_rows(path, field=field))


def _iter_strict_jsonl_rows(path: Path, *, field: str):
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n") or raw == b"\n":
                    raise DerivedTeacherCatalogError(
                        f"{field}:{line_number} is blank or unterminated"
                    )
                try:
                    value = parse_canonical_json_bytes_v2(raw[:-1])
                except LocalDatasetV2Error as exc:
                    raise DerivedTeacherCatalogError(
                        f"{field}:{line_number} is not strict JSON"
                    ) from exc
                if type(value) is not dict:
                    raise DerivedTeacherCatalogError(
                        f"{field}:{line_number} is not a canonical JSON object"
                    )
                yield value
    except OSError as exc:
        raise DerivedTeacherCatalogError(f"{field} is unreadable") from exc


def _validate_permission(
    manifest: Mapping[str, Any], *, policy_sha: str, source_kind: str,
) -> tuple[dict[str, object], bytes]:
    try:
        permission = validate_source_permission_manifest_v1(
            manifest.get("permission_manifest")
        )
        permission_bytes = canonical_json_bytes_v2(permission)
    except LocalDatasetV2Error as exc:
        raise DerivedTeacherCatalogError(
            f"permission content_hash/identity does not verify: {exc}"
        ) from exc
    if (
        permission["artifact_sha256"] != policy_sha
        or permission["source_kind"] != source_kind
        or permission["allowed_usages"] != ["training-local"]
    ):
        raise DerivedTeacherCatalogError(
            "permission source kind, artifact, or usage binding mismatch"
        )
    raw_sha = hashlib.sha256(permission_bytes).hexdigest()
    if (
        manifest.get("permission_content_hash") != permission["content_hash"]
        or manifest.get("permission_trusted_bytes_sha256") != raw_sha
    ):
        raise DerivedTeacherCatalogError(
            "permission content_hash or trusted exact-byte SHA mismatch"
        )
    return permission, permission_bytes


def _validate_collection_contract(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    teacher_id: str,
    source_kind: str,
    policy_sha: str,
    deck_sha: str,
    permission: Mapping[str, object],
    permission_bytes: bytes,
) -> dict[str, Any]:
    collection_root = manifest_path.parent
    contract_path = _exact_path_in_directory(
        root,
        manifest.get("collection_contract_path"),
        collection_root,
        field="collection contract path",
        name="collection_contract.json",
    )
    if _sha_file(contract_path, field="collection contract") != _sha(
        manifest.get("collection_contract_sha256"), field="collection contract SHA-256"
    ):
        raise DerivedTeacherCatalogError("collection contract SHA-256 mismatch")
    contract = _closed(
        _manifest_json(contract_path),
        _COLLECTION_CONTRACT_V2_KEYS,
        field="collection contract",
    )
    if contract.get("schema_version") != "specialist-teacher-collection-contract-v2":
        raise DerivedTeacherCatalogError("collection contract schema mismatch")
    scalar_pairs = {
        "run_name": "run_name",
        "archetype_id": "archetype_id",
        "subject_deck_csv_path": "subject_deck_csv_path",
        "subject_deck_file_sha256": "subject_deck_file_sha256",
        "base_seed": "base_seed",
        "max_steps": "max_steps",
        "source_commit": "source_commit",
        "decision_ref": "derivation_decision_ref",
        "teacher_source_kind": "teacher_source_kind",
        "opponent_ids": "opponent_ids",
        "games_requested": "games_requested",
        "matchup_cap_fraction": "matchup_cap_fraction",
        "permission_content_hash": "permission_content_hash",
        "permission_trusted_bytes_sha256": "permission_trusted_bytes_sha256",
        "collector_source_sha256": "collector_source_sha256",
        "collector_source_snapshot_path": "collector_source_snapshot_path",
    }
    for contract_field, manifest_field in scalar_pairs.items():
        if contract.get(contract_field) != manifest.get(manifest_field):
            raise DerivedTeacherCatalogError(
                f"collection contract {contract_field} disagrees with manifest"
            )
    if (
        contract.get("decision_ref") != DECISION_RELATIVE_PATH
        or contract.get("allowed_usages") != ["training-local"]
        or contract.get("permission_manifest_id") != permission["permission_manifest_id"]
        or contract.get("permission_content_hash") != permission["content_hash"]
        or contract.get("permission_trusted_bytes_sha256")
        != hashlib.sha256(permission_bytes).hexdigest()
    ):
        raise DerivedTeacherCatalogError("collection contract permission binding mismatch")
    teacher = _closed(
        contract.get("teacher"), _COLLECTION_ASSET_KEYS, field="collection contract teacher"
    )
    if (
        teacher.get("opponent_id") != teacher_id
        or teacher.get("policy_sha256") != policy_sha
        or teacher.get("deck_file_sha256") != deck_sha
        or teacher.get("usage_boundary") != "local_eval_only"
        or contract.get("teacher_source_kind") != source_kind
    ):
        raise DerivedTeacherCatalogError("collection contract teacher asset mismatch")
    opponents = contract.get("opponents")
    opponent_ids = contract.get("opponent_ids")
    if (
        type(opponents) is not list
        or type(opponent_ids) is not list
        or len(opponents) != len(opponent_ids)
        or not opponent_ids
    ):
        raise DerivedTeacherCatalogError("collection contract opponent set is invalid")
    for expected_id, opponent in zip(opponent_ids, opponents, strict=True):
        row = _closed(
            opponent, _COLLECTION_ASSET_KEYS, field="collection contract opponent"
        )
        if row.get("opponent_id") != expected_id:
            raise DerivedTeacherCatalogError("collection contract opponent identity mismatch")
        for field in ("policy_sha256", "deck_file_sha256", "canonical_deck_hash"):
            value = row.get(field)
            if value is not None:
                _sha(value, field=f"collection contract opponent {field}")
    if (
        contract.get("seat_schedule") != "seat=(game_index//opponent_count)%2"
        or contract.get("opponent_schedule") != "opponent_ids[game_index%opponent_count]"
    ):
        raise DerivedTeacherCatalogError("collection contract schedule mismatch")
    pool_root = _artifact_path(root, contract.get("pool_root"), field="pool root")
    if not pool_root.is_dir():
        raise DerivedTeacherCatalogError("collection contract pool root is not a directory")
    pool_manifest = pool_root / "pool_manifest.json"
    if _sha_file(pool_manifest, field="pool manifest") != _sha(
        contract.get("pool_manifest_sha256"), field="pool manifest SHA-256"
    ):
        raise DerivedTeacherCatalogError("collection contract pool manifest mismatch")
    engine_path = _artifact_path(
        root, contract.get("engine_entry_point"), field="engine entry point"
    )
    if _sha_file(engine_path, field="engine source") != _sha(
        contract.get("engine_source_sha256"), field="engine source SHA-256"
    ):
        raise DerivedTeacherCatalogError("collection contract engine source mismatch")
    collector_snapshot = _exact_path_in_directory(
        root,
        contract.get("collector_source_snapshot_path"),
        collection_root,
        field="collector source snapshot path",
        name="collector_source_snapshot.py",
    )
    if _sha_file(collector_snapshot, field="collector source snapshot") != _sha(
        contract.get("collector_source_sha256"), field="collector source SHA-256"
    ):
        raise DerivedTeacherCatalogError("collector source snapshot SHA-256 mismatch")
    vocabulary = contract.get("vocabulary_manifest")
    if type(vocabulary) is not dict or vocabulary.get("test_only") is not False:
        raise DerivedTeacherCatalogError("collection contract production vocabulary is missing")
    _sha(contract.get("feature_schema_hash"), field="feature schema hash")
    return contract


def _validate_collection_sidecars(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    teacher_id: str,
    policy_sha: str,
    deck_sha: str,
    permission_id: str,
) -> list[dict[str, Any]]:
    collection_root = manifest_path.parent
    records_dir = _artifact_path(
        root, manifest.get("records_dir"), field="records directory"
    )
    if not records_dir.is_dir() or records_dir.parent != collection_root:
        raise DerivedTeacherCatalogError("records directory must be inside the collection root")
    sidecar_dir = collection_root / "game-results"
    paths = sorted(sidecar_dir.glob("game-*.result.json")) if sidecar_dir.is_dir() else []
    declared_sidecars = _strict_int(
        manifest.get("game_result_sidecars"), field="game_result_sidecars"
    )
    if len(paths) != declared_sidecars:
        raise DerivedTeacherCatalogError("game sidecar count does not match the manifest")
    if declared_sidecars != manifest.get("games_requested"):
        raise DerivedTeacherCatalogError(
            "game sidecar count does not cover every requested game"
        )
    rows: list[dict[str, Any]] = []
    sidecar_omissions: list[dict[str, Any]] = []
    for path in paths:
        row = _closed(_manifest_json(path), _GAME_SIDECAR_V2_KEYS, field="game sidecar")
        if row.get("schema_version") != "specialist-teacher-collection-game-result-v2":
            raise DerivedTeacherCatalogError("game sidecar schema mismatch")
        game_index = _strict_int(row.get("game_index"), field="game sidecar index")
        if path.name != f"game-{game_index:06d}.result.json":
            raise DerivedTeacherCatalogError("game sidecar path/index mismatch")
        if (
            row.get("seed") != contract.get("base_seed") + game_index
            or row.get("seat") != (game_index // len(contract["opponent_ids"])) % 2
            or row.get("opponent_id")
            != contract["opponent_ids"][game_index % len(contract["opponent_ids"])]
        ):
            raise DerivedTeacherCatalogError("game sidecar schedule mismatch")
        if row.get("subject_deck_sha256") != deck_sha:
            raise DerivedTeacherCatalogError("game sidecar subject deck mismatch")
        if row.get("teacher_policy_sha256") != policy_sha:
            raise DerivedTeacherCatalogError("game sidecar teacher policy mismatch")
        if row.get("permission_manifest_id") != permission_id:
            raise DerivedTeacherCatalogError("game sidecar permission mismatch")
        omissions = row.get("omissions")
        if type(omissions) is not list or any(type(item) is not dict for item in omissions):
            raise DerivedTeacherCatalogError("game sidecar omissions are invalid")
        if _strict_int(row.get("unlabelled"), field="game sidecar unlabelled") != len(omissions):
            raise DerivedTeacherCatalogError("game sidecar omission count mismatch")
        sidecar_omissions.extend(omissions)
        expected_record_path = records_dir / f"game-{game_index:06d}.jsonl"
        if Path(str(row.get("record_path"))).resolve() != expected_record_path.resolve():
            raise DerivedTeacherCatalogError("game sidecar record path mismatch")
        record_count = _strict_int(row.get("record_count"), field="game sidecar record count")
        if row.get("status") == "DONE":
            if row.get("outcome") not in ("win", "draw", "loss") or record_count <= 0:
                raise DerivedTeacherCatalogError("DONE game sidecar has no labelled records")
            if _sha_file(expected_record_path, field="game record") != _sha(
                row.get("record_sha256"), field="game record SHA-256"
            ):
                raise DerivedTeacherCatalogError("game sidecar record SHA mismatch")
            if len(_strict_jsonl_rows(expected_record_path, field="game records")) != record_count:
                raise DerivedTeacherCatalogError("game sidecar record count mismatch")
        elif row.get("outcome") is not None or record_count != 0 or row.get("record_sha256") is not None:
            raise DerivedTeacherCatalogError("non-DONE game sidecar retains record claims")
        rows.append(row)
    indices = [row["game_index"] for row in rows]
    if indices != list(range(declared_sidecars)):
        raise DerivedTeacherCatalogError(
            "game sidecar indices do not exactly cover the requested schedule"
        )
    completed = [row for row in rows if row["status"] == "DONE"]
    faulted = [row for row in rows if row["status"] == "faulted"]
    other_statuses = sorted({
        row["status"] for row in rows if row["status"] not in ("DONE", "faulted")
    })
    if (
        len(completed) != manifest.get("games_completed")
        or len(faulted) != manifest.get("games_faulted")
        or other_statuses != manifest.get("games_other_status")
    ):
        raise DerivedTeacherCatalogError("game sidecar status counts mismatch")
    outcome_counts: dict[str, int] = {}
    seat_counts = {"subject_first": 0, "subject_second": 0}
    matchup_record_counts: dict[str, int] = {}
    for row in rows:
        if row["status"] == "DONE":
            outcome = row["outcome"]
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        seat_key = "subject_first" if row["seat"] == 0 else "subject_second"
        seat_counts[seat_key] += 1
        opponent_id = row["opponent_id"]
        matchup_record_counts[opponent_id] = (
            matchup_record_counts.get(opponent_id, 0) + row["record_count"]
        )
    if outcome_counts != manifest.get("outcome_counts"):
        raise DerivedTeacherCatalogError("game sidecar outcome counts mismatch")
    if seat_counts != manifest.get("seat_counts"):
        raise DerivedTeacherCatalogError("game sidecar seat counts mismatch")
    if matchup_record_counts != manifest.get("matchup_record_counts"):
        raise DerivedTeacherCatalogError("game sidecar matchup record counts mismatch")
    if sum(row["record_count"] for row in rows if row["status"] == "DONE") != manifest.get("records_written"):
        raise DerivedTeacherCatalogError("game sidecar record total mismatch")
    if sum(row["unlabelled"] for row in rows) != manifest.get("decisions_unlabelled"):
        raise DerivedTeacherCatalogError("game sidecar omission total mismatch")

    attempt_dir = collection_root / "game-attempts"
    attempt_paths = sorted(attempt_dir.glob("game-*-attempt-*.json")) if attempt_dir.is_dir() else []
    if len(attempt_paths) != manifest.get("game_attempts_total"):
        raise DerivedTeacherCatalogError("game attempts count does not match the manifest")
    non_done = 0
    attempts_by_game: dict[int, list[dict[str, Any]]] = {}
    for path in attempt_paths:
        attempt = _closed(
            _manifest_json(path), _GAME_ATTEMPT_V2_KEYS, field="game attempt"
        )
        if attempt.get("schema_version") != "specialist-teacher-collection-game-result-v2":
            raise DerivedTeacherCatalogError("game attempt schema mismatch")
        game_index = _strict_int(attempt.get("game_index"), field="game attempt index")
        ordinal = _strict_int(attempt.get("attempt_ordinal"), field="game attempt ordinal", minimum=1)
        if path.name != f"game-{game_index:06d}-attempt-{ordinal:04d}.json":
            raise DerivedTeacherCatalogError("game attempt path/index mismatch")
        if (
            attempt.get("subject_deck_sha256") != deck_sha
            or attempt.get("teacher_policy_sha256") != policy_sha
            or attempt.get("permission_manifest_id") != permission_id
        ):
            raise DerivedTeacherCatalogError("game attempt source binding mismatch")
        if (
            attempt.get("seed") != contract.get("base_seed") + game_index
            or attempt.get("seat")
            != (game_index // len(contract["opponent_ids"])) % 2
            or attempt.get("opponent_id")
            != contract["opponent_ids"][game_index % len(contract["opponent_ids"])]
        ):
            raise DerivedTeacherCatalogError("game attempt schedule mismatch")
        attempts_by_game.setdefault(game_index, []).append(attempt)
        non_done += int(attempt.get("status") != "DONE")
    if non_done != manifest.get("game_attempts_non_done"):
        raise DerivedTeacherCatalogError("game attempts non-DONE count mismatch")
    if set(attempts_by_game) != set(range(declared_sidecars)):
        raise DerivedTeacherCatalogError("game attempts do not cover every game")
    by_index = {row["game_index"]: row for row in rows}
    for game_index, attempts in attempts_by_game.items():
        ordinals = [attempt["attempt_ordinal"] for attempt in attempts]
        if ordinals != list(range(1, len(attempts) + 1)):
            raise DerivedTeacherCatalogError("game attempt ordinals are not contiguous")
        final_attempt = dict(attempts[-1])
        del final_attempt["attempt_ordinal"]
        if final_attempt != by_index[game_index]:
            raise DerivedTeacherCatalogError("final game attempt disagrees with sidecar")
    return sidecar_omissions


def _validate_snapshot_artifacts(
    root: Path,
    snapshot_path: Path,
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    teacher_id: str,
    policy_sha: str,
    source_kind: str,
    deck_sha: str,
    permission: Mapping[str, object],
    permission_bytes: bytes,
) -> dict[str, Any]:
    index = _closed(
        _manifest_json(snapshot_path), _SNAPSHOT_INDEX_V1_KEYS, field="snapshot index"
    )
    if index.get("schema_version") != "specialist-training-snapshot-index-v1":
        raise DerivedTeacherCatalogError("snapshot index schema mismatch")
    expected_bindings = {
        source_kind: policy_sha,
        "teacher_collection_manifest_v2": _sha_file(
            manifest_path, field="teacher collection manifest"
        ),
        "teacher_collection_contract_v2": manifest["collection_contract_sha256"],
        "teacher_collection_omissions_v2": manifest["omissions_sha256"],
        "teacher_collector_source_snapshot_v2": manifest["collector_source_sha256"],
        "teacher_permission_trusted_bytes_v1": hashlib.sha256(permission_bytes).hexdigest(),
        f"teacher_source_kind:{source_kind}": hashlib.sha256(
            source_kind.encode("utf-8")
        ).hexdigest(),
    }
    source_rows = index.get("source_artifacts")
    if (
        type(source_rows) is not list
        or any(
            type(row) is not dict
            or set(row) != {"kind", "artifact_sha256"}
            or type(row.get("kind")) is not str
            or _SHA256.fullmatch(str(row.get("artifact_sha256"))) is None
            for row in source_rows
        )
        or {row["kind"]: row["artifact_sha256"] for row in source_rows}
        != expected_bindings
        or len(source_rows) != len(expected_bindings)
    ):
        raise DerivedTeacherCatalogError("snapshot index source artifact binding mismatch")
    chunks = index.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise DerivedTeacherCatalogError("snapshot index has no dataset chunks")
    chunk_hashes: list[str] = []
    chunk_bindings: list[tuple[Path, dict[str, Any]]] = []
    for item in chunks:
        row = _closed(item, _SNAPSHOT_CHUNK_KEYS, field="snapshot dataset chunk")
        path = _artifact_path(
            root,
            row.get("path"),
            field="snapshot dataset chunk path",
            base=snapshot_path.parent,
        )
        if path.parent != snapshot_path.parent:
            raise DerivedTeacherCatalogError("snapshot dataset chunk escapes collection root")
        actual = _sha_file(path, field="snapshot dataset chunk")
        if actual != _sha(row.get("dataset_snapshot_sha256"), field="snapshot dataset chunk SHA-256"):
            raise DerivedTeacherCatalogError("snapshot dataset chunk SHA-256 mismatch")
        chunk_hashes.append(actual)
        chunk_bindings.append((path, row))
    if corpus_dataset_sha256_v1(chunk_hashes) != index.get("dataset_snapshot_sha256"):
        raise DerivedTeacherCatalogError("snapshot corpus dataset SHA-256 mismatch")
    permission_id = permission["permission_manifest_id"]
    permission_raw_sha = hashlib.sha256(permission_bytes).hexdigest()
    shard_rows = index.get("shards")
    if type(shard_rows) is not list or not shard_rows:
        raise DerivedTeacherCatalogError("snapshot index has no shards")
    split_counts = {name: 0 for name in ("train", "development", "test")}
    examples_total = 0
    sealed_records: dict[str, tuple[str, str, object]] = {}
    vocabulary_environments: set[str] = set()
    try:
        for item in shard_rows:
            row = _closed(item, _SNAPSHOT_SHARD_KEYS, field="snapshot shard index row")
            shard_path = _artifact_path(
                root,
                row.get("path"),
                field="snapshot shard path",
                base=snapshot_path.parent,
            )
            if shard_path.parent != snapshot_path.parent:
                raise DerivedTeacherCatalogError("snapshot shard escapes collection root")
            snapshot = read_training_snapshot_v1(shard_path)
            if snapshot.get("snapshot_id") != row.get("snapshot_id"):
                raise DerivedTeacherCatalogError("snapshot shard identity mismatch")
            examples = snapshot.get("examples")
            if type(examples) is not list or len(examples) != row.get("examples"):
                raise DerivedTeacherCatalogError("snapshot shard example count mismatch")
            if snapshot.get("split_counts") != row.get("split_counts"):
                raise DerivedTeacherCatalogError("snapshot shard split counts mismatch")
            for field in (
                "dataset_snapshot_sha256", "manifest_id", "source_artifacts",
                "duplicate_cap", "split_names", "split_weights",
            ):
                if snapshot.get(field) != index.get(field):
                    raise DerivedTeacherCatalogError(f"snapshot shard {field} mismatch")
            if snapshot.get("permissions") != [{
                "permission_manifest_id": permission_id,
                "permission_content_hash": permission["content_hash"],
                "permission_trusted_bytes_sha256": permission_raw_sha,
            }]:
                raise DerivedTeacherCatalogError("snapshot permission exact-byte binding mismatch")
            environment = snapshot.get("vocabulary_environment_version")
            if type(environment) is not str or not environment:
                raise DerivedTeacherCatalogError(
                    "snapshot shard vocabulary environment is missing"
                )
            vocabulary_environments.add(environment)
            for example in examples:
                record_id = _sha(example.get("record_id"), field="snapshot record id")
                if record_id in sealed_records:
                    raise DerivedTeacherCatalogError("snapshot record id is duplicated")
                sealed_records[record_id] = (
                    _sha(
                        example.get("record_content_hash"),
                        field="snapshot record content hash",
                    ),
                    _sha(
                        example.get("episode_id_hash"), field="snapshot episode id"
                    ),
                    example.get("value_target"),
                )
            examples_total += len(examples)
            for split, count in snapshot["split_counts"].items():
                split_counts[split] = split_counts.get(split, 0) + count
    except (TrainingSnapshotV1Error, OSError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DerivedTeacherCatalogError):
            raise
        raise DerivedTeacherCatalogError(f"snapshot shard validation failed: {exc}") from exc
    if examples_total != index.get("examples_total") or split_counts != index.get("split_counts"):
        raise DerivedTeacherCatalogError("snapshot index totals do not match its shards")
    if len(vocabulary_environments) != 1:
        raise DerivedTeacherCatalogError(
            "snapshot shards disagree on vocabulary environment"
        )
    trusted = build_trusted_permission_set_v1((permission_bytes,))
    raw_records: dict[str, tuple[str, str, object]] = {}
    for chunk_path, binding in chunk_bindings:
        def records():
            for record in _iter_strict_jsonl_rows(
                chunk_path, field="snapshot dataset chunk"
            ):
                record_id = _sha(record.get("record_id"), field="raw record id")
                source = record.get("source")
                teacher = record.get("teacher")
                if (
                    type(source) is not dict
                    or source.get("kind") != source_kind
                    or source.get("artifact_sha256") != policy_sha
                    or source.get("permission_manifest_id")
                    != permission["permission_manifest_id"]
                    or source.get("synthetic") is not False
                    or source.get("synthetic_fields") != []
                    or source.get("training_eligible") is not True
                    or source.get("usage_class") != "qualified_training"
                ):
                    raise DerivedTeacherCatalogError(
                        "raw record source_kind/artifact/permission binding mismatch"
                    )
                if (
                    type(teacher) is not dict
                    or teacher.get("status") != "available"
                    or teacher.get("teacher_id") != teacher_id
                ):
                    raise DerivedTeacherCatalogError("raw record teacher identity mismatch")
                if record_id in raw_records:
                    raise DerivedTeacherCatalogError("raw record id is duplicated")
                raw_records[record_id] = (
                    _sha(record.get("content_hash"), field="raw record content hash"),
                    _sha(record.get("episode_id_hash"), field="raw record episode id"),
                    teacher.get("value_target"),
                )
                yield record

        try:
            recomputed = build_local_dataset_manifest_streaming_v2(
                records=records(),
                # The local-dataset environment used by the sealer is a
                # distinct closed contract from the card-vocabulary
                # environment carried by each training snapshot shard.
                environment_version=DATASET_ENVIRONMENT_VERSION_V1,
                deck_fingerprint=deck_sha,
                trusted_permissions=trusted,
            )
        except (LocalDatasetV2Error, StopIteration) as exc:
            raise DerivedTeacherCatalogError(
                f"raw record dataset manifest validation failed: {exc}"
            ) from exc
        if (
            recomputed["manifest_id"] != binding.get("manifest_id")
            or recomputed["content_hash"] != binding.get("manifest_content_hash")
        ):
            raise DerivedTeacherCatalogError(
                "raw record dataset manifest identity mismatch"
            )
    if raw_records != sealed_records:
        raise DerivedTeacherCatalogError("raw records and snapshot examples disagree")
    return index


def _sha_file(path: Path, *, field: str) -> str:
    if not path.is_file():
        raise DerivedTeacherCatalogError(f"{field} must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise DerivedTeacherCatalogError(f"{field} must be a lowercase SHA-256")
    return value


def _text(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise DerivedTeacherCatalogError(f"{field} must be a non-empty string")
    return value


def _bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise DerivedTeacherCatalogError(f"{field} must be bool")
    return value


def _inside_root(root: Path, relative: object, *, field: str) -> Path:
    if type(relative) is not str:
        raise DerivedTeacherCatalogError(f"{field} must be a relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DerivedTeacherCatalogError(f"{field} escapes repository root")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DerivedTeacherCatalogError(f"{field} escapes repository root") from exc
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _catalog_sha(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical({key: value for key, value in payload.items() if key != "catalog_sha256"})).hexdigest()


def _decision_semantics(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    required = tuple(spec["teacher_id"] for spec in _TEACHERS) + ("derivation_qualified", "training-local")
    if any(token not in text for token in required):
        raise DerivedTeacherCatalogError("decision document does not authorize the closed teacher set")


def _asset_binding(root: Path, teacher_id: str, filename: str) -> dict[str, str]:
    path = root / "opponents" / teacher_id / filename
    return {"path": _relative(root, path), "sha256": _sha_file(path, field=f"{teacher_id}.{filename}")}


def _ready_collection(root: Path, spec: Mapping[str, str], policy: Mapping[str, str], deck: Mapping[str, str]) -> dict[str, object]:
    manifest_path = _inside_root(root, spec["collection_manifest"], field="collection manifest path")
    manifest = _manifest_json(manifest_path)
    schema = manifest.get("schema_version")
    if schema == "specialist-teacher-dataset-manifest-v1":
        raise DerivedTeacherCatalogError(
            "LEGACY_V1_BLOCKED: READY requires a hash-bound v2 collection"
        )
    manifest = _closed(
        manifest, _COLLECTION_MANIFEST_V2_KEYS, field="teacher dataset manifest"
    )
    if schema != "specialist-teacher-dataset-manifest-v2":
        raise DerivedTeacherCatalogError("teacher dataset manifest schema mismatch")
    snapshot_path = _inside_root(root, spec["snapshot_index"], field="snapshot index path")
    teacher_id = spec["teacher_id"]
    source_kind = spec["source_kind"]
    policy_sha = policy["sha256"]
    if manifest.get("teacher_id") != teacher_id:
        raise DerivedTeacherCatalogError("sealed teacher dataset teacher identity mismatch")
    if manifest.get("teacher_policy_hash") != policy_sha:
        raise DerivedTeacherCatalogError("sealed teacher dataset policy SHA-256 mismatch")
    if manifest.get("teacher_source_kind") != source_kind:
        raise DerivedTeacherCatalogError("sealed teacher dataset source_kind mismatch")
    if manifest.get("teacher_usage_boundary") != "local_eval_only":
        raise DerivedTeacherCatalogError("sealed teacher dataset may not expand local_eval_only")
    if manifest.get("derivation_decision_ref") != DECISION_RELATIVE_PATH:
        raise DerivedTeacherCatalogError("sealed teacher dataset decision binding mismatch")
    permission, permission_bytes = _validate_permission(
        manifest, policy_sha=policy_sha, source_kind=source_kind
    )
    permission_id = _sha(
        permission.get("permission_manifest_id"), field="permission manifest id"
    )
    deck_path = _inside_root(root, deck["path"], field="deck path")
    subject_deck = manifest.get("subject_deck_csv_path")
    if type(subject_deck) is not str or Path(subject_deck).resolve() != deck_path:
        raise DerivedTeacherCatalogError("sealed teacher dataset deck binding mismatch")
    if (
        manifest.get("subject_deck_file_sha256") != deck["sha256"]
        or manifest.get("teacher_deck_file_sha256") != deck["sha256"]
    ):
        raise DerivedTeacherCatalogError("sealed teacher dataset deck SHA-256 mismatch")
    contract = _validate_collection_contract(
        root,
        manifest_path,
        manifest,
        teacher_id=teacher_id,
        source_kind=source_kind,
        policy_sha=policy_sha,
        deck_sha=deck["sha256"],
        permission=permission,
        permission_bytes=permission_bytes,
    )
    game_counts = {
        "requested": manifest.get("games_requested"),
        "completed": manifest.get("games_completed"),
        "faulted": manifest.get("games_faulted"),
        "unlabelled": manifest.get("decisions_unlabelled"),
        "other_status_count": len(manifest.get("games_other_status", ())),
    }
    if (
        game_counts["requested"] != 96
        or game_counts["completed"] != 96
        or game_counts["faulted"] != 0
        or game_counts["other_status_count"] != 0
        or type(game_counts["unlabelled"]) is not int
        or game_counts["unlabelled"] < 0
    ):
        raise DerivedTeacherCatalogError("teacher collection counts are not a 96/96 fault-free seal")
    seat_counts = manifest.get("seat_counts")
    if seat_counts != {"subject_first": 48, "subject_second": 48}:
        raise DerivedTeacherCatalogError("teacher collection seats are not balanced 48/48")
    omissions_path = _exact_path_in_directory(
        root,
        manifest.get("omissions_path"),
        manifest_path.parent,
        field="omissions path",
        name="omissions.jsonl",
    )
    if _sha_file(omissions_path, field="omissions") != _sha(
        manifest.get("omissions_sha256"), field="omissions SHA-256"
    ):
        raise DerivedTeacherCatalogError("teacher collection omissions SHA-256 mismatch")
    omissions = _strict_jsonl_rows(omissions_path, field="omissions ledger")
    sidecar_omissions = _validate_collection_sidecars(
        root,
        manifest_path,
        manifest,
        contract,
        teacher_id=teacher_id,
        policy_sha=policy_sha,
        deck_sha=deck["sha256"],
        permission_id=permission_id,
    )
    if len(omissions) != game_counts["unlabelled"] or len(sidecar_omissions) != len(omissions):
        raise DerivedTeacherCatalogError("teacher collection omission ledger count mismatch")
    if omissions != sidecar_omissions:
        raise DerivedTeacherCatalogError(
            "teacher collection omission ledger rows disagree with game sidecars"
        )
    snapshot = _validate_snapshot_artifacts(
        root,
        snapshot_path,
        manifest_path=manifest_path,
        manifest=manifest,
        teacher_id=teacher_id,
        policy_sha=policy_sha,
        source_kind=source_kind,
        deck_sha=deck["sha256"],
        permission=permission,
        permission_bytes=permission_bytes,
    )
    examples_total = snapshot.get("examples_total")
    split_counts = snapshot.get("split_counts")
    if type(examples_total) is not int or examples_total <= 0 or type(split_counts) is not dict:
        raise DerivedTeacherCatalogError("snapshot index examples or split counts are invalid")
    if set(split_counts) != {"train", "development", "test"} or any(type(value) is not int or value < 0 for value in split_counts.values()):
        raise DerivedTeacherCatalogError("snapshot index split counts are invalid")
    if sum(split_counts.values()) != examples_total or manifest.get("records_written") != examples_total:
        raise DerivedTeacherCatalogError("snapshot index counts do not bind teacher records")
    return {
        "status": "READY",
        "dataset_manifest": {
            "path": _relative(root, manifest_path),
            "file_sha256": _sha_file(manifest_path, field="sealed teacher dataset manifest"),
            "teacher_policy_sha256": policy_sha,
            "permission_manifest_id": permission_id,
        },
        "snapshot_index": {
            "path": _relative(root, snapshot_path),
            "file_sha256": _sha_file(snapshot_path, field="snapshot index"),
            "schema_version": "specialist-training-snapshot-index-v1",
            "examples_total": examples_total,
            "split_counts": dict(sorted(split_counts.items())),
        },
        "game_counts": game_counts,
        "seat_counts": dict(seat_counts),
    }


def _teacher_row(root: Path, spec: Mapping[str, str]) -> dict[str, object]:
    policy = _asset_binding(root, spec["teacher_id"], "main.py")
    deck = _asset_binding(root, spec["teacher_id"], "deck.csv")
    collection: dict[str, object]
    collection = _ready_collection(root, spec, policy, deck)
    return {
        "teacher_id": spec["teacher_id"],
        "archetype": spec["archetype"],
        "source_kind": spec["source_kind"],
        "teacher_usage_boundary": "local_eval_only",
        "derived_weights_allowed": True,
        "allowed_usages": ["training-local"],
        "teacher_code_submission_allowed": False,
        "deck_submission_allowed": False,
        "promotion_authority": False,
        "submission_authority": False,
        "policy": policy,
        "deck": deck,
        "collection": collection,
    }


def _validate_asset(root: Path, value: object, *, field: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != _ASSET_KEYS:
        raise DerivedTeacherCatalogError(f"{field} has an invalid closed schema")
    path = _inside_root(root, value.get("path"), field=f"{field}.path")
    supplied = _sha(value.get("sha256"), field=f"{field}.sha256")
    if _sha_file(path, field=field) != supplied:
        raise DerivedTeacherCatalogError(f"{field} SHA-256 mismatch")
    return {"path": _relative(root, path), "sha256": supplied}


def _validate_collection(root: Path, row: Mapping[str, object], *, teacher_id: str, policy: Mapping[str, str], deck: Mapping[str, str]) -> None:
    value = row.get("collection")
    if type(value) is not dict or set(value) != _COLLECTION_KEYS:
        raise DerivedTeacherCatalogError("collection has an invalid closed schema")
    spec = next(spec for spec in _TEACHERS if spec["teacher_id"] == teacher_id)
    if (
        value.get("status") != "READY"
        or type(value.get("dataset_manifest")) is not dict
        or set(value["dataset_manifest"]) != _SEALED_MANIFEST_KEYS
    ):
        raise DerivedTeacherCatalogError("READY teacher collection schema mismatch")
    if (
        type(value.get("snapshot_index")) is not dict
        or set(value["snapshot_index"]) != _SNAPSHOT_INDEX_KEYS
    ):
        raise DerivedTeacherCatalogError("READY snapshot index schema mismatch")
    if type(value.get("game_counts")) is not dict or set(value["game_counts"]) != _GAME_COUNT_KEYS:
        raise DerivedTeacherCatalogError("READY game counts schema mismatch")
    if type(value.get("seat_counts")) is not dict or set(value["seat_counts"]) != _SEAT_COUNT_KEYS:
        raise DerivedTeacherCatalogError("READY seat counts schema mismatch")
    expected = _ready_collection(root, spec, policy, deck)
    if value.get("dataset_manifest") != expected["dataset_manifest"]:
        raise DerivedTeacherCatalogError("sealed dataset manifest binding mismatch")
    if value.get("snapshot_index") != expected["snapshot_index"]:
        raise DerivedTeacherCatalogError("snapshot index file SHA-256 or binding mismatch")
    if value.get("game_counts") != expected["game_counts"]:
        raise DerivedTeacherCatalogError("READY game count binding mismatch")
    if value.get("seat_counts") != expected["seat_counts"]:
        raise DerivedTeacherCatalogError("READY seat count binding mismatch")


def _validate_payload(payload: object, root: Path) -> dict[str, object]:
    if type(payload) is not dict or set(payload) != _TOP_LEVEL_KEYS:
        raise DerivedTeacherCatalogError("catalog has an invalid closed schema; evaluation splits are not permitted")
    if payload.get("schema_version") != SCHEMA_V1:
        raise DerivedTeacherCatalogError("catalog schema_version mismatch")
    supplied_catalog_sha = _sha(payload.get("catalog_sha256"), field="catalog_sha256")
    if _catalog_sha(payload) != supplied_catalog_sha:
        raise DerivedTeacherCatalogError("catalog self SHA-256 mismatch")
    if payload.get("derived_weights_allowed") is not True or payload.get("allowed_usages") != ["training-local"]:
        raise DerivedTeacherCatalogError("catalog may grant only derived_weights_allowed/training-local")
    for field in ("training_authority", "promotion_authority", "submission_authority"):
        if _bool(payload.get(field), field=field):
            raise DerivedTeacherCatalogError(f"{field} must remain false")
    decision = payload.get("decision")
    if type(decision) is not dict or set(decision) != _ASSET_KEYS:
        raise DerivedTeacherCatalogError("decision has an invalid closed schema")
    if decision.get("path") != DECISION_RELATIVE_PATH:
        raise DerivedTeacherCatalogError("decision path mismatch")
    decision_path = _inside_root(root, decision.get("path"), field="decision.path")
    if _sha_file(decision_path, field="decision") != _sha(decision.get("sha256"), field="decision.sha256"):
        raise DerivedTeacherCatalogError("decision SHA-256 mismatch")
    _decision_semantics(decision_path)
    teachers = payload.get("teachers")
    if type(teachers) is not list or len(teachers) != len(_TEACHERS):
        raise DerivedTeacherCatalogError("catalog must contain exactly the closed teacher set")
    expected_specs = {spec["teacher_id"]: spec for spec in _TEACHERS}
    seen: set[str] = set()
    for row in teachers:
        if type(row) is not dict or set(row) != _TEACHER_KEYS:
            raise DerivedTeacherCatalogError("teacher has an invalid closed schema")
        teacher_id = _text(row.get("teacher_id"), field="teacher_id")
        if teacher_id in seen or teacher_id not in expected_specs:
            raise DerivedTeacherCatalogError("teacher identity is duplicate or outside closed set")
        seen.add(teacher_id)
        spec = expected_specs[teacher_id]
        for field in ("archetype", "source_kind"):
            if row.get(field) != spec[field]:
                raise DerivedTeacherCatalogError(f"{teacher_id} {field} mismatch")
        if row.get("teacher_usage_boundary") != "local_eval_only":
            raise DerivedTeacherCatalogError("teacher usage boundary may not expand local_eval_only")
        if row.get("derived_weights_allowed") is not True or row.get("allowed_usages") != ["training-local"]:
            raise DerivedTeacherCatalogError("teacher derived weight usage mismatch")
        for field in ("teacher_code_submission_allowed", "deck_submission_allowed", "promotion_authority", "submission_authority"):
            if _bool(row.get(field), field=field):
                raise DerivedTeacherCatalogError(f"{teacher_id} {field} must remain false")
        policy = _validate_asset(root, row.get("policy"), field=f"{teacher_id} policy")
        deck = _validate_asset(root, row.get("deck"), field=f"{teacher_id} deck")
        _validate_collection(root, row, teacher_id=teacher_id, policy=policy, deck=deck)
    if seen != set(expected_specs):
        raise DerivedTeacherCatalogError("catalog teacher set is incomplete")
    notes = payload.get("notes")
    if type(notes) is not list or not notes or any(type(note) is not str or not note for note in notes):
        raise DerivedTeacherCatalogError("catalog notes must be a non-empty string list")
    return payload


def build_derived_teacher_catalog_v1(
    repo_root: str | Path, *, output_path: str | Path, replace_existing: bool = False,
) -> dict[str, object]:
    """Build a new immutable catalog and verify it before publishing it.

    ``output_path`` must not exist unless ``replace_existing`` is explicitly
    selected after the new payload has passed the same full verification.  The
    caller receives no evaluation schedule and no authority capable of
    promoting or submitting a learned model.
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise DerivedTeacherCatalogError("repo_root must be a directory")
    output = Path(output_path).resolve()
    if output.exists() and not replace_existing:
        raise FileExistsError(f"refusing to overwrite catalog: {output}")
    decision_path = _inside_root(root, DECISION_RELATIVE_PATH, field="decision path")
    _decision_semantics(decision_path)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "decision": {"path": DECISION_RELATIVE_PATH, "sha256": _sha_file(decision_path, field="decision")},
        "derived_weights_allowed": True,
        "allowed_usages": ["training-local"],
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "teachers": [_teacher_row(root, spec) for spec in _TEACHERS],
        "notes": [
            "This catalog permits only teacher-derived weights for local training.",
            "Copied teacher code and source decks remain local_eval_only and are not submission assets.",
            "No evaluation split, promotion authority, or submission authority is present in this catalog.",
        ],
    }
    payload["catalog_sha256"] = _catalog_sha(payload)
    _validate_payload(payload, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return _strict_catalog_json(output)


def verify_derived_teacher_catalog_v1(path: str | Path, repo_root: str | Path) -> dict[str, object]:
    """Verify a catalog against the decision, code, deck, and sealed datasets."""
    root = Path(repo_root).resolve()
    payload = _strict_catalog_json(Path(path).resolve())
    return _validate_payload(payload, root)
