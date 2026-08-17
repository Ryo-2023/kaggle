"""Deterministic contradiction detection between KnowledgeClaims (O1-3 §4).

Two claims are flagged as a *candidate* contradiction when they share the
same ``claim_type`` and their ``scope`` mappings overlap (at least one key
present with an equal value in both). This is a conservative, deterministic
baseline -- it cannot tell "hold card X" from "use card X early" apart by
meaning, only that both claims talk about the same claim_type in an
overlapping situation. Both claims are always preserved; neither is deleted,
merged, or silently overwritten by "latest wins" -- this module only ever
*adds* a ``Contradiction`` relation alongside the two claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Mapping, Sequence

from .canonical import digest
from .contracts import ContractError, KnowledgeClaim

CONTRADICTION_SCHEMA_VERSION = "knowledge-contradiction-v1"


@dataclass(frozen=True, slots=True)
class Contradiction:
    schema_version: str
    contradiction_id: str
    claim_id_a: str
    claim_id_b: str
    overlap_reason: str
    scope_overlap: Mapping[str, object]
    evidence_grade_a: str
    evidence_grade_b: str
    confidence: float
    resolved: bool

    def __post_init__(self) -> None:
        if self.schema_version != CONTRADICTION_SCHEMA_VERSION:
            raise ContractError(f"unsupported Contradiction schema_version {self.schema_version!r}")
        if self.claim_id_a == self.claim_id_b:
            raise ContractError("a claim cannot contradict itself")
        if self.claim_id_a >= self.claim_id_b:
            raise ContractError("claim_id_a must sort before claim_id_b (canonical pair ordering)")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractError("confidence must be within [0, 1]")

    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "claim_id_a": self.claim_id_a,
            "claim_id_b": self.claim_id_b,
            "overlap_reason": self.overlap_reason,
            "scope_overlap": dict(sorted(self.scope_overlap.items())),
            "evidence_grade_a": self.evidence_grade_a,
            "evidence_grade_b": self.evidence_grade_b,
            "confidence": self.confidence,
            "resolved": self.resolved,
        }
        return digest(payload, domain="knowledge-contradiction")


def _scope_overlap(a: Mapping[str, object], b: Mapping[str, object]) -> dict[str, object]:
    return {key: a[key] for key in (set(a) & set(b)) if a[key] == b[key]}


def detect_contradictions(claims: Sequence[KnowledgeClaim]) -> tuple[Contradiction, ...]:
    """Detect candidate contradictions across all pairs of ``claims``.

    Deterministic regardless of input order: pairs are canonicalized by
    sorting ``claim_id`` before comparison, so the same claim set always
    produces the same ``contradiction_id`` set.
    """
    results: list[Contradiction] = []
    ordered = sorted(claims, key=lambda claim: claim.claim_id)
    for first, second in combinations(ordered, 2):
        if first.claim_type != second.claim_type:
            continue
        overlap = _scope_overlap(first.scope, second.scope)
        if not overlap:
            continue
        claim_a, claim_b = (first, second) if first.claim_id < second.claim_id else (second, first)
        contradiction_id = digest(
            {"claim_id_a": claim_a.claim_id, "claim_id_b": claim_b.claim_id, "overlap": dict(sorted(overlap.items()))},
            domain="knowledge-contradiction-id",
        )
        # overlap is non-empty here (checked above) and is a subset of the
        # union, so union_key_count >= len(overlap) >= 1 always.
        union_key_count = len(set(claim_a.scope) | set(claim_b.scope))
        results.append(Contradiction(
            schema_version=CONTRADICTION_SCHEMA_VERSION,
            contradiction_id=contradiction_id,
            claim_id_a=claim_a.claim_id,
            claim_id_b=claim_b.claim_id,
            overlap_reason=f"same claim_type={first.claim_type!r} with overlapping scope keys {sorted(overlap)}",
            scope_overlap=overlap,
            evidence_grade_a=claim_a.evidence_grade.value,
            evidence_grade_b=claim_b.evidence_grade.value,
            confidence=min(1.0, len(overlap) / union_key_count),
            resolved=False,
        ))
    return tuple(results)


__all__ = ["CONTRADICTION_SCHEMA_VERSION", "Contradiction", "detect_contradictions"]
