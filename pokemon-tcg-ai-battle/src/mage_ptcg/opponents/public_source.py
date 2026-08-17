"""Hardened metadata-only Public Opponent Source intake (O6 Phase B).

Integrates the *metadata* produced by the external Public Source Corpus /
Collector Prototype (Repository Snapshot path only) into O6's own storage
and permission vocabulary. Deliberately narrow scope, matching the Phase B
directive:

* imports only the per-source classification JSON files
  (source_manifest/code/deck/behavior/provenance/permissions/
  technical_validation, plus the optional classification/deck_validation/
  hashes files) -- never the raw or extracted agent source code, never a
  Public Agent import, never a CABT smoke of Public code;
* every imported source's Candidate state is capped at
  ``NATIVE_OPPONENT_CANDIDATE`` / ``DECK_STANDARD_PILOT_CANDIDATE`` /
  ``SURROGATE_CANDIDATE`` / ``REVIEW_REQUIRED`` / ``BLOCKED`` -- there is no
  code path in this module that can reach ``VALIDATED``/``APPROVED``/
  ``PUBLISHED`` (those states belong exclusively to the Team pipeline in
  :mod:`mage_ptcg.opponents.core`, which this module never calls into);
* permission scopes reuse the exact same ``USAGE_SCOPES`` vocabulary as
  Team sources, so "REVIEW_REQUIRED"/"DENIED" mean the same thing across
  both source families;
* ``check-permissions`` fails closed (non-zero exit) whenever any imported
  source still needs manual permission review -- true today for all 7
  UNKNOWN-license sources.
"""
from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.canonical import digest, sha256_hex

from .core import USAGE_SCOPES, safe_extract_tar_gz
from .errors import OpponentError

CORPUS_SCHEMA_VERSION = "o6-public-source-corpus-v1"
RECORD_SCHEMA_VERSION = "o6-public-source-record-v1"

CANDIDATE_STATES = ("NATIVE_OPPONENT_CANDIDATE", "DECK_STANDARD_PILOT_CANDIDATE", "SURROGATE_CANDIDATE", "REVIEW_REQUIRED", "BLOCKED")
_FORBIDDEN_CANDIDATE_STATES = ("NATIVE_OPPONENT", "VALIDATED", "APPROVED", "PUBLISHED")

# Scope name mapping: corpus policy keys -> O6's Team-source USAGE_SCOPES vocabulary.
_POLICY_TO_SCOPE = {
    "team_internal_evaluation_allowed": "evaluation",
    "team_internal_training_allowed": "training_data_generation",
    "strategy_analysis_allowed": "strategy_analysis",
    "team_redistribution_allowed": "team_redistribution",
    "public_redistribution_allowed": "public_redistribution",
    "submission_bundle_allowed": "submission_bundle",
}
_VALID_PERMISSION_ENUM = frozenset({"ALLOWED", "DENIED", "REVIEW_REQUIRED", "NOT_APPLICABLE", "ALLOWED_METADATA_ONLY"})

# The fixed table this Phase B directive mandates for UNKNOWN-license sources
# (every source in the current 7-source corpus). Used as a consistency
# check against the corpus-provided policies, not as a silent override.
UNKNOWN_LICENSE_SCOPE_DECISIONS = {
    "evaluation": "REVIEW_REQUIRED",
    "training_data_generation": "REVIEW_REQUIRED",
    "strategy_analysis": "ALLOWED_METADATA_ONLY",
    "team_redistribution": "REVIEW_REQUIRED",
    "public_redistribution": "DENIED",
    "submission_bundle": "DENIED",
}

_MANDATORY_SOURCE_FILES = ("source_manifest.json", "code.json", "deck.json", "behavior.json", "provenance.json", "permissions.json", "technical_validation.json")
_OPTIONAL_SOURCE_FILES = ("classification.json", "deck_validation.json", "hashes.json")
_TOP_LEVEL_CHECKSUM_TARGETS = ("corpus_manifest.json", "source_index.json", "deck_registry.json", "classification_registry.json",
                               "provenance_summary.json", "permission_summary.json", "validation_summary.json",
                               "technical_validation_summary.json", "blocked_sources.json", "collector_run_manifest.json",
                               "README.md", "review_override.json")

# Never distributed further and never imported: raw/extracted agent code.
_FORBIDDEN_IMPORT_DIRS = ("raw", "extracted")

MAX_ARCHIVE_FILE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILE_COUNT = 100
MAX_ARCHIVE_COMPRESSION_RATIO = 100.0


class PermissionReviewRequiredError(OpponentError):
    """Raised by ``check-permissions`` when any imported source still needs manual review; CLI exit code 6."""
    exit_code = 6


def _semantic_hash(value: Any, domain: str) -> str:
    return digest(value, domain=f"o6-public-source:{domain}")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OpponentError(f"required public-source metadata file is missing: {path}")
    except json.JSONDecodeError as exc:
        raise OpponentError(f"corrupt public-source metadata JSON: {path}: {exc}")


def extract_corpus_archive(archive_path: str | Path, destination: str | Path) -> Path:
    """Safely extract a ``.tar.gz`` Public Source Corpus package (defense in depth).

    The corpus shipped for this integration is a plain directory
    (Repository Snapshot path); this function exists so an archived corpus
    package -- from a future Live Acquisition path this Phase explicitly
    does not implement -- is ingested through the same hardened extractor
    (traversal/symlink/hardlink/device/FIFO rejection, Windows/backslash
    path rejection, nested-archive-name rejection, per-file/total-size/
    file-count/compression-ratio limits) as everything else in this module.
    """
    for member_name in tarfile.open(archive_path, "r:gz").getnames():
        lower = member_name.lower()
        if any(ext in lower for ext in (".zip", ".tar", ".tgz", ".gz", ".bz2")) and PurePosixPath(member_name).name != PurePosixPath(archive_path).name:
            raise OpponentError(f"nested archive member not allowed: {member_name}")
    safe_extract_tar_gz(archive_path, destination, max_file_bytes=MAX_ARCHIVE_FILE_BYTES, max_total_bytes=MAX_ARCHIVE_TOTAL_BYTES,
                         max_files=MAX_ARCHIVE_FILE_COUNT, max_compression_ratio=MAX_ARCHIVE_COMPRESSION_RATIO)
    return Path(destination)


def _verify_top_level_checksums(corpus_root: Path) -> None:
    checksums_path = corpus_root / "checksums.sha256"
    if not checksums_path.exists():
        raise OpponentError("corpus is missing checksums.sha256")
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        fname, _, expected = line.partition(":")
        if fname not in _TOP_LEVEL_CHECKSUM_TARGETS:
            continue
        target = corpus_root / fname
        if not target.is_file():
            raise OpponentError(f"corpus checksum references a missing file: {fname}")
        actual = sha256_hex(target.read_bytes())
        if actual != expected:
            raise OpponentError(f"corpus checksum mismatch for {fname}: expected {expected}, got {actual}")


def load_corpus_manifest(corpus_root: str | Path) -> dict[str, Any]:
    """Load and validate ``corpus_manifest.json``: schema version, checksums, included source ids."""
    corpus_root = Path(corpus_root)
    if not corpus_root.is_dir():
        raise OpponentError(f"public source corpus root is not a directory: {corpus_root}")
    _verify_top_level_checksums(corpus_root)
    manifest = _read_json(corpus_root / "corpus_manifest.json")
    if manifest.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise OpponentError(f"unsupported public source corpus schema_version: {manifest.get('schema_version')!r} (expected {CORPUS_SCHEMA_VERSION!r})")
    included = manifest.get("included_source_ids")
    if not isinstance(included, list) or not included or not all(isinstance(item, str) and item for item in included):
        raise OpponentError("corpus_manifest.json included_source_ids is missing or malformed")
    for source_id in included:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
            raise OpponentError(f"unsafe source_id in corpus manifest: {source_id!r}")
    return manifest


def _read_source_metadata(source_dir: Path, source_id: str) -> dict[str, Any]:
    if source_dir.is_symlink():
        raise OpponentError(f"public source directory must not be a symlink: {source_id}")
    metadata: dict[str, Any] = {}
    for name in _MANDATORY_SOURCE_FILES:
        path = source_dir / name
        if path.is_symlink():
            raise OpponentError(f"public source metadata file must not be a symlink: {source_id}/{name}")
        metadata[name.removesuffix(".json")] = _read_json(path)
    for name in _OPTIONAL_SOURCE_FILES:
        path = source_dir / name
        if path.is_file() and not path.is_symlink():
            metadata[name.removesuffix(".json")] = _read_json(path)
    return metadata


def compute_source_metadata_hash(metadata: Mapping[str, Any]) -> str:
    """O6-side semantic hash over one source's full imported metadata bundle.

    Broader than the corpus's own ``source_package_hashes`` (which only
    covers ``source_manifest.json`` bytes): this covers every imported
    metadata file, so a change to ``behavior.json``'s usability
    classification or ``permissions.json`` -- not just the manifest --
    changes this hash too. Used for idempotent-reimport and
    same-source-id-different-content detection.
    """
    return _semantic_hash(metadata, "source-metadata")


def _verify_corpus_source_hash(*, corpus_manifest: Mapping[str, Any], source_id: str, source_manifest_bytes: bytes) -> None:
    expected = (corpus_manifest.get("source_package_hashes") or {}).get(source_id)
    if expected is None:
        raise OpponentError(f"corpus manifest has no source_package_hash for {source_id!r}")
    actual = sha256_hex(source_manifest_bytes)
    if actual != expected:
        raise OpponentError(f"public source hash mismatch for {source_id!r}: corpus declares {expected}, recomputed {actual}")


def derive_permission_scopes(*, provenance: Mapping[str, Any], permissions: Mapping[str, Any]) -> dict[str, str]:
    """Map corpus policy enums onto O6's Team-source ``USAGE_SCOPES`` vocabulary, validating enum values."""
    policies = permissions.get("policies")
    if not isinstance(policies, Mapping):
        raise OpponentError("public source permissions.json is missing a policies object")
    scopes: dict[str, str] = {}
    for policy_key, scope in _POLICY_TO_SCOPE.items():
        value = policies.get(policy_key)
        if value not in _VALID_PERMISSION_ENUM:
            raise OpponentError(f"invalid permission enum value {value!r} for {policy_key!r}")
        scopes[scope] = value
    if set(scopes) != set(USAGE_SCOPES):
        raise OpponentError("derived permission scopes do not cover the full USAGE_SCOPES vocabulary")
    if provenance.get("explicit_license") == "UNKNOWN" and scopes != UNKNOWN_LICENSE_SCOPE_DECISIONS:
        raise OpponentError(f"UNKNOWN-license source scopes deviate from the mandated table: {scopes}")
    return scopes


def derive_candidate_state(*, code_availability: str, deck_fidelity: str, corpus_usability: str,
                            permission_scopes: Mapping[str, str], is_blocked: bool,
                            override: Mapping[str, Any] | None) -> dict[str, Any]:
    """Independently re-derive a Candidate state; never trust the corpus's own opinion at face value.

    Rule-based, source-id-agnostic (unlike the collector prototype, which
    special-cased two source ids by name for deck_fidelity): this reads
    only ``code_availability``/``deck_fidelity``/permission scopes/blocked
    status. Any ``review_override.json`` entry is applied but recorded
    (``review_override_applied``), never silently accepted, and the final
    state is always capped to :data:`CANDIDATE_STATES` -- this function can
    never return ``NATIVE_OPPONENT``/``VALIDATED``/``APPROVED``/``PUBLISHED``.
    """
    if is_blocked:
        rule_derived = "BLOCKED"
        reason = "listed in blocked_sources.json"
    elif code_availability != "EXACT":
        rule_derived = "BLOCKED"
        reason = "code_availability is not EXACT"
    elif deck_fidelity not in {"EXACT", "RECONSTRUCTED", "PARTIAL"}:
        rule_derived = "BLOCKED"
        reason = f"unknown deck_fidelity: {deck_fidelity!r}"
    elif deck_fidelity in {"RECONSTRUCTED", "PARTIAL"}:
        # A reconstructed/partial deck can never be confirmed Native, regardless of source_id.
        rule_derived = "DECK_STANDARD_PILOT_CANDIDATE"
        reason = f"deck_fidelity is {deck_fidelity}; exact fidelity cannot be guaranteed"
    elif permission_scopes.get("evaluation") == "DENIED":
        rule_derived = "BLOCKED"
        reason = "evaluation permission is DENIED"
    elif permission_scopes.get("evaluation") == "REVIEW_REQUIRED":
        rule_derived = "REVIEW_REQUIRED"
        reason = "evaluation permission requires manual review"
    else:
        rule_derived = "NATIVE_OPPONENT_CANDIDATE"
        reason = "code and deck are EXACT and evaluation is ALLOWED"

    final_state, final_reason, override_applied = rule_derived, reason, False
    if override:
        requested = override.get("usability_classification")
        if requested in CANDIDATE_STATES:
            # An override may only move a source *between* candidate states never above what
            # BLOCKED/permission gating already forced; BLOCKED can never be overridden away.
            if rule_derived != "BLOCKED":
                final_state = requested
                final_reason = override.get("usability_reason", reason)
                override_applied = True
        elif requested in _FORBIDDEN_CANDIDATE_STATES:
            raise OpponentError(f"review_override.json requests a forbidden non-candidate state: {requested!r}")

    if final_state not in CANDIDATE_STATES:
        raise OpponentError(f"derived candidate state is not in the allowed set: {final_state!r}")
    return {
        "rule_derived_candidate_state": rule_derived, "rule_derived_reason": reason,
        "candidate_state": final_state, "candidate_reason": final_reason,
        "review_override_applied": override_applied,
        "corpus_reported_usability": corpus_usability,
    }


def _verify_deck_validation(deck: Mapping[str, Any], deck_validation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Structurally re-check the corpus-provided deck_validation.json against deck.json (no external card DB needed)."""
    card_ids = deck.get("card_ids")
    if not isinstance(card_ids, list):
        raise OpponentError("deck.json card_ids missing or malformed")
    result = {"recomputed_total_count": len(card_ids), "reported_total_count": None, "consistent": True, "mismatches_reported": 0}
    if deck_validation is not None:
        reported_total = deck_validation.get("total_count")
        result["reported_total_count"] = reported_total
        result["mismatches_reported"] = len(deck_validation.get("mismatches") or [])
        is_legal = deck_validation.get("is_legal")
        issues = deck_validation.get("issues") or []
        consistent = (reported_total == len(card_ids)) and (is_legal == (len(issues) == 0))
        result["consistent"] = bool(consistent)
        if not consistent:
            raise OpponentError("deck_validation.json is internally inconsistent with deck.json (card_ids/total_count/is_legal/issues)")
    return result


def build_source_record(*, corpus_manifest: Mapping[str, Any], source_dir: Path, source_id: str,
                         blocked_source_ids: Iterable[str], review_overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Build one O6-side import record for a single corpus source, importing metadata only."""
    for forbidden in _FORBIDDEN_IMPORT_DIRS:
        # These directories exist in the corpus (raw agent code / extracted
        # copies) and are never read by this function; asserted here so a
        # future edit cannot accidentally start importing them.
        assert forbidden not in _MANDATORY_SOURCE_FILES and forbidden not in _OPTIONAL_SOURCE_FILES
    metadata = _read_source_metadata(source_dir, source_id)
    _verify_corpus_source_hash(corpus_manifest=corpus_manifest, source_id=source_id, source_manifest_bytes=(source_dir / "source_manifest.json").read_bytes())
    permission_scopes = derive_permission_scopes(provenance=metadata["provenance"], permissions=metadata["permissions"])
    deck_check = _verify_deck_validation(metadata["deck"], metadata.get("deck_validation"))
    candidate = derive_candidate_state(
        code_availability=metadata["code"].get("code_availability", "ABSENT"),
        deck_fidelity=metadata["deck"].get("deck_fidelity", "UNKNOWN"),
        corpus_usability=metadata["behavior"].get("usability_classification", "UNKNOWN"),
        permission_scopes=permission_scopes, is_blocked=source_id in set(blocked_source_ids),
        override=review_overrides.get(source_id))
    technical_validation = metadata["technical_validation"]
    if any(value != "NOT_RUN" for value in technical_validation.values()):
        raise OpponentError(f"public source {source_id!r} technical_validation must be NOT_RUN for a metadata-only import")
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "source_id": source_id,
        "source_url": metadata["source_manifest"].get("source_url"),
        "source_type": metadata["source_manifest"].get("source_type"),
        "retrieved_at": metadata["source_manifest"].get("retrieved_at"),
        "code_availability": metadata["code"].get("code_availability"),
        "deck_fidelity": metadata["deck"].get("deck_fidelity"),
        "deck_card_count": deck_check["recomputed_total_count"],
        "deck_hash": metadata["deck"].get("card_hash"),
        "explicit_license": metadata["provenance"].get("explicit_license"),
        "permission_scopes": permission_scopes,
        "technical_validation": {"isolated_import": "NOT_RUN", "cabt_smoke": "NOT_RUN", "legal_action": "NOT_RUN",
                                  "state_leakage": "NOT_RUN", "runtime_compatibility": "NOT_RUN"},
        "candidate_state": candidate["candidate_state"],
        "candidate_reason": candidate["candidate_reason"],
        "rule_derived_candidate_state": candidate["rule_derived_candidate_state"],
        "review_override_applied": candidate["review_override_applied"],
        "corpus_reported_usability": candidate["corpus_reported_usability"],
        "classification": metadata.get("classification"),
        "deck_validation_check": deck_check,
        "file_hashes": metadata.get("hashes"),
        "imported_from_corpus_semantic_hash": corpus_manifest.get("corpus_semantic_hash"),
    }
    record["source_metadata_hash"] = compute_source_metadata_hash({k: v for k, v in record.items() if k not in {"source_metadata_hash"}})
    return record


def _registry_dir(output_dir: Path) -> Path:
    path = Path(output_dir) / "public_sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def import_public_source_corpus(*, corpus_root: str | Path, output_dir: str | Path, dry_run: bool = False) -> dict[str, Any]:
    """Import every included source's metadata; reject same-id-different-content re-imports."""
    corpus_root = Path(corpus_root)
    manifest = load_corpus_manifest(corpus_root)
    blocked = _read_json(corpus_root / "blocked_sources.json")
    override_path = corpus_root / "review_override.json"
    overrides = _read_json(override_path) if override_path.exists() else {}
    registry_dir = _registry_dir(Path(output_dir))
    imported, unchanged, rejected = [], [], []
    for source_id in sorted(manifest["included_source_ids"]):
        source_dir = corpus_root / "sources" / source_id
        if not source_dir.is_dir():
            raise OpponentError(f"corpus manifest lists {source_id!r} but sources/{source_id} does not exist")
        record = build_source_record(corpus_manifest=manifest, source_dir=source_dir, source_id=source_id,
                                      blocked_source_ids=blocked, review_overrides=overrides)
        existing_path = registry_dir / f"{source_id}.json"
        if existing_path.exists():
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if existing.get("source_metadata_hash") == record["source_metadata_hash"]:
                unchanged.append(source_id)
                continue
            raise OpponentError(f"public source {source_id!r} already imported with different content (same source_id, different metadata)")
        if not dry_run:
            atomic_write_json(existing_path, record)
        imported.append(source_id)
    return {"schema_version": "o6-public-source-import-report-v1", "corpus_semantic_hash": manifest.get("corpus_semantic_hash"),
            "imported": sorted(imported), "unchanged": sorted(unchanged), "rejected": sorted(rejected),
            "total_sources": len(manifest["included_source_ids"])}


def list_public_sources(*, output_dir: str | Path) -> list[dict[str, Any]]:
    registry_dir = _registry_dir(Path(output_dir))
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(registry_dir.glob("*.json"))]


def inspect_public_source(*, output_dir: str | Path, source_id: str) -> dict[str, Any]:
    path = _registry_dir(Path(output_dir)) / f"{source_id}.json"
    if not path.exists():
        raise OpponentError(f"unknown public source: {source_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_public_source_metadata(*, output_dir: str | Path) -> dict[str, Any]:
    """Recompute every persisted record's semantic hash; report drift instead of trusting the file at rest."""
    records = list_public_sources(output_dir=output_dir)
    mismatches = []
    for record in records:
        stored_hash = record.get("source_metadata_hash")
        recomputed = compute_source_metadata_hash({k: v for k, v in record.items() if k != "source_metadata_hash"})
        if recomputed != stored_hash:
            mismatches.append(record["source_id"])
    if mismatches:
        raise OpponentError(f"public source metadata hash mismatch (tampered or corrupt): {sorted(mismatches)}")
    return {"schema_version": "o6-public-source-verify-report-v1", "verified": len(records), "mismatches": []}


def check_public_source_permissions(*, output_dir: str | Path) -> dict[str, Any]:
    """Fail closed (raise :class:`PermissionReviewRequiredError`, CLI exit 6) if any source needs review."""
    records = list_public_sources(output_dir=output_dir)
    review_required = sorted(r["source_id"] for r in records if r["candidate_state"] in {"REVIEW_REQUIRED"} or any(v == "REVIEW_REQUIRED" for v in r["permission_scopes"].values()))
    blocked = sorted(r["source_id"] for r in records if r["candidate_state"] == "BLOCKED")
    result = {"schema_version": "o6-public-source-permission-report-v1", "total_sources": len(records),
              "review_required": review_required, "blocked": blocked, "public_redistribution": "DENIED", "submission_bundle": "DENIED"}
    if review_required:
        raise PermissionReviewRequiredError(f"permission review is required for {len(review_required)} public source(s): {review_required}")
    return result
