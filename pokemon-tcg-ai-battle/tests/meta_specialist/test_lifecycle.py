from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from mage_ptcg.meta_specialist.lifecycle import (
    LifecycleError,
    SubmissionLifecycleRecord,
    SubmissionState,
    advance_lifecycle,
)


def test_manual_submission_lifecycle_requires_external_evidence() -> None:
    """Catches lifecycle advancement without the evidence recorded by a human."""
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="a" * 64,
        active_slot_intent="primary",
    )

    with pytest.raises(LifecycleError, match="submission_id"):
        advance_lifecycle(draft, SubmissionState.SUBMITTED, {})

    submitted = advance_lifecycle(
        draft,
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )
    assert submitted.state is SubmissionState.SUBMITTED

    with pytest.raises(LifecycleError, match="validation_log"):
        advance_lifecycle(submitted, SubmissionState.VALIDATION_PASSED, {})


def test_local_code_cannot_skip_to_active_or_final_selected() -> None:
    """Catches local state changes that bypass Kaggle's external lifecycle."""
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="b" * 64,
        active_slot_intent="backup",
    )

    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        advance_lifecycle(
            draft,
            SubmissionState.ACTIVE_CONFIRMED,
            {"active_checked_at_utc": "2026-08-10T02:00:00Z"},
        )

    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        advance_lifecycle(
            draft,
            SubmissionState.FINAL_SELECTED,
            {"final_selected_at_utc": "2026-08-10T02:00:00Z"},
        )


@pytest.mark.parametrize(
    "state",
    [
        SubmissionState.SUBMITTED,
        SubmissionState.VALIDATION_PASSED,
        SubmissionState.VALIDATION_FAILED,
        SubmissionState.ACTIVE_CONFIRMED,
        SubmissionState.FINAL_SELECTED,
    ],
)
def test_direct_construction_rejects_every_post_draft_state(
    state: SubmissionState,
) -> None:
    """Catches local fabrication of a state that lacks transition evidence."""
    with pytest.raises(LifecycleError, match="only draft"):
        SubmissionLifecycleRecord(
            bundle_sha256="d" * 64,
            active_slot_intent="primary",
            state=state,
        )


def test_submission_record_exposes_no_transition_factory() -> None:
    """Catches a callable record API that fabricates post-draft states."""
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="2" * 64,
        active_slot_intent="primary",
    )

    assert not hasattr(SubmissionLifecycleRecord, "_from_transition")
    assert not hasattr(draft, "_from_transition")


def test_transition_rejects_a_non_record_lookalike() -> None:
    """Catches a fabricated object entering the post-draft construction path."""
    class Lookalike:
        state = SubmissionState.VALIDATION_PASSED
        bundle_sha256 = "2" * 64
        active_slot_intent = "primary"
        submission_id = "54812345"
        evidence: dict[str, object] = {}

    with pytest.raises(LifecycleError, match="SubmissionLifecycleRecord"):
        advance_lifecycle(
            Lookalike(),
            SubmissionState.ACTIVE_CONFIRMED,
            {
                "active_checked_at_utc": "2026-08-10T02:00:00Z",
                "active_submission_ids": ["54812345"],
            },
        )


def test_lifecycle_evidence_cannot_be_mutated_after_transition() -> None:
    """Catches callers rewriting an externally recorded submission fact."""
    submitted = advance_lifecycle(
        SubmissionLifecycleRecord.draft(
            bundle_sha256="e" * 64,
            active_slot_intent="primary",
        ),
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )

    with pytest.raises(TypeError):
        submitted.evidence["recorded_by"] = "local-code"
    assert submitted.evidence["recorded_by"] == "human"


def test_immutable_evidence_supports_dataclass_and_json_serialization() -> None:
    """Catches immutable evidence that breaks standard record serialization."""
    submitted = advance_lifecycle(
        SubmissionLifecycleRecord.draft(
            bundle_sha256="1" * 64,
            active_slot_intent="primary",
        ),
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )

    serialized = json.loads(json.dumps(asdict(submitted)))
    assert serialized["evidence"]["submission_id"] == "54812345"


@pytest.mark.parametrize(
    "invalid_value",
    [
        {"not-json"},
        frozenset({"not-json"}),
        b"not-json",
        {1: "non-string-key"},
        float("nan"),
        float("inf"),
    ],
)
def test_submission_rejects_json_incompatible_evidence(
    invalid_value: object,
) -> None:
    """Catches accepted evidence that cannot be serialized as JSON."""
    with pytest.raises(LifecycleError, match="JSON-compatible"):
        advance_lifecycle(
            SubmissionLifecycleRecord.draft(
                bundle_sha256="3" * 64,
                active_slot_intent="primary",
            ),
            SubmissionState.SUBMITTED,
            {
                "submission_id": "54812345",
                "submitted_at_utc": "2026-08-10T01:00:00Z",
                "daily_slot_number": 1,
                "recorded_by": "human",
                "extra": invalid_value,
            },
        )


def test_later_transition_cannot_overwrite_submission_evidence() -> None:
    """Catches a later stage rewriting the audited submission identifier."""
    submitted = advance_lifecycle(
        SubmissionLifecycleRecord.draft(
            bundle_sha256="f" * 64,
            active_slot_intent="backup",
        ),
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )

    with pytest.raises(LifecycleError, match="already recorded"):
        advance_lifecycle(
            submitted,
            SubmissionState.VALIDATION_PASSED,
            {
                "submission_id": "54812346",
                "validation_log": "validation succeeded",
                "validated_at_utc": "2026-08-10T01:05:00Z",
            },
        )


def test_happy_path_requires_evidence_at_each_manual_lifecycle_stage() -> None:
    """Catches a valid external-evidence progression that no longer reaches final."""
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="0" * 64,
        active_slot_intent="primary",
    )
    submitted = advance_lifecycle(
        draft,
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )
    validated = advance_lifecycle(
        submitted,
        SubmissionState.VALIDATION_PASSED,
        {
            "validation_log": "validation succeeded",
            "validated_at_utc": "2026-08-10T01:05:00Z",
        },
    )
    active = advance_lifecycle(
        validated,
        SubmissionState.ACTIVE_CONFIRMED,
        {
            "active_checked_at_utc": "2026-08-10T02:00:00Z",
            "active_submission_ids": ["54812345"],
        },
    )
    final = advance_lifecycle(
        active,
        SubmissionState.FINAL_SELECTED,
        {
            "final_selected_at_utc": "2026-08-10T03:00:00Z",
            "final_submission_ids": ["54812345"],
        },
    )

    assert final.state is SubmissionState.FINAL_SELECTED
    assert final.evidence["submission_id"] == "54812345"
    assert final.evidence["final_submission_ids"] == ("54812345",)


def test_validation_failed_is_terminal_after_recording_external_evidence() -> None:
    """Catches a failed validation that can be advanced after becoming terminal."""
    submitted = advance_lifecycle(
        SubmissionLifecycleRecord.draft(
            bundle_sha256="4" * 64,
            active_slot_intent="backup",
        ),
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )
    failed = advance_lifecycle(
        submitted,
        SubmissionState.VALIDATION_FAILED,
        {
            "validation_log": "deck.csv is invalid",
            "validated_at_utc": "2026-08-10T01:05:00Z",
        },
    )

    assert failed.state is SubmissionState.VALIDATION_FAILED
    assert failed.evidence["validation_log"] == "deck.csv is invalid"
    for target in SubmissionState:
        with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
            advance_lifecycle(failed, target, {})


def test_active_and_final_confirmation_enforce_two_submission_limits() -> None:
    """Catches records confirmed beyond Kaggle's active or final-selection limit."""
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="c" * 64,
        active_slot_intent="primary",
    )
    submitted = advance_lifecycle(
        draft,
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 5,
            "recorded_by": "human",
        },
    )
    validated = advance_lifecycle(
        submitted,
        SubmissionState.VALIDATION_PASSED,
        {
            "validation_log": "validation succeeded",
            "validated_at_utc": "2026-08-10T01:05:00Z",
        },
    )

    with pytest.raises(LifecycleError, match="at most 2"):
        advance_lifecycle(
            validated,
            SubmissionState.ACTIVE_CONFIRMED,
            {
                "active_checked_at_utc": "2026-08-10T02:00:00Z",
                "active_submission_ids": ["54812345", "54812346", "54812347"],
            },
        )

    active = advance_lifecycle(
        validated,
        SubmissionState.ACTIVE_CONFIRMED,
        {
            "active_checked_at_utc": "2026-08-10T02:00:00Z",
            "active_submission_ids": ["54812345", "54812346"],
        },
    )
    assert active.state is SubmissionState.ACTIVE_CONFIRMED

    with pytest.raises(LifecycleError, match="at most 2"):
        advance_lifecycle(
            active,
            SubmissionState.FINAL_SELECTED,
            {
                "final_selected_at_utc": "2026-08-10T03:00:00Z",
                "final_submission_ids": ["54812345", "54812346", "54812347"],
            },
        )
