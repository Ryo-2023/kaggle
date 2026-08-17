"""Immutable, self-verifying Knowledge Snapshot (O1-3 §6).

Mirrors ``contracts.IntelligenceSnapshot``'s self-verification pattern:
``snapshot_sha256`` must match the content hash of every other field, and
``snapshot_id`` is derived from that hash, so two builds from identical
inputs always produce the same id/hash and any post-hoc edit is detectable.
``build_knowledge_snapshot()`` is the only construction path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .canonical import digest
from .contracts import ContractError

KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION = "knowledge-snapshot-v1"
_SNAPSHOT_ID_HASH_LENGTH = 20


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    schema_version: str
    snapshot_id: str
    created_at: str
    cutoff_time: str
    included_claim_ids: tuple[str, ...]
    excluded_claims: Mapping[str, str]
    source_hashes: Mapping[str, str]
    permissions_summary: Mapping[str, object]
    lifecycle_summary: Mapping[str, int]
    evidence_grade_summary: Mapping[str, int]
    evidence_basis_summary: Mapping[str, int]
    contradiction_count: int
    normalizer_versions: Mapping[str, str]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION:
            raise ContractError(f"unsupported KnowledgeSnapshot schema_version {self.schema_version!r}")
        if not isinstance(self.included_claim_ids, tuple):
            raise ContractError("included_claim_ids must be a tuple[str, ...]")
        if self.contradiction_count < 0:
            raise ContractError("contradiction_count must be >= 0")
        overlap = set(self.included_claim_ids) & set(self.excluded_claims)
        if overlap:
            raise ContractError(f"claim ids cannot be both included and excluded: {sorted(overlap)}")
        expected_hash = digest(self.content_payload(), domain="knowledge-snapshot")
        if self.snapshot_sha256 != expected_hash:
            raise ContractError(
                f"snapshot_sha256 mismatch: recomputed {expected_hash} but got {self.snapshot_sha256}; "
                "a KnowledgeSnapshot's fields must never be edited after construction"
            )
        expected_id = f"knowledge-snapshot-{expected_hash[:_SNAPSHOT_ID_HASH_LENGTH]}"
        if self.snapshot_id != expected_id:
            raise ContractError(f"snapshot_id must be content-derived: expected {expected_id!r}, got {self.snapshot_id!r}")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "cutoff_time": self.cutoff_time,
            "included_claim_ids": sorted(self.included_claim_ids),
            "excluded_claims": dict(sorted(self.excluded_claims.items())),
            "source_hashes": dict(sorted(self.source_hashes.items())),
            "permissions_summary": dict(sorted(self.permissions_summary.items())),
            "lifecycle_summary": dict(sorted(self.lifecycle_summary.items())),
            "evidence_grade_summary": dict(sorted(self.evidence_grade_summary.items())),
            "evidence_basis_summary": dict(sorted(self.evidence_basis_summary.items())),
            "contradiction_count": self.contradiction_count,
            "normalizer_versions": dict(sorted(self.normalizer_versions.items())),
        }


def build_knowledge_snapshot(**fields: object) -> KnowledgeSnapshot:
    """Construct a ``KnowledgeSnapshot`` by computing its own hash/id.

    Callers pass every field except ``snapshot_id``/``snapshot_sha256``.
    """
    if "snapshot_id" in fields or "snapshot_sha256" in fields:
        raise ContractError("snapshot_id and snapshot_sha256 are derived; do not pass them explicitly")
    content_payload = {
        "schema_version": KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION,
        "created_at": fields["created_at"],
        "cutoff_time": fields["cutoff_time"],
        "included_claim_ids": sorted(fields["included_claim_ids"]),
        "excluded_claims": dict(sorted(dict(fields["excluded_claims"]).items())),
        "source_hashes": dict(sorted(dict(fields["source_hashes"]).items())),
        "permissions_summary": dict(sorted(dict(fields["permissions_summary"]).items())),
        "lifecycle_summary": dict(sorted(dict(fields["lifecycle_summary"]).items())),
        "evidence_grade_summary": dict(sorted(dict(fields["evidence_grade_summary"]).items())),
        "evidence_basis_summary": dict(sorted(dict(fields["evidence_basis_summary"]).items())),
        "contradiction_count": fields["contradiction_count"],
        "normalizer_versions": dict(sorted(dict(fields["normalizer_versions"]).items())),
    }
    snapshot_hash = digest(content_payload, domain="knowledge-snapshot")
    snapshot_id = f"knowledge-snapshot-{snapshot_hash[:_SNAPSHOT_ID_HASH_LENGTH]}"
    return KnowledgeSnapshot(
        schema_version=KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        snapshot_sha256=snapshot_hash,
        **fields,  # type: ignore[arg-type]
    )


__all__ = ["KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION", "KnowledgeSnapshot", "build_knowledge_snapshot"]
