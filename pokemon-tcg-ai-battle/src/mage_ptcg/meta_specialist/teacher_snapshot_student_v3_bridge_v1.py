"""Strict derived-teacher bridge for generic Student v3 set targets.

The formal derived-teacher catalog loader owns the closed teacher and
collection-state vocabulary.  This bridge revalidates its result and every
bound raw/snapshot record, then emits at most one actor-visible source row per
decision.  Optional decline and variable/fixed unordered selections are
lossless.  Ordered selections remain an explicit dataset-wide NO-GO until an
ordered pointer head exists.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
    verify_derived_teacher_catalog_v1,
)
from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
    _atomic_write_new,
    _canonical,
    _digest,
    _hard_target,
    _inside_root,
    _iter_chunk_records,
    _outcome,
    _require_sha,
    _require_record_teacher_binding,
    _require_snapshot_record_match,
    _require_teacher_manifest_counts,
    _rule_example,
    _selected_teachers,
    _sha_file,
    _snapshot_source,
    _strict_json,
)
from mage_ptcg.offline_scaleup.gpu_student_v3_set import PURPOSE, SOURCE_SCHEMA
from mage_ptcg.student.dataset import RuleBCExample, validate_example


BRIDGE_SCHEMA_V1 = "meta-specialist-teacher-student-v3-set-bridge-v2"
DEFAULT_V3_SPLIT_SEED = "sealed-training-snapshot-split-v1"
SEALED_SPLIT_MAP_V1 = {
    "train": "train",
    "development": "validation",
    "test": "test",
}
_ROW_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}
_MANIFEST_AUTHORITY = {
    **_ROW_AUTHORITY,
    "teacher_code_submission_allowed": False,
    "teacher_deck_submission_allowed": False,
}
_BRIDGE_KEYS_V2 = frozenset(
    {
        "schema_version",
        "purpose",
        "catalog_path",
        "catalog_file_sha256",
        "catalog_sha256",
        "decision_sha256",
        "selected_teacher_ids",
        "sources",
        "trainer_contract",
        "feature_boundary",
        "compatibility",
        "split",
        "performance_training_ready",
        "blocked_reasons",
        "output_dataset",
        "output_dataset_sha256",
        "output_rows",
        "partial_dataset_published",
        "authority",
        "bridge_sha256",
    }
)
_SOURCE_BINDING_KEYS = frozenset(
    {
        "teacher_id",
        "archetype",
        "source_kind",
        "policy_sha256",
        "deck_sha256",
        "teacher_manifest_sha256",
        "permission_manifest_id",
        "permission_trusted_bytes_sha256",
        "snapshot_index_sha256",
        "dataset_snapshot_sha256",
        "dataset_chunks",
        "snapshot_shards",
        "source_records",
        "source_episodes",
        "trainable_decisions",
        "trainable_episodes",
        "sealed_split_audit",
        "native_code_bundled",
        "native_deck_bundled",
    }
)
_SOURCE_ROW_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "record_id",
        "split",
        "episode_id",
        "near_duplicate_id",
        "near_duplicate_ubiquitous",
        "candidate_outcome",
        "sample_weight",
        "rule_bc_example",
        "provenance",
        "authority",
    }
)


class TeacherSnapshotStudentV3BridgeError(ValueError):
    """Raised when a V3 teacher bridge boundary cannot be verified."""


def audit_sealed_split_integrity_v1(
    records: Mapping[str, Mapping[str, object]],
    *,
    declared_ubiquitous_near_duplicate_ids: Sequence[str],
) -> dict[str, object]:
    """Audit the exact sealed assignment without deriving a second split."""
    ubiquitous = {
        _require_sha(value, field="declared ubiquitous near-duplicate id")
        for value in declared_ubiquitous_near_duplicate_ids
    }
    if len(ubiquitous) != len(declared_ubiquitous_near_duplicate_ids):
        raise TeacherSnapshotStudentV3BridgeError(
            "declared ubiquitous near-duplicate ids are duplicated"
        )
    mapped_counts: Counter[str] = Counter()
    episode_splits: dict[str, set[str]] = defaultdict(set)
    near_duplicate_splits: dict[str, set[str]] = defaultdict(set)
    for record_id, sealed in records.items():
        _require_sha(record_id, field="sealed record id")
        episode_id = _require_sha(
            sealed.get("episode_id_hash"), field="sealed episode id"
        )
        near_duplicate_id = _require_sha(
            sealed.get("near_duplicate_id"), field="sealed near-duplicate id"
        )
        source_split = sealed.get("split")
        if source_split not in SEALED_SPLIT_MAP_V1:
            raise TeacherSnapshotStudentV3BridgeError(
                "sealed snapshot split is outside the canonical three-way mapping"
            )
        mapped = SEALED_SPLIT_MAP_V1[str(source_split)]
        mapped_counts[mapped] += 1
        episode_splits[episode_id].add(mapped)
        near_duplicate_splits[near_duplicate_id].add(mapped)

    episode_cross = sorted(
        identity for identity, splits in episode_splits.items() if len(splits) > 1
    )
    near_cross = sorted(
        identity
        for identity, splits in near_duplicate_splits.items()
        if len(splits) > 1
    )
    declared_cross = sorted(set(near_cross) & ubiquitous)
    non_ubiquitous_cross = sorted(set(near_cross) - ubiquitous)
    return {
        "source_split_mapping": dict(SEALED_SPLIT_MAP_V1),
        "mapped_record_counts": dict(sorted(mapped_counts.items())),
        "episode_split_intersection_count": len(episode_cross),
        "episode_split_intersection_ids": episode_cross,
        "near_duplicate_split_intersection_count": len(near_cross),
        "near_duplicate_split_intersection_ids": near_cross,
        "non_ubiquitous_near_duplicate_split_intersection_count": len(
            non_ubiquitous_cross
        ),
        "non_ubiquitous_near_duplicate_split_intersection_ids": non_ubiquitous_cross,
        "declared_ubiquitous_near_duplicate_split_intersection_count": len(
            declared_cross
        ),
        "declared_ubiquitous_near_duplicate_split_intersection_ids": declared_cross,
    }


def classify_student_v3_set_compatibility_v1(
    *,
    selection_type: object,
    selection_context: object,
    minimum: int,
    maximum: int,
    target_digests: Sequence[str],
    legal_digests: Sequence[str],
) -> dict[str, object]:
    """Classify one decision under exact unordered set+count semantics."""
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or minimum < 0
        or maximum < minimum
        or maximum > len(legal_digests)
    ):
        raise TeacherSnapshotStudentV3BridgeError("selection cardinality is invalid")
    legal = tuple(_require_sha(value, field="legal action digest") for value in legal_digests)
    targets = tuple(_require_sha(value, field="target action digest") for value in target_digests)
    if len(targets) != len(set(targets)) or not set(targets).issubset(legal):
        raise TeacherSnapshotStudentV3BridgeError(
            "target digests are duplicate or non-legal"
        )
    if not minimum <= len(targets) <= maximum:
        raise TeacherSnapshotStudentV3BridgeError(
            "target cardinality violates selection bounds"
        )
    try:
        ordered = is_ordered_selection(selection_type, selection_context)
    except ValueError as exc:
        raise TeacherSnapshotStudentV3BridgeError(
            "selection schema is not recognized"
        ) from exc
    schema = f"{selection_type}:{selection_context}"
    if ordered:
        return {
            "status": "UNSUPPORTED",
            "reason": "ordered_selection_requires_pointer_head",
            "target_count": len(targets),
            "cardinality_semantics": "ordered_sequence",
            "selection_schema": schema,
        }
    if maximum == 0:
        return {
            "status": "NO_TRAINABLE_CHOICE",
            "reason": "forced_empty_selection",
            "target_count": 0,
            "cardinality_semantics": "forced_empty",
            "selection_schema": schema,
        }
    if any(legal.count(target) != 1 for target in targets):
        return {
            "status": "UNSUPPORTED",
            "reason": "target_action_alias_collision",
            "target_count": len(targets),
            "cardinality_semantics": "ambiguous_action_alias",
            "selection_schema": schema,
        }
    if not targets and minimum == 0:
        semantics = "optional_decline"
    elif minimum == maximum:
        semantics = "fixed_cardinality"
    else:
        semantics = "variable_cardinality"
    return {
        "status": "SUPPORTED_SET",
        "reason": None,
        "target_count": len(targets),
        "cardinality_semantics": semantics,
        "selection_schema": schema,
    }


def _v3_example(
    record: Mapping[str, Any],
    *,
    teacher: Mapping[str, Any],
    target_local_ids: Sequence[str],
) -> RuleBCExample:
    base = _rule_example(
        record,
        teacher=teacher,
        target_local_ids=target_local_ids,
    )
    value = replace(
        base,
        metadata={
            "bridge_schema": BRIDGE_SCHEMA_V1,
            "source_record_sha256": str(record["content_hash"]),
        },
    )
    validate_example(value)
    return value


def _source_row(
    *,
    record: Mapping[str, Any],
    teacher: Mapping[str, Any],
    example: RuleBCExample,
    split: str,
    near_duplicate_id: str,
    near_duplicate_ubiquitous: bool,
    quality_weight: float,
    catalog_sha256: str,
    teacher_manifest_sha256: str,
    snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": SOURCE_SCHEMA,
        "purpose": PURPOSE,
        "record_id": example.example_id,
        "split": split,
        "episode_id": example.source_id,
        "near_duplicate_id": near_duplicate_id,
        "near_duplicate_ubiquitous": near_duplicate_ubiquitous,
        "candidate_outcome": _outcome(record["teacher"].get("value_target")),
        "sample_weight": quality_weight,
        "rule_bc_example": example.to_dict(),
        "provenance": {
            "catalog_sha256": catalog_sha256,
            "snapshot_sha256": snapshot_sha256,
            "source_record_sha256": record["content_hash"],
            "teacher_policy_sha256": teacher["policy"]["sha256"],
            "teacher_deck_sha256": teacher["deck"]["sha256"],
            "teacher_manifest_sha256": teacher_manifest_sha256,
            "native_code_bundled": False,
            "native_deck_bundled": False,
        },
        "authority": dict(_ROW_AUTHORITY),
    }


def _compatibility(
    example: RuleBCExample,
) -> dict[str, object]:
    return classify_student_v3_set_compatibility_v1(
        selection_type=example.selection_type,
        selection_context=example.selection_context,
        minimum=example.min_count,
        maximum=example.max_count,
        target_digests=example.target_action_digests,
        legal_digests=tuple(row["digest"] for row in example.legal_actions),
    )


def _strict_source_rows(path: Path) -> Sequence[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise TeacherSnapshotStudentV3BridgeError(
                    "duplicate JSON key in bridge source"
                )
            value[key] = item
        return value

    def reject_constant(value: str) -> object:
        raise TeacherSnapshotStudentV3BridgeError(
            f"non-finite JSON value in bridge source: {value}"
        )

    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.endswith(b"\n") or raw == b"\n" or raw.endswith(b"\r\n"):
                raise TeacherSnapshotStudentV3BridgeError(
                    f"bridge source line framing is invalid at line {line_number}"
                )
            body = raw[:-1]
            try:
                value = json.loads(
                    body.decode("utf-8"),
                    object_pairs_hook=reject_pairs,
                    parse_constant=reject_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TeacherSnapshotStudentV3BridgeError(
                    f"bridge source JSON is invalid at line {line_number}"
                ) from exc
            if type(value) is not dict or _canonical(value) != body:
                raise TeacherSnapshotStudentV3BridgeError(
                    f"bridge source line is not canonical at line {line_number}"
                )
            rows.append(value)
    return rows


def verify_teacher_snapshot_student_v3_bridge_manifest_v1(
    path: str | Path,
    repo_root: str | Path,
) -> dict[str, object]:
    """Verify a V3 bridge from the formal catalog through its exact output bytes."""
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise TeacherSnapshotStudentV3BridgeError(
            "repo_root must be a directory"
        )
    manifest_path = Path(path).resolve()
    try:
        manifest = _strict_json(manifest_path, require_canonical=True)
    except (OSError, ValueError) as exc:
        raise TeacherSnapshotStudentV3BridgeError(
            f"bridge manifest is not canonical strict JSON: {exc}"
        ) from exc
    if set(manifest) != _BRIDGE_KEYS_V2:
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge manifest has an invalid closed schema"
        )
    if (
        manifest.get("schema_version") != BRIDGE_SCHEMA_V1
        or manifest.get("purpose") != PURPOSE
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge schema or purpose mismatch"
        )
    supplied_bridge_sha = _require_sha(
        manifest.get("bridge_sha256"), field="bridge semantic SHA-256"
    )
    expected_bridge_sha = _digest(
        {
            key: value
            for key, value in manifest.items()
            if key != "bridge_sha256"
        },
        domain=BRIDGE_SCHEMA_V1,
    )
    if supplied_bridge_sha != expected_bridge_sha:
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge semantic SHA-256 mismatch"
        )

    catalog_value = manifest.get("catalog_path")
    if (
        type(catalog_value) is not str
        or not catalog_value
        or Path(catalog_value).is_absolute()
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "catalog_path must be a repository-relative path"
        )
    try:
        catalog_path = _inside_root(root, catalog_value, field="catalog path")
    except (OSError, ValueError) as exc:
        raise TeacherSnapshotStudentV3BridgeError(
            f"catalog_path cannot be resolved inside repo_root: {exc}"
        ) from exc
    if str(catalog_path.relative_to(root)) != catalog_value:
        raise TeacherSnapshotStudentV3BridgeError(
            "catalog_path is not canonical repository-relative spelling"
        )
    catalog_file_sha = _sha_file(catalog_path)
    if catalog_file_sha != manifest.get("catalog_file_sha256"):
        raise TeacherSnapshotStudentV3BridgeError(
            "catalog file SHA-256 mismatch"
        )
    catalog = verify_derived_teacher_catalog_v1(catalog_path, root)
    if (
        catalog.get("catalog_sha256") != manifest.get("catalog_sha256")
        or catalog.get("decision", {}).get("sha256")
        != manifest.get("decision_sha256")
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "formal catalog semantic or decision binding mismatch"
        )

    selected_ids = manifest.get("selected_teacher_ids")
    sources = manifest.get("sources")
    if (
        type(selected_ids) is not list
        or not selected_ids
        or len(selected_ids) != len(set(selected_ids))
        or any(type(value) is not str or not value for value in selected_ids)
        or type(sources) is not list
        or len(sources) != len(selected_ids)
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "selected teachers or source bindings are invalid"
        )
    catalog_teachers = {row["teacher_id"]: row for row in catalog["teachers"]}
    if any(teacher_id not in catalog_teachers for teacher_id in selected_ids):
        raise TeacherSnapshotStudentV3BridgeError(
            "selected teacher is outside the formal catalog"
        )
    if [row.get("teacher_id") if type(row) is dict else None for row in sources] != selected_ids:
        raise TeacherSnapshotStudentV3BridgeError(
            "source order does not match selected teachers"
        )

    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        load_production_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )

    vocabulary = load_production_card_vocabulary_v1()
    runtime: dict[str, dict[str, Any]] = {}
    all_snapshots: dict[str, tuple[str, Mapping[str, object]]] = {}
    all_ubiquitous: set[str] = set()
    compatibility: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    no_choice: Counter[str] = Counter()
    selection_schemas: Counter[str] = Counter()
    ordered_schemas: Counter[str] = Counter()
    cardinality_semantics: Counter[str] = Counter()
    supported_record_ids: set[str] = set()

    for source, teacher_id in zip(sources, selected_ids, strict=True):
        if type(source) is not dict or set(source) != _SOURCE_BINDING_KEYS:
            raise TeacherSnapshotStudentV3BridgeError(
                "bridge source has an invalid closed schema"
            )
        teacher = catalog_teachers[teacher_id]
        collection = teacher["collection"]["dataset_manifest"]
        teacher_manifest_path = _inside_root(
            root, collection["path"], field="teacher dataset manifest"
        )
        if _sha_file(teacher_manifest_path) != collection["file_sha256"]:
            raise TeacherSnapshotStudentV3BridgeError(
                "teacher dataset manifest SHA-256 mismatch"
            )
        teacher_manifest = _strict_json(teacher_manifest_path)
        permission = teacher_manifest.get("permission_manifest")
        if type(permission) is not dict:
            raise TeacherSnapshotStudentV3BridgeError(
                "teacher permission manifest is missing"
            )
        permission_bytes = canonical_json_bytes_v2(permission)
        trusted = build_trusted_permission_set_v1((permission_bytes,))
        if collection["permission_manifest_id"] not in trusted:
            raise TeacherSnapshotStudentV3BridgeError(
                "catalog permission id is absent from trusted bytes"
            )
        snapshot = _snapshot_source(
            root=root,
            teacher=teacher,
            teacher_manifest_path=teacher_manifest_path,
            permission_id=collection["permission_manifest_id"],
            permission_raw_sha256=hashlib.sha256(permission_bytes).hexdigest(),
        )
        if tuple(snapshot["split_names"]) != tuple(SEALED_SPLIT_MAP_V1):
            raise TeacherSnapshotStudentV3BridgeError(
                "sealed snapshot split names do not match the canonical mapping"
            )
        _require_teacher_manifest_counts(teacher_manifest, teacher=teacher)
        split_audit = audit_sealed_split_integrity_v1(
            snapshot["snapshot_records"],
            declared_ubiquitous_near_duplicate_ids=snapshot[
                "ubiquitous_near_duplicate_ids"
            ],
        )
        expected_static = {
            "teacher_id": teacher_id,
            "archetype": teacher["archetype"],
            "source_kind": teacher["source_kind"],
            "policy_sha256": teacher["policy"]["sha256"],
            "deck_sha256": teacher["deck"]["sha256"],
            "teacher_manifest_sha256": collection["file_sha256"],
            "permission_manifest_id": collection["permission_manifest_id"],
            "permission_trusted_bytes_sha256": hashlib.sha256(
                permission_bytes
            ).hexdigest(),
            "snapshot_index_sha256": snapshot["index_sha256"],
            "dataset_snapshot_sha256": snapshot["dataset_snapshot_sha256"],
            "dataset_chunks": snapshot["chunk_bindings"],
            "snapshot_shards": snapshot["shard_bindings"],
            "source_records": len(snapshot["snapshot_records"]),
            "source_episodes": len(
                {
                    value["episode_id_hash"]
                    for value in snapshot["snapshot_records"].values()
                }
            ),
            "sealed_split_audit": split_audit,
            "native_code_bundled": False,
            "native_deck_bundled": False,
        }
        for field, expected in expected_static.items():
            if source.get(field) != expected:
                raise TeacherSnapshotStudentV3BridgeError(
                    f"source {teacher_id} {field} does not match primary artifacts"
                )
        all_ubiquitous.update(snapshot["ubiquitous_near_duplicate_ids"])
        for record_id, sealed in snapshot["snapshot_records"].items():
            if record_id in all_snapshots:
                raise TeacherSnapshotStudentV3BridgeError(
                    "snapshot record id is duplicated across selected teachers"
                )
            all_snapshots[record_id] = (teacher_id, sealed)

        source_seen: set[str] = set()
        trainable_episodes: set[str] = set()
        source_supported = 0
        for chunk_path in snapshot["chunk_paths"]:
            for record in _iter_chunk_records(chunk_path):
                record_id = _require_sha(record.get("record_id"), field="record id")
                if record_id in source_seen:
                    raise TeacherSnapshotStudentV3BridgeError(
                        "raw record id is duplicated"
                    )
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
                compatibility["source_decisions"] += 1
                target_local = _hard_target(record, teacher_id=teacher_id)
                if target_local is None:
                    unsupported[
                        "probabilistic_teacher_target_not_representable"
                    ] += 1
                    continue
                example = _v3_example(
                    record,
                    teacher=teacher,
                    target_local_ids=target_local,
                )
                result = _compatibility(example)
                schema = str(result["selection_schema"])
                selection_schemas[schema] += 1
                cardinality_semantics[str(result["cardinality_semantics"])] += 1
                if result["status"] == "UNSUPPORTED":
                    unsupported[str(result["reason"])] += 1
                    if result["reason"] == "ordered_selection_requires_pointer_head":
                        ordered_schemas[schema] += 1
                elif result["status"] == "NO_TRAINABLE_CHOICE":
                    no_choice[str(result["reason"])] += 1
                else:
                    compatibility["supported_decisions"] += 1
                    compatibility["would_emit_rows"] += 1
                    source_supported += 1
                    supported_record_ids.add(record_id)
                    trainable_episodes.add(record["episode_id_hash"])
        if source_seen != set(snapshot["snapshot_records"]):
            raise TeacherSnapshotStudentV3BridgeError(
                "raw teacher records do not cover the sealed snapshot"
            )
        if (
            source.get("trainable_decisions") != source_supported
            or source.get("trainable_episodes") != len(trainable_episodes)
        ):
            raise TeacherSnapshotStudentV3BridgeError(
                f"source {teacher_id} trainable counts mismatch"
            )
        runtime[teacher_id] = {
            "teacher": teacher,
            "source": source,
            "snapshot": snapshot,
            "trusted": trusted,
            "teacher_manifest_path": teacher_manifest_path,
            "permission_id": collection["permission_manifest_id"],
            "permission_raw_sha256": hashlib.sha256(permission_bytes).hexdigest(),
        }

    expected_compatibility = {
        "source_decisions": compatibility["source_decisions"],
        "supported_decisions": compatibility["supported_decisions"],
        "would_emit_rows": compatibility["would_emit_rows"],
        "selection_schema_counts": dict(sorted(selection_schemas.items())),
        "cardinality_semantics_counts": dict(
            sorted(cardinality_semantics.items())
        ),
        "ordered_selection_by_schema": dict(sorted(ordered_schemas.items())),
        "no_trainable_choice_by_reason": dict(sorted(no_choice.items())),
        "unsupported_by_reason": dict(sorted(unsupported.items())),
        "unsupported_total": sum(unsupported.values()),
    }
    if manifest.get("compatibility") != expected_compatibility:
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge compatibility audit does not reproduce from primary records"
        )

    global_split_audit = audit_sealed_split_integrity_v1(
        {record_id: sealed for record_id, (_teacher_id, sealed) in all_snapshots.items()},
        declared_ubiquitous_near_duplicate_ids=tuple(sorted(all_ubiquitous)),
    )
    split = manifest.get("split")
    expected_split_keys = {
        "algorithm",
        "seed",
        "requested_seed_ignored",
        "names",
        "source_mapping",
        "audit",
        "ubiquitous_near_duplicate_ids",
        "ubiquitous_near_duplicate_ids_sha256",
    }
    if type(split) is not dict or set(split) != expected_split_keys:
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge split contract has an invalid closed schema"
        )
    expected_split = {
        "algorithm": "sealed-training-snapshot-canonical-three-way-v1",
        "seed": None,
        "names": list(SEALED_SPLIT_MAP_V1.values()),
        "source_mapping": dict(SEALED_SPLIT_MAP_V1),
        "audit": global_split_audit,
        "ubiquitous_near_duplicate_ids": sorted(all_ubiquitous),
        "ubiquitous_near_duplicate_ids_sha256": _digest(
            sorted(all_ubiquitous),
            domain="student-v3-sealed-ubiquitous-near-duplicate-ids-v1",
        ),
    }
    for field, expected in expected_split.items():
        if split.get(field) != expected:
            raise TeacherSnapshotStudentV3BridgeError(
                f"bridge split {field} does not match sealed snapshots"
            )
    if (
        type(split.get("requested_seed_ignored")) is not str
        or not split["requested_seed_ignored"]
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge split requested_seed_ignored is invalid"
        )

    blockers: list[str] = []
    if global_split_audit["episode_split_intersection_count"] != 0:
        blockers.append("sealed_episode_split_intersection_present")
    if (
        global_split_audit[
            "non_ubiquitous_near_duplicate_split_intersection_count"
        ]
        != 0
    ):
        blockers.append(
            "sealed_non_ubiquitous_near_duplicate_split_intersection_present"
        )
    if unsupported:
        blockers.append("unsupported_decisions_present")
    if not compatibility["supported_decisions"]:
        blockers.append("no_trainable_rows")
    expected_blockers = sorted(set(blockers))
    ready = not expected_blockers
    if (
        manifest.get("performance_training_ready") is not ready
        or manifest.get("blocked_reasons") != expected_blockers
        or manifest.get("partial_dataset_published") is not False
        or manifest.get("authority") != _MANIFEST_AUTHORITY
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge readiness, blocker, or authority contract mismatch"
        )
    expected_trainer = {
        "source_schema": SOURCE_SCHEMA,
        "direct_consumer": (
            "mage_ptcg.offline_scaleup.gpu_student_v3_set.build_set_dataset"
        ),
        "target_encoding": "one decision = one unordered digest set + count",
        "ordered_selection_support": False,
    }
    expected_features = {
        "model_inputs": [
            "rule_bc_example.public_state",
            "rule_bc_example.own_private_state",
            "rule_bc_example.visible_history",
            "rule_bc_example.legal_actions",
        ],
        "metadata_excluded_from_features": [
            "teacher_identity",
            "opponent_id",
            "candidate_side",
            "record_id",
        ],
    }
    if (
        manifest.get("trainer_contract") != expected_trainer
        or manifest.get("feature_boundary") != expected_features
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge trainer or feature boundary mismatch"
        )

    if not ready:
        if (
            manifest.get("output_dataset") is not None
            or manifest.get("output_dataset_sha256") is not None
            or manifest.get("output_rows") != 0
        ):
            raise TeacherSnapshotStudentV3BridgeError(
                "blocked bridge published an output dataset"
            )
        if _sha_file(catalog_path) != catalog_file_sha:
            raise TeacherSnapshotStudentV3BridgeError(
                "catalog changed during bridge verification"
            )
        return manifest

    output_path = _inside_root(
        root, manifest.get("output_dataset"), field="bridge output dataset"
    )
    if str(output_path) != manifest.get("output_dataset"):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge output dataset path is not canonical absolute spelling"
        )
    if _sha_file(output_path) != manifest.get("output_dataset_sha256"):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge output dataset SHA-256 mismatch"
        )
    # Reconstruct the canonical output stream from the formally verified raw
    # records.  Comparing the stream SHA (rather than trusting row-declared
    # identities) closes the self-consistent hand-written source loophole and
    # binds every actor-visible feature, legal option, target, split, outcome,
    # weight, and provenance byte-for-byte to the primary artifacts.
    expected_hasher = hashlib.sha256()
    expected_rows = 0
    expected_record_ids: set[str] = set()
    expected_teacher_counts: Counter[str] = Counter()
    expected_teacher_episodes: dict[str, set[str]] = defaultdict(set)
    for teacher_id in sorted(runtime):
        bundle = runtime[teacher_id]
        teacher = bundle["teacher"]
        source = bundle["source"]
        snapshot = bundle["snapshot"]
        teacher_seen: set[str] = set()
        for chunk_path, chunk_binding in zip(
            snapshot["chunk_paths"], source["dataset_chunks"], strict=True
        ):
            if _sha_file(chunk_path) != chunk_binding["sha256"]:
                raise TeacherSnapshotStudentV3BridgeError(
                    "dataset chunk changed before exact output reconstruction"
                )
            for record in _iter_chunk_records(chunk_path):
                record_id = _require_sha(record.get("record_id"), field="record id")
                if record_id in teacher_seen:
                    raise TeacherSnapshotStudentV3BridgeError(
                        "write-verification record id is duplicated"
                    )
                teacher_seen.add(record_id)
                sealed = _require_snapshot_record_match(
                    record, snapshots=snapshot["snapshot_records"]
                )
                require_qualified_training_record_v2(
                    record,
                    vocabulary=vocabulary,
                    trusted_permissions=bundle["trusted"],
                    qualification_time_utc=snapshot["qualification_time_utc"],
                )
                _require_record_teacher_binding(record, teacher=teacher)
                target_local = _hard_target(record, teacher_id=teacher_id)
                if target_local is None:
                    raise TeacherSnapshotStudentV3BridgeError(
                        "ready bridge contains a non-representable teacher target"
                    )
                example = _v3_example(
                    record, teacher=teacher, target_local_ids=target_local
                )
                result = _compatibility(example)
                if result["status"] == "NO_TRAINABLE_CHOICE":
                    continue
                if result["status"] != "SUPPORTED_SET":
                    raise TeacherSnapshotStudentV3BridgeError(
                        "ready bridge contains an unsupported decision"
                    )
                quality = sealed["example_quality_weight"]
                if (
                    type(quality) not in (int, float)
                    or not 0.0 < float(quality) <= 1.0
                ):
                    raise TeacherSnapshotStudentV3BridgeError(
                        "snapshot quality weight is invalid"
                    )
                expected = _source_row(
                    record=record,
                    teacher=teacher,
                    example=example,
                    split=SEALED_SPLIT_MAP_V1[str(sealed["split"])],
                    near_duplicate_id=str(sealed["near_duplicate_id"]),
                    near_duplicate_ubiquitous=(
                        sealed["near_duplicate_id"] in all_ubiquitous
                    ),
                    quality_weight=float(quality),
                    catalog_sha256=catalog["catalog_sha256"],
                    teacher_manifest_sha256=source["teacher_manifest_sha256"],
                    snapshot_sha256=source["snapshot_index_sha256"],
                )
                if set(expected) != _SOURCE_ROW_KEYS:
                    raise TeacherSnapshotStudentV3BridgeError(
                        "internal source row schema drift"
                    )
                expected_hasher.update(_canonical(expected) + b"\n")
                expected_rows += 1
                expected_record_ids.add(record_id)
                expected_teacher_counts[teacher_id] += 1
                expected_teacher_episodes[teacher_id].add(
                    sealed["episode_id_hash"]
                )
            if _sha_file(chunk_path) != chunk_binding["sha256"]:
                raise TeacherSnapshotStudentV3BridgeError(
                    "dataset chunk changed during exact output reconstruction"
                )
        if teacher_seen != set(snapshot["snapshot_records"]):
            raise TeacherSnapshotStudentV3BridgeError(
                "write verification did not cover the complete snapshot"
            )
        if (
            expected_teacher_counts[teacher_id] != source["trainable_decisions"]
            or len(expected_teacher_episodes[teacher_id])
            != source["trainable_episodes"]
        ):
            raise TeacherSnapshotStudentV3BridgeError(
                f"bridge output counts do not match source {teacher_id}"
            )
        fresh_snapshot = _snapshot_source(
            root=root,
            teacher=teacher,
            teacher_manifest_path=bundle["teacher_manifest_path"],
            permission_id=bundle["permission_id"],
            permission_raw_sha256=bundle["permission_raw_sha256"],
        )
        if (
            fresh_snapshot["index_sha256"] != source["snapshot_index_sha256"]
            or fresh_snapshot["dataset_snapshot_sha256"]
            != source["dataset_snapshot_sha256"]
            or fresh_snapshot["chunk_bindings"] != source["dataset_chunks"]
            or fresh_snapshot["shard_bindings"] != source["snapshot_shards"]
        ):
            raise TeacherSnapshotStudentV3BridgeError(
                "snapshot primary artifacts changed during verification"
            )
    if expected_record_ids != supported_record_ids:
        raise TeacherSnapshotStudentV3BridgeError(
            "exact output reconstruction changed the supported record set"
        )
    expected_output_sha = expected_hasher.hexdigest()
    if (
        expected_rows != compatibility["would_emit_rows"]
        or manifest.get("output_rows") != expected_rows
        or manifest.get("output_dataset_sha256") != expected_output_sha
    ):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge output bytes do not reproduce from primary artifacts"
        )
    if _sha_file(catalog_path) != catalog_file_sha:
        raise TeacherSnapshotStudentV3BridgeError(
            "catalog changed during bridge verification"
        )
    return manifest


def build_teacher_snapshot_student_v3_bridge_v1(
    *,
    repo_root: str | Path,
    catalog_path: str | Path,
    output_dataset_path: str | Path,
    output_manifest_path: str | Path,
    teacher_ids: Sequence[str] = (),
    split_seed: str = DEFAULT_V3_SPLIT_SEED,
) -> dict[str, object]:
    """Audit selected catalog teachers and emit a complete V3 source or none."""
    root = Path(repo_root).resolve()
    catalog_file = _inside_root(root, str(catalog_path), field="catalog")
    dataset_path = Path(output_dataset_path).resolve()
    manifest_path = Path(output_manifest_path).resolve()
    if not root.is_dir():
        raise TeacherSnapshotStudentV3BridgeError("repo_root must be a directory")
    if dataset_path == manifest_path:
        raise TeacherSnapshotStudentV3BridgeError(
            "dataset and manifest outputs must differ"
        )
    if dataset_path.exists() or manifest_path.exists():
        raise FileExistsError("bridge outputs already exist")
    if type(split_seed) is not str or not split_seed:
        raise TeacherSnapshotStudentV3BridgeError("split_seed must be non-empty")

    # The formal loader owns the READY collection state and all closed catalog
    # vocabulary.  Downstream code intentionally performs no status string
    # comparison of its own.
    catalog_file_sha = _sha_file(catalog_file)
    catalog = verify_derived_teacher_catalog_v1(catalog_file, root)
    if _sha_file(catalog_file) != catalog_file_sha:
        raise TeacherSnapshotStudentV3BridgeError(
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
    compatibility: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    no_choice: Counter[str] = Counter()
    selection_schemas: Counter[str] = Counter()
    ordered_schemas: Counter[str] = Counter()
    cardinality_semantics: Counter[str] = Counter()
    all_sealed_records: dict[str, Mapping[str, object]] = {}
    all_ubiquitous_near_duplicate_ids: set[str] = set()

    for teacher in selected:
        collection = teacher["collection"]["dataset_manifest"]
        teacher_manifest_path = _inside_root(
            root, collection["path"], field="teacher dataset manifest"
        )
        teacher_manifest_sha = _sha_file(teacher_manifest_path)
        if teacher_manifest_sha != collection["file_sha256"]:
            raise TeacherSnapshotStudentV3BridgeError(
                "teacher dataset manifest SHA-256 mismatch"
            )
        teacher_manifest = _strict_json(teacher_manifest_path)
        permission = teacher_manifest.get("permission_manifest")
        if type(permission) is not dict:
            raise TeacherSnapshotStudentV3BridgeError(
                "teacher permission manifest is missing"
            )
        permission_bytes = canonical_json_bytes_v2(permission)
        trusted = build_trusted_permission_set_v1((permission_bytes,))
        permission_id = _require_sha(
            collection.get("permission_manifest_id"), field="permission manifest id"
        )
        if permission_id not in trusted:
            raise TeacherSnapshotStudentV3BridgeError(
                "catalog permission id is absent from trusted bytes"
            )
        snapshot = _snapshot_source(
            root=root,
            teacher=teacher,
            teacher_manifest_path=teacher_manifest_path,
            permission_id=permission_id,
            permission_raw_sha256=hashlib.sha256(permission_bytes).hexdigest(),
        )
        if tuple(snapshot["split_names"]) != tuple(SEALED_SPLIT_MAP_V1):
            raise TeacherSnapshotStudentV3BridgeError(
                "sealed snapshot split names do not match the canonical three-way mapping"
            )
        teacher_split_audit = audit_sealed_split_integrity_v1(
            snapshot["snapshot_records"],
            declared_ubiquitous_near_duplicate_ids=snapshot[
                "ubiquitous_near_duplicate_ids"
            ],
        )
        if (
            teacher_split_audit["episode_split_intersection_count"] != 0
            or teacher_split_audit[
                "non_ubiquitous_near_duplicate_split_intersection_count"
            ]
            != 0
        ):
            raise TeacherSnapshotStudentV3BridgeError(
                "sealed snapshot violates episode/non-ubiquitous near-duplicate split isolation"
            )
        all_ubiquitous_near_duplicate_ids.update(
            snapshot["ubiquitous_near_duplicate_ids"]
        )
        for record_id, sealed in snapshot["snapshot_records"].items():
            if record_id in all_sealed_records:
                raise TeacherSnapshotStudentV3BridgeError(
                    "sealed record id is duplicated across teachers"
                )
            all_sealed_records[record_id] = sealed
        source_seen: set[str] = set()
        source_episodes: set[str] = set()
        trainable_episodes: set[str] = set()
        source_records = 0
        source_supported = 0
        for chunk_path in snapshot["chunk_paths"]:
            for record in _iter_chunk_records(chunk_path):
                source_records += 1
                record_id = _require_sha(record.get("record_id"), field="record id")
                if record_id in seen_record_ids:
                    raise TeacherSnapshotStudentV3BridgeError(
                        "record id is duplicated across teachers"
                    )
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
                source_episodes.add(record["episode_id_hash"])
                compatibility["source_decisions"] += 1
                target_local = _hard_target(record, teacher_id=teacher["teacher_id"])
                if target_local is None:
                    unsupported["probabilistic_teacher_target_not_representable"] += 1
                    continue
                example = _v3_example(
                    record, teacher=teacher, target_local_ids=target_local
                )
                result = _compatibility(example)
                schema = str(result["selection_schema"])
                selection_schemas[schema] += 1
                cardinality_semantics[str(result["cardinality_semantics"])] += 1
                if result["status"] == "UNSUPPORTED":
                    unsupported[str(result["reason"])] += 1
                    if result["reason"] == "ordered_selection_requires_pointer_head":
                        ordered_schemas[schema] += 1
                elif result["status"] == "NO_TRAINABLE_CHOICE":
                    no_choice[str(result["reason"])] += 1
                else:
                    compatibility["supported_decisions"] += 1
                    compatibility["would_emit_rows"] += 1
                    source_supported += 1
                    trainable_episodes.add(record["episode_id_hash"])
        if source_seen != set(snapshot["snapshot_records"]):
            raise TeacherSnapshotStudentV3BridgeError(
                "snapshot contains raw records not audited"
            )
        if source_records != teacher_manifest.get("records_written"):
            raise TeacherSnapshotStudentV3BridgeError(
                "teacher manifest record count mismatch"
            )
        _require_teacher_manifest_counts(teacher_manifest, teacher=teacher)
        sources.append(
            {
                "teacher_id": teacher["teacher_id"],
                "archetype": teacher["archetype"],
                "source_kind": teacher["source_kind"],
                "policy_sha256": teacher["policy"]["sha256"],
                "deck_sha256": teacher["deck"]["sha256"],
                "teacher_manifest_sha256": teacher_manifest_sha,
                "permission_manifest_id": permission_id,
                "permission_trusted_bytes_sha256": hashlib.sha256(
                    permission_bytes
                ).hexdigest(),
                "snapshot_index_sha256": snapshot["index_sha256"],
                "dataset_snapshot_sha256": snapshot["dataset_snapshot_sha256"],
                "dataset_chunks": snapshot["chunk_bindings"],
                "snapshot_shards": snapshot["shard_bindings"],
                "source_records": source_records,
                "source_episodes": len(source_episodes),
                "trainable_decisions": source_supported,
                "trainable_episodes": len(trainable_episodes),
                "sealed_split_audit": teacher_split_audit,
                "native_code_bundled": False,
                "native_deck_bundled": False,
            }
        )
        runtime[teacher["teacher_id"]] = {
            "teacher": teacher,
            "trusted": trusted,
            "snapshot": snapshot,
            "teacher_manifest_sha256": teacher_manifest_sha,
        }

    global_split_audit = audit_sealed_split_integrity_v1(
        all_sealed_records,
        declared_ubiquitous_near_duplicate_ids=tuple(
            sorted(all_ubiquitous_near_duplicate_ids)
        ),
    )
    blockers: list[str] = []
    if global_split_audit["episode_split_intersection_count"] != 0:
        blockers.append("sealed_episode_split_intersection_present")
    if (
        global_split_audit[
            "non_ubiquitous_near_duplicate_split_intersection_count"
        ]
        != 0
    ):
        blockers.append(
            "sealed_non_ubiquitous_near_duplicate_split_intersection_present"
        )
    if unsupported:
        blockers.append("unsupported_decisions_present")
    if not compatibility["supported_decisions"]:
        blockers.append("no_trainable_rows")
    training_ready = not blockers
    output_sha: str | None = None
    output_name: str | None = None
    output_rows = 0
    if training_ready:
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = dataset_path.with_name(f".{dataset_path.name}.tmp-{os.getpid()}")
        try:
            with temporary.open("xb") as handle:
                for teacher_id in sorted(runtime):
                    bundle = runtime[teacher_id]
                    teacher = bundle["teacher"]
                    snapshot = bundle["snapshot"]
                    write_seen: set[str] = set()
                    for chunk_path in snapshot["chunk_paths"]:
                        for record in _iter_chunk_records(chunk_path):
                            record_id = _require_sha(
                                record.get("record_id"), field="record id"
                            )
                            if record_id in write_seen:
                                raise TeacherSnapshotStudentV3BridgeError(
                                    "write-pass record id is duplicated"
                                )
                            write_seen.add(record_id)
                            sealed = _require_snapshot_record_match(
                                record, snapshots=snapshot["snapshot_records"]
                            )
                            require_qualified_training_record_v2(
                                record,
                                vocabulary=vocabulary,
                                trusted_permissions=bundle["trusted"],
                                qualification_time_utc=snapshot[
                                    "qualification_time_utc"
                                ],
                            )
                            _require_record_teacher_binding(record, teacher=teacher)
                            target_local = _hard_target(
                                record, teacher_id=teacher_id
                            )
                            if target_local is None:
                                raise TeacherSnapshotStudentV3BridgeError(
                                    "audit/write teacher target drift"
                                )
                            example = _v3_example(
                                record,
                                teacher=teacher,
                                target_local_ids=target_local,
                            )
                            result = _compatibility(example)
                            if result["status"] == "NO_TRAINABLE_CHOICE":
                                continue
                            if result["status"] != "SUPPORTED_SET":
                                raise TeacherSnapshotStudentV3BridgeError(
                                    "audit/write compatibility drift"
                                )
                            quality = sealed["example_quality_weight"]
                            if (
                                type(quality) not in (int, float)
                                or not 0.0 < float(quality) <= 1.0
                            ):
                                raise TeacherSnapshotStudentV3BridgeError(
                                    "snapshot quality weight is invalid"
                                )
                            row = _source_row(
                                record=record,
                                teacher=teacher,
                                example=example,
                                split=SEALED_SPLIT_MAP_V1[str(sealed["split"])],
                                near_duplicate_id=str(sealed["near_duplicate_id"]),
                                near_duplicate_ubiquitous=(
                                    sealed["near_duplicate_id"]
                                    in all_ubiquitous_near_duplicate_ids
                                ),
                                quality_weight=float(quality),
                                catalog_sha256=catalog["catalog_sha256"],
                                teacher_manifest_sha256=bundle[
                                    "teacher_manifest_sha256"
                                ],
                                snapshot_sha256=snapshot["index_sha256"],
                            )
                            handle.write(_canonical(row) + b"\n")
                            output_rows += 1
                    if write_seen != set(snapshot["snapshot_records"]):
                        raise TeacherSnapshotStudentV3BridgeError(
                            "write pass did not cover the complete snapshot"
                        )
                handle.flush()
                os.fsync(handle.fileno())
            if output_rows != compatibility["would_emit_rows"]:
                raise TeacherSnapshotStudentV3BridgeError(
                    "audit/write row count drift"
                )
            os.replace(temporary, dataset_path)
        finally:
            temporary.unlink(missing_ok=True)
        output_sha = _sha_file(dataset_path)
        output_name = str(dataset_path)

    manifest: dict[str, object] = {
        "schema_version": BRIDGE_SCHEMA_V1,
        "purpose": PURPOSE,
        "catalog_path": str(catalog_file.relative_to(root)),
        "catalog_file_sha256": catalog_file_sha,
        "catalog_sha256": catalog["catalog_sha256"],
        "decision_sha256": catalog["decision"]["sha256"],
        "selected_teacher_ids": [teacher["teacher_id"] for teacher in selected],
        "sources": sources,
        "trainer_contract": {
            "source_schema": SOURCE_SCHEMA,
            "direct_consumer": (
                "mage_ptcg.offline_scaleup.gpu_student_v3_set.build_set_dataset"
            ),
            "target_encoding": "one decision = one unordered digest set + count",
            "ordered_selection_support": False,
        },
        "feature_boundary": {
            "model_inputs": [
                "rule_bc_example.public_state",
                "rule_bc_example.own_private_state",
                "rule_bc_example.visible_history",
                "rule_bc_example.legal_actions",
            ],
            "metadata_excluded_from_features": [
                "teacher_identity",
                "opponent_id",
                "candidate_side",
                "record_id",
            ],
        },
        "compatibility": {
            "source_decisions": compatibility["source_decisions"],
            "supported_decisions": compatibility["supported_decisions"],
            "would_emit_rows": compatibility["would_emit_rows"],
            "selection_schema_counts": dict(sorted(selection_schemas.items())),
            "cardinality_semantics_counts": dict(
                sorted(cardinality_semantics.items())
            ),
            "ordered_selection_by_schema": dict(sorted(ordered_schemas.items())),
            "no_trainable_choice_by_reason": dict(sorted(no_choice.items())),
            "unsupported_by_reason": dict(sorted(unsupported.items())),
            "unsupported_total": sum(unsupported.values()),
        },
        "split": {
            "algorithm": "sealed-training-snapshot-canonical-three-way-v1",
            "seed": None,
            "requested_seed_ignored": split_seed,
            "names": list(SEALED_SPLIT_MAP_V1.values()),
            "source_mapping": dict(SEALED_SPLIT_MAP_V1),
            "audit": global_split_audit,
            "ubiquitous_near_duplicate_ids": sorted(
                all_ubiquitous_near_duplicate_ids
            ),
            "ubiquitous_near_duplicate_ids_sha256": _digest(
                sorted(all_ubiquitous_near_duplicate_ids),
                domain="student-v3-sealed-ubiquitous-near-duplicate-ids-v1",
            ),
        },
        "performance_training_ready": training_ready,
        "blocked_reasons": sorted(set(blockers)),
        "output_dataset": output_name,
        "output_dataset_sha256": output_sha,
        "output_rows": output_rows,
        "partial_dataset_published": False,
        "authority": dict(_MANIFEST_AUTHORITY),
        "bridge_sha256": None,
    }
    manifest["bridge_sha256"] = _digest(
        {key: value for key, value in manifest.items() if key != "bridge_sha256"},
        domain=BRIDGE_SCHEMA_V1,
    )
    try:
        if _sha_file(catalog_file) != catalog_file_sha:
            raise TeacherSnapshotStudentV3BridgeError(
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
    "DEFAULT_V3_SPLIT_SEED",
    "SEALED_SPLIT_MAP_V1",
    "TeacherSnapshotStudentV3BridgeError",
    "build_teacher_snapshot_student_v3_bridge_v1",
    "verify_teacher_snapshot_student_v3_bridge_manifest_v1",
    "audit_sealed_split_integrity_v1",
    "classify_student_v3_set_compatibility_v1",
]
