"""Seal a stratified, cross-snapshot visible-state behavior meta source.

This lane addresses a weakness of the first cross-snapshot generator: the
source identities were distinct, but the train/dev/final split was implicit
and could be dominated by one behavior family.  v2 keeps the exact, audited
transform recipes while requiring an explicit, family-balanced split and a
unique source commit for every emitted policy.

The output is research-only.  It never mutates the repository opponent pool,
and it grants no training, promotion, long-run, or submission authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1

from .behavior_factorial_meta_v1 import (
    COMFEY_FACTORIAL_VARIANTS_V1,
    _replace_alakazam_factorial_behavior,
    _replace_comfey_factorial_behavior,
)
from .behavior_family_meta_v1 import (
    ALAKAZAM_BEHAVIOR_VARIANTS_V1,
    COMFEY_BEHAVIOR_VARIANTS_V1,
    FESTIVAL_BEHAVIOR_VARIANTS_V1,
    METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1,
    PSYCHIC_BEHAVIOR_VARIANTS_V1,
    _replace_alakazam_behavior,
    _replace_comfey_behavior,
    _replace_festival_behavior,
    _replace_metal_runtime_safe_behavior,
    _replace_psychic_behavior,
)
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


STRATIFIED_BEHAVIOR_META_SCHEMA_V2 = "meta-specialist-cg-stratified-behavior-meta-v2"
STRATIFIED_BEHAVIOR_SOURCE_V2 = "internal_agents_stratified_behavior_derived_v2"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
SUPPORTED_SPLITS_V2 = ("META_TRAIN", "META_DEV", "META_FINAL")
SUPPORTED_FAMILIES_V2 = ("alakazam", "comfey", "festival", "metal", "psychic")
_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ROOT = Path(__file__).resolve().parents[3]


class StratifiedBehaviorMetaError(DerivedInternalMetaError):
    """Raised when the stratified source contract cannot be sealed."""


def _normalize_entries(entries: Sequence[Mapping[str, object]]) -> tuple[dict[str, str], ...]:
    """Validate the declarative source list before reading any source files."""

    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise StratifiedBehaviorMetaError("entries must be a sequence")
    if len(entries) < 8:
        raise StratifiedBehaviorMetaError("at least eight entries are required")

    normalized: list[dict[str, str]] = []
    seen_roots: set[str] = set()
    seen_labels: set[str] = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise StratifiedBehaviorMetaError(f"entry {index} must be a mapping")
        values: dict[str, str] = {}
        for key in ("base_root", "family", "variant", "label", "split"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise StratifiedBehaviorMetaError(f"entry {index} requires non-empty {key}")
            values[key] = value.strip()
        values["family"] = values["family"].lower()
        values["split"] = values["split"].upper()
        if values["family"] not in SUPPORTED_FAMILIES_V2:
            raise StratifiedBehaviorMetaError(f"unknown family: {values['family']}")
        if values["split"] not in SUPPORTED_SPLITS_V2:
            raise StratifiedBehaviorMetaError(f"unknown split: {values['split']}")
        if not _LABEL_RE.fullmatch(values["label"]):
            raise StratifiedBehaviorMetaError(f"invalid label: {values['label']}")
        if values["base_root"] in seen_roots:
            raise StratifiedBehaviorMetaError(f"base_root is duplicated: {values['base_root']}")
        if values["label"] in seen_labels:
            raise StratifiedBehaviorMetaError(f"label is duplicated: {values['label']}")
        seen_roots.add(values["base_root"])
        seen_labels.add(values["label"])
        normalized.append(values)

    for split in SUPPORTED_SPLITS_V2:
        members = [item for item in normalized if item["split"] == split]
        if len(members) < 2 or len({item["family"] for item in members}) < 2:
            raise StratifiedBehaviorMetaError(
                f"split family coverage is insufficient for {split}; "
                "each split needs at least two entries from two families"
            )
    return tuple(normalized)


def _transform_entry(source: bytes, *, family: str, variant: str) -> tuple[bytes, str]:
    """Dispatch only to exact transforms already covered by source-family lanes."""

    family_name = str(family).lower()
    variant_name = str(variant)
    if family_name == "alakazam":
        if variant_name in {
            "ABRA_POFFIN",
            "ABRA_FEZANDIPITI",
            "DUNSPARCE_POFFIN",
            "DUNSPARCE_FEZANDIPITI",
        }:
            return _replace_alakazam_factorial_behavior(source, variant_name)
        return _replace_alakazam_behavior(source, variant_name)
    if family_name == "comfey":
        if variant_name in COMFEY_FACTORIAL_VARIANTS_V1:
            return _replace_comfey_factorial_behavior(source, variant_name)
        return _replace_comfey_behavior(source, variant_name)
    if family_name == "festival":
        return _replace_festival_behavior(source, variant_name)
    if family_name == "metal":
        # v2 deliberately exposes only the runtime-safe metal recipes.  The
        # unbounded search variants are a known timeout hard-negative.
        return _replace_metal_runtime_safe_behavior(source, variant_name)
    if family_name == "psychic":
        return _replace_psychic_behavior(source, variant_name)
    raise StratifiedBehaviorMetaError(f"unknown family: {family}")


def validate_stratified_lineage(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Validate split balance and source identity independence.

    ``rows`` is the prepared, source-resolved lineage, not the user spec.  A
    source commit may occur only once across the complete pool so that a split
    cannot contain two variants of the same historical snapshot.
    """

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) < 8:
        raise StratifiedBehaviorMetaError("stratified lineage requires at least eight rows")
    required = ("base_candidate_id", "source_commit", "source_family", "split")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise StratifiedBehaviorMetaError(f"lineage row {index} must be a mapping")
        for key in required:
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                raise StratifiedBehaviorMetaError(f"lineage row {index} requires {key}")
        if str(row["split"]) not in SUPPORTED_SPLITS_V2:
            raise StratifiedBehaviorMetaError(f"unknown split in lineage: {row['split']}")

    base_ids = [str(row["base_candidate_id"]) for row in rows]
    commits = [str(row["source_commit"]) for row in rows]
    policy_hashes = [str(row["policy_sha256"]) for row in rows if row.get("policy_sha256")]
    if len(base_ids) != len(set(base_ids)):
        raise StratifiedBehaviorMetaError("base candidate ids are duplicated")
    if len(commits) != len(set(commits)):
        raise StratifiedBehaviorMetaError("source commit is duplicated across stratified pool")
    if len(policy_hashes) != len(set(policy_hashes)):
        raise StratifiedBehaviorMetaError("policy SHA is duplicated across stratified pool")

    split_counts: dict[str, int] = {}
    split_family_counts: dict[str, dict[str, int]] = {}
    for split in SUPPORTED_SPLITS_V2:
        members = [row for row in rows if str(row["split"]) == split]
        families: dict[str, int] = {}
        for row in members:
            family = str(row["source_family"])
            families[family] = families.get(family, 0) + 1
        if len(members) < 2 or len(families) < 2:
            raise StratifiedBehaviorMetaError(
                f"split family coverage is insufficient for {split}; "
                "each split needs at least two entries from two families"
            )
        split_counts[split] = len(members)
        split_family_counts[split] = dict(sorted(families.items()))
    return {
        "row_count": len(rows),
        "distinct_base_candidates": len(set(base_ids)),
        "distinct_source_commits": len(set(commits)),
        "distinct_policy_hashes": len(set(policy_hashes)),
        "split_counts": split_counts,
        "split_family_counts": split_family_counts,
    }


def _resolve_root(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else _ROOT / path).resolve()


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _source_note(*, target: Path, base, policy_sha: str, recipe: str, family: str, label: str, split: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Stratified behavior meta source (research-only)\n\n"
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
            f"- split: `{split}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_stratified_behavior_meta_v2(
    *,
    entries: Sequence[Mapping[str, object]],
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a fresh, split-balanced pool and its hash-bound cg split."""

    normalized = _normalize_entries(entries)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite stratified behavior root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise StratifiedBehaviorMetaError("source_epoch and seed_namespace must be non-empty")

    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise StratifiedBehaviorMetaError("P1 package must contain main.py and deck.csv")
    existing_hashes: set[str] = set()
    if current_pool_manifest is not None:
        existing_hashes = _existing_policy_hashes(Path(current_pool_manifest).resolve())
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for entry in normalized:
        base_root = _resolve_root(entry["base_root"])
        base = _read_base_source(base_root)
        source_bytes = (base_root / "main.py").read_bytes()
        deck_bytes = (base_root / "deck.csv").read_bytes()
        findings, imports, _environment_keys = _static_findings(source_bytes.decode("utf-8"))
        if findings:
            raise StratifiedBehaviorMetaError(
                f"base policy is not statically safe: {base.candidate_id}: {findings}"
            )
        policy_bytes, recipe = _transform_entry(
            source_bytes,
            family=entry["family"],
            variant=entry["variant"],
        )
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise StratifiedBehaviorMetaError(
                f"stratified behavior policy identity is already used: {entry['label']}"
            )
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise StratifiedBehaviorMetaError(
                f"stratified behavior policy identity appears in artifacts: {entry['label']}"
            )
        transformed_findings, transformed_imports, environment_keys = _static_findings(
            policy_bytes.decode("utf-8")
        )
        if transformed_findings:
            raise StratifiedBehaviorMetaError(
                f"derived behavior policy is not statically safe: {entry['label']}: {transformed_findings}"
            )
        candidate_id = (
            f"derived_{base.candidate_id}_stratified_{entry['label']}_{policy_sha[:12]}"
        )
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

    lineage = [
        {
            "base_candidate_id": str(item["base"].candidate_id),
            "source_commit": str(item["base"].source_commit),
            "source_family": str(item["entry"]["family"]),
            "split": str(item["entry"]["split"]),
            "policy_sha256": str(item["policy_sha"]),
        }
        for item in prepared
    ]
    lineage_report = validate_stratified_lineage(lineage)

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
            split=str(entry["split"]),
        )
        rows.append(
            {
                "id": candidate_id,
                "policy_hash": str(item["policy_sha"]),
                "source_policy_sha256": base.source_policy_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source": STRATIFIED_BEHAVIOR_SOURCE_V2,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "localization_patch": base.localization_patch,
                "smoke_ok": True,
                "derived": True,
                "source_family": str(entry["family"]),
                "source_label": str(entry["label"]),
                "split": str(entry["split"]),
                "observation_boundary": "visible_state_only",
                "derivation_recipe": str(item["recipe"]),
                "asset_preflight": "STATIC_AND_EXACT_60",
            }
        )
        evidence.append(
            {
                "candidate_id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "derived": True,
                "source": STRATIFIED_BEHAVIOR_SOURCE_V2,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": str(entry["family"]),
                "source_label": str(entry["label"]),
                "split": str(entry["split"]),
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

    reference_ids = [str(row["id"]) for row in rows]
    seed_plan_sha = _sha256_bytes(
        _canonical_json(
            {
                "source_epoch": source_epoch,
                "seed_namespace": seed_namespace,
                "entries": [
                    {
                        "id": str(item["candidate_id"]),
                        "family": str(item["entry"]["family"]),
                        "variant": str(item["entry"]["variant"]),
                        "split": str(item["entry"]["split"]),
                        "source_commit": str(item["base"].source_commit),
                    }
                    for item in prepared
                ],
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
                "source_family": item["source_family"],
                "split": item["split"],
            }
        )
    fresh_payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"stratified-behavior-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "explicit split-balanced visible-state transform over distinct sealed source commits; current pool and configured artifact identity scan",
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

    split_report = build_historical_meta_split_v1(
        pool_root=output,
        fresh_meta_path=fresh_path,
        p1_package=p1,
        train_ids=[str(item["candidate_id"]) for item in prepared if item["entry"]["split"] == "META_TRAIN"],
        dev_ids=[str(item["candidate_id"]) for item in prepared if item["entry"]["split"] == "META_DEV"],
        final_ids=[str(item["candidate_id"]) for item in prepared if item["entry"]["split"] == "META_FINAL"],
    )
    report = {
        "schema_version": STRATIFIED_BEHAVIOR_META_SCHEMA_V2,
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
        "lineage": lineage_report,
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
    "STRATIFIED_BEHAVIOR_META_SCHEMA_V2",
    "STRATIFIED_BEHAVIOR_SOURCE_V2",
    "SUPPORTED_SPLITS_V2",
    "SUPPORTED_FAMILIES_V2",
    "StratifiedBehaviorMetaError",
    "_normalize_entries",
    "_transform_entry",
    "validate_stratified_lineage",
    "seal_stratified_behavior_meta_v2",
]
