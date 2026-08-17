"""Publication and source verification for the derived-teacher AWR sidecar."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
    ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1,
    DEFAULT_FOLD_SEED_V1,
    HELDOUT_SPLITS_V1,
    SCHEMA_V1,
    ActorVisibleAwrError,
    ActorVisibleAwrSampleV1,
    actor_visible_awr_diagnostics_v1,
    build_cross_fitted_actor_visible_awr_v1,
    build_derived_teacher_awr_manifest_payload_v1,
    read_actor_visible_awr_sidecar_v1,
    sample_from_training_snapshot_example_v1,
    write_actor_visible_awr_sidecar_v1,
)
from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
    verify_derived_teacher_catalog_v1,
)


_MANIFEST_DOMAIN = b"mage-ptcg:derived-teacher-actor-visible-awr-manifest:v1\0"
_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "catalog", "decision", "sources", "feature_contract",
    "cross_fitting", "weighting", "behavior_probability_required",
    "behavior_probability_used", "counts", "diagnostics", "sidecar", "authority",
    "manifest_sha256",
})
_SOURCE_KEYS = frozenset({
    "teacher_id", "archetype", "policy_sha256", "deck_sha256",
    "snapshot_source_kind",
    "permission_manifest_id", "dataset_manifest_path", "dataset_manifest_sha256",
    "snapshot_index_path", "snapshot_index_sha256", "dataset_snapshot_sha256",
    "feature_domain", "feature_schema_hash", "record_count", "shards",
})
_SHARD_KEYS = frozenset({
    "path", "file_sha256", "snapshot_id", "content_hash", "examples", "split_counts",
})
_INDEX_KEYS = frozenset({
    "schema_version", "dataset_snapshot_sha256", "manifest_id", "dataset_chunks",
    "source_artifacts", "examples_total", "split_names", "split_weights",
    "split_counts", "duplicate_cap", "shards",
})
_INDEX_SHARD_KEYS = frozenset({"path", "snapshot_id", "examples", "split_counts"})


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActorVisibleAwrError("artifact is not finite canonical JSON") from exc


def _manifest_sha(payload: Mapping[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    return hashlib.sha256(_MANIFEST_DOMAIN + _canonical(body)).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path, *, canonical: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_bytes()

        def reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ActorVisibleAwrError("duplicate JSON key")
                result[key] = value
            return result

        def reject_constant(value: str) -> object:
            raise ActorVisibleAwrError(f"non-finite JSON value: {value}")

        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActorVisibleAwrError(f"artifact JSON is unreadable: {path}") from exc
    if type(payload) is not dict:
        raise ActorVisibleAwrError("artifact JSON must be an object")
    if canonical and _canonical(payload) != raw:
        raise ActorVisibleAwrError("artifact JSON is not canonical")
    return payload


def _inside_root(root: Path, value: object, *, field: str, base: Path | None = None) -> Path:
    if type(value) is not str or not value:
        raise ActorVisibleAwrError(f"{field} must be a nonempty path")
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [root / raw]
    if not raw.is_absolute() and base is not None:
        candidates.append(base / raw)
    resolved = next((path.resolve() for path in candidates if path.is_file()), candidates[0].resolve())
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ActorVisibleAwrError(f"{field} escapes repository root") from exc
    return resolved


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ActorVisibleAwrError("artifact output must stay inside repository root") from exc


def _atomic_write_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
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
            raise ActorVisibleAwrError("could not reserve atomic artifact output")
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


def validate_snapshot_source_artifacts_v1(
    value: object, *, expected_policy_sha256: str, expected_source_kind: str,
) -> str:
    """Bind policy identity plus the exact collector-v2 provenance closure."""

    if type(expected_policy_sha256) is not str or len(expected_policy_sha256) != 64:
        raise ActorVisibleAwrError("expected policy SHA-256 is invalid")
    if expected_source_kind not in {
        "pooled_external_submission_agent", "team_internal_agent",
    }:
        raise ActorVisibleAwrError("expected policy source kind is invalid")
    expected_kinds = {
        expected_source_kind,
        "teacher_collection_manifest_v2",
        "teacher_collection_contract_v2",
        "teacher_collection_omissions_v2",
        "teacher_collector_source_snapshot_v2",
        "teacher_permission_trusted_bytes_v1",
        f"teacher_source_kind:{expected_source_kind}",
    }
    if type(value) is not list or len(value) != len(expected_kinds):
        raise ActorVisibleAwrError(
            "snapshot source artifacts do not match the exact collector-v2 kind set"
        )
    by_kind: dict[str, str] = {}
    for row in value:
        if type(row) is not dict or set(row) != {"kind", "artifact_sha256"}:
            raise ActorVisibleAwrError("snapshot policy source schema is invalid")
        kind = row.get("kind")
        sha = row.get("artifact_sha256")
        if type(kind) is not str or kind in by_kind:
            raise ActorVisibleAwrError("snapshot source kind is invalid or duplicated")
        if (
            type(sha) is not str
            or len(sha) != 64
            or any(character not in "0123456789abcdef" for character in sha)
        ):
            raise ActorVisibleAwrError("snapshot source artifact SHA-256 is invalid")
        by_kind[kind] = sha
    if set(by_kind) != expected_kinds:
        raise ActorVisibleAwrError(
            "snapshot source artifacts do not match the exact collector-v2 kind set"
        )
    if by_kind[expected_source_kind] != expected_policy_sha256:
        raise ActorVisibleAwrError("snapshot policy source SHA-256 mismatch")
    source_kind_sha = hashlib.sha256(expected_source_kind.encode("utf-8")).hexdigest()
    if by_kind[f"teacher_source_kind:{expected_source_kind}"] != source_kind_sha:
        raise ActorVisibleAwrError("snapshot source kind hash binding mismatch")
    return expected_source_kind


def write_derived_teacher_awr_manifest_v1(
    payload: Mapping[str, object], path: str | Path,
) -> Path:
    """Write a pre-self-hashed manifest without replacing an existing leaf."""

    raw = dict(payload)
    if set(raw) != _TOP_LEVEL_KEYS or raw.get("schema_version") != SCHEMA_V1:
        raise ActorVisibleAwrError("AWR manifest has an open or invalid schema")
    if raw.get("manifest_sha256") != _manifest_sha(raw):
        raise ActorVisibleAwrError("AWR manifest self SHA-256 does not verify")
    authority = raw.get("authority")
    if type(authority) is not dict or set(authority.values()) != {False}:
        raise ActorVisibleAwrError("AWR manifest must deny every authority")
    destination = Path(path).resolve()
    _atomic_write_new(destination, _canonical(raw))
    return destination


def _verify_source_files(
    payload: Mapping[str, object], *, root: Path,
) -> None:
    catalog_binding = payload["catalog"]
    decision_binding = payload["decision"]
    if type(catalog_binding) is not dict or set(catalog_binding) != {
        "path", "file_sha256", "catalog_sha256",
    }:
        raise ActorVisibleAwrError("catalog binding schema is invalid")
    if type(decision_binding) is not dict or set(decision_binding) != {"path", "sha256"}:
        raise ActorVisibleAwrError("decision binding schema is invalid")
    catalog_path = _inside_root(root, catalog_binding["path"], field="catalog")
    if not catalog_path.is_file() or _file_sha(catalog_path) != catalog_binding["file_sha256"]:
        raise ActorVisibleAwrError("catalog file SHA-256 mismatch")
    catalog = verify_derived_teacher_catalog_v1(catalog_path, root)
    if catalog.get("catalog_sha256") != catalog_binding["catalog_sha256"]:
        raise ActorVisibleAwrError("catalog semantic SHA-256 mismatch")
    decision_path = _inside_root(root, decision_binding["path"], field="decision")
    if not decision_path.is_file() or _file_sha(decision_path) != decision_binding["sha256"]:
        raise ActorVisibleAwrError("decision SHA-256 mismatch")
    catalog_rows = {row["teacher_id"]: row for row in catalog["teachers"]}
    sources = payload["sources"]
    if type(sources) is not list or set(row.get("teacher_id") for row in sources) != set(catalog_rows):
        raise ActorVisibleAwrError("source teacher set does not equal the catalog")
    for source in sources:
        if type(source) is not dict or set(source) != _SOURCE_KEYS:
            raise ActorVisibleAwrError("source binding has an open or invalid schema")
        teacher = catalog_rows[source["teacher_id"]]
        collection = teacher["collection"]
        if (
            source["archetype"] != teacher["archetype"]
            or source["policy_sha256"] != teacher["policy"]["sha256"]
            or source["deck_sha256"] != teacher["deck"]["sha256"]
            or source["permission_manifest_id"] != collection["dataset_manifest"]["permission_manifest_id"]
        ):
            raise ActorVisibleAwrError("source identity does not bind its catalog teacher")
        for path_field, sha_field in (
            ("dataset_manifest_path", "dataset_manifest_sha256"),
            ("snapshot_index_path", "snapshot_index_sha256"),
        ):
            path = _inside_root(root, source[path_field], field=path_field)
            if not path.is_file() or _file_sha(path) != source[sha_field]:
                raise ActorVisibleAwrError(f"{path_field} SHA-256 mismatch")
        index_path = _inside_root(root, source["snapshot_index_path"], field="snapshot index")
        index = _strict_json(index_path)
        actual_source_kind = validate_snapshot_source_artifacts_v1(
            index.get("source_artifacts"),
            expected_policy_sha256=source["policy_sha256"],
            expected_source_kind=teacher["source_kind"],
        )
        if actual_source_kind != source["snapshot_source_kind"]:
            raise ActorVisibleAwrError("snapshot source kind binding mismatch")
        shards = source["shards"]
        if type(shards) is not list or not shards:
            raise ActorVisibleAwrError("verified source must bind at least one snapshot shard")
        for shard in shards:
            if type(shard) is not dict or set(shard) != _SHARD_KEYS:
                raise ActorVisibleAwrError("snapshot shard binding schema is invalid")
            path = _inside_root(root, shard["path"], field="snapshot shard")
            if not path.is_file() or _file_sha(path) != shard["file_sha256"]:
                raise ActorVisibleAwrError("snapshot shard SHA-256 mismatch")


def read_derived_teacher_awr_manifest_v1(
    path: str | Path,
    *,
    repo_root: str | Path,
    verify_sources: bool = True,
) -> dict[str, object]:
    """Read, re-hash, and cross-check the manifest against its sidecar."""

    root = Path(repo_root).resolve()
    payload = _strict_json(Path(path).resolve())
    if set(payload) != _TOP_LEVEL_KEYS or payload.get("schema_version") != SCHEMA_V1:
        raise ActorVisibleAwrError("AWR manifest has an open or invalid schema")
    if payload.get("manifest_sha256") != _manifest_sha(payload):
        raise ActorVisibleAwrError("AWR manifest SHA-256 mismatch; manifest may be tampered")
    authority = payload.get("authority")
    expected_authority = {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    if authority != expected_authority:
        raise ActorVisibleAwrError("AWR manifest authority must remain false")
    if payload.get("behavior_probability_required") is not False or payload.get("behavior_probability_used") is not False:
        raise ActorVisibleAwrError("AWR manifest may not require or claim behavior probabilities")
    feature = payload.get("feature_contract")
    if (
        type(feature) is not dict
        or feature.get("feature_schema") != ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1
        or feature.get("strict_public_only") is not False
        or feature.get("information_boundary") != "actor-visible-including-own-private-state"
    ):
        raise ActorVisibleAwrError("actor-visible feature boundary was reclassified")
    cross = payload.get("cross_fitting")
    if (
        type(cross) is not dict
        or cross.get("fit_splits") != ["train"]
        or cross.get("fit_forbidden_splits") != sorted(HELDOUT_SPLITS_V1)
        or any(row.get("fit_score_episode_intersection_count") != 0 for row in cross.get("fold_models", []))
    ):
        raise ActorVisibleAwrError("cross-fitting contract permits leakage")
    sidecar = payload.get("sidecar")
    if type(sidecar) is not dict or set(sidecar) != {"path", "sha256", "row_count", "format"}:
        raise ActorVisibleAwrError("sidecar binding schema is invalid")
    sidecar_path = _inside_root(root, sidecar["path"], field="sidecar")
    rows = read_actor_visible_awr_sidecar_v1(sidecar_path, expected_sha256=sidecar["sha256"])
    if sidecar.get("format") != "canonical-jsonl-v1" or sidecar.get("row_count") != len(rows):
        raise ActorVisibleAwrError("sidecar row count or format mismatch")
    counts = {
        "rows": len(rows),
        "episodes": len({row.episode_id for row in rows}),
        "train_rows": sum(row.split == "train" for row in rows),
        "heldout_rows": sum(row.split != "train" for row in rows),
        "teachers": len({row.teacher_id for row in rows}),
    }
    if payload.get("counts") != counts:
        raise ActorVisibleAwrError("manifest counts do not match sidecar rows")
    if payload.get("diagnostics") != actor_visible_awr_diagnostics_v1(rows):
        raise ActorVisibleAwrError("manifest diagnostics do not match sidecar rows")
    weighting = payload.get("weighting")
    if type(weighting) is not dict or weighting.get("behavior_probability_required") is not False or weighting.get("behavior_probability_used") is not False:
        raise ActorVisibleAwrError("weighting behavior-probability contract is invalid")
    cap = weighting.get("normalized_upper_bound")
    if type(cap) not in (int, float) or type(cap) is bool or cap < 1.0:
        raise ActorVisibleAwrError("normalized AWR upper bound is invalid")
    train_weights = [row.awr_weight for row in rows if row.split == "train"]
    if (
        not train_weights
        or max(row.awr_weight for row in rows) > float(cap) + 1e-12
        or abs(sum(train_weights) / len(train_weights) - 1.0) > 1e-12
    ):
        raise ActorVisibleAwrError("sidecar AWR weights violate normalization/bounds")
    sources = payload.get("sources")
    if type(sources) is not list or not sources or any(type(row) is not dict or set(row) != _SOURCE_KEYS for row in sources):
        raise ActorVisibleAwrError("manifest source binding schema is invalid")
    if sum(row["record_count"] for row in sources) != len(rows):
        raise ActorVisibleAwrError("source record counts do not match sidecar")
    if verify_sources:
        _verify_source_files(payload, root=root)
    return payload


def _snapshot_path(root: Path, index_path: Path, value: object) -> Path:
    return _inside_root(root, value, field="snapshot shard", base=index_path.parent)


def load_derived_teacher_snapshot_samples_v1(
    *, repo_root: str | Path, catalog_path: str | Path,
) -> tuple[tuple[ActorVisibleAwrSampleV1, ...], list[dict[str, object]], dict[str, object]]:
    """Load all six READY catalog teachers through verified sealed snapshots."""

    root = Path(repo_root).resolve()
    catalog_file = Path(catalog_path).resolve()
    catalog = verify_derived_teacher_catalog_v1(catalog_file, root)
    samples: list[ActorVisibleAwrSampleV1] = []
    sources: list[dict[str, object]] = []
    seen_records: set[str] = set()
    for teacher in catalog["teachers"]:
        collection = teacher["collection"]
        if collection.get("status") != "READY":
            raise ActorVisibleAwrError("every selected derived teacher must be READY")
        index_binding = collection["snapshot_index"]
        index_path = _inside_root(root, index_binding["path"], field="snapshot index")
        if _file_sha(index_path) != index_binding["file_sha256"]:
            raise ActorVisibleAwrError("snapshot index SHA-256 differs from catalog")
        index = _strict_json(index_path)
        if set(index) != _INDEX_KEYS or index.get("schema_version") != "specialist-training-snapshot-index-v1":
            raise ActorVisibleAwrError("snapshot index has an open or invalid schema")
        if index.get("examples_total") != index_binding["examples_total"] or index.get("split_counts") != index_binding["split_counts"]:
            raise ActorVisibleAwrError("snapshot index counts differ from catalog")
        source_artifacts = index.get("source_artifacts")
        snapshot_source_kind = validate_snapshot_source_artifacts_v1(
            source_artifacts,
            expected_policy_sha256=teacher["policy"]["sha256"],
            expected_source_kind=teacher["source_kind"],
        )
        shard_rows = index.get("shards")
        if type(shard_rows) is not list or not shard_rows:
            raise ActorVisibleAwrError("snapshot index has no shards")
        teacher_samples: list[ActorVisibleAwrSampleV1] = []
        shard_bindings: list[dict[str, object]] = []
        split_counts: Counter[str] = Counter()
        feature_identities: set[tuple[str, str]] = set()
        from mage_ptcg.meta_specialist.training_snapshot_v1 import read_training_snapshot_v1

        for shard_row in shard_rows:
            if type(shard_row) is not dict or set(shard_row) != _INDEX_SHARD_KEYS:
                raise ActorVisibleAwrError("snapshot index shard row is invalid")
            shard_path = _snapshot_path(root, index_path, shard_row["path"])
            snapshot = read_training_snapshot_v1(shard_path)
            if (
                snapshot.get("snapshot_id") != shard_row["snapshot_id"]
                or len(snapshot["examples"]) != shard_row["examples"]
                or snapshot.get("split_counts") != shard_row["split_counts"]
                or snapshot.get("dataset_snapshot_sha256") != index["dataset_snapshot_sha256"]
                or snapshot.get("manifest_id") != index["manifest_id"]
                or snapshot.get("source_artifacts") != index["source_artifacts"]
            ):
                raise ActorVisibleAwrError("snapshot shard does not bind its index")
            feature_identities.add((snapshot["feature_domain"], snapshot["feature_schema_hash"]))
            for example in snapshot["examples"]:
                sample = sample_from_training_snapshot_example_v1(
                    example, teacher_id=teacher["teacher_id"],
                )
                if sample.record_id in seen_records:
                    raise ActorVisibleAwrError("record ID is duplicated across teacher snapshots")
                seen_records.add(sample.record_id)
                teacher_samples.append(sample)
            split_counts.update(snapshot["split_counts"])
            shard_bindings.append({
                "path": _relative(root, shard_path),
                "file_sha256": _file_sha(shard_path),
                "snapshot_id": snapshot["snapshot_id"],
                "content_hash": snapshot["content_hash"],
                "examples": len(snapshot["examples"]),
                "split_counts": snapshot["split_counts"],
            })
        if len(feature_identities) != 1:
            raise ActorVisibleAwrError("one teacher snapshot uses multiple feature schemas")
        if len(teacher_samples) != index["examples_total"] or dict(split_counts) != index["split_counts"]:
            raise ActorVisibleAwrError("snapshot shard totals do not match index")
        feature_domain, feature_schema_hash = next(iter(feature_identities))
        dataset_binding = collection["dataset_manifest"]
        samples.extend(teacher_samples)
        sources.append({
            "teacher_id": teacher["teacher_id"],
            "archetype": teacher["archetype"],
            "policy_sha256": teacher["policy"]["sha256"],
            "deck_sha256": teacher["deck"]["sha256"],
            "snapshot_source_kind": snapshot_source_kind,
            "permission_manifest_id": dataset_binding["permission_manifest_id"],
            "dataset_manifest_path": dataset_binding["path"],
            "dataset_manifest_sha256": dataset_binding["file_sha256"],
            "snapshot_index_path": index_binding["path"],
            "snapshot_index_sha256": index_binding["file_sha256"],
            "dataset_snapshot_sha256": index["dataset_snapshot_sha256"],
            "feature_domain": feature_domain,
            "feature_schema_hash": feature_schema_hash,
            "record_count": len(teacher_samples),
            "shards": shard_bindings,
        })
    return tuple(samples), sources, catalog


def build_derived_teacher_awr_artifact_v1(
    *,
    repo_root: str | Path,
    catalog_path: str | Path,
    output_sidecar_path: str | Path,
    output_manifest_path: str | Path,
    fold_count: int = 5,
    fold_seed: str = DEFAULT_FOLD_SEED_V1,
    ridge_lambda: float = 1.0,
    beta: float = 1.0,
    max_weight: float = 20.0,
) -> dict[str, object]:
    """Build, publish, and fully reverify the six-teacher AWR artifact."""

    root = Path(repo_root).resolve()
    catalog_file = Path(catalog_path).resolve()
    sidecar_path = Path(output_sidecar_path).resolve()
    manifest_path = Path(output_manifest_path).resolve()
    if sidecar_path == manifest_path or sidecar_path.exists() or manifest_path.exists():
        raise FileExistsError("AWR outputs must be distinct new paths")
    _relative(root, sidecar_path)
    _relative(root, manifest_path)
    samples, sources, catalog = load_derived_teacher_snapshot_samples_v1(
        repo_root=root, catalog_path=catalog_file,
    )
    result = build_cross_fitted_actor_visible_awr_v1(
        samples,
        fold_count=fold_count,
        fold_seed=fold_seed,
        ridge_lambda=ridge_lambda,
        beta=beta,
        max_weight=max_weight,
    )
    sidecar_binding = write_actor_visible_awr_sidecar_v1(result.rows, sidecar_path)
    sidecar_binding["path"] = _relative(root, sidecar_path)
    decision = catalog["decision"]
    payload = build_derived_teacher_awr_manifest_payload_v1(
        result=result,
        catalog_binding={
            "path": _relative(root, catalog_file),
            "file_sha256": _file_sha(catalog_file),
            "catalog_sha256": catalog["catalog_sha256"],
        },
        decision_binding={"path": decision["path"], "sha256": decision["sha256"]},
        source_bindings=sources,
        sidecar_binding=sidecar_binding,
    )
    try:
        write_derived_teacher_awr_manifest_v1(payload, manifest_path)
        return read_derived_teacher_awr_manifest_v1(
            manifest_path, repo_root=root, verify_sources=True,
        )
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        raise


__all__ = [
    "validate_snapshot_source_artifacts_v1",
    "write_derived_teacher_awr_manifest_v1",
    "read_derived_teacher_awr_manifest_v1",
    "load_derived_teacher_snapshot_samples_v1",
    "build_derived_teacher_awr_artifact_v1",
]
