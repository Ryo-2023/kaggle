"""Strict adapter from actor-visible AWR rows to Student v3 GPU weights.

The adapter emits only the raw normalized ``awr_weight``.  Student v3 owns the
single multiplication by its sealed ``source_sample_weight``; copying the AWR
row's ``effective_weight`` here would apply teacher quality twice.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
    ActorVisibleAwrError,
    ActorVisibleAwrRowV1,
    read_actor_visible_awr_sidecar_v1,
)
from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
    read_derived_teacher_awr_manifest_v1,
)
from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
    TeacherSnapshotStudentV3BridgeError,
    verify_teacher_snapshot_student_v3_bridge_manifest_v1,
)
from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
    GPUStudentV3SetError,
    WEIGHT_SIDECAR_SCHEMA,
    _examples,
    _verify_dataset_manifest,
    load_training_weight_sidecar,
)


OBJECTIVE_KIND_V1 = "AWR_FINE_TUNE"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}


class DerivedTeacherStudentV3AwrAdapterError(ValueError):
    """Raised when AWR and Student v3 identities cannot be joined exactly."""


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
        raise DerivedTeacherStudentV3AwrAdapterError(
            "adapter payload is not finite canonical JSON"
        ) from exc


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{field} must be a lowercase SHA-256"
        )
    return value


def _file_sha(path: Path, *, field: str) -> str:
    if not path.is_file():
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{field} must be a regular file"
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_root(root: Path, value: str | Path, *, field: str) -> Path:
    path = Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{field} escapes repository root"
        ) from exc
    return path


def _binding_path(root: Path, value: object, *, field: str) -> Path:
    if type(value) is not str or not value:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{field} must be a nonempty path"
        )
    raw = Path(value)
    return _inside_root(root, raw if raw.is_absolute() else root / raw, field=field)


def _strict_json_object(path: Path, *, artifact: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()

        def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            payload: dict[str, object] = {}
            for key, value in pairs:
                if key in payload:
                    raise DerivedTeacherStudentV3AwrAdapterError(
                        f"{artifact} has a duplicate JSON key"
                    )
                payload[key] = value
            return payload

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                DerivedTeacherStudentV3AwrAdapterError(
                    f"{artifact} has a non-finite value: {value}"
                )
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{artifact} is unreadable strict JSON"
        ) from exc
    if type(payload) is not dict or _canonical(payload) != raw:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"{artifact} must be exact canonical JSON without a newline"
        )
    return payload


def _atomic_write_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite weight sidecar: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_verified_gpu_metadata_v1(
    dataset_dir: Path,
    manifest: dict[str, Any],
    *,
    repo_root: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Use the official shard oracle under the caller's explicit repo root."""

    # The official verifier additionally hardcodes the checkout root while the
    # adapter accepts an explicit root.  Production always uses that checkout;
    # a different root is reserved for isolated fixture verification, where the
    # adapter formally verifies the bridge itself immediately afterward.
    checkout_root = Path(__file__).resolve().parents[3]
    if repo_root == checkout_root:
        loaded = _verify_dataset_manifest(dataset_dir, manifest)
    else:
        manifest_for_shards = dict(manifest)
        manifest_for_shards.update(
            {
                "bridge_manifest_path": None,
                "bridge_manifest_sha256": None,
                "bridge_sha256": None,
                "selected_teacher_ids": ["SYNTHETIC_TEST_ONLY"],
                "synthetic_test_only": True,
            }
        )
        manifest_for_shards["dataset_sha256"] = hashlib.sha256(
            b"offline-scaleup-gpu-set-dataset-v1\0"
            + _canonical(
                {
                    key: value
                    for key, value in manifest_for_shards.items()
                    if key != "dataset_sha256"
                }
            )
        ).hexdigest()
        loaded = _verify_dataset_manifest(dataset_dir, manifest_for_shards)
    by_split: dict[str, list[dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    shards = manifest.get("shards")
    if type(shards) is not list:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU manifest shard bindings are invalid"
        )
    for shard in shards:
        if type(shard) is not dict:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU manifest shard binding is invalid"
            )
        split = shard.get("split")
        path_value = shard.get("path")
        if split not in by_split or type(path_value) is not str:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU manifest shard split/path is invalid"
            )
        payload = loaded.get(path_value)
        if type(payload) is not dict:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU verifier did not return a bound shard"
            )
        by_split[split].extend(example[6] for example in _examples((payload,)))
    return by_split


def _join_selected_awr_rows_v1(
    *,
    awr_rows: tuple[ActorVisibleAwrRowV1, ...],
    gpu_metadata_by_split: Mapping[str, list[dict[str, Any]]],
    selected_teacher_ids: list[str],
    catalog_teacher_ids: list[str],
) -> dict[str, float]:
    """Join selected-teacher train rows and reject heldout or identity drift."""

    if (
        type(selected_teacher_ids) is not list
        or not selected_teacher_ids
        or len(selected_teacher_ids) != len(set(selected_teacher_ids))
        or any(type(value) is not str or not value for value in selected_teacher_ids)
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU selected_teacher_ids are invalid"
        )
    selected = set(selected_teacher_ids)
    if (
        type(catalog_teacher_ids) is not list
        or not catalog_teacher_ids
        or len(catalog_teacher_ids) != len(set(catalog_teacher_ids))
        or any(type(value) is not str or not value for value in catalog_teacher_ids)
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "formal catalog teacher ids are invalid"
        )
    catalog_teachers = set(catalog_teacher_ids)
    if not selected <= catalog_teachers:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU selected teacher is outside the formal catalog"
        )
    if any(row.teacher_id not in catalog_teachers for row in awr_rows):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "AWR row teacher is outside the formal catalog"
        )
    awr_by_id = {row.record_id: row for row in awr_rows}
    if len(awr_by_id) != len(awr_rows):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "AWR record_id is duplicated"
        )
    gpu_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for split in ("train", "validation", "test"):
        values = gpu_metadata_by_split.get(split)
        if type(values) is not list:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU metadata split set is incomplete"
            )
        for metadata in values:
            if type(metadata) is not dict or metadata.get("split") != split:
                raise DerivedTeacherStudentV3AwrAdapterError(
                    "GPU shard metadata split mismatch"
                )
            record_id = _sha(metadata.get("record_id"), field="GPU record_id")
            if record_id in gpu_by_id:
                raise DerivedTeacherStudentV3AwrAdapterError(
                    "GPU record_id is duplicated across splits"
                )
            gpu_by_id[record_id] = (split, metadata)
    for record_id, (split, metadata) in gpu_by_id.items():
        row = awr_by_id.get(record_id)
        if row is None:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU dataset contains a missing or old AWR record_id"
            )
        expected_awr_split = "train" if split == "train" else (
            "development" if split == "validation" else "test"
        )
        if row.split != expected_awr_split:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU/AWR record split mismatch or heldout record in train"
            )
        if row.teacher_id not in selected:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "GPU record comes from a nonselected AWR teacher"
            )
        if row.record_content_hash != metadata.get("source_record_sha256"):
            raise DerivedTeacherStudentV3AwrAdapterError(
                "AWR/GPU record_content_hash mismatch"
            )
    selected_rows = [row for row in awr_rows if row.teacher_id in selected]
    if {row.record_id for row in selected_rows} != set(gpu_by_id):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "selected teacher has missing or extra GPU records"
        )
    train_ids = {
        record_id for record_id, (split, _metadata) in gpu_by_id.items()
        if split == "train"
    }
    return {
        record_id: awr_by_id[record_id].awr_weight for record_id in train_ids
    }


def build_derived_teacher_student_v3_awr_sidecar_v1(
    *,
    repo_root: str | Path,
    awr_manifest_path: str | Path,
    gpu_dataset_dir: str | Path,
    catalog_path: str | Path,
    expected_catalog_file_sha256: str,
    output_path: str | Path,
) -> dict[str, object]:
    """Verify and join one raw AWR weight to every and only V3 train row."""

    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise DerivedTeacherStudentV3AwrAdapterError(
            "repo_root must be a directory"
        )
    awr_path = _inside_root(root, awr_manifest_path, field="AWR manifest")
    dataset_dir = _inside_root(root, gpu_dataset_dir, field="GPU dataset")
    catalog_file = _inside_root(root, catalog_path, field="catalog")
    destination = _inside_root(root, output_path, field="weight sidecar output")
    expected_catalog_sha = _sha(
        expected_catalog_file_sha256, field="expected catalog file SHA-256"
    )
    if _file_sha(catalog_file, field="catalog") != expected_catalog_sha:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "exact catalog file SHA-256 mismatch"
        )
    catalog = _strict_json_object(catalog_file, artifact="catalog")
    gpu_manifest_path = dataset_dir / "manifest.json"
    gpu_manifest_file_sha = _file_sha(
        gpu_manifest_path, field="GPU dataset manifest"
    )
    gpu_manifest = _strict_json_object(
        gpu_manifest_path, artifact="GPU manifest"
    )
    if gpu_manifest.get("synthetic_test_only") is not False:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "synthetic GPU dataset cannot produce a performance weight sidecar"
        )
    bridge_manifest_path = _binding_path(
        root,
        gpu_manifest.get("bridge_manifest_path"),
        field="GPU bridge manifest",
    )
    bridge_manifest_file_sha = _file_sha(
        bridge_manifest_path, field="GPU bridge manifest"
    )
    if bridge_manifest_file_sha != gpu_manifest.get("bridge_manifest_sha256"):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU bridge manifest file SHA-256 mismatch"
        )
    bridge_manifest = _strict_json_object(
        bridge_manifest_path, artifact="GPU bridge manifest"
    )
    try:
        bridge_catalog_path = _binding_path(
            root, bridge_manifest.get("catalog_path"), field="bridge catalog"
        )
    except DerivedTeacherStudentV3AwrAdapterError as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU bridge is old or invalid: exact catalog_path is required"
        ) from exc
    if (
        gpu_manifest.get("catalog_sha256") != catalog.get("catalog_sha256")
        or bridge_catalog_path != catalog_file
        or bridge_manifest.get("catalog_file_sha256") != expected_catalog_sha
        or bridge_manifest.get("catalog_sha256") != catalog.get("catalog_sha256")
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU bridge uses an old or cross-catalog binding"
        )
    awr_manifest_file_sha = _file_sha(awr_path, field="AWR manifest")
    try:
        awr_manifest = read_derived_teacher_awr_manifest_v1(
            awr_path, repo_root=root, verify_sources=True
        )
    except ActorVisibleAwrError as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"AWR manifest does not formally verify: {exc}"
        ) from exc
    awr_catalog = awr_manifest.get("catalog")
    if (
        type(awr_catalog) is not dict
        or _binding_path(root, awr_catalog.get("path"), field="AWR catalog")
        != catalog_file
        or awr_catalog.get("file_sha256") != expected_catalog_sha
        or type(awr_catalog.get("catalog_sha256")) is not str
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "AWR manifest uses an old or cross-catalog binding"
        )
    sidecar = awr_manifest.get("sidecar")
    if type(sidecar) is not dict:
        raise DerivedTeacherStudentV3AwrAdapterError(
            "AWR manifest sidecar binding is missing"
        )
    awr_weights_path = _binding_path(
        root, sidecar.get("path"), field="AWR weights"
    )
    try:
        awr_rows = read_actor_visible_awr_sidecar_v1(
            awr_weights_path,
            expected_sha256=sidecar.get("sha256"),
        )
    except ActorVisibleAwrError as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"AWR weights do not verify: {exc}"
        ) from exc
    if catalog.get("catalog_sha256") != awr_catalog.get("catalog_sha256"):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "AWR manifest uses an old or cross-catalog semantic SHA-256"
        )
    try:
        gpu_metadata_by_split = _load_verified_gpu_metadata_v1(
            dataset_dir, gpu_manifest, repo_root=root
        )
    except GPUStudentV3SetError as exc:
        raise DerivedTeacherStudentV3AwrAdapterError(
            f"GPU dataset does not verify: {exc}"
        ) from exc
    # Production GPU verification above invokes the formal bridge verifier.
    # Isolated fixture roots take the explicit-root path here instead.
    if root != Path(__file__).resolve().parents[3]:
        try:
            bridge_manifest = verify_teacher_snapshot_student_v3_bridge_manifest_v1(
                bridge_manifest_path, root
            )
        except TeacherSnapshotStudentV3BridgeError as exc:
            raise DerivedTeacherStudentV3AwrAdapterError(
                f"GPU bridge manifest does not verify: {exc}"
            ) from exc
    bridge_output_path = _binding_path(
        root, bridge_manifest.get("output_dataset"), field="bridge output dataset"
    )
    gpu_source_path = _binding_path(
        root, gpu_manifest.get("source_dataset"), field="GPU source dataset"
    )
    if (
        _file_sha(bridge_manifest_path, field="GPU bridge manifest")
        != bridge_manifest_file_sha
        or bridge_manifest.get("bridge_sha256")
        != gpu_manifest.get("bridge_sha256")
        or bridge_manifest.get("selected_teacher_ids")
        != gpu_manifest.get("selected_teacher_ids")
        or bridge_catalog_path != catalog_file
        or bridge_manifest.get("catalog_file_sha256") != expected_catalog_sha
        or bridge_manifest.get("catalog_sha256") != catalog.get("catalog_sha256")
        or bridge_output_path != gpu_source_path
        or bridge_manifest.get("output_dataset_sha256")
        != gpu_manifest.get("source_dataset_sha256")
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "GPU bridge/catalog/source binding mismatch"
        )
    selected_teacher_ids = gpu_manifest.get("selected_teacher_ids")
    weights = _join_selected_awr_rows_v1(
        awr_rows=awr_rows,
        gpu_metadata_by_split=gpu_metadata_by_split,
        selected_teacher_ids=selected_teacher_ids,
        catalog_teacher_ids=[teacher["teacher_id"] for teacher in catalog["teachers"]],
    )
    train_ids = [
        metadata["record_id"] for metadata in gpu_metadata_by_split["train"]
    ]

    if (
        _file_sha(catalog_file, field="catalog") != expected_catalog_sha
        or _file_sha(awr_path, field="AWR manifest") != awr_manifest_file_sha
        or _file_sha(awr_weights_path, field="AWR weights") != sidecar.get("sha256")
        or _file_sha(gpu_manifest_path, field="GPU dataset manifest")
        != gpu_manifest_file_sha
        or _file_sha(gpu_source_path, field="GPU source dataset")
        != gpu_manifest.get("source_dataset_sha256")
    ):
        raise DerivedTeacherStudentV3AwrAdapterError(
            "verified catalog/AWR/GPU input changed before publication"
        )
    dataset_manifest_sha = gpu_manifest_file_sha
    payload = {
        "schema_version": WEIGHT_SIDECAR_SCHEMA,
        "objective_kind": OBJECTIVE_KIND_V1,
        "dataset_manifest_sha256": dataset_manifest_sha,
        "catalog_sha256": catalog["catalog_sha256"],
        "weights": [
            {
                "record_id": record_id,
                "weight": weights[record_id],
            }
            for record_id in sorted(train_ids)
        ],
        "authority": dict(_AUTHORITY),
    }
    body = _canonical(payload)
    _atomic_write_new(destination, body)
    try:
        _joined, stats = load_training_weight_sidecar(
            destination,
            dataset_manifest_sha256=dataset_manifest_sha,
            catalog_sha256=catalog["catalog_sha256"],
            train_record_ids=train_ids,
        )
        if destination.read_bytes() != body:
            raise DerivedTeacherStudentV3AwrAdapterError(
                "weight sidecar bytes changed after atomic publication"
            )
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {
        "output_path": str(destination),
        "output_sha256": _file_sha(destination, field="weight sidecar output"),
        "rows": len(train_ids),
        "selected_teacher_ids": list(selected_teacher_ids),
        "selected_records": sum(
            len(metadata) for metadata in gpu_metadata_by_split.values()
        ),
        "gpu_records": {
            split: len(metadata)
            for split, metadata in sorted(gpu_metadata_by_split.items())
        },
        "dataset_manifest_sha256": dataset_manifest_sha,
        "dataset_sha256": gpu_manifest["dataset_sha256"],
        "catalog_file_sha256": expected_catalog_sha,
        "catalog_sha256": catalog["catalog_sha256"],
        "gpu_bridge_manifest_file_sha256": bridge_manifest_file_sha,
        "gpu_bridge_sha256": bridge_manifest["bridge_sha256"],
        "awr_manifest_file_sha256": awr_manifest_file_sha,
        "awr_manifest_sha256": awr_manifest["manifest_sha256"],
        "awr_weights_sha256": sidecar["sha256"],
        "external_weight_mass": stats["external_weight_mass"],
        "external_weight_ess": stats["external_weight_ess"],
        "authority": dict(_AUTHORITY),
    }


__all__ = [
    "DerivedTeacherStudentV3AwrAdapterError",
    "OBJECTIVE_KIND_V1",
    "build_derived_teacher_student_v3_awr_sidecar_v1",
]
