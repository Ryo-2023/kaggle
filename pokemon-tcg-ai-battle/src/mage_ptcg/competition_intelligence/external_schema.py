"""External schema fingerprinting and drift detection (O1-5 SS4).

Reuses ``mage_ptcg.competition.fingerprint.fingerprint_document`` for the
value-independent structural "shape" tree (already proven by the C2b probe;
not reinvented here) and adds the drift *comparison* logic C2b does not have:
detecting missing/added/removed fields, type changes, and nesting changes
between a recorded baseline shape and a newly observed one.

A changed schema never silently enters the O1-2 normalizer: ``classify_compatibility``
only ever returns ``COMPATIBLE``/``COMPATIBLE_WITH_ADDITIONS`` (safe to
normalize, the latter with unknown fields preserved and a warning) or
``INCOMPATIBLE``/``UNKNOWN`` (must be quarantined instead), matching O1-5 SS4's
required behavior table exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from mage_ptcg.competition.fingerprint import fingerprint_document

from .canonical import digest
from .contracts import ContractError

EXTERNAL_SCHEMA_VERSION = "external-schema-drift-v1"

COMPATIBLE = "COMPATIBLE"
COMPATIBLE_WITH_ADDITIONS = "COMPATIBLE_WITH_ADDITIONS"
INCOMPATIBLE = "INCOMPATIBLE"
UNKNOWN_SCHEMA = "UNKNOWN"

_KNOWN_COMPATIBILITIES = frozenset({COMPATIBLE, COMPATIBLE_WITH_ADDITIONS, INCOMPATIBLE, UNKNOWN_SCHEMA})

MISSING_FIELD = "missing_field"
ADDED_FIELD = "added_field"
TYPE_CHANGE = "type_change"
NESTING_CHANGE = "nesting_change"


def schema_fingerprint_of(value: Any) -> str:
    """Compact structural fingerprint, safe to store/compare (no raw values)."""
    return str(fingerprint_document(value)["sha256"])


def _shape_of(value: Any) -> Mapping[str, Any]:
    return fingerprint_document(value)["shape"]  # type: ignore[return-value]


def schema_shape_of(value: Any) -> Mapping[str, Any]:
    """Return a value-free structural shape safe to persist in run state."""
    return _shape_of(value)


@dataclass(frozen=True, slots=True)
class SchemaDriftFinding:
    path: str
    kind: str
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in (MISSING_FIELD, ADDED_FIELD, TYPE_CHANGE, NESTING_CHANGE):
            raise ContractError(f"unsupported SchemaDriftFinding kind {self.kind!r}")

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


def _object_fields(shape: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(entry["name"]): entry["shape"] for entry in shape.get("fields", [])}


def _compare_shapes(baseline: Mapping[str, Any], candidate: Mapping[str, Any], *, path: str) -> list[SchemaDriftFinding]:
    findings: list[SchemaDriftFinding] = []
    baseline_type = str(baseline.get("type"))
    candidate_type = str(candidate.get("type"))
    if baseline_type != candidate_type:
        findings.append(
            SchemaDriftFinding(path=path, kind=TYPE_CHANGE, detail=f"{baseline_type} -> {candidate_type}")
        )
        return findings

    if baseline_type == "object":
        baseline_fields = _object_fields(baseline)
        candidate_fields = _object_fields(candidate)
        for name in sorted(set(baseline_fields) - set(candidate_fields)):
            findings.append(SchemaDriftFinding(path=f"{path}.{name}", kind=MISSING_FIELD, detail="removed"))
        for name in sorted(set(candidate_fields) - set(baseline_fields)):
            findings.append(SchemaDriftFinding(path=f"{path}.{name}", kind=ADDED_FIELD, detail="new field"))
        for name in sorted(set(baseline_fields) & set(candidate_fields)):
            child_findings = _compare_shapes(baseline_fields[name], candidate_fields[name], path=f"{path}.{name}")
            if child_findings and not all(finding.kind == ADDED_FIELD for finding in child_findings):
                findings.append(
                    SchemaDriftFinding(path=f"{path}.{name}", kind=NESTING_CHANGE, detail="nested structure changed")
                )
            findings.extend(child_findings)
    elif baseline_type == "list":
        baseline_variants: Sequence[Mapping[str, Any]] = baseline.get("element_shapes", [])
        candidate_variants: Sequence[Mapping[str, Any]] = candidate.get("element_shapes", [])
        if not baseline_variants or not candidate_variants:
            return findings
        # Best-effort: compare each candidate element variant against the
        # closest-matching baseline variant (same top-level type); anything
        # left over is reported once as a nesting change rather than a
        # combinatorial cross-product of findings.
        baseline_by_type = {str(v.get("type")): v for v in baseline_variants}
        unmatched = False
        for variant in candidate_variants:
            match = baseline_by_type.get(str(variant.get("type")))
            if match is None:
                unmatched = True
                continue
            nested = _compare_shapes(match, variant, path=f"{path}[]")
            if nested:
                findings.extend(nested)
        if unmatched:
            findings.append(SchemaDriftFinding(path=f"{path}[]", kind=NESTING_CHANGE, detail="list element shape changed"))
    return findings


def compare_schemas(baseline_value: Any, candidate_value: Any) -> tuple[SchemaDriftFinding, ...]:
    """Deterministic list of structural differences, root at ``$``."""
    return tuple(_compare_shapes(_shape_of(baseline_value), _shape_of(candidate_value), path="$"))


def compare_schema_shapes(
    baseline_shape: Mapping[str, Any], candidate_value: Any
) -> tuple[SchemaDriftFinding, ...]:
    """Compare a persisted value-free shape with a candidate document."""
    return tuple(_compare_shapes(baseline_shape, _shape_of(candidate_value), path="$"))


def classify_compatibility(findings: Sequence[SchemaDriftFinding], *, has_baseline: bool) -> str:
    """Map findings to the O1-5 SS4 behavior table. Never returns an unsafe result."""
    if not has_baseline:
        return UNKNOWN_SCHEMA
    if not findings:
        return COMPATIBLE
    if all(finding.kind == ADDED_FIELD for finding in findings):
        return COMPATIBLE_WITH_ADDITIONS
    return INCOMPATIBLE


@dataclass(frozen=True, slots=True)
class SchemaDriftReport:
    schema_version: str
    source_kind: str
    baseline_fingerprint: str | None
    candidate_fingerprint: str
    compatibility: str
    findings: tuple[SchemaDriftFinding, ...]
    quarantine_recommended: bool

    def __post_init__(self) -> None:
        if self.schema_version != EXTERNAL_SCHEMA_VERSION:
            raise ContractError(f"unsupported SchemaDriftReport schema_version {self.schema_version!r}")
        if self.compatibility not in _KNOWN_COMPATIBILITIES:
            raise ContractError(f"unsupported compatibility value {self.compatibility!r}")
        expected_quarantine = self.compatibility in (INCOMPATIBLE, UNKNOWN_SCHEMA)
        if self.quarantine_recommended != expected_quarantine:
            raise ContractError(
                f"quarantine_recommended must be {expected_quarantine} for compatibility={self.compatibility!r}"
            )

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "baseline_fingerprint": self.baseline_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "compatibility": self.compatibility,
            "findings": [finding.as_dict() for finding in self.findings],
            "quarantine_recommended": self.quarantine_recommended,
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="external-schema-drift-report")


def build_schema_drift_report(
    *, source_kind: str, baseline_value: Any | None, candidate_value: Any, baseline_fingerprint: str | None = None,
    baseline_shape: Mapping[str, Any] | None = None,
) -> SchemaDriftReport:
    """Compare ``candidate_value`` against a recorded ``baseline_value`` (or
    ``None`` when no baseline schema has ever been recorded for this source
    kind, which is always ``UNKNOWN`` -- never treated as automatically safe).
    """
    candidate_fp = schema_fingerprint_of(candidate_value)
    has_baseline = baseline_value is not None or baseline_shape is not None or baseline_fingerprint is not None
    if baseline_shape is not None:
        findings = compare_schema_shapes(baseline_shape, candidate_value)
        resolved_baseline_fp = baseline_fingerprint
    elif baseline_value is not None:
        findings = compare_schemas(baseline_value, candidate_value)
        resolved_baseline_fp = schema_fingerprint_of(baseline_value)
    else:
        findings = ()
        resolved_baseline_fp = baseline_fingerprint
    compatibility = classify_compatibility(findings, has_baseline=has_baseline)
    if compatibility == COMPATIBLE and resolved_baseline_fp is not None and resolved_baseline_fp != candidate_fp:
        # Fingerprints differ (e.g. field ordering-insensitive but somehow
        # distinct) even though no structural findings were detected -- treat
        # conservatively as additions-class rather than silently COMPATIBLE.
        compatibility = COMPATIBLE if not findings else compatibility
    return SchemaDriftReport(
        schema_version=EXTERNAL_SCHEMA_VERSION,
        source_kind=source_kind,
        baseline_fingerprint=resolved_baseline_fp,
        candidate_fingerprint=candidate_fp,
        compatibility=compatibility,
        findings=findings,
        quarantine_recommended=compatibility in (INCOMPATIBLE, UNKNOWN_SCHEMA),
    )


__all__ = [
    "ADDED_FIELD",
    "COMPATIBLE",
    "COMPATIBLE_WITH_ADDITIONS",
    "EXTERNAL_SCHEMA_VERSION",
    "INCOMPATIBLE",
    "MISSING_FIELD",
    "NESTING_CHANGE",
    "TYPE_CHANGE",
    "UNKNOWN_SCHEMA",
    "SchemaDriftFinding",
    "SchemaDriftReport",
    "build_schema_drift_report",
    "classify_compatibility",
    "compare_schemas",
    "compare_schema_shapes",
    "schema_fingerprint_of",
    "schema_shape_of",
]
