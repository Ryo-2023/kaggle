"""Scalable, fail-closed teacher-quality authority (v2).

No actual rule digest is approved yet.  Consequently this module can validate
and seal candidate evidence, but its production API cannot currently emit a
READY authority.  Adding an approval requires a reviewed code/canonical
change to ``_TRUSTED_RULE_FILE_SHA256_V2``; caller-supplied ``APPROVED`` text
and its matching caller-supplied SHA are never a trust root.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO, Iterator


_HEX = frozenset("0123456789abcdef")
_RULE_SCHEMA = "meta-specialist-teacher-quality-rule-v2"
_BUNDLE_SCHEMA = "meta-specialist-teacher-quality-primary-bundle-v2"
_ATTEMPT_SCHEMA = "meta-specialist-teacher-quality-attempt-v2"
_MANIFEST_SCHEMA = "meta-specialist-teacher-quality-manifest-v2"

# Deliberately empty.  Actual approval must be introduced as a reviewed source
# change.  There is no fixture-only or caller-extensible production escape.
_TRUSTED_RULE_FILE_SHA256_V2: frozenset[str] = frozenset()

_OVERLAY_KEYS = frozenset({
    "record_id", "content_hash", "teacher_id", "source_artifact_sha256",
    "evidence_class_sha256", "quality_weight", "exclusion_reason",
})
_RULE_KEYS = frozenset({
    "schema", "approval_status", "rule_id", "rule_version", "allowed_weights",
    "min_logical_games", "max_fault_rate", "unavailable_strength_policy", "assignments",
})
_BUNDLE_KEYS = frozenset({
    "schema", "lane", "teacher_id", "teacher_revision", "policy", "deck_bytes_sha256",
    "source_permission_sha256", "current_pool", "logical_game_matrix",
    "per_attempt_fault_provenance", "result_aggregate", "result_files", "strength",
    "source_artifact_sha256",
})
_ATTEMPT_KEYS = frozenset({
    "schema", "lane", "teacher_id", "teacher_revision", "logical_game_id",
    "opponent_id", "seat", "repetition", "attempt_index", "fault", "outcome",
})
_MANIFEST_KEYS = frozenset({
    "schema", "lane", "status", "theta0_allowed", "authority_gap", "rule",
    "primary_bundle", "overlay", "row_count", "eligible_record_count",
    "excluded_record_count", "weight_histogram", "manifest_sha256",
})


@dataclass(frozen=True)
class TeacherQualityOverlayRowV2:
    record_id: str
    content_hash: str
    teacher_id: str
    source_artifact_sha256: str
    evidence_class_sha256: str
    quality_weight: float
    exclusion_reason: str | None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON value {value}")


def _strict_json(raw: bytes, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("duplicate JSON key", "non-finite JSON value")):
            raise
        raise ValueError(f"{name} is not strict JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    if _canonical(value) != raw:
        raise ValueError(f"{name} is not canonical JSON")
    return value


class _DigestingReader:
    """Read one parse pass while hashing the exact bytes the parser consumes."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._digest = hashlib.sha256()
        self._reached_eof = False

    def read(self, size: int = -1) -> bytes:
        raw = self._handle.read(size)
        self._digest.update(raw)
        if size < 0 or (size != 0 and not raw):
            self._reached_eof = True
        return raw

    def readline(self, size: int = -1) -> bytes:
        raw = self._handle.readline(size)
        self._digest.update(raw)
        if not raw:
            self._reached_eof = True
        return raw

    @property
    def reached_eof(self) -> bool:
        return self._reached_eof

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _descriptor_identity(item: os.stat_result) -> tuple[int, int, int, int, int]:
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


@contextmanager
def _open_anchored(path: str | Path, expected_sha256: str, name: str) -> Iterator[_DigestingReader]:
    """Hash a regular file before parsing, then keep the descriptor pinned."""
    _require_sha(expected_sha256, f"expected {name}")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("anchored authority access requires O_NOFOLLOW")
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        digest = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        anchored = os.fstat(handle.fileno())
        if _descriptor_identity(before) != _descriptor_identity(anchored):
            raise ValueError(f"{name} changed during SHA-256 read")
        if digest.hexdigest() != expected_sha256:
            raise ValueError(f"external {name} SHA-256 does not match")
        handle.seek(0)
        parsed = _DigestingReader(handle)
        yield parsed
        after = os.fstat(handle.fileno())
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise ValueError(f"{name} changed during parse")
        if not parsed.reached_eof:
            raise ValueError(f"{name} parse did not consume through EOF")
        if parsed.hexdigest() != expected_sha256:
            raise ValueError(f"{name} parse SHA-256 does not match")


def _read_anchored(path: str | Path, expected_sha256: str, name: str) -> bytes:
    with _open_anchored(path, expected_sha256, name) as handle:
        return handle.read()


def _exact(value: object, keys: frozenset[str], name: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise ValueError(f"{name} has an invalid closed key set")
    return value


def _string(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _weight(value: object, field: str) -> float:
    if type(value) is bool or type(value) not in {int, float} or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError(f"{field} must be finite in [0,1]")
    return float(value)


def _safe_basename(value: object, field: str) -> str:
    name = _string(value, field)
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"{field} escapes manifest directory")
    return name


def _validate_rule(value: dict[str, object]) -> dict[str, object]:
    rule = _exact(value, _RULE_KEYS, "teacher-quality rule")
    if rule["schema"] != _RULE_SCHEMA or rule["approval_status"] != "APPROVED":
        raise ValueError("teacher-quality rule is not approved v2")
    _string(rule["rule_id"], "rule_id")
    _string(rule["rule_version"], "rule_version")
    allowed = rule["allowed_weights"]
    if type(allowed) is not list or not allowed:
        raise ValueError("allowed_weights must be nonempty")
    allowed_values = {_weight(item, "allowed weight") for item in allowed}
    if len(allowed_values) != len(allowed):
        raise ValueError("allowed_weights contains duplicates")
    _positive_int(rule["min_logical_games"], "min_logical_games")
    _weight(rule["max_fault_rate"], "max_fault_rate")
    if rule["unavailable_strength_policy"] not in {"ALLOW", "EXCLUDE"}:
        raise ValueError("unavailable_strength_policy is invalid")
    assignments = rule["assignments"]
    if type(assignments) is not list or not assignments:
        raise ValueError("assignments must be nonempty")
    seen: set[tuple[str, str]] = set()
    for raw in assignments:
        item = _exact(raw, frozenset({"teacher_id", "teacher_revision", "quality_weight"}), "rule assignment")
        identity = (_string(item["teacher_id"], "teacher_id"), _string(item["teacher_revision"], "teacher_revision"))
        if identity in seen:
            raise ValueError("duplicate rule assignment")
        seen.add(identity)
        if _weight(item["quality_weight"], "assignment quality_weight") not in allowed_values:
            raise ValueError("assignment weight is not allowed")
    return rule


def _validate_bundle(value: dict[str, object]) -> dict[str, object]:
    bundle = _exact(value, _BUNDLE_KEYS, "teacher-quality primary bundle")
    if bundle["schema"] != _BUNDLE_SCHEMA:
        raise ValueError("teacher-quality primary bundle schema is invalid")
    for field in ("lane", "teacher_id", "teacher_revision"):
        _string(bundle[field], field)
    policy = _exact(bundle["policy"], frozenset({"implementation_sha256", "version", "usage_boundary"}), "policy")
    _require_sha(policy["implementation_sha256"], "policy implementation SHA-256")
    _string(policy["version"], "policy version")
    _string(policy["usage_boundary"], "policy usage boundary")
    for field in ("deck_bytes_sha256", "source_permission_sha256", "source_artifact_sha256"):
        _require_sha(bundle[field], field)
    pool = _exact(bundle["current_pool"], frozenset({"schedule_sha256", "pool_sha256", "engine_sha256", "source_commit_sha256"}), "current_pool")
    for field, item in pool.items():
        _require_sha(item, f"current_pool {field}")
    matrix = _exact(bundle["logical_game_matrix"], frozenset({"logical_games", "teachers", "opponents", "seats", "repetitions"}), "logical_game_matrix")
    for field, item in matrix.items():
        _positive_int(item, f"logical_game_matrix {field}")
    if matrix["teachers"] != 1 or matrix["opponents"] != 6 or matrix["seats"] != 2:
        raise ValueError("logical_game_matrix must be one teacher x six opponents x two seats")
    if matrix["logical_games"] != matrix["opponents"] * matrix["seats"] * matrix["repetitions"]:
        raise ValueError("logical_game_matrix product does not match logical_games")
    faults = _exact(bundle["per_attempt_fault_provenance"], frozenset({"attempts", "faults", "result_sha256"}), "per_attempt_fault_provenance")
    _positive_int(faults["attempts"], "attempts")
    _nonnegative_int(faults["faults"], "faults")
    if faults["faults"] > faults["attempts"]:
        raise ValueError("faults exceed attempts")
    _require_sha(faults["result_sha256"], "result evidence SHA-256")
    aggregate = _exact(bundle["result_aggregate"], frozenset({"games", "wins", "draws", "losses"}), "result_aggregate")
    for field, item in aggregate.items():
        _nonnegative_int(item, f"result_aggregate {field}")
    results = bundle["result_files"]
    if type(results) is not list or not results or len(results) > 64:
        raise ValueError("result_files must contain 1..64 entries")
    names: set[str] = set()
    for raw in results:
        item = _exact(raw, frozenset({"basename", "file_sha256"}), "result file")
        name = _safe_basename(item["basename"], "result file basename")
        if name in names:
            raise ValueError("duplicate result file basename")
        names.add(name)
        _require_sha(item["file_sha256"], "result file SHA-256")
    if faults["result_sha256"] != _sha(_canonical(results)):
        raise ValueError("fault result_sha256 does not bind result_files")
    strength = bundle["strength"]
    allowed_strength_keys = {frozenset({"status"}), frozenset({"status", "confidence", "agreement", "search_strength"})}
    if type(strength) is not dict or frozenset(strength) not in allowed_strength_keys or strength.get("status") not in {"available", "unavailable"}:
        raise ValueError("strength has an invalid closed schema")
    if strength["status"] == "available":
        if len(strength) != 4:
            raise ValueError("available strength lacks values")
        for field in ("confidence", "agreement", "search_strength"):
            _weight(strength[field], field)
    elif len(strength) != 1:
        raise ValueError("unavailable strength must not invent values")
    return bundle


def _jsonl_rows(handle: _DigestingReader, name: str) -> Iterator[dict[str, object]]:
    line_number = 0
    while raw := handle.readline():
        line_number += 1
        if b"\r" in raw:
            raise ValueError(f"{name} uses CRLF or carriage returns")
        if not raw.endswith(b"\n"):
            raise ValueError(f"{name} final row lacks LF")
        body = raw[:-1]
        if not body:
            raise ValueError(f"{name} contains an empty row")
        yield _strict_json(body, f"{name} row {line_number}")
    if line_number == 0:
        raise ValueError(f"{name} must be nonempty")


def _logical_game_id(bundle: dict[str, object], opponent_id: str, seat: int, repetition: int) -> str:
    return _sha(_canonical({
        "lane": bundle["lane"], "teacher_id": bundle["teacher_id"], "teacher_revision": bundle["teacher_revision"],
        "opponent_id": opponent_id, "seat": seat, "repetition": repetition,
    }))


def _scan_result_ledgers(directory: Path, bundle: dict[str, object]) -> None:
    matrix = bundle["logical_game_matrix"]
    declared_fault = bundle["per_attempt_fault_provenance"]
    declared_result = bundle["result_aggregate"]
    refs = bundle["result_files"]
    assert type(matrix) is dict and type(declared_fault) is dict and type(declared_result) is dict and type(refs) is list
    attempts = faults = games = wins = draws = losses = 0
    opponent_ids: set[str] = set()
    previous_key: tuple[str, int, int, int] | None = None
    current_game: tuple[str, int, int] | None = None
    current_last_fault: object = None
    current_last_outcome: object = None

    def finish_game() -> None:
        nonlocal games, wins, draws, losses
        if current_game is None:
            return
        if current_last_fault is not None or current_last_outcome not in {"win", "draw", "loss"}:
            raise ValueError("logical game lacks a terminal non-fault result")
        games += 1
        if current_last_outcome == "win":
            wins += 1
        elif current_last_outcome == "draw":
            draws += 1
        else:
            losses += 1

    for ref in refs:
        assert type(ref) is dict
        path = directory / _safe_basename(ref["basename"], "result file basename")
        with _open_anchored(path, _require_sha(ref["file_sha256"], "result file SHA-256"), "result file") as handle:
            for row in _jsonl_rows(handle, "result ledger"):
                item = _exact(row, _ATTEMPT_KEYS, "result ledger attempt")
                if item["schema"] != _ATTEMPT_SCHEMA or item["lane"] != bundle["lane"] or item["teacher_id"] != bundle["teacher_id"] or item["teacher_revision"] != bundle["teacher_revision"]:
                    raise ValueError("result ledger authority identity mismatch")
                opponent = _string(item["opponent_id"], "opponent_id")
                opponent_ids.add(opponent)
                if len(opponent_ids) > 6:
                    raise ValueError("result ledger contains extra opponents")
                seat = item["seat"]
                repetition = item["repetition"]
                attempt_index = item["attempt_index"]
                if type(seat) is not int or type(seat) is bool or seat not in {0, 1}:
                    raise ValueError("result ledger seat is invalid")
                if type(repetition) is not int or repetition < 0 or repetition >= matrix["repetitions"]:
                    raise ValueError("result ledger repetition is invalid")
                if type(attempt_index) is not int or attempt_index < 0:
                    raise ValueError("result ledger attempt_index is invalid")
                key = (opponent, seat, repetition, attempt_index)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("duplicate attempt or invalid attempt order")
                game_key = key[:3]
                if game_key != current_game:
                    finish_game()
                    if attempt_index != 0:
                        raise ValueError("first attempt_index must be zero")
                    current_game = game_key
                else:
                    assert previous_key is not None
                    if attempt_index != previous_key[3] + 1 or current_last_fault is None:
                        raise ValueError("retry attempt order or fault provenance is invalid")
                if item["logical_game_id"] != _logical_game_id(bundle, opponent, seat, repetition):
                    raise ValueError("logical_game_id does not match closed game identity")
                fault = item["fault"]
                outcome = item["outcome"]
                if fault is None:
                    if outcome not in {"win", "draw", "loss"}:
                        raise ValueError("non-fault attempt outcome is invalid")
                else:
                    provenance = _exact(fault, frozenset({"kind", "source_exception", "exit_code"}), "fault provenance")
                    _string(provenance["kind"], "fault kind")
                    if provenance["source_exception"] is not None and type(provenance["source_exception"]) is not str:
                        raise ValueError("fault provenance source_exception is invalid")
                    if provenance["exit_code"] is not None and type(provenance["exit_code"]) is not int:
                        raise ValueError("fault provenance exit_code is invalid")
                    if outcome is not None:
                        raise ValueError("faulted attempt cannot claim an outcome")
                    faults += 1
                attempts += 1
                previous_key = key
                current_last_fault = fault
                current_last_outcome = outcome
    finish_game()
    if len(opponent_ids) != matrix["opponents"] or games != matrix["logical_games"]:
        raise ValueError("result ledger missing or extra logical games")
    actual_fault = {"attempts": attempts, "faults": faults, "result_sha256": _sha(_canonical(refs))}
    if actual_fault != declared_fault:
        raise ValueError("attempt/fault aggregate does not match result ledger")
    actual_result = {"games": games, "wins": wins, "draws": draws, "losses": losses}
    if actual_result != declared_result:
        raise ValueError("result aggregate does not match result ledger")


def _expected_weight(rule: dict[str, object], bundle: dict[str, object]) -> tuple[float, str | None]:
    strength = bundle["strength"]
    matrix = bundle["logical_game_matrix"]
    faults = bundle["per_attempt_fault_provenance"]
    assert type(strength) is dict and type(matrix) is dict and type(faults) is dict
    if strength["status"] == "unavailable" and rule["unavailable_strength_policy"] == "EXCLUDE":
        return 0.0, "strength_unavailable"
    if matrix["logical_games"] < rule["min_logical_games"]:
        return 0.0, "insufficient_logical_games"
    if faults["faults"] / faults["attempts"] > float(rule["max_fault_rate"]):
        return 0.0, "fault_rate_exceeds_rule"
    for raw in rule["assignments"]:
        assert type(raw) is dict
        if raw["teacher_id"] == bundle["teacher_id"] and raw["teacher_revision"] == bundle["teacher_revision"]:
            return _weight(raw["quality_weight"], "assignment quality_weight"), None
    return 0.0, "teacher_assignment_missing"


def _validated_overlay_rows(
    handle: _DigestingReader, rule: dict[str, object], bundle: dict[str, object],
) -> Iterator[TeacherQualityOverlayRowV2]:
    evidence_class = _sha(_canonical(bundle))
    expected_weight, expected_reason = _expected_weight(rule, bundle)
    previous = ""
    for row in _jsonl_rows(handle, "overlay"):
        item = _exact(row, _OVERLAY_KEYS, "overlay row")
        record_id = _require_sha(item["record_id"], "overlay record_id")
        content_hash = _require_sha(item["content_hash"], "overlay content_hash")
        if previous and record_id.encode("ascii") <= previous.encode("ascii"):
            reason = "duplicate record_id" if record_id == previous else "record_id rows are not sorted"
            raise ValueError(f"overlay {reason}")
        previous = record_id
        if item["teacher_id"] != bundle["teacher_id"] or item["source_artifact_sha256"] != bundle["source_artifact_sha256"] or item["evidence_class_sha256"] != evidence_class:
            raise ValueError("overlay primary authority identity mismatch")
        weight = _weight(item["quality_weight"], "overlay quality_weight")
        if weight != expected_weight:
            raise ValueError("overlay quality_weight does not match deterministic rule derivation")
        if item["exclusion_reason"] != expected_reason:
            raise ValueError("overlay exclusion_reason does not match deterministic rule derivation")
        yield TeacherQualityOverlayRowV2(
            record_id=record_id,
            content_hash=content_hash,
            teacher_id=str(item["teacher_id"]),
            source_artifact_sha256=str(item["source_artifact_sha256"]),
            evidence_class_sha256=str(item["evidence_class_sha256"]),
            quality_weight=weight,
            exclusion_reason=expected_reason,
        )


def _scan_overlay(handle: _DigestingReader, rule: dict[str, object], bundle: dict[str, object]) -> dict[str, object]:
    count = eligible = 0
    histogram: Counter[str] = Counter()
    for item in _validated_overlay_rows(handle, rule, bundle):
        count += 1
        eligible += int(item.quality_weight > 0)
        histogram[format(item.quality_weight, ".15g")] += 1
    return {
        "row_count": count, "eligible_record_count": eligible,
        "excluded_record_count": count - eligible, "weight_histogram": dict(sorted(histogram.items())),
    }


def _overlay_row_payload(item: TeacherQualityOverlayRowV2) -> dict[str, object]:
    return {
        "record_id": item.record_id,
        "content_hash": item.content_hash,
        "teacher_id": item.teacher_id,
        "source_artifact_sha256": item.source_artifact_sha256,
        "evidence_class_sha256": item.evidence_class_sha256,
        "quality_weight": item.quality_weight,
        "exclusion_reason": item.exclusion_reason,
    }


def _derive(directory: Path, rule_raw: bytes, rule_file_sha256: str, bundle_raw: bytes, overlay_handle: _DigestingReader) -> dict[str, object]:
    rule = _validate_rule(_strict_json(rule_raw, "rule"))
    bundle = _validate_bundle(_strict_json(bundle_raw, "primary bundle"))
    _scan_result_ledgers(directory, bundle)
    overlay_summary = _scan_overlay(overlay_handle, rule, bundle)
    gaps: list[str] = []
    if rule_file_sha256 not in _TRUSTED_RULE_FILE_SHA256_V2:
        gaps.append("trusted_rule_digest_missing")
    if overlay_summary["excluded_record_count"]:
        gaps.append("excluded_quality_rows")
    status = "AUTHORITY_GAP" if gaps else "READY"
    return {
        "lane": bundle["lane"], "status": status, "theta0_allowed": not gaps,
        "authority_gap": None if not gaps else {"code": gaps[0], "detail": ",".join(gaps)},
        **overlay_summary,
    }


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def seal_teacher_quality_v2(*, rule_path: str | Path, expected_rule_file_sha256: str, primary_bundle_path: str | Path, expected_primary_bundle_file_sha256: str, overlay_path: str | Path, expected_overlay_file_sha256: str, output_path: str | Path) -> dict[str, object]:
    output = Path(output_path)
    directory = output.parent.resolve()
    supplied = (Path(rule_path), Path(primary_bundle_path), Path(overlay_path))
    if any(path.parent.resolve() != directory for path in supplied):
        raise ValueError("rule, primary bundle, and overlay must be manifest siblings")
    rule_raw = _read_anchored(rule_path, expected_rule_file_sha256, "rule file")
    bundle_raw = _read_anchored(primary_bundle_path, expected_primary_bundle_file_sha256, "primary bundle file")
    with _open_anchored(overlay_path, expected_overlay_file_sha256, "overlay file") as overlay_handle:
        summary = _derive(directory, rule_raw, expected_rule_file_sha256, bundle_raw, overlay_handle)
    manifest: dict[str, object] = {
        "schema": _MANIFEST_SCHEMA, **summary,
        "rule": {"basename": _safe_basename(Path(rule_path).name, "rule basename"), "file_sha256": expected_rule_file_sha256},
        "primary_bundle": {"basename": _safe_basename(Path(primary_bundle_path).name, "primary bundle basename"), "file_sha256": expected_primary_bundle_file_sha256},
        "overlay": {"basename": _safe_basename(Path(overlay_path).name, "overlay basename"), "file_sha256": expected_overlay_file_sha256, "row_count": summary["row_count"]},
    }
    manifest["manifest_sha256"] = _sha(_canonical(manifest))
    _atomic_write(output, _canonical(manifest))
    return manifest


def _sidecar_path(directory: Path, ref: dict[str, object], name: str) -> tuple[Path, str]:
    basename = _safe_basename(ref["basename"], f"{name} basename")
    return directory / basename, _require_sha(ref["file_sha256"], f"{name} file SHA-256")


def _read_manifest_header(
    path: str | Path, *, expected_manifest_file_sha256: str, expected_manifest_sha256: str,
) -> tuple[dict[str, object], Path, tuple[Path, str], tuple[Path, str], tuple[Path, str]]:
    raw = _read_anchored(path, expected_manifest_file_sha256, "manifest file")
    manifest = _exact(_strict_json(raw, "teacher-quality manifest"), _MANIFEST_KEYS, "teacher-quality manifest")
    if manifest["schema"] != _MANIFEST_SCHEMA:
        raise ValueError("teacher-quality manifest schema is invalid")
    _require_sha(expected_manifest_sha256, "expected manifest")
    claimed = manifest.pop("manifest_sha256")
    try:
        if claimed != expected_manifest_sha256 or _sha(_canonical(manifest)) != claimed:
            raise ValueError("manifest self SHA-256 does not match")
    finally:
        manifest["manifest_sha256"] = claimed
    directory = Path(path).parent.resolve()
    rule_ref = _exact(manifest["rule"], frozenset({"basename", "file_sha256"}), "manifest rule")
    bundle_ref = _exact(manifest["primary_bundle"], frozenset({"basename", "file_sha256"}), "manifest primary bundle")
    overlay_ref = _exact(manifest["overlay"], frozenset({"basename", "file_sha256", "row_count"}), "manifest overlay")
    rule_path, rule_sha = _sidecar_path(directory, rule_ref, "rule file")
    bundle_path, bundle_sha = _sidecar_path(directory, bundle_ref, "primary bundle file")
    overlay_path, overlay_sha = _sidecar_path(directory, overlay_ref, "overlay file")
    return manifest, directory, (rule_path, rule_sha), (bundle_path, bundle_sha), (overlay_path, overlay_sha)


def read_teacher_quality_manifest_v2(path: str | Path, *, expected_manifest_file_sha256: str, expected_manifest_sha256: str) -> dict[str, object]:
    manifest, directory, rule_source, bundle_source, overlay_source = _read_manifest_header(
        path,
        expected_manifest_file_sha256=expected_manifest_file_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    rule_path, rule_sha = rule_source
    bundle_path, bundle_sha = bundle_source
    overlay_path, overlay_sha = overlay_source
    overlay_ref = _exact(manifest["overlay"], frozenset({"basename", "file_sha256", "row_count"}), "manifest overlay")
    rule_raw = _read_anchored(rule_path, rule_sha, "rule file")
    bundle_raw = _read_anchored(bundle_path, bundle_sha, "primary bundle file")
    with _open_anchored(overlay_path, overlay_sha, "overlay file") as overlay_handle:
        summary = _derive(directory, rule_raw, rule_sha, bundle_raw, overlay_handle)
    if overlay_ref["row_count"] != summary["row_count"]:
        raise ValueError("overlay row count does not match")
    for key, value in summary.items():
        if manifest.get(key) != value:
            raise ValueError(f"manifest {key} does not match sidecar authority")
    return manifest


def stream_ready_teacher_quality_overlay_v2(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
    expected_manifest_sha256: str,
) -> Iterator[TeacherQualityOverlayRowV2]:
    """Stream a READY overlay from its externally anchored manifest only."""
    manifest, directory, rule_source, bundle_source, overlay_source = _read_manifest_header(
        manifest_path,
        expected_manifest_file_sha256=expected_manifest_file_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if manifest["status"] != "READY" or manifest["theta0_allowed"] is not True or manifest["authority_gap"] is not None:
        raise ValueError("teacher-quality manifest is not READY")
    overlay_ref = _exact(manifest["overlay"], frozenset({"basename", "file_sha256", "row_count"}), "manifest overlay")
    rule_path, rule_sha = rule_source
    bundle_path, bundle_sha = bundle_source
    overlay_path, overlay_sha = overlay_source
    rule = _validate_rule(_strict_json(_read_anchored(rule_path, rule_sha, "rule file"), "rule"))
    bundle = _validate_bundle(_strict_json(_read_anchored(bundle_path, bundle_sha, "primary bundle file"), "primary bundle"))
    _scan_result_ledgers(directory, bundle)
    if rule_sha not in _TRUSTED_RULE_FILE_SHA256_V2:
        raise ValueError("teacher-quality manifest is not backed by a trusted rule")

    with tempfile.TemporaryFile(mode="w+b", prefix="teacher-quality-overlay-v2-") as spool:
        spool_stat = os.fstat(spool.fileno())
        if not stat.S_ISREG(spool_stat.st_mode) or stat.S_IMODE(spool_stat.st_mode) & 0o077:
            raise ValueError("teacher-quality private overlay spool is not a private regular file")
        count = 0
        histogram: Counter[str] = Counter()
        with _open_anchored(overlay_path, overlay_sha, "overlay file") as overlay_handle:
            for row in _validated_overlay_rows(overlay_handle, rule, bundle):
                if row.quality_weight <= 0 or row.exclusion_reason is not None:
                    raise ValueError("READY overlay contains an excluded row")
                spool.write(_canonical(_overlay_row_payload(row)) + b"\n")
                count += 1
                histogram[format(row.quality_weight, ".15g")] += 1

        summary = {
            "lane": bundle["lane"], "status": "READY", "theta0_allowed": True,
            "authority_gap": None, "row_count": count, "eligible_record_count": count,
            "excluded_record_count": 0, "weight_histogram": dict(sorted(histogram.items())),
        }
        if overlay_ref["row_count"] != count:
            raise ValueError("READY overlay row count does not match manifest authority")
        for key, value in summary.items():
            if manifest.get(key) != value:
                raise ValueError(f"manifest {key} does not match sidecar authority")

        spool.flush()
        os.fsync(spool.fileno())
        spool.seek(0)
        spool_reader = _DigestingReader(spool)
        for row in _validated_overlay_rows(spool_reader, rule, bundle):
            yield row


__all__ = [
    "TeacherQualityOverlayRowV2",
    "read_teacher_quality_manifest_v2",
    "seal_teacher_quality_v2",
    "stream_ready_teacher_quality_overlay_v2",
]
