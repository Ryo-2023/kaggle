"""Unit tests for the content-addressed seed qualification report artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    canonical_json_bytes_v2,
)
from mage_ptcg.meta_specialist.seed_qualification_report_v1 import (
    EXPECTED_CANDIDATE_COUNT_V1,
    SEED_QUALIFICATION_REPORT_SCHEMA_V1,
    SeedQualificationReportV1Error,
    atomic_write_seed_qualification_report_v1,
    build_seed_qualification_report_v1,
    read_seed_qualification_report_v1,
    validate_seed_qualification_report_v1,
)


_RUNTIME_IDS = (
    "alakazam",
    "grimmsnarl_froslass_munkidori",
    "crustle_mega_kangaskhan",
    "rocket_mewtwo_spidops",
    "archaludon",
)


def _lanes() -> list[tuple[str, int]]:
    return [(runtime_id, priority) for runtime_id in _RUNTIME_IDS for priority in (1, 2, 3)]


def _deck_identity(runtime_id: str, priority: int) -> str:
    # A stable, syntactically valid deck-<20hex> identity; content is not
    # semantically checked against a real registry by this module.
    digest = hashlib.sha256(f"{runtime_id}:{priority}".encode("utf-8")).hexdigest()
    return f"deck-{digest[:20]}"


def _qualified(runtime_id: str, priority: int) -> dict[str, object]:
    return {
        "runtime_id": runtime_id,
        "priority": priority,
        "deck_identity": _deck_identity(runtime_id, priority),
        "asset_class": "materialized_deck_csv_deduplicated_by_canonical_multiset",
        "materialization_status": "materialized_git_blob",
        "outcome": "qualified",
        "reason": None,
        "cabt_probe_status": "DONE",
        "cabt_probe_evidence": '{"status":"DONE"}',
        "qualified_asset_id": f"seed-{runtime_id}-p{priority}-{_deck_identity(runtime_id, priority)}",
    }


def _failed(runtime_id: str, priority: int, *, with_probe: bool = True) -> dict[str, object]:
    record = {
        "runtime_id": runtime_id,
        "priority": priority,
        "deck_identity": _deck_identity(runtime_id, priority),
        "asset_class": "materialized_deck_csv_deduplicated_by_canonical_multiset",
        "materialization_status": "materialized_git_blob",
        "outcome": "failed",
        "reason": "CABT legality did not pass",
        "cabt_probe_status": "HARD_TIMEOUT" if with_probe else None,
        "cabt_probe_evidence": '{"status":"HARD_TIMEOUT"}' if with_probe else None,
        "qualified_asset_id": None,
    }
    return record


def _not_run(runtime_id: str, priority: int) -> dict[str, object]:
    return {
        "runtime_id": runtime_id,
        "priority": priority,
        "deck_identity": _deck_identity(runtime_id, priority),
        "asset_class": "immutable_meta_jsonl_deck_row",
        "materialization_status": "unmaterialized_meta_row",
        "outcome": "not_run",
        "reason": (
            "meta JSONL rows require separate materialization authority and a new raw hash"
        ),
        "cabt_probe_status": None,
        "cabt_probe_evidence": None,
        "qualified_asset_id": None,
    }


def _full_candidate_set() -> list[dict[str, object]]:
    lanes = _lanes()
    assert len(lanes) == EXPECTED_CANDIDATE_COUNT_V1 == 15
    candidates = []
    for index, (runtime_id, priority) in enumerate(lanes):
        if index % 3 == 0:
            candidates.append(_qualified(runtime_id, priority))
        elif index % 3 == 1:
            candidates.append(_failed(runtime_id, priority))
        else:
            candidates.append(_not_run(runtime_id, priority))
    return candidates


def _build_kwargs(**overrides) -> dict[str, object]:
    kwargs = {
        "registry_content_sha256": "a" * 64,
        "card_database_sha256": "b" * 64,
        "card_vocabulary_sha256": "c" * 64,
        "archetype_registry_schema_version": "meta-specialist-archetypes-v1",
        "cabt_probe_seed": 20260803,
        "cabt_probe_max_steps": 2000,
        "generated_time_utc": "2026-08-03T00:00:00Z",
        "candidates": _full_candidate_set(),
    }
    kwargs.update(overrides)
    return kwargs


def test_build_and_validate_round_trip_ok() -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    assert report["schema_version"] == SEED_QUALIFICATION_REPORT_SCHEMA_V1
    assert report["candidate_count"] == 15
    assert report["qualified_count"] == 5
    assert report["failed_count"] == 5
    assert report["not_run_count"] == 5
    assert len(report["report_id"]) == 64
    assert len(report["content_hash"]) == 64
    # Re-validating an already-built report must be a pure no-op.
    assert validate_seed_qualification_report_v1(report) == report


def test_wrong_candidate_count_is_rejected() -> None:
    kwargs = _build_kwargs(candidates=_full_candidate_set()[:14])
    with pytest.raises(SeedQualificationReportV1Error, match="exactly 15"):
        build_seed_qualification_report_v1(**kwargs)


def test_duplicate_lane_is_rejected() -> None:
    candidates = _full_candidate_set()
    candidates[1] = dict(candidates[0])
    with pytest.raises(SeedQualificationReportV1Error, match="duplicate"):
        build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))


def test_duplicate_deck_identity_is_rejected() -> None:
    candidates = _full_candidate_set()
    mutated = dict(candidates[1])
    mutated["deck_identity"] = candidates[0]["deck_identity"]
    candidates[1] = mutated
    with pytest.raises(SeedQualificationReportV1Error, match="duplicate deck_identity"):
        build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))


def test_qualified_outcome_cannot_carry_a_reason() -> None:
    candidates = _full_candidate_set()
    mutated = dict(candidates[0])
    assert mutated["outcome"] == "qualified"
    mutated["reason"] = "should not be set"
    candidates[0] = mutated
    with pytest.raises(SeedQualificationReportV1Error, match="must not carry a reason"):
        build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))


def test_qualified_outcome_requires_probe_status_done() -> None:
    candidates = _full_candidate_set()
    mutated = dict(candidates[0])
    mutated["cabt_probe_status"] = None
    mutated["cabt_probe_evidence"] = None
    candidates[0] = mutated
    with pytest.raises(SeedQualificationReportV1Error, match="cabt_probe_status == 'DONE'"):
        build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))


def test_qualified_outcome_requires_asset_id() -> None:
    candidates = _full_candidate_set()
    mutated = dict(candidates[0])
    mutated["qualified_asset_id"] = None
    candidates[0] = mutated
    with pytest.raises(SeedQualificationReportV1Error, match="qualified_asset_id"):
        build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))


def test_non_qualified_outcomes_require_a_reason() -> None:
    candidates = _full_candidate_set()
    for index, candidate in enumerate(candidates):
        if candidate["outcome"] != "qualified":
            mutated = dict(candidate)
            mutated["reason"] = None
            candidates[index] = mutated
            with pytest.raises(SeedQualificationReportV1Error, match="must carry a reason"):
                build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))
            return
    raise AssertionError("fixture must contain a non-qualified candidate")


def test_not_run_cannot_carry_cabt_probe_fields() -> None:
    candidates = _full_candidate_set()
    for index, candidate in enumerate(candidates):
        if candidate["outcome"] == "not_run":
            mutated = dict(candidate)
            mutated["cabt_probe_status"] = "DONE"
            mutated["cabt_probe_evidence"] = "{}"
            candidates[index] = mutated
            with pytest.raises(SeedQualificationReportV1Error, match="not_run outcome"):
                build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))
            return
    raise AssertionError("fixture must contain a not_run candidate")


def test_failed_outcome_may_omit_or_carry_probe_evidence() -> None:
    candidates = _full_candidate_set()
    for index, candidate in enumerate(candidates):
        if candidate["outcome"] == "failed":
            mutated = dict(candidate)
            mutated["cabt_probe_status"] = None
            mutated["cabt_probe_evidence"] = None
            candidates[index] = mutated
            break
    report = build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))
    assert report["failed_count"] == 5


def test_only_qualified_may_carry_a_qualified_asset_id() -> None:
    candidates = _full_candidate_set()
    for index, candidate in enumerate(candidates):
        if candidate["outcome"] != "qualified":
            mutated = dict(candidate)
            mutated["qualified_asset_id"] = "forged-asset-id"
            candidates[index] = mutated
            with pytest.raises(SeedQualificationReportV1Error, match="qualified_asset_id"):
                build_seed_qualification_report_v1(**_build_kwargs(candidates=candidates))
            return
    raise AssertionError("fixture must contain a non-qualified candidate")


def test_closed_field_set_is_enforced_on_report_and_candidates() -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    with_extra = dict(report)
    with_extra["unexpected_field"] = 1
    with pytest.raises(SeedQualificationReportV1Error, match="closed field set"):
        validate_seed_qualification_report_v1(with_extra)

    missing_field = dict(report)
    del missing_field["cabt_probe_seed"]
    with pytest.raises(SeedQualificationReportV1Error, match="closed field set"):
        validate_seed_qualification_report_v1(missing_field)

    candidates = list(report["candidates"])
    candidates[0] = {**candidates[0], "unexpected": True}
    tampered = {**report, "candidates": candidates}
    with pytest.raises(SeedQualificationReportV1Error, match="closed field set"):
        validate_seed_qualification_report_v1(tampered)


def test_tampered_counts_are_rejected() -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    tampered = dict(report)
    tampered["qualified_count"] = report["qualified_count"] + 1
    with pytest.raises(SeedQualificationReportV1Error, match="outcome counts"):
        validate_seed_qualification_report_v1(tampered)


def test_tampered_content_is_rejected_by_self_hash() -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    tampered = dict(report)
    tampered["generated_time_utc"] = "2099-01-01T00:00:00Z"
    with pytest.raises(SeedQualificationReportV1Error, match="report_id does not verify"):
        validate_seed_qualification_report_v1(tampered)


def test_tampered_content_hash_alone_is_rejected() -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    tampered = dict(report)
    tampered["content_hash"] = "0" * 64
    with pytest.raises(SeedQualificationReportV1Error, match="content_hash does not verify"):
        validate_seed_qualification_report_v1(tampered)


def test_atomic_write_and_read_round_trip(tmp_path: Path) -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    destination = tmp_path / "nested" / "seed_qualification_report_v1.json"
    written = atomic_write_seed_qualification_report_v1(destination, report)
    assert written == destination
    assert destination.is_file()
    assert destination.read_bytes() == canonical_json_bytes_v2(report)

    reloaded = read_seed_qualification_report_v1(destination)
    assert reloaded == report


def test_atomic_write_overwrites_a_prior_report(tmp_path: Path) -> None:
    destination = tmp_path / "seed_qualification_report_v1.json"
    first = build_seed_qualification_report_v1(**_build_kwargs())
    atomic_write_seed_qualification_report_v1(destination, first)

    second = build_seed_qualification_report_v1(
        **_build_kwargs(generated_time_utc="2026-08-03T01:00:00Z")
    )
    atomic_write_seed_qualification_report_v1(destination, second)

    reloaded = read_seed_qualification_report_v1(destination)
    assert reloaded == second
    assert reloaded != first


def test_read_rejects_non_canonical_bytes(tmp_path: Path) -> None:
    report = build_seed_qualification_report_v1(**_build_kwargs())
    destination = tmp_path / "seed_qualification_report_v1.json"
    # Pretty-printed (non-canonical) bytes of an otherwise-valid report.
    destination.write_bytes(json.dumps(report, indent=2).encode("utf-8"))
    with pytest.raises(LocalDatasetV2Error, match="canonical form"):
        read_seed_qualification_report_v1(destination)
