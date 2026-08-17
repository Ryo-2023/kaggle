"""Versioned C5 teacher registry with explicit capability gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


class TeacherCapabilityError(ValueError):
    """Raised when a teacher is requested without its documented capability."""


@dataclass(frozen=True, slots=True)
class TeacherEntry:
    teacher_id: str
    implementation_revision: str
    required_capabilities: tuple[str, ...]
    legal_action_contract: str
    privacy_contract: str
    deterministic: bool
    classification: str
    evidence_status: str
    actual_cabt_evaluation_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_teacher_registry(revision: str = "unknown") -> dict[str, TeacherEntry]:
    return {
        "rule-agent-v0": TeacherEntry("rule-agent-v0", revision, (), "c1-stable-actionkey-v1", "actor-view-v1", True, "runtime-and-offline", "implemented", "not-run"),
        "student-v0": TeacherEntry("student-v0", revision, ("student_model",), "c1-stable-actionkey-v1", "actor-view-v1", True, "offline-challenger", "fixture-only", "not-run"),
        "bounded-search-v0": TeacherEntry("bounded-search-v0", revision, ("public_engine_adapter",), "c1-stable-actionkey-v1", "actor-view-v1", True, "conditional-challenger", "fake-adapter-contract-only", "not-run"),
        "external-imported": TeacherEntry("external-imported", revision, ("external_teacher_contract",), "declared-by-import", "declared-by-import", False, "offline-only", "unverified", "not-run"),
    }


def require_teacher(teacher_id: str, capabilities: Iterable[str], *, registry: dict[str, TeacherEntry] | None = None) -> TeacherEntry:
    entries = default_teacher_registry() if registry is None else registry
    entry = entries.get(teacher_id)
    if entry is None:
        raise TeacherCapabilityError(f"unsupported teacher: {teacher_id}")
    missing = set(entry.required_capabilities) - set(capabilities)
    if missing:
        raise TeacherCapabilityError(f"teacher {teacher_id} unavailable; missing capabilities: {sorted(missing)}")
    return entry


__all__ = ["TeacherCapabilityError", "TeacherEntry", "default_teacher_registry", "require_teacher"]
