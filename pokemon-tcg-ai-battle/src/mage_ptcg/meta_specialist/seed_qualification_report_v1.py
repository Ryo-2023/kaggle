"""Content-addressed report of one real CABT qualification pass over seed decks.

This module records *what actually happened* when the 15 fixed-lane seed deck
candidates in ``configs/meta_specialist/seed_candidates_v1.json`` were run
through the real CABT legality probe (``cabt_legality_v1.py``) and
``decks.qualify_deck_asset``.  It never runs CABT itself, never calls
``decks.qualify_deck_asset``, and never decides an outcome -- callers do that
and hand this module only already-decided, JSON-safe per-candidate records.
The module's job is strictly to validate that those records are internally
consistent (a ``qualified`` outcome only ever carries a real, nonempty CABT
probe status/evidence and asset id; a ``failed``/``not_run`` outcome always
carries a reason) and to publish them as one canonical, self-verifying
artifact -- so a report on disk cannot silently drift from what was recorded,
and cannot claim a qualification that was never actually attested.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Mapping

from mage_ptcg.continuous_league.contracts import content_id, require_sha256
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
)


SEED_QUALIFICATION_REPORT_SCHEMA_V1 = "meta-specialist-seed-qualification-report-v1"
MAX_SEED_QUALIFICATION_REPORT_BYTES_V1 = 4 * 1024 * 1024
EXPECTED_CANDIDATE_COUNT_V1 = 15

_ID_DOMAIN_V1 = "mage_ptcg:specialist-seed-qualification-report-id:v1"
_CONTENT_DOMAIN_V1 = "mage_ptcg:specialist-seed-qualification-report-content:v1"

_REPORT_KEYS_V1 = frozenset({
    "schema_version", "report_id", "content_hash",
    "registry_content_sha256", "card_database_sha256", "card_vocabulary_sha256",
    "archetype_registry_schema_version",
    "cabt_probe_seed", "cabt_probe_max_steps",
    "generated_time_utc",
    "candidate_count", "qualified_count", "failed_count", "not_run_count",
    "candidates",
})
_CANDIDATE_KEYS_V1 = frozenset({
    "runtime_id", "priority", "deck_identity", "asset_class",
    "materialization_status", "outcome", "reason",
    "cabt_probe_status", "cabt_probe_evidence", "qualified_asset_id",
})
_OUTCOMES_V1 = frozenset({"qualified", "failed", "not_run"})
_ASSET_CLASSES_V1 = frozenset({
    "materialized_deck_csv_deduplicated_by_canonical_multiset",
    "immutable_meta_jsonl_deck_row",
})
_MATERIALIZATION_STATUSES_V1 = frozenset({
    "materialized_git_blob", "unmaterialized_meta_row",
})


class SeedQualificationReportV1Error(ValueError):
    """Raised when a seed qualification report cannot be built or verified."""


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json_bytes_v2(value)
    ).hexdigest()


def _report_identity(payload: Mapping[str, object]) -> str:
    identity = {
        key: value for key, value in payload.items()
        if key not in {"report_id", "content_hash"}
    }
    return _hash(_ID_DOMAIN_V1, identity)


def _report_content_hash(payload: Mapping[str, object]) -> str:
    content = {key: value for key, value in payload.items() if key != "content_hash"}
    return _hash(_CONTENT_DOMAIN_V1, content)


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SeedQualificationReportV1Error(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: object, field: str) -> str:
    string = _require_str(value, field)
    try:
        return require_sha256(string, field)
    except ValueError as exc:
        raise SeedQualificationReportV1Error(str(exc)) from exc


def _require_optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field)


def _validate_candidate(raw: object, index: int) -> dict[str, object]:
    field = f"candidates[{index}]"
    if type(raw) is not dict or set(raw) != _CANDIDATE_KEYS_V1:
        raise SeedQualificationReportV1Error(f"{field} has the wrong closed field set")

    runtime_id = _require_str(raw["runtime_id"], f"{field}.runtime_id")
    priority = raw["priority"]
    if type(priority) is not int or priority not in {1, 2, 3}:
        raise SeedQualificationReportV1Error(
            f"{field}.priority must be one of 1, 2, 3 and not bool"
        )
    deck_identity = _require_str(raw["deck_identity"], f"{field}.deck_identity")
    if not deck_identity.startswith("deck-") or len(deck_identity) != 25:
        raise SeedQualificationReportV1Error(f"{field}.deck_identity must be canonical deck-<20hex>")
    asset_class = _require_str(raw["asset_class"], f"{field}.asset_class")
    if asset_class not in _ASSET_CLASSES_V1:
        raise SeedQualificationReportV1Error(f"{field}.asset_class is unsupported")
    materialization_status = _require_str(
        raw["materialization_status"], f"{field}.materialization_status"
    )
    if materialization_status not in _MATERIALIZATION_STATUSES_V1:
        raise SeedQualificationReportV1Error(
            f"{field}.materialization_status is unsupported"
        )
    outcome = _require_str(raw["outcome"], f"{field}.outcome")
    if outcome not in _OUTCOMES_V1:
        raise SeedQualificationReportV1Error(f"{field}.outcome is unsupported")

    reason = _require_optional_str(raw["reason"], f"{field}.reason")
    cabt_probe_status = _require_optional_str(raw["cabt_probe_status"], f"{field}.cabt_probe_status")
    cabt_probe_evidence = _require_optional_str(
        raw["cabt_probe_evidence"], f"{field}.cabt_probe_evidence"
    )
    qualified_asset_id = _require_optional_str(
        raw["qualified_asset_id"], f"{field}.qualified_asset_id"
    )

    if outcome == "qualified":
        if reason is not None:
            raise SeedQualificationReportV1Error(f"{field}: a qualified outcome must not carry a reason")
        if cabt_probe_status != "DONE":
            raise SeedQualificationReportV1Error(
                f"{field}: a qualified outcome must carry cabt_probe_status == 'DONE'"
            )
        if cabt_probe_evidence is None:
            raise SeedQualificationReportV1Error(
                f"{field}: a qualified outcome must carry nonempty cabt_probe_evidence"
            )
        if qualified_asset_id is None:
            raise SeedQualificationReportV1Error(
                f"{field}: a qualified outcome must carry a qualified_asset_id"
            )
    else:
        if reason is None:
            raise SeedQualificationReportV1Error(f"{field}: a {outcome} outcome must carry a reason")
        if qualified_asset_id is not None:
            raise SeedQualificationReportV1Error(
                f"{field}: only a qualified outcome may carry a qualified_asset_id"
            )
        if outcome == "not_run" and (cabt_probe_status is not None or cabt_probe_evidence is not None):
            raise SeedQualificationReportV1Error(
                f"{field}: a not_run outcome must not carry any CABT probe status or evidence"
            )
        if (cabt_probe_status is None) != (cabt_probe_evidence is None):
            raise SeedQualificationReportV1Error(
                f"{field}: cabt_probe_status and cabt_probe_evidence must be set together"
            )

    return {
        "runtime_id": runtime_id,
        "priority": priority,
        "deck_identity": deck_identity,
        "asset_class": asset_class,
        "materialization_status": materialization_status,
        "outcome": outcome,
        "reason": reason,
        "cabt_probe_status": cabt_probe_status,
        "cabt_probe_evidence": cabt_probe_evidence,
        "qualified_asset_id": qualified_asset_id,
    }


def build_seed_qualification_report_v1(
    *,
    registry_content_sha256: str,
    card_database_sha256: str,
    card_vocabulary_sha256: str,
    archetype_registry_schema_version: str,
    cabt_probe_seed: int,
    cabt_probe_max_steps: int,
    generated_time_utc: str,
    candidates: object,
) -> dict[str, object]:
    """Build one immutable report from already-decided per-candidate outcomes.

    ``candidates`` must contain exactly :data:`EXPECTED_CANDIDATE_COUNT_V1`
    records (one per fixed-lane seed candidate), each shaped like the closed
    dict validated by :func:`_validate_candidate`.  This function never
    invents an outcome; it only accepts, revalidates, and seals the ones it
    is given.
    """
    registry_hash = _require_sha256(registry_content_sha256, "registry_content_sha256")
    database_hash = _require_sha256(card_database_sha256, "card_database_sha256")
    vocabulary_hash = _require_sha256(card_vocabulary_sha256, "card_vocabulary_sha256")
    schema_version = _require_str(
        archetype_registry_schema_version, "archetype_registry_schema_version"
    )
    if type(cabt_probe_seed) is not int or isinstance(cabt_probe_seed, bool):
        raise SeedQualificationReportV1Error("cabt_probe_seed must be an int and not bool")
    if (
        type(cabt_probe_max_steps) is not int
        or isinstance(cabt_probe_max_steps, bool)
        or cabt_probe_max_steps <= 0
    ):
        raise SeedQualificationReportV1Error(
            "cabt_probe_max_steps must be a positive int and not bool"
        )
    time_utc = _require_str(generated_time_utc, "generated_time_utc")

    if not isinstance(candidates, (list, tuple)):
        raise SeedQualificationReportV1Error("candidates must be a list")
    if len(candidates) != EXPECTED_CANDIDATE_COUNT_V1:
        raise SeedQualificationReportV1Error(
            f"seed qualification report requires exactly "
            f"{EXPECTED_CANDIDATE_COUNT_V1} candidate records, got {len(candidates)}"
        )
    validated = [_validate_candidate(raw, index) for index, raw in enumerate(candidates)]
    lane_priorities = [(item["runtime_id"], item["priority"]) for item in validated]
    if len(lane_priorities) != len(set(lane_priorities)):
        raise SeedQualificationReportV1Error("duplicate (runtime_id, priority) lane in candidates")
    identities = [item["deck_identity"] for item in validated]
    if len(identities) != len(set(identities)):
        raise SeedQualificationReportV1Error("duplicate deck_identity in candidates")

    counts = {"qualified": 0, "failed": 0, "not_run": 0}
    for item in validated:
        counts[item["outcome"]] += 1

    payload: dict[str, object] = {
        "schema_version": SEED_QUALIFICATION_REPORT_SCHEMA_V1,
        "report_id": "",
        "content_hash": "",
        "registry_content_sha256": registry_hash,
        "card_database_sha256": database_hash,
        "card_vocabulary_sha256": vocabulary_hash,
        "archetype_registry_schema_version": schema_version,
        "cabt_probe_seed": cabt_probe_seed,
        "cabt_probe_max_steps": cabt_probe_max_steps,
        "generated_time_utc": time_utc,
        "candidate_count": len(validated),
        "qualified_count": counts["qualified"],
        "failed_count": counts["failed"],
        "not_run_count": counts["not_run"],
        "candidates": validated,
    }
    payload["report_id"] = _report_identity(payload)
    payload["content_hash"] = _report_content_hash(payload)
    return validate_seed_qualification_report_v1(payload)


def validate_seed_qualification_report_v1(value: object) -> dict[str, object]:
    """Revalidate a report's closed shape, count consistency, and self-hashes."""
    if type(value) is not dict or set(value) != _REPORT_KEYS_V1:
        raise SeedQualificationReportV1Error(
            "seed qualification report has the wrong closed field set"
        )
    payload = value
    if payload["schema_version"] != SEED_QUALIFICATION_REPORT_SCHEMA_V1:
        raise SeedQualificationReportV1Error(
            "seed qualification report schema_version is invalid"
        )
    _require_sha256(payload["report_id"], "report_id")
    _require_sha256(payload["content_hash"], "content_hash")
    _require_sha256(payload["registry_content_sha256"], "registry_content_sha256")
    _require_sha256(payload["card_database_sha256"], "card_database_sha256")
    _require_sha256(payload["card_vocabulary_sha256"], "card_vocabulary_sha256")
    _require_str(
        payload["archetype_registry_schema_version"], "archetype_registry_schema_version"
    )
    _require_str(payload["generated_time_utc"], "generated_time_utc")

    seed = payload["cabt_probe_seed"]
    if type(seed) is not int or isinstance(seed, bool):
        raise SeedQualificationReportV1Error("cabt_probe_seed must be an int and not bool")
    max_steps = payload["cabt_probe_max_steps"]
    if type(max_steps) is not int or isinstance(max_steps, bool) or max_steps <= 0:
        raise SeedQualificationReportV1Error(
            "cabt_probe_max_steps must be a positive int and not bool"
        )

    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise SeedQualificationReportV1Error("candidates must be a list")
    if len(candidates) != EXPECTED_CANDIDATE_COUNT_V1:
        raise SeedQualificationReportV1Error(
            f"seed qualification report requires exactly "
            f"{EXPECTED_CANDIDATE_COUNT_V1} candidate records"
        )
    validated = [_validate_candidate(raw, index) for index, raw in enumerate(candidates)]
    if validated != candidates:
        raise SeedQualificationReportV1Error("candidates are not in normalized closed form")
    lane_priorities = [(item["runtime_id"], item["priority"]) for item in validated]
    if len(lane_priorities) != len(set(lane_priorities)):
        raise SeedQualificationReportV1Error("duplicate (runtime_id, priority) lane in candidates")
    identities = [item["deck_identity"] for item in validated]
    if len(identities) != len(set(identities)):
        raise SeedQualificationReportV1Error("duplicate deck_identity in candidates")

    counts = {"qualified": 0, "failed": 0, "not_run": 0}
    for item in validated:
        counts[item["outcome"]] += 1
    for key in ("candidate_count", "qualified_count", "failed_count", "not_run_count"):
        value_ = payload[key]
        if type(value_) is not int or isinstance(value_, bool) or value_ < 0:
            raise SeedQualificationReportV1Error(f"{key} must be a non-negative int and not bool")
    if payload["candidate_count"] != len(validated):
        raise SeedQualificationReportV1Error("candidate_count does not match candidates")
    if (
        payload["qualified_count"] != counts["qualified"]
        or payload["failed_count"] != counts["failed"]
        or payload["not_run_count"] != counts["not_run"]
    ):
        raise SeedQualificationReportV1Error("outcome counts do not match candidates")
    if (
        payload["qualified_count"] + payload["failed_count"] + payload["not_run_count"]
        != payload["candidate_count"]
    ):
        raise SeedQualificationReportV1Error("outcome counts do not sum to candidate_count")

    if payload["report_id"] != _report_identity(payload):
        raise SeedQualificationReportV1Error("seed qualification report report_id does not verify")
    if payload["content_hash"] != _report_content_hash(payload):
        raise SeedQualificationReportV1Error(
            "seed qualification report content_hash does not verify"
        )
    return payload


def atomic_write_seed_qualification_report_v1(path: str | Path, report: object) -> Path:
    """Publish one verified report as canonical bytes, replacing any prior report."""
    payload = validate_seed_qualification_report_v1(report)
    body = canonical_json_bytes_v2(payload)
    if len(body) > MAX_SEED_QUALIFICATION_REPORT_BYTES_V1:
        raise SeedQualificationReportV1Error(
            "seed qualification report exceeds the publication byte cap"
        )
    destination = Path(os.path.abspath(os.fspath(path)))
    if not destination.name:
        raise SeedQualificationReportV1Error("report path must name a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp.", dir=destination.parent,
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    parent = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return destination


def read_seed_qualification_report_v1(path: str | Path) -> dict[str, object]:
    """Read and revalidate one published report from exact canonical bytes."""
    body = Path(path).read_bytes()
    if len(body) > MAX_SEED_QUALIFICATION_REPORT_BYTES_V1:
        raise SeedQualificationReportV1Error(
            "seed qualification report exceeds the publication byte cap"
        )
    payload = validate_seed_qualification_report_v1(parse_canonical_json_bytes_v2(body))
    if canonical_json_bytes_v2(payload) != body:
        raise SeedQualificationReportV1Error("seed qualification report bytes are not canonical")
    return payload


__all__ = [
    "EXPECTED_CANDIDATE_COUNT_V1", "MAX_SEED_QUALIFICATION_REPORT_BYTES_V1",
    "SEED_QUALIFICATION_REPORT_SCHEMA_V1", "SeedQualificationReportV1Error",
    "atomic_write_seed_qualification_report_v1", "build_seed_qualification_report_v1",
    "read_seed_qualification_report_v1", "validate_seed_qualification_report_v1",
]
