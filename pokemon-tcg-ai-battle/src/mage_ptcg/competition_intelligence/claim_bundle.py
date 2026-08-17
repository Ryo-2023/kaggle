"""Claim Bundle import: raw text archive + structured YAML/JSON -> validated KnowledgeClaim.

Two separate layers, per the O1-3 design:

    immutable raw human text -> explicitly structured Claim Bundle (YAML/JSON) -> validated KnowledgeClaim

No external LLM API is used or required. PyYAML (already a pinned
dependency in ``requirements.txt``, used elsewhere in this repo for
notebook/docs tooling per prior inventory) is reused for the ``.yaml``/``.yml``
case rather than adding a new dependency; ``.json`` uses the stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    KNOWLEDGE_CLAIM_SCHEMA_VERSION,
    ClaimStatus,
    ContractError,
    EvidenceBasis,
    EvidenceGrade,
    KnowledgeClaim,
)

CLAIM_BUNDLE_SCHEMA_VERSION = "claim-bundle-v1"


class ClaimBundleError(ValueError):
    """Raised when a Claim Bundle file is malformed or a claim fails validation."""


def _load_raw(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ClaimBundleError("PyYAML is required to parse a .yaml/.yml Claim Bundle") from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ClaimBundleError(f"invalid YAML: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaimBundleError(f"invalid JSON: {exc}") from exc


def parse_claim_bundle(path: str | Path) -> list[dict[str, Any]]:
    """Parse a Claim Bundle file into a list of raw claim dicts (not yet validated)."""
    raw = _load_raw(Path(path))
    if not isinstance(raw, Mapping):
        raise ClaimBundleError("Claim Bundle root must be a mapping")
    if raw.get("schema_version") != CLAIM_BUNDLE_SCHEMA_VERSION:
        raise ClaimBundleError(f"unsupported Claim Bundle schema_version {raw.get('schema_version')!r}")
    claims = raw.get("claims")
    if not isinstance(claims, list):
        raise ClaimBundleError("Claim Bundle must have a 'claims' list")
    if not all(isinstance(claim, Mapping) for claim in claims):
        raise ClaimBundleError("every entry in 'claims' must be a mapping")
    return [dict(claim) for claim in claims]


def build_knowledge_claim(raw_claim: Mapping[str, Any], *, raw_source_id: str, created_at: str) -> KnowledgeClaim:
    """Build and validate one ``KnowledgeClaim`` from a raw claim dict.

    A newly imported claim always starts at ``ClaimStatus.RAW`` regardless of
    what the bundle requests -- a Claim Bundle can never import a claim as
    pre-``SUPPORTED``; any ``status``/lifecycle field in the raw dict is
    ignored on import. Advancing the lifecycle requires a separate,
    explicit ``KnowledgeClaim.with_transition()`` call.
    """
    claim_id = raw_claim.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id:
        raise ClaimBundleError("claim_id must be a non-empty string")
    try:
        evidence_grade = EvidenceGrade(raw_claim["evidence_grade"])
    except KeyError as exc:
        raise ClaimBundleError(f"claim {claim_id!r} is missing evidence_grade") from exc
    except ValueError as exc:
        raise ClaimBundleError(f"claim {claim_id!r} has an invalid evidence_grade: {exc}") from exc

    try:
        evidence_basis = EvidenceBasis(raw_claim.get("evidence_basis", EvidenceBasis.INFERRED.value))
    except ValueError as exc:
        raise ClaimBundleError(f"claim {claim_id!r} has an invalid evidence_basis: {exc}") from exc

    try:
        return KnowledgeClaim(
            schema_version=KNOWLEDGE_CLAIM_SCHEMA_VERSION,
            claim_id=claim_id,
            raw_source_id=raw_source_id,
            claim_type=raw_claim.get("claim_type", ""),
            scope=dict(raw_claim.get("scope") or {}),
            preconditions=tuple(raw_claim.get("preconditions") or ()),
            recommendation=raw_claim.get("recommendation", ""),
            expected_effect=raw_claim.get("expected_effect"),
            evidence_grade=evidence_grade,
            status=ClaimStatus.RAW,
            validity=float(raw_claim.get("validity", 0.5)),
            support=float(raw_claim.get("support", 0.0)),
            freshness=float(raw_claim.get("freshness", 1.0)),
            supporting_artifacts=tuple(raw_claim.get("supporting_artifacts") or ()),
            contradicting_claims=tuple(raw_claim.get("contradicting_claims") or ()),
            created_at=created_at,
            updated_at=created_at,
            evidence_basis=evidence_basis,
            # A newly imported claim can never be training/runtime eligible:
            # both require status=SUPPORTED (see KnowledgeClaim.__post_init__),
            # and imports always start RAW -- any bundle-supplied value for
            # these two fields is ignored on import, same as `status` above.
            training_eligible=False,
            runtime_eligible=False,
            supersedes=tuple(raw_claim.get("supersedes") or ()),
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise ClaimBundleError(f"claim {claim_id!r} failed validation: {exc}") from exc


def import_claim_bundle(path: str | Path, *, raw_source_id: str, created_at: str) -> list[KnowledgeClaim]:
    """Parse and validate every claim in a bundle file.

    Raises ``ClaimBundleError`` (naming the offending ``claim_id``) on the
    first invalid claim rather than silently skipping it -- a Claim Bundle is
    a small, human-curated file, so fail-loud is preferred over the
    tolerant-quarantine behavior used for large machine-generated replay data.
    """
    raw_claims = parse_claim_bundle(path)
    return [build_knowledge_claim(claim, raw_source_id=raw_source_id, created_at=created_at) for claim in raw_claims]


__all__ = [
    "CLAIM_BUNDLE_SCHEMA_VERSION",
    "ClaimBundleError",
    "build_knowledge_claim",
    "import_claim_bundle",
    "parse_claim_bundle",
]
