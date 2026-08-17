"""Rules attestation gate: public-other collection remains disabled by default."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


UNVERIFIED_RULES_CONSTRAINT = "UNVERIFIED_RULES_CONSTRAINT"
VERIFIED_RULES_CONSTRAINT = "VERIFIED_RULES_CONSTRAINT"


@dataclass(frozen=True, slots=True)
class RulesAttestation:
    competition: str
    status: str = UNVERIFIED_RULES_CONSTRAINT
    verified_at: str | None = None
    verified_by: str | None = None
    reference: str | None = None

    def permits_public_other_collection(self) -> bool:
        return self.status == VERIFIED_RULES_CONSTRAINT and bool(self.verified_at and self.verified_by and self.reference)


def load_rules_attestation(path: str | Path) -> RulesAttestation:
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("rules attestation must be an object")
    allowed = {"competition", "status", "verified_at", "verified_by", "reference"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown rules attestation keys: {sorted(unknown)}")
    attestation = RulesAttestation(**dict(raw))
    if attestation.status not in {UNVERIFIED_RULES_CONSTRAINT, VERIFIED_RULES_CONSTRAINT}:
        raise ValueError("unsupported rules attestation status")
    if attestation.status == VERIFIED_RULES_CONSTRAINT and not attestation.permits_public_other_collection():
        raise ValueError("verified attestation requires verified_at, verified_by, and reference")
    return attestation


__all__ = [
    "RulesAttestation", "UNVERIFIED_RULES_CONSTRAINT", "VERIFIED_RULES_CONSTRAINT", "load_rules_attestation",
]
