"""Non-authoritative O1-6 promotion reports."""

from __future__ import annotations

from typing import Any, Mapping

from .canonical import digest
from .contracts import ContractError

ALLOWED_DECISIONS = frozenset({"NO_DECISION", "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE"})


def build_promotion_report(*, decision: str, meta_snapshot_hash: str, benchmark_hashes: list[str], evidence: Mapping[str, Any]) -> dict[str, Any]:
    if decision not in ALLOWED_DECISIONS:
        raise ContractError("O1-6 promotion report may only be NO_DECISION, REVIEW_REQUIRED, or INSUFFICIENT_EVIDENCE")
    payload = {"schema_version": "non-authoritative-promotion-report-v1", "decision": decision,
               "meta_snapshot_hash": meta_snapshot_hash, "benchmark_hashes": sorted(benchmark_hashes),
               "evidence": dict(evidence), "authority": "non_authoritative", "auto_promotion": False, "auto_submit": False}
    return {**payload, "report_id": "promotion-report-" + digest(payload, domain="promotion-report")[:24],
            "content_hash": digest(payload, domain="promotion-report")}
