"""Hash-bound bridge from sealed specialist teachers to generic Student v2.

The bridge is deliberately an *audit gate*, not a training launcher.  It
revalidates the derived-teacher catalog, exact source bytes, permissions,
sealed snapshot shards, and every local record before it can publish an
``offline-scaleup-dataset-v2`` JSONL file.  If even one decision cannot be
decoded by the current Student v2 runtime, it publishes only an audit manifest
and refuses to create a partial performance-training dataset.

Student v2 consumes only ``rule_bc_example`` state/action fields.  Population
metadata (teacher identity, opponent identity, and candidate seat) remains
outside that object and therefore never enters ``gpu_student_v2._sample_from_row``.
Copied teacher code and decks are not copied into either output; only their
catalog-bound SHA-256 identities are retained as provenance.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
    verify_derived_teacher_catalog_v1,
)
from mage_ptcg.student.dataset import (
    DATASET_SCHEMA_VERSION,
    RuleBCExample,
    validate_example,
)


BRIDGE_SCHEMA_V1 = "meta-specialist-teacher-student-v2-bridge-v1"
OUTPUT_DATASET_SCHEMA = "offline-scaleup-dataset-v2"
SPLIT_NAMES = ("train", "validation", "test", "opponent_holdout", "deck_holdout")
SPLIT_WEIGHTS = (0.70, 0.10, 0.10, 0.05, 0.05)
DEFAULT_SPLIT_SEED = "derived-teacher-student-v2-split-v1-20260813"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INDEX_KEYS = frozenset({
    "schema_version", "dataset_snapshot_sha256", "manifest_id",
    "dataset_chunks", "source_artifacts", "examples_total", "split_names",
    "split_weights", "split_counts", "duplicate_cap", "shards",
})
_INDEX_CHUNK_KEYS = frozenset({
    "path", "dataset_snapshot_sha256", "manifest_id", "manifest_content_hash",
})
_INDEX_SHARD_KEYS = frozenset({"path", "snapshot_id", "examples", "split_counts"})


class TeacherSnapshotStudentV2BridgeError(ValueError):
    """Raised when a source or output violates the closed bridge contract."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TeacherSnapshotStudentV2BridgeError("value is not finite canonical JSON") from exc


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: object, *, domain: str) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical(value)).hexdigest()


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise TeacherSnapshotStudentV2BridgeError(f"{field} must be a lowercase SHA-256")
    return value


def _strict_json(path: Path, *, require_canonical: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_bytes()

        def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise TeacherSnapshotStudentV2BridgeError("duplicate JSON key")
                value[key] = item
            return value

        def reject_constant(value: str) -> object:
            raise TeacherSnapshotStudentV2BridgeError(f"non-finite JSON value: {value}")

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherSnapshotStudentV2BridgeError(f"unreadable strict JSON: {path}") from exc
    if type(payload) is not dict:
        raise TeacherSnapshotStudentV2BridgeError(f"JSON is not an object: {path}")
    if require_canonical and _canonical(payload) != raw:
        raise TeacherSnapshotStudentV2BridgeError(f"JSON is not canonical: {path}")
    return payload


def _inside_root(root: Path, value: object, *, field: str, base: Path | None = None) -> Path:
    if type(value) is not str or not value:
        raise TeacherSnapshotStudentV2BridgeError(f"{field} must be a non-empty path")
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [root / raw]
    if not raw.is_absolute() and base is not None:
        candidates.append(base / raw)
    resolved = next((candidate.resolve() for candidate in candidates if candidate.is_file()), candidates[0].resolve())
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TeacherSnapshotStudentV2BridgeError(f"{field} escapes repository root") from exc
    if not resolved.is_file():
        raise TeacherSnapshotStudentV2BridgeError(f"{field} is not a regular file")
    return resolved


def _atomic_write_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        for nonce in range(1024):
            candidate = path.with_name(f".{path.name}.tmp-{os.getpid()}-{nonce}")
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise TeacherSnapshotStudentV2BridgeError("could not reserve an atomic output")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def classify_student_v2_decoder_compatibility_v1(
    *,
    selection_type: object,
    selection_context: object,
    minimum: int,
    maximum: int,
    target_digests: Sequence[str],
    legal_digests: Sequence[str],
) -> dict[str, object]:
    """Describe exactly whether current Student v2 can reproduce one target.

    Fixed-cardinality unordered multi-select labels are represented without
    dropping positives: the bridge emits one candidate-order replica per
    selected digest, causing the existing single-target GPU converter to see
    every positive once.  Dynamic cardinality, optional decline, ordered Skill,
    and selected ActionKey alias collisions remain explicit unsupported cases.
    """
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or minimum < 0
        or maximum < minimum
        or maximum > len(legal_digests)
    ):
        raise TeacherSnapshotStudentV2BridgeError("selection cardinality is invalid")
    legal = tuple(_require_sha(value, field="legal action digest") for value in legal_digests)
    targets = tuple(_require_sha(value, field="target action digest") for value in target_digests)
    if len(targets) != len(set(targets)) or not set(targets).issubset(legal):
        raise TeacherSnapshotStudentV2BridgeError("target digests are duplicate or non-legal")
    if not minimum <= len(targets) <= maximum:
        raise TeacherSnapshotStudentV2BridgeError("target cardinality violates selection bounds")
    try:
        ordered = is_ordered_selection(selection_type, selection_context)
    except ValueError as exc:
        raise TeacherSnapshotStudentV2BridgeError("selection schema is not recognized") from exc
    if ordered:
        return {
            "status": "UNSUPPORTED",
            "reason": "ordered_selection_not_representable",
            "decoder_count": None,
            "replica_target_digests": [],
            "stop_semantics": "ordered_sequence",
        }
    if maximum == 0:
        return {
            "status": "NO_TRAINABLE_CHOICE",
            "reason": "forced_empty_selection",
            "decoder_count": 0,
            "replica_target_digests": [],
            "stop_semantics": "forced_stop",
        }
    decoder_count = minimum if minimum else 1
    if not targets:
        return {
            "status": "UNSUPPORTED",
            "reason": "optional_decline_not_representable",
            "decoder_count": decoder_count,
            "replica_target_digests": [],
            "stop_semantics": "optional_stop",
        }
    if len(targets) != decoder_count:
        return {
            "status": "UNSUPPORTED",
            "reason": "decoder_cardinality_mismatch",
            "decoder_count": decoder_count,
            "replica_target_digests": [],
            "stop_semantics": "dynamic_stop_required",
        }
    if any(legal.count(target) != 1 for target in targets):
        return {
            "status": "UNSUPPORTED",
            "reason": "target_action_alias_collision",
            "decoder_count": decoder_count,
            "replica_target_digests": [],
            "stop_semantics": "ambiguous_action_alias",
        }
    if minimum == maximum:
        stop = "fixed_cardinality"
    elif len(targets) < maximum:
        stop = "fixed_decoder_stop"
    else:
        stop = "at_maximum"
    return {
        "status": "SUPPORTED_MULTI_POSITIVE" if len(targets) > 1 else "SUPPORTED_SINGLE",
        "reason": None,
        "decoder_count": decoder_count,
        "replica_target_digests": sorted(targets),
        "stop_semantics": stop,
    }


def _require_snapshot_source_artifacts(
    value: object, *, teacher: Mapping[str, Any]
) -> dict[str, str]:
    """Validate legacy or exact v2b integrity source bindings.

    The formal catalog verifier cross-binds the artifact SHA values to files.
    This downstream reader independently keeps the source-kind vocabulary
    closed and rejects missing/unknown/repeated bindings before reading shards.
    """
    source_kind = teacher.get("source_kind")
    policy = teacher.get("policy")
    if type(source_kind) is not str or type(policy) is not dict:
        raise TeacherSnapshotStudentV2BridgeError(
            "catalog teacher source identity is invalid"
        )
    policy_sha = _require_sha(policy.get("sha256"), field="teacher policy SHA-256")
    if type(value) is not list or not value:
        raise TeacherSnapshotStudentV2BridgeError(
            "snapshot source artifacts must be a non-empty list"
        )
    bindings: dict[str, str] = {}
    for row in value:
        if type(row) is not dict or set(row) != {"kind", "artifact_sha256"}:
            raise TeacherSnapshotStudentV2BridgeError(
                "snapshot source artifact has an invalid closed schema"
            )
        kind = row.get("kind")
        if type(kind) is not str or not kind or kind in bindings:
            raise TeacherSnapshotStudentV2BridgeError(
                "snapshot source artifact kind is empty or duplicated"
            )
        bindings[kind] = _require_sha(
            row.get("artifact_sha256"), field="snapshot source artifact SHA-256"
        )
    if bindings.get(source_kind) != policy_sha:
        raise TeacherSnapshotStudentV2BridgeError(
            "snapshot teacher policy source binding mismatch"
        )
    # Legacy v1 catalog/snapshot compatibility is read-only; formal catalog v2b
    # rejects it before this helper can grant access to a fresh build.
    if set(bindings) == {source_kind}:
        return bindings
    hardened = {
        source_kind,
        "teacher_collection_manifest_v2",
        "teacher_collection_contract_v2",
        "teacher_collection_omissions_v2",
        "teacher_collector_source_snapshot_v2",
        "teacher_permission_trusted_bytes_v1",
        f"teacher_source_kind:{source_kind}",
    }
    if set(bindings) != hardened:
        raise TeacherSnapshotStudentV2BridgeError(
            "snapshot source artifacts do not match the closed hardened binding set"
        )
    return bindings


def _snapshot_source(
    *,
    root: Path,
    teacher: Mapping[str, Any],
    teacher_manifest_path: Path,
    permission_id: str,
    permission_raw_sha256: str,
) -> dict[str, Any]:
    from mage_ptcg.meta_specialist.training_snapshot_v1 import (
        corpus_dataset_sha256_v1,
        read_training_snapshot_v1,
    )

    index_path = teacher_manifest_path.parent / "snapshot_index.json"
    index = _strict_json(index_path, require_canonical=True)
    if set(index) != _INDEX_KEYS or index.get("schema_version") != "specialist-training-snapshot-index-v1":
        raise TeacherSnapshotStudentV2BridgeError("snapshot index has an invalid closed schema")
    chunks = index.get("dataset_chunks")
    shards = index.get("shards")
    if type(chunks) is not list or not chunks or type(shards) is not list or not shards:
        raise TeacherSnapshotStudentV2BridgeError("snapshot index has no chunks or shards")
    chunk_paths: list[Path] = []
    chunk_hashes: list[str] = []
    chunk_bindings: list[dict[str, object]] = []
    for position, row in enumerate(chunks):
        if type(row) is not dict or set(row) != _INDEX_CHUNK_KEYS:
            raise TeacherSnapshotStudentV2BridgeError("snapshot dataset chunk schema mismatch")
        path = _inside_root(root, row.get("path"), field="dataset chunk", base=index_path.parent)
        supplied = _require_sha(row.get("dataset_snapshot_sha256"), field="dataset chunk SHA-256")
        actual = _sha_file(path)
        if actual != supplied:
            raise TeacherSnapshotStudentV2BridgeError("dataset chunk SHA-256 mismatch")
        chunk_paths.append(path)
        chunk_hashes.append(actual)
        chunk_bindings.append({
            "position": position,
            "sha256": actual,
            "manifest_id": _require_sha(row.get("manifest_id"), field="chunk manifest id"),
            "manifest_content_hash": _require_sha(
                row.get("manifest_content_hash"), field="chunk manifest content hash"
            ),
        })
    if corpus_dataset_sha256_v1(chunk_hashes) != index.get("dataset_snapshot_sha256"):
        raise TeacherSnapshotStudentV2BridgeError("corpus dataset SHA-256 does not verify")

    expected_sources = index.get("source_artifacts")
    _require_snapshot_source_artifacts(expected_sources, teacher=teacher)
    snapshot_records: dict[str, dict[str, object]] = {}
    aggregate_counts: Counter[str] = Counter()
    shard_bindings: list[dict[str, object]] = []
    qualification_times: set[str] = set()
    for row in shards:
        if type(row) is not dict or set(row) != _INDEX_SHARD_KEYS:
            raise TeacherSnapshotStudentV2BridgeError("snapshot shard schema mismatch")
        path = _inside_root(root, row.get("path"), field="snapshot shard", base=index_path.parent)
        snapshot = read_training_snapshot_v1(path)
        if snapshot.get("snapshot_id") != row.get("snapshot_id"):
            raise TeacherSnapshotStudentV2BridgeError("snapshot shard identity mismatch")
        examples = snapshot.get("examples")
        if type(examples) is not list or len(examples) != row.get("examples"):
            raise TeacherSnapshotStudentV2BridgeError("snapshot shard example count mismatch")
        if snapshot.get("split_counts") != row.get("split_counts"):
            raise TeacherSnapshotStudentV2BridgeError("snapshot shard split count mismatch")
        for field in ("dataset_snapshot_sha256", "manifest_id", "source_artifacts", "duplicate_cap", "split_names", "split_weights"):
            if snapshot.get(field) != index.get(field):
                raise TeacherSnapshotStudentV2BridgeError(f"snapshot shard {field} mismatch")
        permissions = snapshot.get("permissions")
        if type(permissions) is not list or not any(
            type(item) is dict
            and item.get("permission_manifest_id") == permission_id
            and item.get("permission_trusted_bytes_sha256") == permission_raw_sha256
            for item in permissions
        ):
            raise TeacherSnapshotStudentV2BridgeError("snapshot permission exact-byte binding mismatch")
        qualification = snapshot.get("qualification_time_utc")
        if type(qualification) is not str or not qualification:
            raise TeacherSnapshotStudentV2BridgeError("snapshot qualification time is missing")
        qualification_times.add(qualification)
        for example in examples:
            record_id = _require_sha(example.get("record_id"), field="snapshot record id")
            if record_id in snapshot_records:
                raise TeacherSnapshotStudentV2BridgeError("snapshot record id is duplicated")
            snapshot_records[record_id] = {
                "record_content_hash": _require_sha(
                    example.get("record_content_hash"), field="snapshot record content hash"
                ),
                "episode_id_hash": _require_sha(
                    example.get("episode_id_hash"), field="snapshot episode id"
                ),
                "near_duplicate_id": _require_sha(
                    example.get("near_duplicate_id"),
                    field="snapshot near-duplicate id",
                ),
                "split": example.get("split"),
                "value_target": example.get("value_target"),
                "example_quality_weight": example.get("example_quality_weight"),
            }
        aggregate_counts.update(snapshot["split_counts"])
        shard_bindings.append({
            "sha256": _sha_file(path),
            "snapshot_id": snapshot["snapshot_id"],
            "examples": len(examples),
        })
    if len(qualification_times) != 1:
        raise TeacherSnapshotStudentV2BridgeError("snapshot shards disagree on qualification time")
    if len(snapshot_records) != index.get("examples_total"):
        raise TeacherSnapshotStudentV2BridgeError("snapshot index total does not match its shards")
    if dict(aggregate_counts) != index.get("split_counts"):
        raise TeacherSnapshotStudentV2BridgeError("snapshot index split counts do not match its shards")
    return {
        "index_path": index_path,
        "index_sha256": _sha_file(index_path),
        "dataset_snapshot_sha256": index["dataset_snapshot_sha256"],
        "chunk_paths": chunk_paths,
        "chunk_bindings": chunk_bindings,
        "shard_bindings": shard_bindings,
        "snapshot_records": snapshot_records,
        "split_names": tuple(index["split_names"]),
        "split_counts": dict(index["split_counts"]),
        "ubiquitous_near_duplicate_ids": tuple(
            index["duplicate_cap"]["ubiquitous_near_duplicate_ids"]
        ),
        "qualification_time_utc": next(iter(qualification_times)),
    }


def _state_from_record(record: Mapping[str, Any]) -> Any:
    from mage_ptcg.meta_specialist.actor_visible_v2 import (
        deserialize_actor_visible_decision_state_v2,
    )

    legal_actions = record["legal_actions"]
    counts = Counter(row["public_action_id"] for row in legal_actions)
    payload = {
        "schema_version": 2,
        "information_view": record["information_state"],
        "legal_actions": [
            {
                "binding": {
                    "core": row["actor_binding"],
                    "action_key_digest": row["action_key_digest"],
                    "public_action_id": row["public_action_id"],
                    "local_action_id": row["local_action_id"],
                },
                "action_key": {
                    "payload": row["action_key_payload"],
                    "digest": row["action_key_digest"],
                },
            }
            for row in legal_actions
        ],
        "public_collision_groups": [
            [public_id, count]
            for public_id, count in sorted(counts.items())
            if count > 1
        ],
    }
    return deserialize_actor_visible_decision_state_v2(payload)


def _hard_target(record: Mapping[str, Any], *, teacher_id: str) -> tuple[str, ...] | None:
    teacher = record["teacher"]
    if teacher.get("status") != "available" or teacher.get("teacher_id") != teacher_id:
        raise TeacherSnapshotStudentV2BridgeError("record teacher identity/status mismatch")
    if teacher.get("target_kind") != "hard_selection":
        return None
    rows = teacher.get("mass_rows")
    if type(rows) is not list or len(rows) != 1 or rows[0].get("weight") != 1:
        return None
    selection = rows[0].get("selection")
    if type(selection) is not list or any(type(value) is not str for value in selection):
        raise TeacherSnapshotStudentV2BridgeError("teacher selection has an invalid schema")
    return tuple(selection)


def _require_record_teacher_binding(
    record: Mapping[str, Any], *, teacher: Mapping[str, Any]
) -> None:
    """Cross-bind a qualified raw record to the catalog teacher identity."""
    source = record.get("source")
    if type(source) is not dict:
        raise TeacherSnapshotStudentV2BridgeError("record source is missing")
    if source.get("kind") != teacher.get("source_kind"):
        raise TeacherSnapshotStudentV2BridgeError(
            "record source kind does not bind catalog teacher source kind"
        )
    policy = teacher.get("policy")
    if type(policy) is not dict or source.get("artifact_sha256") != policy.get("sha256"):
        raise TeacherSnapshotStudentV2BridgeError(
            "record source artifact does not bind catalog teacher policy SHA-256"
        )


def _require_snapshot_record_match(
    record: Mapping[str, Any],
    *,
    snapshots: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    """Revalidate raw bytes against the sealed snapshot on every read pass."""
    record_id = _require_sha(record.get("record_id"), field="record id")
    sealed = snapshots.get(record_id)
    if sealed is None:
        raise TeacherSnapshotStudentV2BridgeError(
            "raw record is absent from sealed snapshot"
        )
    teacher = record.get("teacher")
    if type(teacher) is not dict:
        raise TeacherSnapshotStudentV2BridgeError("record teacher payload is missing")
    if (
        record.get("content_hash") != sealed["record_content_hash"]
        or record.get("episode_id_hash") != sealed["episode_id_hash"]
        or record.get("near_duplicate_id") != sealed["near_duplicate_id"]
        or teacher.get("value_target") != sealed["value_target"]
    ):
        raise TeacherSnapshotStudentV2BridgeError(
            "raw record and snapshot example disagree"
        )
    return sealed


def _require_teacher_manifest_counts(
    manifest: Mapping[str, Any], *, teacher: Mapping[str, Any]
) -> None:
    """Bind collection counts without treating explicit omissions as faults."""
    collection = teacher.get("collection")
    expected = collection.get("game_counts") if type(collection) is dict else None
    other_status = manifest.get("games_other_status")
    if type(expected) is not dict or type(other_status) is not list:
        raise TeacherSnapshotStudentV2BridgeError(
            "catalog/teacher manifest game count schema is invalid"
        )
    observed = {
        "requested": manifest.get("games_requested"),
        "completed": manifest.get("games_completed"),
        "faulted": manifest.get("games_faulted"),
        "unlabelled": manifest.get("decisions_unlabelled"),
        "other_status_count": len(other_status),
    }
    if observed != expected:
        raise TeacherSnapshotStudentV2BridgeError(
            "teacher manifest counts do not match the verified catalog"
        )
    if observed["faulted"] != 0 or observed["completed"] != observed["requested"]:
        raise TeacherSnapshotStudentV2BridgeError(
            "teacher collection is not completed and fault-clean"
        )


def _rule_example(
    record: Mapping[str, Any],
    *,
    teacher: Mapping[str, Any],
    target_local_ids: Sequence[str],
) -> RuleBCExample:
    from mage_ptcg.meta_specialist.actor_visible_v2 import (
        project_c1v2_to_c1v1_own_private_state,
        project_c1v2_to_c1v1_public_state,
    )

    state = _state_from_record(record)
    legal_rows = record["legal_actions"]
    by_local = {row["local_action_id"]: row for row in legal_rows}
    try:
        target_digests = tuple(by_local[value]["action_key_digest"] for value in target_local_ids)
    except KeyError as exc:
        raise TeacherSnapshotStudentV2BridgeError("teacher target local action is missing") from exc
    if not is_ordered_selection(
        record["information_state"]["selection_type"],
        record["information_state"]["selection_context"],
    ):
        target_digests = tuple(sorted(target_digests))
    actions = tuple({
        "digest": row["action_key_digest"],
        "payload": row["action_key_payload"],
    } for row in legal_rows)
    example = RuleBCExample(
        schema_version=DATASET_SCHEMA_VERSION,
        example_id=record["record_id"],
        source_id=record["episode_id_hash"],
        public_state=project_c1v2_to_c1v1_public_state(state),
        own_private_state=project_c1v2_to_c1v1_own_private_state(state),
        visible_history=(),
        selection_type=record["information_state"]["selection_type"],
        selection_context=record["information_state"]["selection_context"],
        min_count=record["information_state"]["min_count"],
        max_count=record["information_state"]["max_count"],
        legal_actions=actions,
        target_action_digests=target_digests,
        teacher_ranking=tuple((digest, 0) for digest in sorted({row["digest"] for row in actions})),
        fallback_used=False,
        deck_fingerprint=teacher["deck"]["sha256"],
        source_revision=teacher["policy"]["sha256"],
        metadata={
            "bridge_schema": BRIDGE_SCHEMA_V1,
            "source_record_sha256": record["content_hash"],
        },
    )
    validate_example(example)
    return example


def _outcome(value: object) -> str:
    if value == 1.0:
        return "WIN"
    if value == -1.0:
        return "LOSS"
    if value == 0.0:
        return "DRAW"
    if value is None:
        return "UNKNOWN"
    raise TeacherSnapshotStudentV2BridgeError("teacher value_target is outside {-1,0,1,null}")


def _replica_rows(
    *,
    record: Mapping[str, Any],
    teacher: Mapping[str, Any],
    example: RuleBCExample,
    split: str,
    replica_digests: Sequence[str],
    quality_weight: float,
) -> Iterable[dict[str, object]]:
    original = list(example.legal_actions)
    target_set = set(example.target_action_digests)
    for position, desired in enumerate(replica_digests):
        desired_rows = [row for row in original if row["digest"] == desired]
        if len(desired_rows) != 1:
            raise TeacherSnapshotStudentV2BridgeError("replica target is not a unique legal action")
        reordered = [
            desired_rows[0],
            *(row for row in original if row["digest"] not in target_set),
            *(row for row in original if row["digest"] in target_set and row["digest"] != desired),
        ]
        replica = replace(example, legal_actions=tuple(reordered))
        validate_example(replica)
        yield {
            "schema_version": OUTPUT_DATASET_SCHEMA,
            "episode_id": record["episode_id_hash"],
            "game_id": record["episode_id_hash"],
            "split": split,
            "state_fingerprint": record["decision_id"],
            "candidate_side": None,
            "opponent_id": None,
            "opponent_type": None,
            "teacher_identity": teacher["teacher_id"],
            "teacher_type": teacher["source_kind"],
            "teacher_trust": "DERIVATION_QUALIFIED",
            "candidate_deck_fingerprint": teacher["deck"]["sha256"],
            "candidate_outcome": _outcome(record["teacher"].get("value_target")),
            "sample_weight": quality_weight,
            "selected_action": list(example.target_action_digests),
            "legal_action_candidates": list(replica.legal_actions),
            "target_replica_index": position,
            "target_replica_count": len(replica_digests),
            "target_replica_digest": desired,
            "rule_bc_example": replica.to_dict(),
            "provenance": {
                "bridge_schema": BRIDGE_SCHEMA_V1,
                "catalog_sha256": None,
                "teacher_policy_sha256": teacher["policy"]["sha256"],
                "teacher_deck_sha256": teacher["deck"]["sha256"],
                "source_record_sha256": record["content_hash"],
                "native_code_bundled": False,
                "native_deck_bundled": False,
                "training_authority": False,
                "promotion_authority": False,
                "submission_authority": False,
            },
        }


def _iter_chunk_records(path: Path) -> Iterable[dict[str, Any]]:
    from mage_ptcg.meta_specialist.local_dataset_v2 import parse_canonical_json_bytes_v2

    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith(b"\n") or line == b"\n":
                raise TeacherSnapshotStudentV2BridgeError(
                    f"dataset chunk has malformed line framing: {path}:{line_number}"
                )
            payload = parse_canonical_json_bytes_v2(line[:-1])
            if type(payload) is not dict:
                raise TeacherSnapshotStudentV2BridgeError("dataset record must be an object")
            yield payload


def _split_assignments(
    episodes_by_teacher: Mapping[str, set[str]], *, seed: str
) -> tuple[dict[str, str], list[str]]:
    assignments: dict[str, str] = {}
    blockers: list[str] = []
    for teacher_id, episodes in sorted(episodes_by_teacher.items()):
        ordered = sorted(
            episodes,
            key=lambda episode: _digest(
                {"seed": seed, "teacher_id": teacher_id, "episode_id": episode},
                domain="teacher-student-v2-episode-split-v1",
            ),
        )
        if len(ordered) < len(SPLIT_NAMES):
            blockers.append(f"{teacher_id}:fewer_than_five_episodes")
            continue
        counts = [1] * len(SPLIT_NAMES)
        remaining = len(ordered) - len(SPLIT_NAMES)
        raw = [remaining * weight for weight in SPLIT_WEIGHTS]
        additions = [int(value) for value in raw]
        for index, value in enumerate(additions):
            counts[index] += value
        leftover = len(ordered) - sum(counts)
        order = sorted(
            range(len(SPLIT_NAMES)),
            key=lambda index: (-(raw[index] - additions[index]), index),
        )
        for index in order[:leftover]:
            counts[index] += 1
        offset = 0
        for split, count in zip(SPLIT_NAMES, counts, strict=True):
            for episode in ordered[offset:offset + count]:
                if episode in assignments:
                    raise TeacherSnapshotStudentV2BridgeError(
                        "one episode identity is shared by multiple teachers"
                    )
                assignments[episode] = split
            offset += count
    return assignments, blockers


def _selected_teachers(
    catalog: Mapping[str, Any], teacher_ids: Sequence[str]
) -> list[dict[str, Any]]:
    # ``catalog`` is the return value of verify_derived_teacher_catalog_v1.
    # Its collection state is a closed contract owned by that loader; do not
    # duplicate the loader's status vocabulary here.  In particular, the
    # physical snapshots are sealed while the catalog state is ``READY``.
    rows = {row["teacher_id"]: row for row in catalog["teachers"]}
    requested = tuple(teacher_ids) if teacher_ids else tuple(sorted(rows))
    if not requested or len(requested) != len(set(requested)):
        raise TeacherSnapshotStudentV2BridgeError("teacher selection is empty or duplicated")
    unknown = set(requested) - set(rows)
    if unknown:
        raise TeacherSnapshotStudentV2BridgeError(f"unknown teacher ids: {sorted(unknown)}")
    return [rows[teacher_id] for teacher_id in requested]


def build_teacher_snapshot_student_v2_bridge_v1(
    *,
    repo_root: str | Path,
    catalog_path: str | Path,
    output_dataset_path: str | Path,
    output_manifest_path: str | Path,
    teacher_ids: Sequence[str] = (),
    split_seed: str = DEFAULT_SPLIT_SEED,
) -> dict[str, object]:
    """Audit sealed teachers and publish a complete direct Student v2 dataset.

    ``output_dataset_path`` is created only when every selected decision is
    representable.  ``output_manifest_path`` is always the canonical audit
    result for a successfully inspected source set.  Neither output grants
    training, promotion, packaging, or submission authority.
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise TeacherSnapshotStudentV2BridgeError("repo_root must be a directory")
    catalog_file = Path(catalog_path).resolve()
    dataset_path = Path(output_dataset_path).resolve()
    manifest_path = Path(output_manifest_path).resolve()
    if dataset_path == manifest_path:
        raise TeacherSnapshotStudentV2BridgeError("dataset and manifest outputs must differ")
    if dataset_path.exists() or manifest_path.exists():
        raise FileExistsError("bridge outputs already exist")
    if type(split_seed) is not str or not split_seed:
        raise TeacherSnapshotStudentV2BridgeError("split_seed must be a non-empty string")
    catalog_file_sha = _sha_file(catalog_file)
    catalog = verify_derived_teacher_catalog_v1(catalog_file, root)
    if _sha_file(catalog_file) != catalog_file_sha:
        raise TeacherSnapshotStudentV2BridgeError(
            "catalog file changed during formal verification"
        )
    selected = _selected_teachers(catalog, teacher_ids)

    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        load_production_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )

    vocabulary = load_production_card_vocabulary_v1()
    sources: list[dict[str, object]] = []
    runtime: dict[str, dict[str, Any]] = {}
    seen_record_ids: set[str] = set()
    seen_snapshot_ids: set[str] = set()
    episodes_by_teacher: dict[str, set[str]] = defaultdict(set)
    compatibility: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    no_choice: Counter[str] = Counter()
    total_rows = 0

    for teacher in selected:
        collection = teacher["collection"]["dataset_manifest"]
        teacher_manifest_path = _inside_root(
            root, collection["path"], field="teacher dataset manifest"
        )
        if _sha_file(teacher_manifest_path) != collection["file_sha256"]:
            raise TeacherSnapshotStudentV2BridgeError("teacher dataset manifest SHA-256 mismatch")
        teacher_manifest = _strict_json(teacher_manifest_path)
        permission = teacher_manifest.get("permission_manifest")
        if type(permission) is not dict:
            raise TeacherSnapshotStudentV2BridgeError("teacher permission manifest is missing")
        permission_bytes = canonical_json_bytes_v2(permission)
        trusted = build_trusted_permission_set_v1((permission_bytes,))
        permission_id = _require_sha(
            collection.get("permission_manifest_id"), field="permission manifest id"
        )
        if permission_id not in trusted:
            raise TeacherSnapshotStudentV2BridgeError("catalog permission id is not in trusted bytes")
        snapshot = _snapshot_source(
            root=root,
            teacher=teacher,
            teacher_manifest_path=teacher_manifest_path,
            permission_id=permission_id,
            permission_raw_sha256=hashlib.sha256(permission_bytes).hexdigest(),
        )
        source_seen: set[str] = set()
        source_records = 0
        source_episodes: set[str] = set()
        for chunk_path in snapshot["chunk_paths"]:
            for record in _iter_chunk_records(chunk_path):
                source_records += 1
                record_id = _require_sha(record.get("record_id"), field="record id")
                if record_id in seen_record_ids:
                    raise TeacherSnapshotStudentV2BridgeError("record id is duplicated across sources")
                seen_record_ids.add(record_id)
                source_seen.add(record_id)
                _require_snapshot_record_match(
                    record, snapshots=snapshot["snapshot_records"]
                )
                require_qualified_training_record_v2(
                    record,
                    vocabulary=vocabulary,
                    trusted_permissions=trusted,
                    qualification_time_utc=snapshot["qualification_time_utc"],
                )
                _require_record_teacher_binding(record, teacher=teacher)
                target_local = _hard_target(record, teacher_id=teacher["teacher_id"])
                if target_local is None:
                    compatibility["source_decisions"] += 1
                    unsupported["probabilistic_teacher_target_not_representable"] += 1
                    continue
                example = _rule_example(record, teacher=teacher, target_local_ids=target_local)
                result = classify_student_v2_decoder_compatibility_v1(
                    selection_type=example.selection_type,
                    selection_context=example.selection_context,
                    minimum=example.min_count,
                    maximum=example.max_count,
                    target_digests=example.target_action_digests,
                    legal_digests=tuple(row["digest"] for row in example.legal_actions),
                )
                compatibility["source_decisions"] += 1
                status = str(result["status"])
                if status == "UNSUPPORTED":
                    unsupported[str(result["reason"])] += 1
                elif status == "NO_TRAINABLE_CHOICE":
                    no_choice[str(result["reason"])] += 1
                else:
                    compatibility["supported_decisions"] += 1
                    if status == "SUPPORTED_MULTI_POSITIVE":
                        compatibility["supported_multi_positive_decisions"] += 1
                    else:
                        compatibility["supported_single_decisions"] += 1
                    replicas = result["replica_target_digests"]
                    total_rows += len(replicas)
                    compatibility["would_emit_rows"] += len(replicas)
                    source_episodes.add(record["episode_id_hash"])
                    episodes_by_teacher[teacher["teacher_id"]].add(record["episode_id_hash"])
        if source_seen != set(snapshot["snapshot_records"]):
            raise TeacherSnapshotStudentV2BridgeError("sealed snapshot has raw records missing")
        if source_records != teacher_manifest.get("records_written"):
            raise TeacherSnapshotStudentV2BridgeError("teacher manifest record count mismatch")
        _require_teacher_manifest_counts(teacher_manifest, teacher=teacher)
        sources.append({
            "teacher_id": teacher["teacher_id"],
            "archetype": teacher["archetype"],
            "source_kind": teacher["source_kind"],
            "policy_sha256": teacher["policy"]["sha256"],
            "deck_sha256": teacher["deck"]["sha256"],
            "teacher_manifest_sha256": _sha_file(teacher_manifest_path),
            "permission_manifest_id": permission_id,
            "permission_trusted_bytes_sha256": hashlib.sha256(permission_bytes).hexdigest(),
            "snapshot_index_sha256": snapshot["index_sha256"],
            "dataset_snapshot_sha256": snapshot["dataset_snapshot_sha256"],
            "dataset_chunks": snapshot["chunk_bindings"],
            "snapshot_shards": snapshot["shard_bindings"],
            "source_records": source_records,
            "source_episodes": len({value["episode_id_hash"] for value in snapshot["snapshot_records"].values()}),
            "representable_episodes": len(source_episodes),
            "native_code_bundled": False,
            "native_deck_bundled": False,
        })
        runtime[teacher["teacher_id"]] = {
            "teacher": teacher,
            "trusted": trusted,
            "snapshot": snapshot,
        }

    assignments, split_blockers = _split_assignments(episodes_by_teacher, seed=split_seed)
    blockers = list(split_blockers)
    if unsupported:
        blockers.append("unsupported_decisions_present")
    if total_rows == 0:
        blockers.append("no_trainable_rows")
    training_ready = not blockers
    output_sha: str | None = None
    output_name: str | None = None
    if training_ready:
        lines: list[bytes] = []
        # The bridge reaches this branch only for a fully representable corpus.
        # Revalidate exact source records again rather than caching hundreds of
        # MB of mutable dictionaries from the audit pass.
        for teacher_id in sorted(runtime):
            bundle = runtime[teacher_id]
            teacher = bundle["teacher"]
            snapshot = bundle["snapshot"]
            trusted = bundle["trusted"]
            write_seen: set[str] = set()
            for chunk_path in snapshot["chunk_paths"]:
                for record in _iter_chunk_records(chunk_path):
                    record_id = _require_sha(record.get("record_id"), field="record id")
                    if record_id in write_seen:
                        raise TeacherSnapshotStudentV2BridgeError(
                            "write-pass record id is duplicated"
                        )
                    write_seen.add(record_id)
                    sealed = _require_snapshot_record_match(
                        record, snapshots=snapshot["snapshot_records"]
                    )
                    require_qualified_training_record_v2(
                        record,
                        vocabulary=vocabulary,
                        trusted_permissions=trusted,
                        qualification_time_utc=snapshot["qualification_time_utc"],
                    )
                    _require_record_teacher_binding(record, teacher=teacher)
                    target_local = _hard_target(record, teacher_id=teacher_id)
                    if target_local is None:
                        raise TeacherSnapshotStudentV2BridgeError("audit/write teacher target drift")
                    example = _rule_example(record, teacher=teacher, target_local_ids=target_local)
                    result = classify_student_v2_decoder_compatibility_v1(
                        selection_type=example.selection_type,
                        selection_context=example.selection_context,
                        minimum=example.min_count,
                        maximum=example.max_count,
                        target_digests=example.target_action_digests,
                        legal_digests=tuple(row["digest"] for row in example.legal_actions),
                    )
                    if result["status"] == "NO_TRAINABLE_CHOICE":
                        continue
                    if result["status"] == "UNSUPPORTED":
                        raise TeacherSnapshotStudentV2BridgeError("audit/write compatibility drift")
                    quality = sealed["example_quality_weight"]
                    if type(quality) not in (int, float) or not 0.0 < float(quality) <= 1.0:
                        raise TeacherSnapshotStudentV2BridgeError("snapshot quality weight is invalid")
                    for row in _replica_rows(
                        record=record,
                        teacher=teacher,
                        example=example,
                        split=assignments[record["episode_id_hash"]],
                        replica_digests=result["replica_target_digests"],
                        quality_weight=float(quality),
                    ):
                        row["provenance"]["catalog_sha256"] = catalog["catalog_sha256"]
                        lines.append(_canonical(row) + b"\n")
            if write_seen != set(snapshot["snapshot_records"]):
                raise TeacherSnapshotStudentV2BridgeError(
                    "write pass did not cover the complete snapshot"
                )
        if len(lines) != total_rows:
            raise TeacherSnapshotStudentV2BridgeError("audit/write row count drift")
        _atomic_write_new(dataset_path, b"".join(lines))
        output_sha = _sha_file(dataset_path)
        output_name = str(dataset_path)

    manifest: dict[str, object] = {
        "schema_version": BRIDGE_SCHEMA_V1,
        "catalog_file_sha256": catalog_file_sha,
        "catalog_sha256": catalog["catalog_sha256"],
        "decision_sha256": catalog["decision"]["sha256"],
        "selected_teacher_ids": [row["teacher_id"] for row in selected],
        "sources": sources,
        "trainer_contract": {
            "dataset_schema": OUTPUT_DATASET_SCHEMA,
            "direct_consumer": "mage_ptcg.offline_scaleup.gpu_student_v2.build_dataset",
            "runtime_decoder": "mage_ptcg.offline_scaleup.student_v2_runtime.StudentV2CandidatePolicy",
            "multi_positive_encoding": "one candidate-order replica per selected digest",
        },
        "feature_boundary": {
            "model_inputs": [
                "rule_bc_example.public_state",
                "rule_bc_example.own_private_state",
                "rule_bc_example.visible_history",
                "rule_bc_example.legal_actions",
            ],
            "metadata_excluded_from_features": [
                "opponent_id", "candidate_side", "teacher_identity",
            ],
        },
        "compatibility": {
            "source_decisions": compatibility["source_decisions"],
            "supported_decisions": compatibility["supported_decisions"],
            "supported_single_decisions": compatibility["supported_single_decisions"],
            "supported_multi_positive_decisions": compatibility["supported_multi_positive_decisions"],
            "would_emit_rows": compatibility["would_emit_rows"],
            "no_trainable_choice_by_reason": dict(sorted(no_choice.items())),
            "unsupported_by_reason": dict(sorted(unsupported.items())),
        },
        "split": {
            "algorithm": "teacher-stratified-episode-atomic-v1",
            "seed": split_seed,
            "names": list(SPLIT_NAMES),
            "weights": list(SPLIT_WEIGHTS),
            "episode_counts": dict(Counter(assignments.values())),
            "episode_leakage": 0,
        },
        "performance_training_ready": training_ready,
        "blocked_reasons": sorted(set(blockers)),
        "output_dataset": output_name,
        "output_dataset_sha256": output_sha,
        "output_rows": total_rows if training_ready else 0,
        "partial_dataset_published": False,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "teacher_code_submission_allowed": False,
            "teacher_deck_submission_allowed": False,
        },
        "bridge_sha256": None,
    }
    manifest["bridge_sha256"] = _digest(
        {key: value for key, value in manifest.items() if key != "bridge_sha256"},
        domain="meta-specialist-teacher-student-v2-bridge-v1",
    )
    try:
        if _sha_file(catalog_file) != catalog_file_sha:
            raise TeacherSnapshotStudentV2BridgeError(
                "catalog file changed during bridge execution"
            )
        _atomic_write_new(manifest_path, _canonical(manifest))
    except BaseException:
        if output_name is not None:
            dataset_path.unlink(missing_ok=True)
        raise
    return manifest


__all__ = [
    "BRIDGE_SCHEMA_V1",
    "DEFAULT_SPLIT_SEED",
    "OUTPUT_DATASET_SCHEMA",
    "SPLIT_NAMES",
    "TeacherSnapshotStudentV2BridgeError",
    "build_teacher_snapshot_student_v2_bridge_v1",
    "classify_student_v2_decoder_compatibility_v1",
]
