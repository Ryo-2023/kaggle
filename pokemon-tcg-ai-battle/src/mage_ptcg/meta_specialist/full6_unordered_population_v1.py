"""Strict descriptor for the blocked Full6 unordered population lane.

This module deliberately materializes an audit descriptor only.  It binds the
existing Full6/Tomato bridge descriptors, the existing repair descriptor, and
the six-teacher derived catalog, while keeping the four ordered decisions and
the unresolved non-ubiquitous near-duplicate component explicitly quarantined.
No raw records are copied, no training dataset is published, and no evaluator
or learner is started.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .student_v3_full6_repair_v1 import (
    COMPONENT_SPLIT_ALGORITHM_V1,
    Full6RepairError,
    verify_full6_repair_manifest_v1,
)
from .teacher_snapshot_student_v3_bridge_v1 import BRIDGE_SCHEMA_V1


FULL6_UNORDERED_POPULATION_SCHEMA_V1 = "meta-specialist-full6-unordered-population-v1"
FULL6_UNORDERED_POPULATION_PURPOSE_V1 = "FULL6_UNORDERED_POPULATION_V1"
FULL6_UNORDERED_POPULATION_IDENTITY_V1 = "FULL6_UNORDERED_POPULATION_V1"
_SHA_CHARS = frozenset("0123456789abcdef")
_AUTHORITY = {
    "training_authority": False,
    "behavior_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}
_TEACHER_IDS = (
    "nihei_alakazam",
    "ozawa_grimmsnarl_v2",
    "ozawa_rocket_v2",
    "tomatomato_archaludon",
    "lucifer19_battlecore",
    "plamen06_steel",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "identity",
        "sources",
        "catalog_binding",
        "repair_binding",
        "coverage",
        "ordered_quarantine",
        "component_split",
        "permission_matrix",
        "materialization",
        "readiness",
        "blocked_reasons",
        "authority",
        "manifest_sha256",
    }
)


class Full6UnorderedPopulationError(ValueError):
    """Raised when the Full6 unordered descriptor cannot be proven."""


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
        raise Full6UnorderedPopulationError("value is not canonical JSON") from exc


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Full6UnorderedPopulationError(f"cannot hash source: {path}") from exc
    return digest.hexdigest()


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise Full6UnorderedPopulationError(f"{field} must be a lowercase SHA-256")
    return value


def _inside(root: Path, value: str | Path, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Full6UnorderedPopulationError(f"{field} escapes repo_root") from exc
    if not path.is_file():
        raise Full6UnorderedPopulationError(f"{field} is not a regular file: {path}")
    return path


def _strict_json(path: Path, *, canonical: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except Full6UnorderedPopulationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Full6UnorderedPopulationError(f"invalid JSON source: {path}") from exc
    if type(value) is not dict or (canonical and raw != _canonical(value)):
        raise Full6UnorderedPopulationError(f"source is not canonical JSON: {path}")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise Full6UnorderedPopulationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(token: str) -> object:
    raise Full6UnorderedPopulationError(f"non-finite JSON constant: {token}")


def _semantic_sha(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        FULL6_UNORDERED_POPULATION_SCHEMA_V1.encode("ascii")
        + b"\0"
        + _canonical(value)
    ).hexdigest()


def _load_bridge_descriptor(path: Path, *, label: str) -> dict[str, Any]:
    value = _strict_json(path)
    if value.get("schema_version") != BRIDGE_SCHEMA_V1:
        raise Full6UnorderedPopulationError(f"{label} bridge schema mismatch")
    supplied = _sha(value.get("bridge_sha256"), f"{label}.bridge_sha256")
    expected = hashlib.sha256(
        BRIDGE_SCHEMA_V1.encode("ascii")
        + b"\0"
        + _canonical({key: item for key, item in value.items() if key != "bridge_sha256"})
    ).hexdigest()
    if supplied != expected:
        raise Full6UnorderedPopulationError(f"{label} bridge semantic SHA mismatch")
    if value.get("authority") != {
        "promotion_authority": False,
        "submission_authority": False,
        "teacher_code_submission_allowed": False,
        "teacher_deck_submission_allowed": False,
        "training_authority": False,
    }:
        raise Full6UnorderedPopulationError(f"{label} bridge authority is not false")
    if type(value.get("selected_teacher_ids")) is not list:
        raise Full6UnorderedPopulationError(f"{label} selected teacher ids are missing")
    return value


def _catalog_binding(root: Path, blocked: Mapping[str, object]) -> tuple[dict[str, object], dict[str, Any]]:
    raw_path = blocked.get("catalog_path")
    catalog_path = _inside(root, str(raw_path), "derived teacher catalog")
    catalog = _strict_json(catalog_path)
    catalog_sha = _sha(catalog.get("catalog_sha256"), "catalog_sha256")
    expected = hashlib.sha256(
        _canonical({key: value for key, value in catalog.items() if key != "catalog_sha256"})
    ).hexdigest()
    if catalog_sha != expected or catalog_sha != blocked.get("catalog_sha256"):
        raise Full6UnorderedPopulationError("derived teacher catalog semantic SHA mismatch")
    if (
        catalog.get("derived_weights_allowed") is not True
        or catalog.get("allowed_usages") != ["training-local"]
        or catalog.get("training_authority") is not False
        or catalog.get("promotion_authority") is not False
        or catalog.get("submission_authority") is not False
    ):
        raise Full6UnorderedPopulationError("derived teacher catalog permission boundary is invalid")
    teachers = catalog.get("teachers")
    if type(teachers) is not list or {row.get("teacher_id") for row in teachers if type(row) is dict} != set(_TEACHER_IDS):
        raise Full6UnorderedPopulationError("derived teacher catalog does not contain the closed six-teacher set")
    rows: list[dict[str, object]] = []
    for row in sorted(teachers, key=lambda item: str(item["teacher_id"])):
        if row.get("teacher_usage_boundary") != "local_eval_only":
            raise Full6UnorderedPopulationError("teacher usage boundary is not local_eval_only")
        if row.get("derived_weights_allowed") is not True or row.get("allowed_usages") != ["training-local"]:
            raise Full6UnorderedPopulationError("teacher-derived weight permission is not training-local")
        if any(row.get(field) is not False for field in ("teacher_code_submission_allowed", "deck_submission_allowed", "promotion_authority", "submission_authority")):
            raise Full6UnorderedPopulationError("teacher code/deck or promotion/submission permission is not false")
        collection = row.get("collection")
        if type(collection) is not dict or collection.get("status") != "READY":
            raise Full6UnorderedPopulationError("teacher collection is not READY")
        dataset = collection.get("dataset_manifest")
        if type(dataset) is not dict:
            raise Full6UnorderedPopulationError("teacher dataset manifest binding is missing")
        manifest_path = _inside(root, str(dataset.get("path")), f"{row['teacher_id']} dataset manifest")
        if _sha_file(manifest_path) != dataset.get("file_sha256"):
            raise Full6UnorderedPopulationError(f"{row['teacher_id']} dataset manifest SHA mismatch")
        # Collection manifests predate the canonical Full6 descriptor and are
        # verified by the existing catalog bridge with its own closed schema.
        # Preserve their bytes and only parse them here for permission binding.
        manifest = _strict_json(manifest_path, canonical=False)
        permission = manifest.get("permission_manifest")
        if type(permission) is not dict or permission.get("allowed_usages") != ["training-local"]:
            raise Full6UnorderedPopulationError(f"{row['teacher_id']} permission manifest is not training-local")
        rows.append(
            {
                "teacher_id": row["teacher_id"],
                "archetype": row.get("archetype"),
                "source_kind": row.get("source_kind"),
                "teacher_usage_boundary": row["teacher_usage_boundary"],
                "training_local_allowed": True,
                "behavior_policy_allowed": False,
                "teacher_behavior_labels_allowed": False,
                "derivative_weights_allowed": True,
                "derivative_action_labels_allowed": False,
                "teacher_code_submission_allowed": False,
                "deck_submission_allowed": False,
                "permission_manifest_id": dataset.get("permission_manifest_id"),
                "dataset_manifest_path": str(manifest_path.relative_to(root)),
                "dataset_manifest_sha256": dataset.get("file_sha256"),
                "policy_sha256": row.get("policy", {}).get("sha256") if type(row.get("policy")) is dict else None,
                "deck_sha256": row.get("deck", {}).get("sha256") if type(row.get("deck")) is dict else None,
            }
        )
    binding = {
        "path": str(catalog_path.relative_to(root)),
        "file_sha256": _sha_file(catalog_path),
        "catalog_sha256": catalog_sha,
        "teacher_ids": list(_TEACHER_IDS),
    }
    return binding, {"catalog": catalog, "matrix": rows}


def _derive(
    *,
    root: Path,
    blocked_path: Path,
    tomato_path: Path,
    repair_path: Path,
) -> dict[str, object]:
    blocked = _load_bridge_descriptor(blocked_path, label="blocked Full6")
    tomato = _load_bridge_descriptor(tomato_path, label="Tomato clean")
    repair = verify_full6_repair_manifest_v1(repair_path, root, reproduce_primary=False)
    if blocked.get("performance_training_ready") is not False or blocked.get("blocked_reasons") != [
        "sealed_non_ubiquitous_near_duplicate_split_intersection_present",
        "unsupported_decisions_present",
    ]:
        raise Full6UnorderedPopulationError("blocked Full6 bridge is not the expected blocked lane")
    if tomato.get("performance_training_ready") is not True or tomato.get("blocked_reasons") != []:
        raise Full6UnorderedPopulationError("Tomato clean bridge is not the expected control lane")
    if blocked.get("catalog_path") != tomato.get("catalog_path") or blocked.get("catalog_sha256") != tomato.get("catalog_sha256"):
        raise Full6UnorderedPopulationError("Full6 and Tomato bridges bind different catalogs")
    catalog_binding, catalog_info = _catalog_binding(root, blocked)
    compatibility = blocked.get("compatibility")
    split = blocked.get("split")
    if type(compatibility) is not dict or type(split) is not dict or type(split.get("audit")) is not dict:
        raise Full6UnorderedPopulationError("blocked Full6 bridge lacks compatibility/split audit")
    audit = split["audit"]
    if (
        compatibility.get("source_decisions") != 36684
        or compatibility.get("supported_decisions") != 36680
        or compatibility.get("would_emit_rows") != 36680
        or compatibility.get("unsupported_total") != 4
        or compatibility.get("ordered_selection_by_schema") != {"5:34": 4}
        or compatibility.get("unsupported_by_reason") != {"ordered_selection_requires_pointer_head": 4}
        or audit.get("non_ubiquitous_near_duplicate_split_intersection_count") != 1
    ):
        raise Full6UnorderedPopulationError("blocked Full6 coverage or split facts changed")
    cross_ids = audit.get("non_ubiquitous_near_duplicate_split_intersection_ids")
    if type(cross_ids) is not list or len(cross_ids) != 1:
        raise Full6UnorderedPopulationError("blocked Full6 cross-component identity is missing")
    for item in cross_ids:
        _sha(item, "non-ubiquitous cross-component id")
    sources = [
        {
            "role": "blocked_full6_bridge",
            "path": str(blocked_path.relative_to(root)),
            "file_sha256": _sha_file(blocked_path),
            "bridge_sha256": blocked["bridge_sha256"],
        },
        {
            "role": "tomato_clean_bridge",
            "path": str(tomato_path.relative_to(root)),
            "file_sha256": _sha_file(tomato_path),
            "bridge_sha256": tomato["bridge_sha256"],
        },
        {
            "role": "blocked_full6_repair_descriptor",
            "path": str(repair_path.relative_to(root)),
            "file_sha256": _sha_file(repair_path),
            "repair_sha256": repair["repair_sha256"],
        },
    ]
    return {
        "schema_version": FULL6_UNORDERED_POPULATION_SCHEMA_V1,
        "purpose": FULL6_UNORDERED_POPULATION_PURPOSE_V1,
        "identity": FULL6_UNORDERED_POPULATION_IDENTITY_V1,
        "sources": sources,
        "catalog_binding": catalog_binding,
        "repair_binding": {
            "path": str(repair_path.relative_to(root)),
            "file_sha256": _sha_file(repair_path),
            "repair_sha256": repair["repair_sha256"],
            "repair_performance_training_ready": repair["performance_training_ready"],
        },
        "coverage": {
            "source_decisions": 36684,
            "unordered_set_decisions": 36680,
            "coverage_closed": True,
        },
        "ordered_quarantine": {
            "status": "QUARANTINED_ORDERED_UNSUPPORTED",
            "count": 4,
            "by_schema": {"5:34": 4},
            "record_ids": None,
            "target_sequences": None,
            "identities_materialized": False,
            "silent_drop": False,
        },
        "component_split": {
            "algorithm": COMPONENT_SPLIT_ALGORITHM_V1,
            "seed": repair["derivation"]["component_split_repair"]["seed"],
            "source_non_ubiquitous_cross_count": 1,
            "source_non_ubiquitous_cross_ids": list(cross_ids),
            "output_non_ubiquitous_cross_count": None,
            "assignment_materialized": False,
            "closure_verified": False,
            "silent_drop": False,
        },
        "permission_matrix": catalog_info["matrix"],
        "materialization": {
            "unordered_dataset_path": None,
            "unordered_dataset_sha256": None,
            "planned_unordered_rows": 36680,
            "published_rows": 0,
            "partial_dataset_published": False,
        },
        "readiness": {
            "raw_reproduction_complete": False,
            "ordered_quarantine_materialized": False,
            "component_split_materialized": False,
            "performance_training_ready": False,
            "ready_for_training": False,
            "ready_for_behavior": False,
        },
        "blocked_reasons": [
            "ordered_pointer_head_quarantine_unmaterialized",
            "component_split_assignment_unmaterialized",
            "primary_reproduction_incomplete",
            "behavior_permission_absent",
        ],
        "authority": dict(_AUTHORITY),
        "manifest_sha256": None,
    }


def build_full6_unordered_population_manifest_v1(
    *,
    repo_root: str | Path,
    blocked_full6_bridge_manifest_path: str | Path,
    tomato_clean_bridge_manifest_path: str | Path,
    repair_manifest_path: str | Path,
    output_manifest_path: str | Path,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise Full6UnorderedPopulationError("repo_root is not a directory")
    blocked_path = _inside(root, blocked_full6_bridge_manifest_path, "blocked Full6 bridge")
    tomato_path = _inside(root, tomato_clean_bridge_manifest_path, "Tomato clean bridge")
    repair_path = _inside(root, repair_manifest_path, "Full6 repair descriptor")
    output = Path(output_manifest_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    payload = _derive(root=root, blocked_path=blocked_path, tomato_path=tomato_path, repair_path=repair_path)
    payload["manifest_sha256"] = _semantic_sha({key: value for key, value in payload.items() if key != "manifest_sha256"})
    raw = _canonical(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return json.loads(raw.decode("utf-8"))


def verify_full6_unordered_population_manifest_v1(
    path: str | Path, repo_root: str | Path
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    # The descriptor may be staged in an isolated run/temp root; only its
    # bound source paths must remain inside ``repo_root``.
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise Full6UnorderedPopulationError(
            f"Full6 unordered population manifest is not a regular file: {manifest_path}"
        )
    manifest = _strict_json(manifest_path)
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != FULL6_UNORDERED_POPULATION_SCHEMA_V1:
        raise Full6UnorderedPopulationError("Full6 unordered manifest schema mismatch")
    expected_sha = _semantic_sha({key: value for key, value in manifest.items() if key != "manifest_sha256"})
    if manifest.get("manifest_sha256") != expected_sha:
        raise Full6UnorderedPopulationError("Full6 unordered manifest semantic SHA mismatch")
    sources = manifest.get("sources")
    if type(sources) is not list or len(sources) != 3:
        raise Full6UnorderedPopulationError("Full6 unordered source bindings are invalid")
    source_paths: dict[str, Path] = {}
    for source in sources:
        if type(source) is not dict or type(source.get("role")) is not str:
            raise Full6UnorderedPopulationError("Full6 unordered source binding is invalid")
        source_path = _inside(root, source.get("path", ""), f"source {source['role']}")
        if _sha_file(source_path) != source.get("file_sha256"):
            raise Full6UnorderedPopulationError(f"source {source['role']} SHA mismatch")
        source_paths[source["role"]] = source_path
    expected = _derive(
        root=root,
        blocked_path=source_paths["blocked_full6_bridge"],
        tomato_path=source_paths["tomato_clean_bridge"],
        repair_path=source_paths["blocked_full6_repair_descriptor"],
    )
    expected["manifest_sha256"] = _semantic_sha({key: value for key, value in expected.items() if key != "manifest_sha256"})
    if expected != manifest:
        raise Full6UnorderedPopulationError("Full6 unordered manifest does not reproduce")
    return manifest


__all__ = [
    "FULL6_UNORDERED_POPULATION_IDENTITY_V1",
    "FULL6_UNORDERED_POPULATION_PURPOSE_V1",
    "FULL6_UNORDERED_POPULATION_SCHEMA_V1",
    "Full6UnorderedPopulationError",
    "build_full6_unordered_population_manifest_v1",
    "verify_full6_unordered_population_manifest_v1",
]
