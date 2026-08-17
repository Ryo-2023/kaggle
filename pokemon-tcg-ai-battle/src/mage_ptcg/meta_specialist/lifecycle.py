from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping, NoReturn

from mage_ptcg.continuous_league.contracts import require_sha256


class LifecycleError(ValueError):
    """Raised when manual submission evidence violates the lifecycle contract."""


class SubmissionState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    ACTIVE_CONFIRMED = "active_confirmed"
    FINAL_SELECTED = "final_selected"


_ALLOWED = {
    SubmissionState.DRAFT: {SubmissionState.SUBMITTED},
    SubmissionState.SUBMITTED: {
        SubmissionState.VALIDATION_PASSED,
        SubmissionState.VALIDATION_FAILED,
    },
    SubmissionState.VALIDATION_PASSED: {SubmissionState.ACTIVE_CONFIRMED},
    SubmissionState.ACTIVE_CONFIRMED: {SubmissionState.FINAL_SELECTED},
    SubmissionState.VALIDATION_FAILED: set(),
    SubmissionState.FINAL_SELECTED: set(),
}


def _freeze_evidence_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenEvidence(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_evidence_value(item) for item in value)
    return value


class _FrozenEvidence(dict[str, object]):
    """JSON-serializable immutable evidence with recursively frozen values."""

    def __init__(
        self,
        values: Mapping[str, object] | Iterable[tuple[str, object]],
    ) -> None:
        dict.__init__(
            self,
            {
                key: _freeze_evidence_value(value)
                for key, value in dict(values).items()
            },
        )

    def _immutable(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise TypeError("lifecycle evidence is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, _memo: object) -> _FrozenEvidence:
        return self


def _require_json_compatible(value: object, active_ids: set[int] | None = None) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if isfinite(value):
            return
        raise LifecycleError("evidence must contain only JSON-compatible values")
    if not isinstance(value, (Mapping, list, tuple)):
        raise LifecycleError("evidence must contain only JSON-compatible values")

    active_ids = active_ids if active_ids is not None else set()
    value_id = id(value)
    if value_id in active_ids:
        raise LifecycleError("evidence must contain only JSON-compatible values")
    active_ids.add(value_id)
    try:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise LifecycleError(
                        "evidence must contain only JSON-compatible values"
                    )
                _require_json_compatible(item, active_ids)
        else:
            for item in value:
                _require_json_compatible(item, active_ids)
    finally:
        active_ids.remove(value_id)


def _require_nonempty(evidence: Mapping[str, object], field: str) -> object:
    value = evidence.get(field)
    if value is None or value == "":
        raise LifecycleError(f"{field} is required")
    return value


def _require_submission_id(evidence: Mapping[str, object]) -> str:
    submission_id = _require_nonempty(evidence, "submission_id")
    if not isinstance(submission_id, str):
        raise LifecycleError("submission_id is required")
    return submission_id


def _require_slot_number(evidence: Mapping[str, object]) -> None:
    slot_number = _require_nonempty(evidence, "daily_slot_number")
    if (
        not isinstance(slot_number, int)
        or isinstance(slot_number, bool)
        or not 1 <= slot_number <= 5
    ):
        raise LifecycleError("daily_slot_number must be in 1..5")


def _require_submission_ids(
    evidence: Mapping[str, object], *, field: str, submission_id: str
) -> None:
    submission_ids = _require_nonempty(evidence, field)
    if not isinstance(submission_ids, list):
        raise LifecycleError(f"{field} must be a list")
    if len(submission_ids) > 2:
        raise LifecycleError(f"{field} must contain at most 2 submissions")
    if submission_id not in submission_ids:
        raise LifecycleError(f"{field} must contain submission_id")


def _validate_evidence(
    record: SubmissionLifecycleRecord,
    target: SubmissionState,
    evidence: Mapping[str, object],
) -> str | None:
    if target is SubmissionState.SUBMITTED:
        submission_id = _require_submission_id(evidence)
        _require_nonempty(evidence, "submitted_at_utc")
        _require_slot_number(evidence)
        _require_nonempty(evidence, "recorded_by")
        return submission_id
    if target in {
        SubmissionState.VALIDATION_PASSED,
        SubmissionState.VALIDATION_FAILED,
    }:
        _require_nonempty(evidence, "validation_log")
        _require_nonempty(evidence, "validated_at_utc")
    elif target is SubmissionState.ACTIVE_CONFIRMED:
        _require_nonempty(evidence, "active_checked_at_utc")
        if record.submission_id is None:
            raise LifecycleError("active confirmation requires a submission_id")
        _require_submission_ids(
            evidence,
            field="active_submission_ids",
            submission_id=record.submission_id,
        )
    elif target is SubmissionState.FINAL_SELECTED:
        _require_nonempty(evidence, "final_selected_at_utc")
        if record.submission_id is None:
            raise LifecycleError("final selection requires a submission_id")
        _require_submission_ids(
            evidence,
            field="final_submission_ids",
            submission_id=record.submission_id,
        )
    return None


def _build_lifecycle_api() -> tuple[object, object]:
    @dataclass(frozen=True, init=False)
    class SubmissionLifecycleRecord:
        """Locally recorded state for a submission advanced by external evidence."""

        bundle_sha256: str
        active_slot_intent: str
        state: SubmissionState
        submission_id: str | None
        evidence: Mapping[str, object]

        def __init__(
            self,
            *,
            bundle_sha256: str,
            active_slot_intent: str,
            state: SubmissionState = SubmissionState.DRAFT,
            submission_id: str | None = None,
            evidence: Mapping[str, object] | None = None,
        ) -> None:
            if state is not SubmissionState.DRAFT:
                raise LifecycleError(
                    "only draft records may be constructed directly"
                )
            if submission_id is not None or evidence:
                raise LifecycleError(
                    "only empty draft evidence may be constructed directly"
                )
            object.__setattr__(
                self,
                "bundle_sha256",
                require_sha256(bundle_sha256, "bundle_sha256"),
            )
            object.__setattr__(self, "active_slot_intent", active_slot_intent)
            object.__setattr__(self, "state", SubmissionState.DRAFT)
            object.__setattr__(self, "submission_id", None)
            object.__setattr__(self, "evidence", _FrozenEvidence({}))

        @classmethod
        def draft(
            cls, *, bundle_sha256: str, active_slot_intent: str
        ) -> SubmissionLifecycleRecord:
            return cls(
                bundle_sha256=bundle_sha256,
                active_slot_intent=active_slot_intent,
            )

    def advance_lifecycle(
        record: SubmissionLifecycleRecord,
        target: SubmissionState,
        evidence: Mapping[str, object],
    ) -> SubmissionLifecycleRecord:
        """Advance one externally evidenced lifecycle step, or fail closed."""
        if not isinstance(record, SubmissionLifecycleRecord):
            raise LifecycleError("record must be a SubmissionLifecycleRecord")
        if target not in _ALLOWED[record.state]:
            raise LifecycleError(
                f"invalid lifecycle transition: {record.state.value} -> {target.value}"
            )
        if not isinstance(evidence, Mapping):
            raise LifecycleError("evidence must be a mapping")
        _require_json_compatible(evidence)
        submission_id = _validate_evidence(record, target, evidence)
        duplicate_keys = set(record.evidence).intersection(evidence)
        if duplicate_keys:
            duplicate_key = sorted(duplicate_keys)[0]
            raise LifecycleError(f"evidence key already recorded: {duplicate_key}")
        transitioned = object.__new__(SubmissionLifecycleRecord)
        object.__setattr__(transitioned, "bundle_sha256", record.bundle_sha256)
        object.__setattr__(
            transitioned,
            "active_slot_intent",
            record.active_slot_intent,
        )
        object.__setattr__(transitioned, "state", target)
        object.__setattr__(
            transitioned,
            "submission_id",
            submission_id or record.submission_id,
        )
        object.__setattr__(
            transitioned,
            "evidence",
            _FrozenEvidence({**record.evidence, **dict(evidence)}),
        )
        return transitioned

    return SubmissionLifecycleRecord, advance_lifecycle


SubmissionLifecycleRecord, advance_lifecycle = _build_lifecycle_api()
del _build_lifecycle_api
