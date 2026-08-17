"""Fail-closed teacher-quality authority for recurrent θ0.

The current teacher corpus does not contain the independently attested
current-pool, fault, and strength evidence needed to define the planned
five-tier weight rule.  This module therefore screens and seals evidence but
never invents a quality weight or permits θ0 while that authority is absent.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from collections.abc import Mapping


_EVIDENCE_SCHEMA = "meta-specialist-teacher-quality-evidence-v1"
_MANIFEST_SCHEMA = "meta-specialist-teacher-quality-manifest-v1"
_HEX = frozenset("0123456789abcdef")
_AUTHORITY_GAP_MANIFEST_KEYS = frozenset({
    "schema", "lane", "status", "theta0_allowed", "authority_gap",
    "records_total", "eligible_record_count", "exclusion_counts", "exclusions",
    "source_hashes", "manifest_sha256",
})
_READY_MANIFEST_KEYS = _AUTHORITY_GAP_MANIFEST_KEYS | frozenset({
    "approved_rule", "quality_records", "primary_artifacts",
})
_AUTHORITY_GAP_KEYS = frozenset({"code", "detail"})
_EXCLUSION_KEYS = frozenset({"record_id", "reasons"})
_APPROVED_RULE_KEYS = frozenset({
    "schema", "approval_status", "rule_id", "rule_version", "rule_file_sha256", "assignments",
})
_RULE_ARTIFACT_KEYS = _APPROVED_RULE_KEYS - frozenset({"rule_file_sha256"})
_RULE_ASSIGNMENT_KEYS = frozenset({"evidence_sha256", "quality_weight"})
_QUALITY_RECORD_KEYS = frozenset({
    "record_id", "content_hash", "source", "teacher", "policy", "deck",
    "current_pool", "fault", "strength", "evidence_sha256", "quality_weight",
})
_READY_SOURCE_KEYS = frozenset({"synthetic", "attested", "training_eligible", "usage_class"})
_READY_TEACHER_KEYS = frozenset({"teacher_id", "teacher_revision"})
_READY_POLICY_KEYS = frozenset({"implementation_sha256", "version", "usage_boundary"})
_READY_DECK_KEYS = frozenset({"fingerprint_sha256"})
_READY_CURRENT_POOL_KEYS = frozenset({"evaluation_id", "result_sha256", "games", "wins", "draws", "losses"})
_READY_FAULT_KEYS = frozenset({"result_sha256", "games", "faults"})
_READY_STRENGTH_KEYS = frozenset({"confidence", "agreement", "search_strength"})
_PRIMARY_ARTIFACT_KEYS = frozenset({"record_id", "kind", "source_name", "file_sha256"})
_PRIMARY_ARTIFACT_CONTENT_KEYS = frozenset({"schema", "kind", "record_id", "content_hash", "value"})
_PRIMARY_ARTIFACT_KINDS = frozenset({
    "source", "teacher", "policy", "deck", "current_pool", "fault", "strength",
})
_RULE_ARTIFACT_SCHEMA = "meta-specialist-teacher-quality-rule-v1"
_PRIMARY_ARTIFACT_SCHEMA = "meta-specialist-teacher-quality-primary-evidence-v1"
_EXCLUSION_REASONS = frozenset({
    "synthetic_or_unattested", "policy_deck_version_provenance_missing",
    "current_pool_result_missing", "fault_provenance_missing",
    "teacher_strength_evidence_missing", "weight_rule_authority_missing",
})
_QUALITY_WEIGHTS = frozenset({0.0, 0.2, 0.4, 0.7, 1.0})


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON value {value}")


def _assert_finite(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is float:
            if not math.isfinite(current):
                raise ValueError("non-finite JSON value")
        elif type(current) is dict:
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)


def _strict_json(raw: bytes, *, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates, parse_constant=_reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("duplicate JSON key", "non-finite JSON value")):
            raise
        raise ValueError(f"{name} is not strict JSON") from exc
    _assert_finite(value)
    if type(value) is not dict:
        raise ValueError(f"{name} must be a JSON object")
    return value


def _read_anchored(path: str | Path, *, expected_sha256: str, name: str) -> bytes:
    _require_sha256(expected_sha256, field=f"expected {name}")
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("anchored teacher-quality file access requires O_NOFOLLOW")
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError(f"{name} changed during read")
    if _sha256(raw) != expected_sha256:
        raise ValueError(f"external {name} SHA-256 does not match")
    return raw


def _mapping(value: object, *, field: str) -> Mapping[str, object] | None:
    return value if type(value) is dict else None


def _exact_mapping(value: object, *, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise ValueError(f"{field} has an invalid closed key set")
    return value


def _nonempty_string(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _nonnegative_int(value: object, *, field: str, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{field} must be a {qualifier} integer")
    return value


def _unit_float(value: object, *, field: str) -> float:
    if type(value) not in {int, float} or type(value) is bool or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError(f"{field} must be finite in [0,1]")
    return float(value)


def _relative_artifact_path(value: object) -> None:
    if type(value) is not str or not value:
        raise ValueError("policy implementation path is missing")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ValueError("policy implementation path escapes its artifact namespace")


def _available_source(value: object) -> bool:
    source = _mapping(value, field="source")
    return bool(source and source.get("synthetic") is False and source.get("training_eligible") is True and source.get("usage_class") == "qualified_training")


def _valid_policy_deck(record: Mapping[str, object]) -> bool:
    policy = _mapping(record.get("policy"), field="policy")
    deck = _mapping(record.get("deck"), field="deck")
    teacher = _mapping(record.get("teacher"), field="teacher")
    if policy is None or deck is None or teacher is None:
        return False
    implementation_path = policy.get("implementation_path")
    if implementation_path is not None:
        _relative_artifact_path(implementation_path)
    try:
        _require_sha256(policy.get("implementation_sha256"), field="policy implementation SHA-256")
        _require_sha256(deck.get("fingerprint_sha256"), field="deck fingerprint SHA-256")
    except ValueError:
        return False
    return all(type(value) is str and value for value in (
        policy.get("implementation_path"), policy.get("version"), policy.get("usage_boundary"),
        teacher.get("teacher_id"), teacher.get("teacher_revision"),
    )) and teacher.get("status") == "available"


def _valid_current_pool(record: Mapping[str, object]) -> bool:
    value = _mapping(record.get("current_pool"), field="current pool")
    if value is None:
        return False
    try:
        _require_sha256(value.get("result_sha256"), field="current-pool result SHA-256")
    except ValueError:
        return False
    numbers = tuple(value.get(name) for name in ("games", "wins", "draws", "losses"))
    return type(value.get("evaluation_id")) is str and bool(value["evaluation_id"]) and all(type(item) is int and item >= 0 for item in numbers) and numbers[0] > 0 and sum(numbers[1:]) == numbers[0]


def _valid_fault(record: Mapping[str, object]) -> bool:
    value = _mapping(record.get("fault"), field="fault")
    if value is None:
        return False
    try:
        _require_sha256(value.get("result_sha256"), field="fault result SHA-256")
    except ValueError:
        return False
    return type(value.get("games")) is int and type(value.get("faults")) is int and value["games"] > 0 and 0 <= value["faults"] <= value["games"]


def _valid_strength(record: Mapping[str, object]) -> bool:
    value = _mapping(record.get("strength"), field="strength")
    if value is None:
        return False
    scores = tuple(value.get(name) for name in ("confidence", "agreement", "search_strength"))
    return all(type(score) in {int, float} and type(score) is not bool and math.isfinite(float(score)) and 0 <= float(score) <= 1 for score in scores)


def _screen_record(record: object) -> tuple[str, list[str]]:
    if type(record) is not dict:
        raise ValueError("teacher-quality record must be an object")
    record_id = _require_sha256(record.get("record_id"), field="teacher-quality record_id")
    _require_sha256(record.get("content_hash"), field="teacher-quality content_hash")
    if not _available_source(record.get("source")):
        return record_id, ["synthetic_or_unattested"]
    reasons: list[str] = []
    if not _valid_policy_deck(record):
        reasons.append("policy_deck_version_provenance_missing")
    if not _valid_current_pool(record):
        reasons.append("current_pool_result_missing")
    if not _valid_fault(record):
        reasons.append("fault_provenance_missing")
    if not _valid_strength(record):
        reasons.append("teacher_strength_evidence_missing")
    # No primary, versioned threshold/rule artifact exists in this task's
    # authority.  Do not convert the observed scores into a guessed tier.
    if not reasons:
        reasons.append("weight_rule_authority_missing")
    return record_id, reasons


def _validate_evidence(value: Mapping[str, object]) -> tuple[str, list[object]]:
    if value.get("schema") != _EVIDENCE_SCHEMA or type(value.get("lane")) is not str or not value["lane"]:
        raise ValueError("teacher-quality evidence schema or lane is invalid")
    records = value.get("records")
    if type(records) is not list:
        raise ValueError("teacher-quality evidence records must be a list")
    return value["lane"], records


def _manifest_body(*, lane: str, evidence_name: str, evidence_sha256: str, records: list[object]) -> dict[str, object]:
    exclusions: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for record in records:
        record_id, reasons = _screen_record(record)
        exclusions.append({"record_id": record_id, "reasons": reasons})
        counts.update(reasons)
    return {
        "schema": _MANIFEST_SCHEMA,
        "lane": lane,
        "status": "AUTHORITY_GAP",
        "theta0_allowed": False,
        "authority_gap": {
            "code": "QUALITY_WEIGHT_RULE_UNATTESTED",
            "detail": "no approved primary-evidence rule maps current-pool, fault, provenance, confidence, agreement, and search strength to quality tiers",
        },
        "records_total": len(records),
        "eligible_record_count": 0,
        "exclusion_counts": dict(sorted(counts.items())),
        "exclusions": exclusions,
        "source_hashes": {evidence_name: evidence_sha256},
    }


def _validate_source_hashes(value: object) -> dict[str, object]:
    hashes = value if type(value) is dict else None
    if not hashes:
        raise ValueError("teacher-quality source hashes must be a nonempty object")
    for name, digest in hashes.items():
        if type(name) is not str or not name:
            raise ValueError("teacher-quality source hash name is invalid")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise ValueError("teacher-quality source hash path escapes its namespace")
        _require_sha256(digest, field=f"teacher-quality source hash {name}")
    return hashes


def _source_name(value: object, *, field: str) -> str:
    name = _nonempty_string(value, field=field)
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1 or path.name != name:
        raise ValueError(f"{field} escapes the manifest artifact namespace")
    return name


def _canonical_anchored_object(path: str | Path, *, expected_sha256: str, name: str) -> dict[str, object]:
    raw = _read_anchored(path, expected_sha256=expected_sha256, name=name)
    value = _strict_json(raw, name=name)
    if raw != _canonical(value):
        raise ValueError(f"{name} bytes are not canonical")
    return value


def _validate_exclusions(manifest: Mapping[str, object]) -> None:
    counts = manifest.get("exclusion_counts")
    exclusions = manifest.get("exclusions")
    if type(counts) is not dict or type(exclusions) is not list:
        raise ValueError("teacher-quality exclusion counts/exclusions have invalid types")
    normalized_counts: Counter[str] = Counter()
    for reason, count in counts.items():
        if reason not in _EXCLUSION_REASONS or type(count) is not int or count <= 0:
            raise ValueError("teacher-quality exclusion counts are invalid")
        normalized_counts[reason] = count
    observed: Counter[str] = Counter()
    record_ids: set[str] = set()
    for item in exclusions:
        exclusion = _exact_mapping(item, field="teacher-quality exclusion", keys=_EXCLUSION_KEYS)
        record_id = _require_sha256(exclusion["record_id"], field="teacher-quality exclusion record_id")
        reasons = exclusion["reasons"]
        if record_id in record_ids or type(reasons) is not list or not reasons:
            raise ValueError("teacher-quality exclusion member is invalid")
        if any(type(reason) is not str or reason not in _EXCLUSION_REASONS for reason in reasons) or len(set(reasons)) != len(reasons):
            raise ValueError("teacher-quality exclusion reasons are invalid")
        record_ids.add(record_id)
        observed.update(reasons)
    if observed != normalized_counts:
        raise ValueError("teacher-quality exclusion counts do not match members")


def _validate_common_manifest(manifest: Mapping[str, object]) -> tuple[int, int, dict[str, object]]:
    if manifest.get("schema") != _MANIFEST_SCHEMA:
        raise ValueError("teacher-quality manifest schema is invalid")
    _nonempty_string(manifest.get("lane"), field="teacher-quality lane")
    records_total = _nonnegative_int(manifest.get("records_total"), field="teacher-quality records_total")
    eligible = _nonnegative_int(manifest.get("eligible_record_count"), field="teacher-quality eligible_record_count")
    if eligible > records_total:
        raise ValueError("teacher-quality eligible count exceeds total records")
    _validate_exclusions(manifest)
    source_hashes = _validate_source_hashes(manifest.get("source_hashes"))
    return records_total, eligible, source_hashes


def _validate_authority_gap_manifest(manifest: Mapping[str, object]) -> None:
    if frozenset(manifest) != _AUTHORITY_GAP_MANIFEST_KEYS:
        raise ValueError("teacher-quality authority-gap manifest has an invalid key set")
    records_total, eligible, _source_hashes = _validate_common_manifest(manifest)
    gap = _exact_mapping(manifest.get("authority_gap"), field="teacher-quality authority gap", keys=_AUTHORITY_GAP_KEYS)
    if (
        manifest.get("status") != "AUTHORITY_GAP"
        or manifest.get("theta0_allowed") is not False
        or gap.get("code") != "QUALITY_WEIGHT_RULE_UNATTESTED"
        or type(gap.get("detail")) is not str
        or not gap["detail"]
        or eligible != 0
        or len(manifest["exclusions"]) != records_total
    ):
        raise ValueError("teacher-quality manifest is not a closed fail-closed authority gap")


def _validate_ready_source(value: object) -> None:
    source = _exact_mapping(value, field="teacher-quality READY source", keys=_READY_SOURCE_KEYS)
    if (
        source["synthetic"] is not False
        or source["attested"] is not True
        or source["training_eligible"] is not True
        or source["usage_class"] != "qualified_training"
    ):
        raise ValueError("teacher-quality READY source is synthetic or unattested")


def _validate_ready_record(value: object) -> tuple[str, str, float]:
    record = _exact_mapping(value, field="teacher-quality READY record", keys=_QUALITY_RECORD_KEYS)
    record_id = _require_sha256(record["record_id"], field="teacher-quality READY record_id")
    _require_sha256(record["content_hash"], field="teacher-quality READY content_hash")
    _validate_ready_source(record["source"])
    teacher = _exact_mapping(record["teacher"], field="teacher-quality READY teacher", keys=_READY_TEACHER_KEYS)
    _nonempty_string(teacher["teacher_id"], field="teacher-quality READY teacher_id")
    _nonempty_string(teacher["teacher_revision"], field="teacher-quality READY teacher_revision")
    policy = _exact_mapping(record["policy"], field="teacher-quality READY policy", keys=_READY_POLICY_KEYS)
    _require_sha256(policy["implementation_sha256"], field="teacher-quality READY policy implementation")
    _nonempty_string(policy["version"], field="teacher-quality READY policy version")
    _nonempty_string(policy["usage_boundary"], field="teacher-quality READY policy usage boundary")
    deck = _exact_mapping(record["deck"], field="teacher-quality READY deck", keys=_READY_DECK_KEYS)
    _require_sha256(deck["fingerprint_sha256"], field="teacher-quality READY deck fingerprint")
    current_pool = _exact_mapping(record["current_pool"], field="teacher-quality READY current pool", keys=_READY_CURRENT_POOL_KEYS)
    _nonempty_string(current_pool["evaluation_id"], field="teacher-quality READY current-pool evaluation_id")
    _require_sha256(current_pool["result_sha256"], field="teacher-quality READY current-pool result")
    games = _nonnegative_int(current_pool["games"], field="teacher-quality READY current-pool games", positive=True)
    outcomes = tuple(_nonnegative_int(current_pool[name], field=f"teacher-quality READY current-pool {name}") for name in ("wins", "draws", "losses"))
    if sum(outcomes) != games:
        raise ValueError("teacher-quality READY current-pool outcomes do not match games")
    fault = _exact_mapping(record["fault"], field="teacher-quality READY fault", keys=_READY_FAULT_KEYS)
    _require_sha256(fault["result_sha256"], field="teacher-quality READY fault result")
    fault_games = _nonnegative_int(fault["games"], field="teacher-quality READY fault games", positive=True)
    faults = _nonnegative_int(fault["faults"], field="teacher-quality READY faults")
    if faults > fault_games or fault_games != games:
        raise ValueError("teacher-quality READY fault accounting is invalid")
    strength = _exact_mapping(record["strength"], field="teacher-quality READY strength", keys=_READY_STRENGTH_KEYS)
    for name in sorted(_READY_STRENGTH_KEYS):
        _unit_float(strength[name], field=f"teacher-quality READY {name}")
    evidence_sha256 = _require_sha256(record["evidence_sha256"], field="teacher-quality READY evidence SHA-256")
    quality_weight = _unit_float(record["quality_weight"], field="teacher-quality READY quality weight")
    if quality_weight not in _QUALITY_WEIGHTS or quality_weight == 0:
        raise ValueError("teacher-quality READY quality weight is not an eligible tier")
    evidence = {key: record[key] for key in _QUALITY_RECORD_KEYS if key not in {"evidence_sha256", "quality_weight"}}
    if _sha256(_canonical(evidence)) != evidence_sha256:
        raise ValueError("teacher-quality READY evidence hash does not match primary evidence")
    return record_id, evidence_sha256, quality_weight


def _validate_approved_rule(value: object, *, source_hashes: Mapping[str, object]) -> dict[str, float]:
    rule = _exact_mapping(value, field="teacher-quality approved rule", keys=_APPROVED_RULE_KEYS)
    if rule["schema"] != "meta-specialist-teacher-quality-rule-v1" or rule["approval_status"] != "APPROVED":
        raise ValueError("teacher-quality READY rule is not approved")
    _nonempty_string(rule["rule_id"], field="teacher-quality READY rule_id")
    _nonempty_string(rule["rule_version"], field="teacher-quality READY rule_version")
    rule_file_sha256 = _require_sha256(rule["rule_file_sha256"], field="teacher-quality READY rule file")
    if rule_file_sha256 not in source_hashes.values():
        raise ValueError("teacher-quality READY approved rule is not source-hash anchored")
    assignments = rule["assignments"]
    if type(assignments) is not list or not assignments:
        raise ValueError("teacher-quality READY rule assignments are invalid")
    result: dict[str, float] = {}
    for value in assignments:
        assignment = _exact_mapping(value, field="teacher-quality rule assignment", keys=_RULE_ASSIGNMENT_KEYS)
        evidence_sha256 = _require_sha256(assignment["evidence_sha256"], field="teacher-quality rule evidence SHA-256")
        quality_weight = _unit_float(assignment["quality_weight"], field="teacher-quality rule quality weight")
        if evidence_sha256 in result or quality_weight not in _QUALITY_WEIGHTS or quality_weight == 0:
            raise ValueError("teacher-quality READY rule assignment is duplicate or ineligible")
        result[evidence_sha256] = quality_weight
    return result


def _ready_record_index(quality_records: list[object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for value in quality_records:
        if type(value) is not dict:
            raise ValueError("teacher-quality READY record is not an object")
        record_id = _require_sha256(value.get("record_id"), field="teacher-quality READY record_id")
        if record_id in result:
            raise ValueError("teacher-quality READY records duplicate identity/evidence")
        result[record_id] = value
    return result


def _validate_primary_artifacts(
    value: object, *, quality_records: list[object], source_hashes: Mapping[str, object], rule_file_sha256: str,
) -> dict[tuple[str, str], tuple[str, str]]:
    """Validate that every material field has one sealed physical-artifact reference."""
    if type(value) is not list:
        raise ValueError("teacher-quality READY primary artifacts must be a list")
    records = _ready_record_index(quality_records)
    expected_pairs = {(record_id, kind) for record_id in records for kind in _PRIMARY_ARTIFACT_KINDS}
    references: dict[tuple[str, str], tuple[str, str]] = {}
    source_names: set[str] = set()
    for item in value:
        reference = _exact_mapping(item, field="teacher-quality primary artifact", keys=_PRIMARY_ARTIFACT_KEYS)
        record_id = _require_sha256(reference["record_id"], field="teacher-quality primary artifact record_id")
        kind = reference["kind"]
        if kind not in _PRIMARY_ARTIFACT_KINDS:
            raise ValueError("teacher-quality primary artifact kind is invalid")
        source_name = _source_name(reference["source_name"], field="teacher-quality primary artifact source name")
        file_sha256 = _require_sha256(reference["file_sha256"], field="teacher-quality primary artifact file SHA-256")
        pair = (record_id, kind)
        if pair not in expected_pairs or pair in references or source_name in source_names:
            raise ValueError("teacher-quality primary artifacts are duplicate or do not cover READY records")
        if source_hashes.get(source_name) != file_sha256:
            raise ValueError("teacher-quality primary artifact is not source-hash anchored")
        references[pair] = (source_name, file_sha256)
        source_names.add(source_name)
    if set(references) != expected_pairs:
        raise ValueError("teacher-quality primary artifacts do not completely cover READY records")
    rule_names = set(source_hashes) - source_names
    if len(rule_names) != 1 or source_hashes[next(iter(rule_names))] != rule_file_sha256:
        raise ValueError("teacher-quality approved rule is not the sole remaining source hash")
    return references


def _validate_ready_manifest(manifest: Mapping[str, object]) -> None:
    if frozenset(manifest) != _READY_MANIFEST_KEYS:
        raise ValueError("teacher-quality READY manifest has an invalid key set")
    records_total, eligible, source_hashes = _validate_common_manifest(manifest)
    if manifest.get("status") != "READY" or manifest.get("theta0_allowed") is not True or manifest.get("authority_gap") is not None:
        raise ValueError("teacher-quality manifest does not have closed READY status")
    quality_records = manifest.get("quality_records")
    if type(quality_records) is not list or not quality_records or eligible != len(quality_records):
        raise ValueError("teacher-quality READY records are missing or inconsistent")
    if records_total != eligible + len(manifest["exclusions"]):
        raise ValueError("teacher-quality READY total does not match eligible/excluded records")
    assignments = _validate_approved_rule(manifest.get("approved_rule"), source_hashes=source_hashes)
    seen_record_ids: set[str] = set()
    seen_evidence: dict[str, float] = {}
    for value in quality_records:
        record_id, evidence_sha256, quality_weight = _validate_ready_record(value)
        if record_id in seen_record_ids or evidence_sha256 in seen_evidence:
            raise ValueError("teacher-quality READY records duplicate identity/evidence")
        seen_record_ids.add(record_id)
        seen_evidence[evidence_sha256] = quality_weight
    if assignments != seen_evidence:
        raise ValueError("teacher-quality READY quality is not derived from the approved rule")
    approved_rule = manifest["approved_rule"]
    assert type(approved_rule) is dict
    _validate_primary_artifacts(
        manifest.get("primary_artifacts"), quality_records=quality_records, source_hashes=source_hashes,
        rule_file_sha256=str(approved_rule["rule_file_sha256"]),
    )


def _read_validated_manifest(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
) -> dict[str, object]:
    raw = _read_anchored(manifest_path, expected_sha256=expected_manifest_file_sha256, name="manifest file")
    manifest = _strict_json(raw, name="teacher-quality manifest")
    if raw != _canonical(manifest):
        raise ValueError("teacher-quality manifest bytes are not canonical")
    if manifest.get("schema") != _MANIFEST_SCHEMA:
        raise ValueError("teacher-quality manifest schema is invalid")
    supplied = _require_sha256(manifest.get("manifest_sha256"), field="teacher-quality manifest SHA-256")
    body = dict(manifest)
    del body["manifest_sha256"]
    if _sha256(_canonical(body)) != supplied:
        raise ValueError("teacher-quality manifest self hash does not match")
    status = manifest.get("status")
    if status == "AUTHORITY_GAP":
        _validate_authority_gap_manifest(manifest)
    elif status == "READY":
        _validate_ready_manifest(manifest)
    else:
        raise ValueError("teacher-quality manifest status is invalid")
    return manifest


def _validate_ready_physical_authority(
    manifest: Mapping[str, object], *, approved_rule_path: str | Path,
    expected_approved_rule_file_sha256: str,
    primary_evidence_paths: Mapping[str, str | Path],
    expected_primary_evidence_file_sha256: Mapping[str, str],
) -> None:
    """Reopen every primary artifact under caller-owned byte anchors.

    The manifest declares what must be proven, but cannot turn its own strings
    into authority.  Each result/provenance field is therefore reloaded from a
    physical canonical artifact whose raw-file digest is supplied by the
    caller, before the embedded READY row is accepted.
    """
    if type(primary_evidence_paths) is not dict or type(expected_primary_evidence_file_sha256) is not dict:
        raise ValueError("teacher-quality primary artifact paths and SHA anchors must be plain mappings")
    approved_rule = manifest.get("approved_rule")
    quality_records = manifest.get("quality_records")
    source_hashes = manifest.get("source_hashes")
    if type(approved_rule) is not dict or type(quality_records) is not list or type(source_hashes) is not dict:
        raise ValueError("teacher-quality READY manifest is incomplete")
    rule_sha256 = _require_sha256(
        expected_approved_rule_file_sha256, field="expected approved rule file SHA-256",
    )
    if approved_rule.get("rule_file_sha256") != rule_sha256:
        raise ValueError("teacher-quality approved rule does not match caller-owned SHA anchor")
    rule = _canonical_anchored_object(
        approved_rule_path, expected_sha256=rule_sha256, name="approved rule artifact",
    )
    rule = _exact_mapping(rule, field="approved rule artifact", keys=_RULE_ARTIFACT_KEYS)
    embedded_rule = {key: approved_rule[key] for key in _RULE_ARTIFACT_KEYS}
    if rule != embedded_rule:
        raise ValueError("teacher-quality embedded rule is not re-derived from the approved rule artifact")

    references = _validate_primary_artifacts(
        manifest.get("primary_artifacts"), quality_records=quality_records, source_hashes=source_hashes,
        rule_file_sha256=rule_sha256,
    )
    required_names = {source_name for source_name, _digest in references.values()}
    if set(primary_evidence_paths) != required_names or set(expected_primary_evidence_file_sha256) != required_names:
        raise ValueError("teacher-quality caller primary artifact closure is incomplete or has extra entries")
    records = _ready_record_index(quality_records)
    for (record_id, kind), (source_name, manifest_sha256) in references.items():
        expected_sha256 = _require_sha256(
            expected_primary_evidence_file_sha256[source_name],
            field=f"expected primary artifact {source_name} SHA-256",
        )
        if expected_sha256 != manifest_sha256:
            raise ValueError("teacher-quality primary artifact does not match caller-owned SHA anchor")
        artifact = _canonical_anchored_object(
            primary_evidence_paths[source_name], expected_sha256=expected_sha256,
            name=f"primary artifact {source_name}",
        )
        artifact = _exact_mapping(
            artifact, field=f"primary artifact {source_name}", keys=_PRIMARY_ARTIFACT_CONTENT_KEYS,
        )
        record = records[record_id]
        if (
            artifact["schema"] != _PRIMARY_ARTIFACT_SCHEMA
            or artifact["kind"] != kind
            or artifact["record_id"] != record_id
            or artifact["content_hash"] != record["content_hash"]
            or artifact["value"] != record[kind]
        ):
            raise ValueError("teacher-quality primary artifact does not re-derive the sealed READY record")


def _atomic_write(path: Path, raw: bytes) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def seal_teacher_quality_v3(
    evidence_path: str | Path, *, expected_evidence_file_sha256: str, output_path: str | Path,
) -> dict[str, object]:
    """Anchor, screen, and atomically seal a teacher-quality authority-gap manifest."""
    raw = _read_anchored(evidence_path, expected_sha256=expected_evidence_file_sha256, name="evidence file")
    evidence = _strict_json(raw, name="teacher-quality evidence")
    lane, records = _validate_evidence(evidence)
    source = Path(evidence_path)
    body = _manifest_body(lane=lane, evidence_name=source.name, evidence_sha256=expected_evidence_file_sha256, records=records)
    manifest = {**body, "manifest_sha256": _sha256(_canonical(body))}
    _atomic_write(Path(output_path), _canonical(manifest))
    return manifest


def read_teacher_quality_manifest_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
) -> dict[str, object]:
    """Strictly load a manifest only after verifying its out-of-band file SHA."""
    manifest = _read_validated_manifest(
        manifest_path, expected_manifest_file_sha256=expected_manifest_file_sha256,
    )
    if manifest["status"] != "AUTHORITY_GAP":
        raise ValueError("generic teacher-quality reader only returns fail-closed authority gaps")
    return manifest


def read_ready_teacher_quality_manifest_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
    expected_manifest_sha256: str, approved_rule_path: str | Path,
    expected_approved_rule_file_sha256: str,
    primary_evidence_paths: Mapping[str, str | Path],
    expected_primary_evidence_file_sha256: Mapping[str, str],
) -> dict[str, object]:
    """Return the sole θ0-ready contract after all external and nested checks."""
    expected = _require_sha256(expected_manifest_sha256, field="expected teacher-quality manifest SHA-256")
    manifest = _read_validated_manifest(
        manifest_path, expected_manifest_file_sha256=expected_manifest_file_sha256,
    )
    if manifest["manifest_sha256"] != expected or manifest["status"] != "READY":
        raise ValueError("teacher-quality manifest is not the expected READY authority")
    _validate_ready_physical_authority(
        manifest, approved_rule_path=approved_rule_path,
        expected_approved_rule_file_sha256=expected_approved_rule_file_sha256,
        primary_evidence_paths=primary_evidence_paths,
        expected_primary_evidence_file_sha256=expected_primary_evidence_file_sha256,
    )
    return manifest


def require_theta0_teacher_quality_v3(manifest: Mapping[str, object]) -> None:
    """Always reject θ0 until a separately approved, attested weight rule exists."""
    del manifest
    raise ValueError("teacher quality authority gap blocks recurrent θ0")


__all__ = [
    "read_teacher_quality_manifest_v3",
    "read_ready_teacher_quality_manifest_v3",
    "require_theta0_teacher_quality_v3",
    "seal_teacher_quality_v3",
]
