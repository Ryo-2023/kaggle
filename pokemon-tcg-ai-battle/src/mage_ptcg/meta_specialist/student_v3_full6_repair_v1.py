"""Research-only repair primitives for the blocked six-teacher V3 bridge.

The production V3 bridge remains untouched.  This lane records ordered
decisions in an exact quarantine and repairs cross-teacher split leakage by
assigning each episode/non-ubiquitous-near-duplicate connected component as a
unit.  A quarantined ordered target is never represented as an unordered set.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


REPAIR_SCHEMA_V1 = "meta-specialist-student-v3-full6-repair-v1"
COMPONENT_SPLIT_ALGORITHM_V1 = (
    "episode-nonubiquitous-near-duplicate-connected-component-majority-v1"
)
SPLITS_V1 = ("train", "validation", "test")
_SHA_CHARS = frozenset("0123456789abcdef")


class Full6RepairError(ValueError):
    """Raised when the Full6 repair boundary cannot be proven."""


_REPAIR_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "blocked_full6_bridge",
        "tomato_clean_lane",
        "catalog_binding",
        "derivation",
        "materialization",
        "performance_training_ready",
        "blocked_reasons",
        "completion_condition",
        "authority",
        "repair_sha256",
    }
)
_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}


def _sha(value: object, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in _SHA_CHARS for char in value)
    ):
        raise Full6RepairError(f"{field} must be a lowercase SHA-256")
    return value


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise Full6RepairError(f"value is not canonical JSON: {exc}") from exc


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _UnionFind:
    def __init__(self, values: Sequence[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        while parent != self.parent[parent]:
            parent = self.parent[parent]
        while value != parent:
            following = self.parent[value]
            self.parent[value] = parent
            value = following
        return parent

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low


def _cross_audit(
    rows: Sequence[Mapping[str, object]],
    *,
    ubiquitous: frozenset[str],
) -> dict[str, object]:
    episode_splits: dict[str, set[str]] = defaultdict(set)
    near_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        episode = _sha(row.get("episode_id"), "episode_id")
        near = _sha(row.get("near_duplicate_id"), "near_duplicate_id")
        split = row.get("split")
        if split not in SPLITS_V1:
            raise Full6RepairError("split must use the canonical three-way vocabulary")
        declared = row.get("near_duplicate_ubiquitous")
        if type(declared) is not bool or declared is not (near in ubiquitous):
            raise Full6RepairError("near_duplicate_ubiquitous disagrees with the declared set")
        episode_splits[episode].add(str(split))
        near_splits[near].add(str(split))
    episode_cross = sorted(key for key, value in episode_splits.items() if len(value) > 1)
    near_cross = sorted(key for key, value in near_splits.items() if len(value) > 1)
    return {
        "episode_cross_ids": episode_cross,
        "non_ubiquitous_cross_ids": sorted(set(near_cross) - ubiquitous),
        "declared_ubiquitous_cross_ids": sorted(set(near_cross) & ubiquitous),
    }


def repair_component_splits_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    ubiquitous_near_duplicate_ids: Sequence[str],
    seed: str,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Repair split leakage without separating an episode/component.

    Components that already occupy one split retain it.  A cross-split
    component moves to the split holding the most records; a hash-bound tie
    break is used only when counts tie.  Ubiquitous near-duplicate identities
    are audited but intentionally do not connect otherwise independent games.
    """
    if type(seed) is not str or not seed:
        raise Full6RepairError("seed must be non-empty")
    ubiquitous = frozenset(
        _sha(value, "ubiquitous near-duplicate id")
        for value in ubiquitous_near_duplicate_ids
    )
    if len(ubiquitous) != len(ubiquitous_near_duplicate_ids):
        raise Full6RepairError("ubiquitous near-duplicate ids are duplicated")
    normalized: list[dict[str, object]] = []
    record_ids: list[str] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise Full6RepairError("repair row must be an object")
        row = copy.deepcopy(dict(raw))
        record_id = _sha(row.get("record_id"), "record_id")
        if record_id in record_ids:
            raise Full6RepairError("record_id is duplicated")
        record_ids.append(record_id)
        _sha(row.get("episode_id"), "episode_id")
        _sha(row.get("near_duplicate_id"), "near_duplicate_id")
        if row.get("split") not in SPLITS_V1:
            raise Full6RepairError("split must use the canonical three-way vocabulary")
        normalized.append(row)
    normalized.sort(key=lambda row: str(row["record_id"]))
    source_audit = _cross_audit(normalized, ubiquitous=ubiquitous)
    union = _UnionFind([str(row["record_id"]) for row in normalized])
    episode_owner: dict[str, str] = {}
    near_owner: dict[str, str] = {}
    for row in normalized:
        record_id = str(row["record_id"])
        episode = str(row["episode_id"])
        previous = episode_owner.setdefault(episode, record_id)
        union.union(previous, record_id)
        near = str(row["near_duplicate_id"])
        if near not in ubiquitous:
            previous = near_owner.setdefault(near, record_id)
            union.union(previous, record_id)

    components: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in normalized:
        components[union.find(str(row["record_id"]))].append(row)
    assignments: list[dict[str, object]] = []
    moved = 0
    cross_components = 0
    for component_rows in components.values():
        component_ids = sorted(str(row["record_id"]) for row in component_rows)
        component_id = hashlib.sha256("\n".join(component_ids).encode("utf-8")).hexdigest()
        counts = Counter(str(row["split"]) for row in component_rows)
        largest = max(counts.values())
        candidates = sorted(split for split, count in counts.items() if count == largest)
        selected = min(
            candidates,
            key=lambda split: hashlib.sha256(
                f"{seed}\0{component_id}\0{split}".encode("utf-8")
            ).hexdigest(),
        )
        if len(counts) > 1:
            cross_components += 1
        for row in component_rows:
            if row["split"] != selected:
                moved += 1
            row["split"] = selected
        assignments.append(
            {
                "component_id": component_id,
                "record_ids": component_ids,
                "source_split_counts": dict(sorted(counts.items())),
                "assigned_split": selected,
            }
        )
    normalized.sort(key=lambda row: str(row["record_id"]))
    output_audit = _cross_audit(normalized, ubiquitous=ubiquitous)
    if output_audit["episode_cross_ids"] or output_audit["non_ubiquitous_cross_ids"]:
        raise Full6RepairError("component repair did not close split leakage")
    assignment_canonical = _canonical(sorted(assignments, key=lambda row: row["component_id"]))
    audit: dict[str, object] = {
        "algorithm": COMPONENT_SPLIT_ALGORITHM_V1,
        "seed": seed,
        "component_count": len(components),
        "cross_component_count": cross_components,
        "moved_record_count": moved,
        "source_episode_cross_count": len(source_audit["episode_cross_ids"]),
        "source_episode_cross_ids": source_audit["episode_cross_ids"],
        "source_non_ubiquitous_cross_count": len(source_audit["non_ubiquitous_cross_ids"]),
        "source_non_ubiquitous_cross_ids": source_audit["non_ubiquitous_cross_ids"],
        "declared_ubiquitous_cross_count": len(source_audit["declared_ubiquitous_cross_ids"]),
        "declared_ubiquitous_cross_ids": source_audit["declared_ubiquitous_cross_ids"],
        "output_episode_cross_count": len(output_audit["episode_cross_ids"]),
        "output_non_ubiquitous_cross_count": len(output_audit["non_ubiquitous_cross_ids"]),
        "assignment_canonical_json": assignment_canonical,
        "assignment_sha256": _digest_text(assignment_canonical),
    }
    return normalized, audit


def partition_full6_decisions_v1(
    decisions: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Separate exactly the known ordered pointer-head gap, nothing broader."""
    supported: list[dict[str, object]] = []
    quarantine: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise Full6RepairError("decision must be an object")
        row = copy.deepcopy(dict(raw))
        record_id = _sha(row.get("record_id"), "decision.record_id")
        if record_id in seen:
            raise Full6RepairError("decision.record_id is duplicated")
        seen.add(record_id)
        status = row.get("status")
        reason = row.get("reason")
        if status == "SUPPORTED_SET" and reason is None:
            supported.append(row)
        elif status == "UNSUPPORTED" and reason == "ordered_selection_requires_pointer_head":
            schema = row.get("selection_schema")
            targets = row.get("target_action_digests")
            if type(schema) is not str or not schema or type(targets) is not list:
                raise Full6RepairError("ordered quarantine row lacks exact sequence metadata")
            for target in targets:
                _sha(target, "ordered target digest")
            quarantine.append(row)
        elif status == "NO_TRAINABLE_CHOICE":
            continue
        elif status == "UNSUPPORTED":
            raise Full6RepairError(f"non-ordered unsupported decision: {reason}")
        else:
            raise Full6RepairError("decision status is invalid")
    supported.sort(key=lambda row: str(row["record_id"]))
    quarantine.sort(key=lambda row: str(row["record_id"]))
    return supported, quarantine


def _derive_full6_repair_v1(
    *,
    root: Path,
    blocked_bridge_path: Path,
    tomato_bridge_path: Path,
    seed: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Reproduce the dry-run repair directly from formally verified bridges."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        _hard_target,
        _inside_root,
        _iter_chunk_records,
        _require_record_teacher_binding,
        _require_snapshot_record_match,
        _sha_file,
        _snapshot_source,
        _strict_json,
    )
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        SEALED_SPLIT_MAP_V1,
        _compatibility,
        _v3_example,
        verify_teacher_snapshot_student_v3_bridge_manifest_v1,
    )

    full = verify_teacher_snapshot_student_v3_bridge_manifest_v1(
        blocked_bridge_path, root
    )
    tomato = verify_teacher_snapshot_student_v3_bridge_manifest_v1(
        tomato_bridge_path, root
    )
    if full.get("performance_training_ready") is not False or full.get(
        "blocked_reasons"
    ) != [
        "sealed_non_ubiquitous_near_duplicate_split_intersection_present",
        "unsupported_decisions_present",
    ]:
        raise Full6RepairError("input Full6 bridge is not the expected blocked lane")
    if (
        tomato.get("performance_training_ready") is not True
        or tomato.get("blocked_reasons") != []
        or tomato.get("selected_teacher_ids") != ["tomatomato_archaludon"]
    ):
        raise Full6RepairError("Tomato clean lane is not formally ready")
    catalog_path = _inside_root(root, str(full["catalog_path"]), field="catalog")
    catalog = _strict_json(catalog_path)
    teachers = {row["teacher_id"]: row for row in catalog["teachers"]}
    ubiquitous = tuple(full["split"]["ubiquitous_near_duplicate_ids"])
    ubiquitous_set = frozenset(ubiquitous)
    decisions: list[dict[str, object]] = []
    minimal_rows: list[dict[str, object]] = []
    metadata: dict[str, dict[str, object]] = {}
    per_teacher: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[str] = set()
    for source in full["sources"]:
        teacher_id = str(source["teacher_id"])
        teacher = teachers[teacher_id]
        collection = teacher["collection"]["dataset_manifest"]
        teacher_manifest_path = _inside_root(
            root, collection["path"], field="teacher manifest"
        )
        teacher_manifest = _strict_json(teacher_manifest_path)
        permission_bytes = canonical_json_bytes_v2(
            teacher_manifest["permission_manifest"]
        )
        permission_sha = hashlib.sha256(permission_bytes).hexdigest()
        if permission_sha != source["permission_trusted_bytes_sha256"]:
            raise Full6RepairError("permission bytes changed after bridge verification")
        snapshot = _snapshot_source(
            root=root,
            teacher=teacher,
            teacher_manifest_path=teacher_manifest_path,
            permission_id=collection["permission_manifest_id"],
            permission_raw_sha256=permission_sha,
        )
        teacher_seen: set[str] = set()
        for chunk_path, binding in zip(
            snapshot["chunk_paths"], source["dataset_chunks"], strict=True
        ):
            if _sha_file(chunk_path) != binding["sha256"]:
                raise Full6RepairError("dataset chunk changed before repair derivation")
            for record in _iter_chunk_records(chunk_path):
                sealed = _require_snapshot_record_match(
                    record, snapshots=snapshot["snapshot_records"]
                )
                _require_record_teacher_binding(record, teacher=teacher)
                target = _hard_target(record, teacher_id=teacher_id)
                if target is None:
                    raise Full6RepairError(
                        "probabilistic target cannot enter the Full6 repair lane"
                    )
                example = _v3_example(record, teacher=teacher, target_local_ids=target)
                result = _compatibility(example)
                record_id = example.example_id
                if record_id in seen or record_id in teacher_seen:
                    raise Full6RepairError("repair record identity is duplicated")
                seen.add(record_id)
                teacher_seen.add(record_id)
                decision = {
                    "record_id": record_id,
                    "status": result["status"],
                    "reason": result["reason"],
                    "selection_schema": result["selection_schema"],
                    "target_action_digests": list(example.target_action_digests),
                }
                decisions.append(decision)
                if result["status"] == "NO_TRAINABLE_CHOICE":
                    per_teacher[teacher_id]["no_trainable_choice"] += 1
                    continue
                minimal = {
                    "record_id": record_id,
                    "episode_id": example.source_id,
                    "near_duplicate_id": str(sealed["near_duplicate_id"]),
                    "near_duplicate_ubiquitous": (
                        sealed["near_duplicate_id"] in ubiquitous_set
                    ),
                    "split": SEALED_SPLIT_MAP_V1[str(sealed["split"])],
                }
                minimal_rows.append(minimal)
                metadata[record_id] = {
                    "teacher_id": teacher_id,
                    "source_split": minimal["split"],
                    "source_record_sha256": str(record["content_hash"]),
                    "snapshot_index_sha256": str(source["snapshot_index_sha256"]),
                    "teacher_policy_sha256": str(source["policy_sha256"]),
                    "teacher_deck_sha256": str(source["deck_sha256"]),
                    "episode_id": example.source_id,
                    "near_duplicate_id": str(sealed["near_duplicate_id"]),
                    "selection_schema": str(result["selection_schema"]),
                    "target_action_digests": list(example.target_action_digests),
                }
                per_teacher[teacher_id][str(result["status"])] += 1
            if _sha_file(chunk_path) != binding["sha256"]:
                raise Full6RepairError("dataset chunk changed during repair derivation")
        if teacher_seen != set(snapshot["snapshot_records"]):
            raise Full6RepairError("repair scan did not cover a complete snapshot")

    supported, quarantine = partition_full6_decisions_v1(decisions)
    repaired, audit = repair_component_splits_v1(
        minimal_rows,
        ubiquitous_near_duplicate_ids=ubiquitous,
        seed=seed,
    )
    assigned = {str(row["record_id"]): str(row["split"]) for row in repaired}
    quarantine_rows: list[dict[str, object]] = []
    for row in quarantine:
        record_id = str(row["record_id"])
        primary = metadata[record_id]
        targets = list(row["target_action_digests"])
        quarantine_rows.append(
            {
                "record_id": record_id,
                "teacher_id": primary["teacher_id"],
                "reason": "ordered_selection_requires_pointer_head",
                "selection_schema": primary["selection_schema"],
                "source_split": primary["source_split"],
                "component_assigned_split": assigned[record_id],
                "episode_id": primary["episode_id"],
                "near_duplicate_id": primary["near_duplicate_id"],
                "target_action_digests_in_teacher_order": targets,
                "target_sequence_sha256": hashlib.sha256(
                    _canonical(targets).encode("utf-8")
                ).hexdigest(),
                "source_record_sha256": primary["source_record_sha256"],
                "snapshot_index_sha256": primary["snapshot_index_sha256"],
                "teacher_policy_sha256": primary["teacher_policy_sha256"],
                "teacher_deck_sha256": primary["teacher_deck_sha256"],
            }
        )
    quarantine_rows.sort(key=lambda row: str(row["record_id"]))
    assignments = json.loads(str(audit["assignment_canonical_json"]))
    cross_components = [
        {
            "component_id": row["component_id"],
            "source_split_counts": row["source_split_counts"],
            "assigned_split": row["assigned_split"],
            "record_count": len(row["record_ids"]),
            "record_ids": row["record_ids"],
        }
        for row in assignments
        if len(row["source_split_counts"]) > 1
    ]
    slim_audit = {
        key: value
        for key, value in audit.items()
        if key != "assignment_canonical_json"
    }
    slim_audit["cross_components"] = cross_components
    split_counts = Counter(str(row["split"]) for row in repaired)
    derivation = {
        "selected_teacher_ids": list(full["selected_teacher_ids"]),
        "source_decisions": len(decisions),
        "unordered_set_decisions": len(supported),
        "ordered_pointer_head_quarantine_count": len(quarantine_rows),
        "no_trainable_choice_count": sum(
            counts["no_trainable_choice"] for counts in per_teacher.values()
        ),
        "per_teacher_counts": {
            key: dict(sorted(value.items())) for key, value in sorted(per_teacher.items())
        },
        "component_split_repair": slim_audit,
        "output_split_counts_if_materialized": dict(sorted(split_counts.items())),
        "ordered_quarantine": quarantine_rows,
    }
    if (
        len(decisions) != full["compatibility"]["source_decisions"]
        or len(supported) != full["compatibility"]["supported_decisions"]
        or len(quarantine_rows) != 4
        or slim_audit["source_non_ubiquitous_cross_count"] != 1
        or slim_audit["output_non_ubiquitous_cross_count"] != 0
    ):
        raise Full6RepairError("actual Full6 repair counts do not match the formal blocker")
    return derivation, full, tomato


def _repair_digest(body: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update((REPAIR_SCHEMA_V1 + "\0").encode("utf-8"))
    digest.update(_canonical(body).encode("utf-8"))
    return digest.hexdigest()


def _load_bridge_descriptor_light_v1(path: Path) -> dict[str, object]:
    """Verify canonical/self-hash bridge bytes without replaying raw records."""
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        _digest,
        _strict_json,
    )
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        BRIDGE_SCHEMA_V1,
    )

    manifest = _strict_json(path, require_canonical=True)
    supplied = manifest.get("bridge_sha256")
    expected = _digest(
        {key: value for key, value in manifest.items() if key != "bridge_sha256"},
        domain=BRIDGE_SCHEMA_V1,
    )
    if supplied != expected or manifest.get("schema_version") != BRIDGE_SCHEMA_V1:
        raise Full6RepairError(
            "bridge descriptor canonical/self-hash verification failed"
        )
    return manifest


def _lightweight_derivation_v1(
    full: Mapping[str, object], *, seed: str
) -> dict[str, object]:
    compatibility = full.get("compatibility")
    split = full.get("split")
    if type(compatibility) is not dict or type(split) is not dict:
        raise Full6RepairError("blocked Full6 bridge lacks compatibility/split audit")
    audit = split.get("audit")
    if type(audit) is not dict:
        raise Full6RepairError("blocked Full6 bridge lacks global split audit")
    ordered = compatibility.get("ordered_selection_by_schema")
    unsupported = compatibility.get("unsupported_by_reason")
    cross_ids = audit.get(
        "non_ubiquitous_near_duplicate_split_intersection_ids"
    )
    if (
        compatibility.get("source_decisions") != 36684
        or compatibility.get("supported_decisions") != 36680
        or compatibility.get("would_emit_rows") != 36680
        or compatibility.get("unsupported_total") != 4
        or ordered != {"5:34": 4}
        or unsupported != {"ordered_selection_requires_pointer_head": 4}
        or audit.get(
            "non_ubiquitous_near_duplicate_split_intersection_count"
        )
        != 1
        or type(cross_ids) is not list
        or len(cross_ids) != 1
    ):
        raise Full6RepairError("blocked Full6 primary facts changed")
    cross_id = _sha(cross_ids[0], "global non-ubiquitous cross id")
    return {
        "selected_teacher_ids": list(full["selected_teacher_ids"]),
        "source_decisions": 36684,
        "unordered_set_decisions": 36680,
        "ordered_pointer_head_quarantine": {
            "count": 4,
            "by_schema": {"5:34": 4},
            "record_ids": None,
            "target_sequences": None,
            "status": "BLOCKED_IDENTITIES_NOT_MATERIALIZED",
            "silent_drop": False,
        },
        "component_split_repair": {
            "algorithm": COMPONENT_SPLIT_ALGORITHM_V1,
            "seed": seed,
            "source_non_ubiquitous_cross_count": 1,
            "source_non_ubiquitous_cross_ids": [cross_id],
            "component_assignment": None,
            "moved_record_count": None,
            "output_non_ubiquitous_cross_count": None,
            "status": "BLOCKED_COMPONENT_NOT_MATERIALIZED",
            "silent_drop": False,
        },
        "primary_reproduction": {
            "attempted": True,
            "complete": False,
            "reproduction_skipped": True,
            "reason": "time_bounded_full_raw_scan_stopped_after_10_minutes",
            "claims_exact_record_id_or_component_assignment": False,
        },
    }


def build_full6_repair_manifest_v1(
    *,
    repo_root: str | Path,
    blocked_bridge_manifest_path: str | Path,
    tomato_bridge_manifest_path: str | Path,
    output_manifest_path: str | Path,
    seed: str = "full6-component-repair-v1",
    reproduce_primary: bool = False,
) -> dict[str, object]:
    """Write a dry-run repair manifest; never materialize or train a dataset."""
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        _atomic_write_new,
        _canonical as canonical_bytes,
        _inside_root,
        _sha_file,
    )

    root = Path(repo_root).resolve()
    blocked = _inside_root(root, str(blocked_bridge_manifest_path), field="blocked bridge")
    tomato = _inside_root(root, str(tomato_bridge_manifest_path), field="tomato bridge")
    output = Path(output_manifest_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    if reproduce_primary:
        derivation, full, tomato_manifest = _derive_full6_repair_v1(
            root=root,
            blocked_bridge_path=blocked,
            tomato_bridge_path=tomato,
            seed=seed,
        )
    else:
        full = _load_bridge_descriptor_light_v1(blocked)
        tomato_manifest = _load_bridge_descriptor_light_v1(tomato)
        if (
            full.get("performance_training_ready") is not False
            or full.get("blocked_reasons")
            != [
                "sealed_non_ubiquitous_near_duplicate_split_intersection_present",
                "unsupported_decisions_present",
            ]
            or tomato_manifest.get("performance_training_ready") is not True
            or tomato_manifest.get("blocked_reasons") != []
            or tomato_manifest.get("selected_teacher_ids")
            != ["tomatomato_archaludon"]
        ):
            raise Full6RepairError("input Full6/Tomato bridge state changed")
        derivation = _lightweight_derivation_v1(full, seed=seed)
    if (
        full.get("catalog_file_sha256")
        != tomato_manifest.get("catalog_file_sha256")
        or full.get("catalog_sha256") != tomato_manifest.get("catalog_sha256")
        or full.get("decision_sha256") != tomato_manifest.get("decision_sha256")
    ):
        raise Full6RepairError("Full6 and Tomato bridges bind different catalogs")
    catalog = _inside_root(root, str(full["catalog_path"]), field="catalog")
    if _sha_file(catalog) != full.get("catalog_file_sha256"):
        raise Full6RepairError("bound catalog file SHA-256 mismatch")
    manifest: dict[str, object] = {
        "schema_version": REPAIR_SCHEMA_V1,
        "purpose": "FULL6_REPAIR_DRY_RUN_NO_TRAINING",
        "blocked_full6_bridge": {
            "path": str(blocked.relative_to(root)),
            "file_sha256": _sha_file(blocked),
            "bridge_sha256": full["bridge_sha256"],
        },
        "tomato_clean_lane": {
            "path": str(tomato.relative_to(root)),
            "file_sha256": _sha_file(tomato),
            "bridge_sha256": tomato_manifest["bridge_sha256"],
            "input_manifest_declares_performance_training_ready": True,
            "primary_reproduction_in_this_run": False,
        },
        "catalog_binding": {
            "path": full["catalog_path"],
            "file_sha256": full["catalog_file_sha256"],
            "catalog_sha256": full["catalog_sha256"],
            "decision_sha256": full["decision_sha256"],
        },
        "derivation": derivation,
        "materialization": {
            "unordered_dataset_path": None,
            "unordered_dataset_sha256": None,
            "published_rows": 0,
            "planned_unordered_rows": derivation["unordered_set_decisions"],
            "partial_dataset_published": False,
        },
        "performance_training_ready": False,
        "blocked_reasons": (
            ["ordered_pointer_head_quarantine_present"]
            if reproduce_primary
            else [
                "component_split_assignment_unmaterialized",
                "ordered_pointer_head_quarantine_unmaterialized",
                "primary_reproduction_incomplete",
            ]
        ),
        "completion_condition": (
            "complete the primary raw-record reproduction; materialize all four exact "
            "ordered record IDs/target sequences; materialize and formally verify the "
            "global connected-component split assignment; then add an ordered pointer head"
        ),
        "authority": dict(_AUTHORITY),
        "repair_sha256": None,
    }
    manifest["repair_sha256"] = _repair_digest(
        {key: value for key, value in manifest.items() if key != "repair_sha256"}
    )
    payload = canonical_bytes(manifest)
    _atomic_write_new(output, payload)
    return json.loads(payload.decode("utf-8"))


def verify_full6_repair_manifest_v1(
    path: str | Path, repo_root: str | Path, *, reproduce_primary: bool = True
) -> dict[str, object]:
    """Verify self/source bindings and optionally reproduce all actual decisions."""
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v2_bridge_v1 import (
        _inside_root,
        _sha_file,
        _strict_json,
    )

    root = Path(repo_root).resolve()
    manifest_path = Path(path).resolve()
    manifest = _strict_json(manifest_path, require_canonical=True)
    if set(manifest) != _REPAIR_MANIFEST_KEYS or manifest.get("schema_version") != REPAIR_SCHEMA_V1:
        raise Full6RepairError("repair manifest has an invalid closed schema")
    expected = _repair_digest(
        {key: value for key, value in manifest.items() if key != "repair_sha256"}
    )
    if manifest.get("repair_sha256") != expected:
        raise Full6RepairError("repair semantic SHA-256 mismatch")
    full_binding = manifest.get("blocked_full6_bridge")
    tomato_binding = manifest.get("tomato_clean_lane")
    if type(full_binding) is not dict or type(tomato_binding) is not dict:
        raise Full6RepairError("repair bridge bindings are invalid")
    blocked = _inside_root(root, str(full_binding.get("path")), field="blocked bridge")
    tomato = _inside_root(root, str(tomato_binding.get("path")), field="tomato bridge")
    if _sha_file(blocked) != full_binding.get("file_sha256") or _sha_file(tomato) != tomato_binding.get("file_sha256"):
        raise Full6RepairError("repair input bridge SHA-256 mismatch")
    reproduction = manifest.get("derivation", {}).get("primary_reproduction")
    lightweight = type(reproduction) is dict and reproduction.get("complete") is False
    expected_blockers = (
        [
            "component_split_assignment_unmaterialized",
            "ordered_pointer_head_quarantine_unmaterialized",
            "primary_reproduction_incomplete",
        ]
        if lightweight
        else ["ordered_pointer_head_quarantine_present"]
    )
    if (
        manifest.get("purpose") != "FULL6_REPAIR_DRY_RUN_NO_TRAINING"
        or manifest.get("performance_training_ready") is not False
        or manifest.get("blocked_reasons") != expected_blockers
        or manifest.get("authority") != _AUTHORITY
        or manifest.get("materialization", {}).get("published_rows") != 0
        or manifest.get("materialization", {}).get("partial_dataset_published") is not False
    ):
        raise Full6RepairError("repair readiness/materialization authority is invalid")
    full_light = _load_bridge_descriptor_light_v1(blocked)
    tomato_light = _load_bridge_descriptor_light_v1(tomato)
    if lightweight:
        catalog_binding = manifest.get("catalog_binding")
        if type(catalog_binding) is not dict:
            raise Full6RepairError("repair catalog binding is invalid")
        catalog = _inside_root(
            root, str(catalog_binding.get("path")), field="catalog"
        )
        expected_catalog = {
            "path": full_light["catalog_path"],
            "file_sha256": full_light["catalog_file_sha256"],
            "catalog_sha256": full_light["catalog_sha256"],
            "decision_sha256": full_light["decision_sha256"],
        }
        expected_derivation = _lightweight_derivation_v1(
            full_light,
            seed=str(manifest["derivation"]["component_split_repair"]["seed"]),
        )
        if (
            manifest.get("derivation") != expected_derivation
            or catalog_binding != expected_catalog
            or _sha_file(catalog) != expected_catalog["file_sha256"]
            or full_binding.get("bridge_sha256") != full_light["bridge_sha256"]
            or tomato_binding.get("bridge_sha256") != tomato_light["bridge_sha256"]
            or tomato_binding.get(
                "input_manifest_declares_performance_training_ready"
            )
            is not True
            or tomato_binding.get("primary_reproduction_in_this_run") is not False
        ):
            raise Full6RepairError("lightweight repair descriptor does not reproduce")
        if reproduce_primary:
            raise Full6RepairError("primary reproduction incomplete")
    elif reproduce_primary:
        derivation, full, tomato_manifest = _derive_full6_repair_v1(
            root=root,
            blocked_bridge_path=blocked,
            tomato_bridge_path=tomato,
            seed=str(manifest["derivation"]["component_split_repair"]["seed"]),
        )
        expected_catalog = {
            "path": full["catalog_path"],
            "file_sha256": full["catalog_file_sha256"],
            "catalog_sha256": full["catalog_sha256"],
            "decision_sha256": full["decision_sha256"],
        }
        if (
            derivation != manifest.get("derivation")
            or manifest.get("catalog_binding") != expected_catalog
            or full_binding.get("bridge_sha256") != full["bridge_sha256"]
            or tomato_binding.get("bridge_sha256") != tomato_manifest["bridge_sha256"]
        ):
            raise Full6RepairError("repair derivation does not reproduce from primary artifacts")
    return manifest


__all__ = [
    "COMPONENT_SPLIT_ALGORITHM_V1",
    "Full6RepairError",
    "REPAIR_SCHEMA_V1",
    "build_full6_repair_manifest_v1",
    "partition_full6_decisions_v1",
    "repair_component_splits_v1",
    "verify_full6_repair_manifest_v1",
]
