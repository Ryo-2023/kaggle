"""Seal a cross-snapshot, visible-state behavior meta source family.

This lane intentionally composes *different sealed base snapshots*: each
entry names one base snapshot and exactly one already-audited behavior
transform.  It is a source-generation experiment, not a promotion or
submission path.  The resulting pool remains ``local_eval_only`` and the
emitted split keeps META_DEV/META_FINAL out of the search runner until their
normal gates are reached.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1

from .behavior_family_meta_v1 import _replace_alakazam_behavior, _replace_comfey_behavior
from .behavior_factorial_meta_v1 import _replace_comfey_factorial_behavior
from .derived_internal_meta_v1 import (
    DerivedInternalMetaError,
    _artifact_hits,
    _canonical_json,
    _existing_policy_hashes,
    _read_base_source,
    _sha256_bytes,
    _sha256_file,
    _static_findings,
    _write_json_new,
    _write_new,
)


CROSS_SNAPSHOT_BEHAVIOR_META_SCHEMA_V1 = "meta-specialist-cg-cross-snapshot-behavior-meta-v1"
CROSS_SNAPSHOT_BEHAVIOR_SOURCE_V1 = "internal_agents_cross_snapshot_behavior_derived"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
SUPPORTED_FAMILIES_V1 = ("alakazam", "comfey")
_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ROOT = Path(__file__).resolve().parents[3]


class CrossSnapshotBehaviorMetaError(DerivedInternalMetaError):
    """Raised when cross-snapshot intake cannot be sealed fail-closed."""


def _normalize_entries(entries: Sequence[Mapping[str, object]]) -> tuple[dict[str, str], ...]:
    """Validate and normalize the small declarative cross-snapshot spec."""

    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise CrossSnapshotBehaviorMetaError("entries must be a sequence")
    if len(entries) < 4:
        raise CrossSnapshotBehaviorMetaError("at least four entries are required")
    normalized: list[dict[str, str]] = []
    seen_roots: set[str] = set()
    seen_labels: set[str] = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise CrossSnapshotBehaviorMetaError(f"entry {index} must be a mapping")
        values: dict[str, str] = {}
        for key in ("base_root", "family", "variant", "label"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise CrossSnapshotBehaviorMetaError(f"entry {index} requires non-empty {key}")
            values[key] = value.strip()
        values["family"] = values["family"].lower()
        if values["family"] not in SUPPORTED_FAMILIES_V1:
            raise CrossSnapshotBehaviorMetaError(f"unknown family: {values['family']}")
        if not _LABEL_RE.fullmatch(values["label"]):
            raise CrossSnapshotBehaviorMetaError(f"invalid label: {values['label']}")
        if values["base_root"] in seen_roots:
            raise CrossSnapshotBehaviorMetaError(f"base_root is duplicated: {values['base_root']}")
        if values["label"] in seen_labels:
            raise CrossSnapshotBehaviorMetaError(f"label is duplicated: {values['label']}")
        seen_roots.add(values["base_root"])
        seen_labels.add(values["label"])
        normalized.append(values)
    return tuple(normalized)


def validate_cross_snapshot_lineage(entries: Sequence[Mapping[str, object]]) -> None:
    """Require distinct base identities and at least three source commits."""

    candidate_ids: list[str] = []
    source_commits: list[str] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise CrossSnapshotBehaviorMetaError(f"lineage entry {index} must be a mapping")
        candidate_id = raw.get("base_candidate_id")
        source_commit = raw.get("source_commit")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise CrossSnapshotBehaviorMetaError(f"lineage entry {index} requires base_candidate_id")
        if not isinstance(source_commit, str) or not source_commit.strip():
            raise CrossSnapshotBehaviorMetaError(f"lineage entry {index} requires source_commit")
        candidate_ids.append(candidate_id.strip())
        source_commits.append(source_commit.strip())
    if len(candidate_ids) < 4:
        raise CrossSnapshotBehaviorMetaError("cross-snapshot lineage requires four base candidates")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CrossSnapshotBehaviorMetaError("base candidate ids are duplicated")
    if len(set(source_commits)) < 3:
        raise CrossSnapshotBehaviorMetaError("cross-snapshot lineage requires at least three distinct source commits")


def _transform_entry(source: bytes, *, family: str, variant: str) -> tuple[bytes, str]:
    """Dispatch only to transforms already covered by their source-family lane."""

    family_name = str(family).lower()
    if family_name == "alakazam":
        return _replace_alakazam_behavior(source, str(variant))
    if family_name == "comfey":
        return _replace_comfey_factorial_behavior(source, str(variant))
    raise CrossSnapshotBehaviorMetaError(f"unknown family: {family}")


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _resolve_root(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else _ROOT / path).resolve()


def _source_note(*, target: Path, base, policy_sha: str, recipe: str, family: str, label: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Cross-snapshot behavior meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (preserved)\n"
            f"- source family: `{family}`\n"
            f"- source label: `{label}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_cross_snapshot_behavior_meta_v1(
    *,
    entries: Sequence[Mapping[str, object]],
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a fresh cross-snapshot pool and a historical META split."""

    normalized = _normalize_entries(entries)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite cross-snapshot root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise CrossSnapshotBehaviorMetaError("source_epoch and seed_namespace must be non-empty")

    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise CrossSnapshotBehaviorMetaError("P1 package must contain main.py and deck.csv")
    existing_hashes: set[str] = set()
    if current_pool_manifest is not None:
        existing_hashes = _existing_policy_hashes(Path(current_pool_manifest).resolve())
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    lineage: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    # Read and transform every source before creating output.  A bad source or
    # unsupported recipe therefore cannot leave a claimed sealed root behind.
    for entry in normalized:
        base_root = _resolve_root(entry["base_root"])
        base = _read_base_source(base_root)
        source_bytes = (base_root / "main.py").read_bytes()
        deck_bytes = (base_root / "deck.csv").read_bytes()
        findings, imports, _environment_keys = _static_findings(source_bytes.decode("utf-8"))
        if findings:
            raise CrossSnapshotBehaviorMetaError(
                f"base policy is not statically safe: {base.candidate_id}: {findings}"
            )
        policy_bytes, recipe = _transform_entry(
            source_bytes, family=entry["family"], variant=entry["variant"]
        )
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise CrossSnapshotBehaviorMetaError(
                f"cross-snapshot behavior policy identity is already used: {entry['label']}"
            )
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise CrossSnapshotBehaviorMetaError(
                f"cross-snapshot behavior policy identity appears in artifacts: {entry['label']}"
            )
        transformed_findings, transformed_imports, environment_keys = _static_findings(
            policy_bytes.decode("utf-8")
        )
        if transformed_findings:
            raise CrossSnapshotBehaviorMetaError(
                f"derived policy is not statically safe: {entry['label']}: {transformed_findings}"
            )
        candidate_id = f"derived_{base.candidate_id}_cross_{entry['label']}_{policy_sha[:12]}"
        prepared.append(
            {
                "entry": entry,
                "base": base,
                "source_bytes": source_bytes,
                "deck_bytes": deck_bytes,
                "policy_bytes": policy_bytes,
                "policy_sha": policy_sha,
                "recipe": recipe,
                "candidate_id": candidate_id,
                "imports": tuple(transformed_imports or imports),
                "environment_keys": tuple(environment_keys),
            }
        )
        prepared_hashes.add(policy_sha)
        lineage.append({"base_candidate_id": base.candidate_id, "source_commit": base.source_commit})

    validate_cross_snapshot_lineage(lineage)
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    for item in prepared:
        entry = item["entry"]
        base = item["base"]
        candidate_id = str(item["candidate_id"])
        target = output / candidate_id
        target.mkdir(parents=True, exist_ok=False)
        _write_new(target / "main.py", bytes(item["policy_bytes"]))
        _write_new(target / "deck.csv", bytes(item["deck_bytes"]))
        _source_note(
            target=target,
            base=base,
            policy_sha=str(item["policy_sha"]),
            recipe=str(item["recipe"]),
            family=str(entry["family"]),
            label=str(entry["label"]),
        )
        row = {
            "id": candidate_id,
            "policy_hash": str(item["policy_sha"]),
            "source_policy_sha256": base.source_policy_sha256,
            "canonical_deck_hash": base.canonical_deck_hash,
            "source": CROSS_SNAPSHOT_BEHAVIOR_SOURCE_V1,
            "source_branch": base.source_branch,
            "source_commit": base.source_commit,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "localization_patch": base.localization_patch,
            "smoke_ok": True,
            "derived": True,
            "base_candidate_id": base.candidate_id,
            "source_family": str(entry["family"]),
            "source_label": str(entry["label"]),
            "observation_boundary": "visible_state_only",
            "derivation_recipe": str(item["recipe"]),
            "asset_preflight": "STATIC_AND_EXACT_60",
        }
        rows.append(row)
        evidence.append(
            {
                "candidate_id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "derived": True,
                "source": CROSS_SNAPSHOT_BEHAVIOR_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "base_candidate_id": base.candidate_id,
                "source_family": str(entry["family"]),
                "source_label": str(entry["label"]),
                "derivation_recipe": str(item["recipe"]),
                "observation_boundary": "visible_state_only",
                "imports": list(item["imports"]),
                "environment_keys": list(item["environment_keys"]),
                "static_findings": [],
            }
        )

    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    evidence_dir = output / "evidence"
    for item in evidence:
        _write_json_new(evidence_dir / f"{item['candidate_id']}.json", item)

    reference_ids = sorted(str(row["id"]) for row in rows)
    seed_plan_sha = _sha256_bytes(
        _canonical_json(
            {
                "source_epoch": source_epoch,
                "seed_namespace": seed_namespace,
                "reference_ids": reference_ids,
            }
        )
    )
    references = []
    for item in evidence:
        evidence_path = evidence_dir / f"{item['candidate_id']}.json"
        references.append(
            {
                "id": item["candidate_id"],
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": item["policy_sha256"],
                "canonical_deck_hash": item["canonical_deck_hash"],
                "source": item["source"],
                "derived": True,
                "derivation_recipe": item["derivation_recipe"],
            }
        )
    fresh_payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"cross-snapshot-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "one fixed visible-state transform per distinct sealed base snapshot; current pool and configured artifact identity scan",
        "references": references,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh_payload)

    ordered_ids = [str(row["id"]) for row in rows]
    split_report = build_historical_meta_split_v1(
        pool_root=output,
        fresh_meta_path=fresh_path,
        p1_package=p1,
        train_ids=ordered_ids[:2],
        dev_ids=[ordered_ids[2]],
        final_ids=ordered_ids[3:],
    )
    report = {
        "schema_version": CROSS_SNAPSHOT_BEHAVIOR_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_ids": [str(item["base"].candidate_id) for item in prepared],
        "source_commits": sorted({str(item["base"].source_commit) for item in prepared}),
        "source_families": sorted({str(item["entry"]["family"]) for item in prepared}),
        "accepted_count": len(rows),
        "accepted_ids": reference_ids,
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "split_path": split_report["split_path"],
        "split_sha256": split_report["split_sha256"],
        "meta_manifest_path": split_report["meta_manifest_path"],
        "meta_manifest_sha256": split_report["meta_manifest_sha256"],
        "lineage_distinct_source_commits": len({str(item["base"].source_commit) for item in prepared}),
        "imports_executed": False,
        "network_access": False,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
    }
    _write_json_new(output / "intake_report.json", report)
    load_opponent_pool_v1(output)
    return report


__all__ = [
    "CROSS_SNAPSHOT_BEHAVIOR_META_SCHEMA_V1",
    "CROSS_SNAPSHOT_BEHAVIOR_SOURCE_V1",
    "CrossSnapshotBehaviorMetaError",
    "SUPPORTED_FAMILIES_V1",
    "_normalize_entries",
    "_transform_entry",
    "validate_cross_snapshot_lineage",
    "seal_cross_snapshot_behavior_meta_v1",
]
