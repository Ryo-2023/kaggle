"""Seal an official-data-only self-owned CG package as fresh meta evidence.

This module is deliberately a source boundary, not a promotion path.  It copies
only the already-verified package entry point and deck into an isolated,
registry-driven opponent pool.  A second, no-clobber promotion step is needed
after a fault-free CABT smoke; only that promoted root contains ``fresh_meta``
that the BestKnown loop can consume.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import (
    AUTHORITY_FALSE_V1,
    build_fresh_meta_batch_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (
    verify_self_owned_cg_package_v1,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


SCHEMA_V1 = "self-owned-cg-meta-source-v1"
FRESHNESS_SCHEMA_V1 = "self-owned-cg-meta-source-freshness-v1"
SOURCE_KIND_V1 = "self_owned_official_card_data_deck_with_p1_policy"
SOURCE_KIND_INDEPENDENT_V1 = "self_owned_official_card_data_deck_with_independent_root_policy"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PACKAGE_AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


class SelfOwnedCgMetaSourceError(ValueError):
    """Raised when a self-owned meta source cannot be sealed fail-closed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SelfOwnedCgMetaSourceError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SelfOwnedCgMetaSourceError("value is not canonical JSON") from exc


def _write_json_new(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value) + b"\n"
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def _prepare_empty_root(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise FileExistsError(path)
        return
    path.mkdir(parents=True, exist_ok=False)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgMetaSourceError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise SelfOwnedCgMetaSourceError(f"JSON object required: {path}")
    return value


def _deck_identity(deck_path: Path) -> tuple[str, str]:
    try:
        values = [int(token) for token in deck_path.read_text(encoding="utf-8").split()]
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SelfOwnedCgMetaSourceError(f"cannot parse deck: {deck_path}") from exc
    if len(values) != 60 or any(value <= 0 for value in values):
        raise SelfOwnedCgMetaSourceError(f"deck must contain exactly 60 positive IDs: {deck_path}")
    return _sha256_file(deck_path), canonical_deck_sha256(values)


def _validate_source_id(source_id: str) -> str:
    if type(source_id) is not str or not _SAFE_ID.fullmatch(source_id):
        raise SelfOwnedCgMetaSourceError(
            "source_id must be an ASCII identifier of at most 128 characters"
        )
    return source_id


def _package_inputs(candidate_package: Path) -> tuple[Mapping[str, Any], str, str, str]:
    package_manifest = verify_self_owned_cg_package_v1(candidate_package)
    main = candidate_package / "main.py"
    deck = candidate_package / "deck.csv"
    policy_sha = _sha256_file(main)
    deck_file_sha, canonical_deck_sha = _deck_identity(deck)
    if package_manifest.get("policy_sha256") != policy_sha:
        raise SelfOwnedCgMetaSourceError("package policy identity changed")
    if package_manifest.get("deck_file_sha256") != deck_file_sha:
        raise SelfOwnedCgMetaSourceError("package deck identity changed")
    if package_manifest.get("canonical_deck_sha256") != canonical_deck_sha:
        raise SelfOwnedCgMetaSourceError("package canonical deck identity changed")
    if package_manifest.get("parent_deck") is not None or package_manifest.get("public_parent_read") is not False:
        raise SelfOwnedCgMetaSourceError("package is not an official-data-only self-owned deck")
    if package_manifest.get("authority") != _PACKAGE_AUTHORITY_FALSE:
        raise SelfOwnedCgMetaSourceError("package grants forbidden authority")
    return package_manifest, policy_sha, deck_file_sha, canonical_deck_sha


def materialize_self_owned_cg_meta_source_v1(
    *,
    candidate_package: str | Path,
    output_root: str | Path,
    seed_namespace: str,
    source_id: str | None = None,
    generation_manifest: str | Path | None = None,
) -> dict[str, object]:
    """Create a staged, unpromoted one-source opponent pool."""

    if type(seed_namespace) is not str or not seed_namespace.strip():
        raise SelfOwnedCgMetaSourceError("seed_namespace must be non-empty")
    package_root = Path(candidate_package).resolve()
    output = Path(output_root).resolve()
    manifest, policy_sha, deck_file_sha, canonical_deck_sha = _package_inputs(package_root)
    candidate_id = manifest.get("candidate_id")
    if type(candidate_id) is not str or not candidate_id:
        raise SelfOwnedCgMetaSourceError("package candidate_id is missing")
    resolved_id = source_id or f"self-owned-cg-{candidate_id}"
    _validate_source_id(resolved_id)
    _prepare_empty_root(output)

    source_dir = output / resolved_id
    source_dir.mkdir(parents=False, exist_ok=False)
    for name in ("main.py", "deck.csv", "self_owned_cg_package_manifest.json"):
        source = package_root / name
        if source.is_symlink() or not source.is_file():
            raise SelfOwnedCgMetaSourceError(f"package is missing {name}: {package_root}")
        shutil.copy2(source, source_dir / name)

    generation_sha: str | None = None
    generation_path: str | None = None
    if generation_manifest is not None:
        generation = Path(generation_manifest).resolve()
        generation_sha = _sha256_file(generation)
        generation_path = str(generation)

    source_body: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "status": "STAGED",
        "source_id": resolved_id,
        "source_kind": SOURCE_KIND_V1,
        "seed_namespace": seed_namespace,
        "candidate_id": candidate_id,
        "candidate_package_manifest_sha256": _sha256_file(package_root / "self_owned_cg_package_manifest.json"),
        "parent_policy_sha256": manifest.get("parent_policy_sha256"),
        "policy_sha256": policy_sha,
        "deck_file_sha256": deck_file_sha,
        "canonical_deck_hash": canonical_deck_sha,
        "parent_deck": None,
        "public_parent_read": False,
        "generation_manifest_path": generation_path,
        "generation_manifest_sha256": generation_sha,
        "usage_boundary": "local_eval_only",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    source_body["manifest_sha256"] = _sha256_bytes(_canonical_json(source_body))
    source_manifest_sha = _write_json_new(output / "source_manifest.json", source_body)

    pool_row = {
        "id": resolved_id,
        "policy_hash": policy_sha,
        "canonical_deck_hash": canonical_deck_sha,
        "source": SOURCE_KIND_V1,
        "usage_boundary": "local_eval_only",
        "smoke_ok": False,
        "mean_decision_ms": None,
        "source_manifest_sha256": source_manifest_sha,
    }
    pool_sha = _write_json_new(output / "pool_manifest.json", [pool_row])
    return {
        "schema_version": SCHEMA_V1,
        "status": "STAGED",
        "source_id": resolved_id,
        "pool_manifest_sha256": pool_sha,
        "source_manifest_sha256": source_manifest_sha,
        "policy_sha256": policy_sha,
        "deck_file_sha256": deck_file_sha,
        "canonical_deck_hash": canonical_deck_sha,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }


def _fault_free_smoke(path: Path) -> Mapping[str, Any]:
    smoke = _read_json(path)
    if smoke.get("status") != "COMPLETE":
        raise SelfOwnedCgMetaSourceError("smoke summary is not COMPLETE")
    evaluation = smoke.get("evaluator_summary")
    if not isinstance(evaluation, Mapping):
        raise SelfOwnedCgMetaSourceError("smoke summary has no evaluator_summary")
    faults = evaluation.get("faults")
    requested = evaluation.get("requested_games")
    completed = evaluation.get("completed_games")
    if type(faults) is not int or faults != 0:
        raise SelfOwnedCgMetaSourceError("smoke summary contains fault games")
    if type(requested) is not int or requested <= 0 or completed != requested:
        raise SelfOwnedCgMetaSourceError("smoke summary is incomplete")
    status_distribution = evaluation.get("status_distribution")
    if isinstance(status_distribution, Mapping) and status_distribution.get("DONE") != requested:
        raise SelfOwnedCgMetaSourceError("smoke summary is not all DONE")
    return smoke


def promote_self_owned_cg_meta_source_v1(
    *,
    staged_root: str | Path,
    output_root: str | Path,
    smoke_summary: str | Path,
) -> dict[str, object]:
    """Copy a staged source into a new root after a fault-free smoke gate."""

    staged = Path(staged_root).resolve()
    output = Path(output_root).resolve()
    staged_manifest = _read_json(staged / "source_manifest.json")
    if staged_manifest.get("schema_version") != SCHEMA_V1 or staged_manifest.get("status") != "STAGED":
        raise SelfOwnedCgMetaSourceError("staged source manifest is invalid")
    source_id = _validate_source_id(str(staged_manifest.get("source_id")))
    smoke_path = Path(smoke_summary).resolve()
    smoke = _fault_free_smoke(smoke_path)
    pool_rows = json.loads((staged / "pool_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(pool_rows, list) or len(pool_rows) != 1 or pool_rows[0].get("id") != source_id:
        raise SelfOwnedCgMetaSourceError("staged pool has an unexpected source row")
    staged_row = dict(pool_rows[0])
    source_dir = staged / source_id
    if not source_dir.is_dir():
        raise SelfOwnedCgMetaSourceError("staged source directory is missing")
    _prepare_empty_root(output)
    target_dir = output / source_id
    shutil.copytree(source_dir, target_dir, symlinks=False)
    shutil.copy2(smoke_path, output / "smoke_summary.json")

    promoted_body = dict(staged_manifest)
    promoted_body.update(
        {
            "status": "PROMOTED",
            "smoke_summary_sha256": _sha256_file(smoke_path),
            "smoke_requested_games": smoke["evaluator_summary"]["requested_games"],
            "smoke_faults": 0,
        }
    )
    promoted_body.pop("manifest_sha256", None)
    promoted_body["manifest_sha256"] = _sha256_bytes(_canonical_json(promoted_body))
    source_manifest_sha = _write_json_new(output / "source_manifest.json", promoted_body)

    staged_row.update(
        {
            "smoke_ok": True,
            "smoke_summary_sha256": _sha256_file(smoke_path),
            "source_manifest_sha256": source_manifest_sha,
        }
    )
    pool_sha = _write_json_new(output / "pool_manifest.json", [staged_row])

    evidence = {
        "schema_version": FRESHNESS_SCHEMA_V1,
        "source_id": source_id,
        "source": staged_row["source"],
        "candidate_id": promoted_body["candidate_id"],
        "policy_sha256": staged_row["policy_hash"],
        "canonical_deck_hash": staged_row["canonical_deck_hash"],
        "source_manifest_sha256": source_manifest_sha,
        "pool_manifest_sha256": pool_sha,
        "smoke_summary_sha256": _sha256_file(smoke_path),
        "fresh": True,
        "unused_before_run": True,
        "usage_boundary": "local_eval_only",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    evidence_sha = _write_json_new(output / "freshness-evidence.json", evidence)
    seed_namespace = str(promoted_body["seed_namespace"])
    seed_plan = {
        "source_id": source_id,
        "seed_namespace": seed_namespace,
        "smoke_summary_sha256": _sha256_file(smoke_path),
        "pool_manifest_sha256": pool_sha,
    }
    fresh_meta = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"self-owned-cg-{source_id}-{seed_namespace}",
        "source_epoch": "self_owned_official_card_data_deck_v1",
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": _sha256_bytes(_canonical_json(seed_plan)),
        "pool_manifest_sha256": pool_sha,
        "reference_ids": [source_id],
        "references": [
            {
                "id": source_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": evidence_sha,
                "freshness_evidence_path": "freshness-evidence.json",
                "canonical_deck_hash": staged_row["canonical_deck_hash"],
                "policy_sha256": staged_row["policy_hash"],
                "source": staged_row["source"],
            }
        ],
        "freshness_basis": "official-card-data-only self-owned deck package plus fault-free CABT smoke",
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh_meta)
    build_fresh_meta_batch_v1(
        manifest_path=fresh_path,
        pool_manifest_path=output / "pool_manifest.json",
        consumed_ids=(),
        consumed_seed_namespaces=(),
    )
    return {
        "schema_version": SCHEMA_V1,
        "status": "PROMOTED",
        "source_id": source_id,
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "fresh_meta_verified": True,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }


def materialize_self_owned_cg_meta_batch_v1(
    *,
    candidate_packages: Sequence[str | Path],
    output_root: str | Path,
    seed_namespace: str,
    generation_manifests: Sequence[str | Path] = (),
    source_epoch: str = "self_owned_official_card_data_deck_v2",
    source_kind: str = SOURCE_KIND_V1,
) -> dict[str, object]:
    """Stage several distinct self-owned packages under one pool manifest."""

    packages = tuple(Path(value).resolve() for value in candidate_packages)
    if not packages or len(packages) != len(set(packages)):
        raise SelfOwnedCgMetaSourceError("candidate_packages must contain unique non-empty paths")
    if generation_manifests and len(generation_manifests) != len(packages):
        raise SelfOwnedCgMetaSourceError("generation_manifests must match candidate_packages")
    generation_paths = tuple(Path(value).resolve() for value in generation_manifests)
    if not seed_namespace.strip():
        raise SelfOwnedCgMetaSourceError("seed_namespace must be non-empty")
    if type(source_epoch) is not str or not source_epoch.strip():
        raise SelfOwnedCgMetaSourceError("source_epoch must be non-empty")
    if type(source_kind) is not str or not source_kind.strip():
        raise SelfOwnedCgMetaSourceError("source_kind must be non-empty")
    output = Path(output_root).resolve()
    _prepare_empty_root(output)
    rows: list[dict[str, object]] = []
    source_ids: list[str] = []
    for index, package_root in enumerate(packages):
        manifest, policy_sha, deck_file_sha, canonical_deck_sha = _package_inputs(package_root)
        candidate_id = manifest.get("candidate_id")
        if type(candidate_id) is not str or not candidate_id:
            raise SelfOwnedCgMetaSourceError("package candidate_id is missing")
        source_id = _validate_source_id(f"self-owned-cg-{candidate_id}")
        if source_id in source_ids:
            raise SelfOwnedCgMetaSourceError(f"duplicate generated source id: {source_id}")
        source_ids.append(source_id)
        source_dir = output / source_id
        source_dir.mkdir(parents=False, exist_ok=False)
        for name in ("main.py", "deck.csv", "self_owned_cg_package_manifest.json"):
            source = package_root / name
            if source.is_symlink() or not source.is_file():
                raise SelfOwnedCgMetaSourceError(f"package is missing {name}: {package_root}")
            shutil.copy2(source, source_dir / name)
        generation_path = str(generation_paths[index]) if generation_paths else None
        generation_sha = _sha256_file(generation_paths[index]) if generation_paths else None
        source_body: dict[str, object] = {
            "schema_version": SCHEMA_V1,
            "status": "STAGED",
            "source_id": source_id,
            "source_kind": source_kind,
            "seed_namespace": seed_namespace,
            "candidate_id": candidate_id,
            "candidate_package_manifest_sha256": _sha256_file(package_root / "self_owned_cg_package_manifest.json"),
            "parent_policy_sha256": manifest.get("parent_policy_sha256"),
            "policy_sha256": policy_sha,
            "deck_file_sha256": deck_file_sha,
            "canonical_deck_hash": canonical_deck_sha,
            "parent_deck": None,
            "public_parent_read": False,
            "generation_manifest_path": generation_path,
            "generation_manifest_sha256": generation_sha,
            "usage_boundary": "local_eval_only",
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        }
        source_body["manifest_sha256"] = _sha256_bytes(_canonical_json(source_body))
        source_manifest_sha = _write_json_new(source_dir / "source_manifest.json", source_body)
        rows.append(
            {
                "id": source_id,
                "policy_hash": policy_sha,
                "canonical_deck_hash": canonical_deck_sha,
                "source": source_kind,
                "usage_boundary": "local_eval_only",
                "smoke_ok": False,
                "mean_decision_ms": None,
                "source_manifest_sha256": source_manifest_sha,
            }
        )
    rows.sort(key=lambda row: str(row["id"]))
    pool_sha = _write_json_new(output / "pool_manifest.json", rows)
    batch_body: dict[str, object] = {
        "schema_version": "self-owned-cg-meta-batch-v1",
        "status": "STAGED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "source_ids": [str(row["id"]) for row in rows],
        "pool_manifest_sha256": pool_sha,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    batch_body["manifest_sha256"] = _sha256_bytes(_canonical_json(batch_body))
    batch_sha = _write_json_new(output / "batch_manifest.json", batch_body)
    return {
        "schema_version": "self-owned-cg-meta-batch-v1",
        "status": "STAGED",
        "source_ids": [str(row["id"]) for row in rows],
        "pool_manifest_sha256": pool_sha,
        "batch_manifest_sha256": batch_sha,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }


def promote_self_owned_cg_meta_batch_v1(
    *,
    staged_root: str | Path,
    output_root: str | Path,
    smoke_summary: str | Path,
) -> dict[str, object]:
    """Promote a staged batch after one fault-free, all-source smoke."""

    staged = Path(staged_root).resolve()
    output = Path(output_root).resolve()
    batch = _read_json(staged / "batch_manifest.json")
    if batch.get("schema_version") != "self-owned-cg-meta-batch-v1" or batch.get("status") != "STAGED":
        raise SelfOwnedCgMetaSourceError("staged batch manifest is invalid")
    source_ids = tuple(sorted(str(value) for value in batch.get("source_ids", [])))
    if not source_ids:
        raise SelfOwnedCgMetaSourceError("staged batch has no source ids")
    smoke_path = Path(smoke_summary).resolve()
    smoke = _fault_free_smoke(smoke_path)
    raw_rows = json.loads((staged / "pool_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(raw_rows, list) or {str(row.get("id")) for row in raw_rows} != set(source_ids):
        raise SelfOwnedCgMetaSourceError("staged batch pool does not match source ids")
    _prepare_empty_root(output)
    smoke_sha = _sha256_file(smoke_path)
    promoted_rows: list[dict[str, object]] = []
    for raw_row in sorted(raw_rows, key=lambda row: str(row["id"])):
        source_id = str(raw_row["id"])
        source_dir = staged / source_id
        if not source_dir.is_dir():
            raise SelfOwnedCgMetaSourceError(f"staged source directory is missing: {source_id}")
        target_dir = output / source_id
        shutil.copytree(source_dir, target_dir, symlinks=False)
        source_manifest_path = target_dir / "source_manifest.json"
        source_manifest = _read_json(source_manifest_path)
        source_manifest = dict(source_manifest)
        source_manifest.pop("manifest_sha256", None)
        source_manifest.update(
            {
                "status": "PROMOTED",
                "smoke_summary_sha256": smoke_sha,
                "smoke_requested_games": smoke["evaluator_summary"]["requested_games"],
                "smoke_faults": 0,
            }
        )
        source_manifest["manifest_sha256"] = _sha256_bytes(_canonical_json(source_manifest))
        source_manifest_path.write_bytes(_canonical_json(source_manifest) + b"\n")
        row = dict(raw_row)
        row.update({"smoke_ok": True, "smoke_summary_sha256": smoke_sha, "source_manifest_sha256": _sha256_file(source_manifest_path)})
        promoted_rows.append(row)
    promoted_rows.sort(key=lambda row: str(row["id"]))
    pool_sha = _write_json_new(output / "pool_manifest.json", promoted_rows)
    # The promotion rewrites each source manifest and therefore produces a
    # new pool manifest identity.  Preserve the original smoke evidence while
    # rebinding its pool SHA to that promoted identity; otherwise downstream
    # immutable-pool mergers correctly reject the promoted root as mismatched.
    smoke_out = output / "smoke_summary.json"
    promoted_smoke = dict(smoke)
    promoted_smoke["input_pool_manifest_sha256"] = _sha256_file(staged / "pool_manifest.json")
    promoted_smoke["input_smoke_summary_sha256"] = smoke_sha
    promoted_smoke["pool_manifest_sha256"] = pool_sha
    promoted_smoke["promotion_schema_version"] = "self-owned-cg-meta-promotion-v1"
    promoted_smoke["status"] = "COMPLETE"
    promoted_smoke["faults"] = 0
    promoted_smoke["reference_ids"] = list(source_ids)
    promoted_smoke["requested_games"] = smoke["evaluator_summary"]["requested_games"]
    promoted_smoke["completed_rows"] = smoke["evaluator_summary"]["completed_games"]
    promoted_smoke["partial_promotion"] = False
    _write_json_new(smoke_out, promoted_smoke)
    output_smoke_sha = _sha256_file(smoke_out)
    promoted_batch = dict(batch)
    promoted_batch.pop("manifest_sha256", None)
    promoted_batch.update({"status": "PROMOTED", "pool_manifest_sha256": pool_sha, "smoke_summary_sha256": output_smoke_sha})
    promoted_batch["manifest_sha256"] = _sha256_bytes(_canonical_json(promoted_batch))
    _write_json_new(output / "batch_manifest.json", promoted_batch)

    references: list[dict[str, object]] = []
    for row in promoted_rows:
        source_id = str(row["id"])
        evidence = {
            "schema_version": FRESHNESS_SCHEMA_V1,
            "source_id": source_id,
            "source": row["source"],
            "policy_sha256": row["policy_hash"],
            "canonical_deck_hash": row["canonical_deck_hash"],
            "source_manifest_sha256": row["source_manifest_sha256"],
            "pool_manifest_sha256": pool_sha,
            "smoke_summary_sha256": output_smoke_sha,
            "fresh": True,
            "unused_before_run": True,
            "usage_boundary": "local_eval_only",
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        }
        evidence_name = f"freshness-evidence-{source_id}.json"
        evidence_sha = _write_json_new(output / evidence_name, evidence)
        references.append(
            {
                "id": source_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": evidence_sha,
                "freshness_evidence_path": evidence_name,
                "canonical_deck_hash": row["canonical_deck_hash"],
                "policy_sha256": row["policy_hash"],
                "source": row["source"],
            }
        )
    references.sort(key=lambda row: str(row["id"]))
    seed_namespace = str(batch["seed_namespace"])
    seed_plan = {
        "source_ids": source_ids,
        "seed_namespace": seed_namespace,
        "smoke_summary_sha256": output_smoke_sha,
        "pool_manifest_sha256": pool_sha,
    }
    fresh_meta = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"self-owned-cg-batch-{seed_namespace}",
        "source_epoch": str(batch["source_epoch"]),
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": _sha256_bytes(_canonical_json(seed_plan)),
        "pool_manifest_sha256": pool_sha,
        "reference_ids": list(source_ids),
        "references": references,
        "freshness_basis": "official-card-data-only self-owned deck batch plus fault-free CABT smoke",
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh_meta)
    build_fresh_meta_batch_v1(
        manifest_path=fresh_path,
        pool_manifest_path=output / "pool_manifest.json",
        consumed_ids=(),
        consumed_seed_namespaces=(),
    )
    return {
        "schema_version": "self-owned-cg-meta-batch-v1",
        "status": "PROMOTED",
        "source_ids": list(source_ids),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "fresh_meta_verified": True,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }


__all__ = [
    "SCHEMA_V1",
    "FRESHNESS_SCHEMA_V1",
    "SOURCE_KIND_V1",
    "SOURCE_KIND_INDEPENDENT_V1",
    "SelfOwnedCgMetaSourceError",
    "materialize_self_owned_cg_meta_source_v1",
    "promote_self_owned_cg_meta_source_v1",
    "materialize_self_owned_cg_meta_batch_v1",
    "promote_self_owned_cg_meta_batch_v1",
]
