"""Integration recovery classifications and test-only hook contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol


class IntegrationRecoveryClass(StrEnum):
    """Conservative classifications for a root after an integration crash."""

    UNAPPLIED = "UNAPPLIED"
    FULLY_APPLIED = "FULLY_APPLIED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    SOURCE_CHANGED_EXTERNALLY = "SOURCE_CHANGED_EXTERNALLY"
    UNKNOWN = "UNKNOWN"


class IntegrationHookPoint(StrEnum):
    """Auditable integration barriers available only through constructor injection."""

    AFTER_APPROVAL_EVENT_DURABLE = "AFTER_APPROVAL_EVENT_DURABLE"
    AFTER_PATCH_TARGET_VERIFIED_BEFORE_APPLIED_EVENT = (
        "AFTER_PATCH_TARGET_VERIFIED_BEFORE_APPLIED_EVENT"
    )


class IntegrationFaultController(Protocol):
    """Local test controller; TaskContracts, providers, env, and CLI cannot construct it."""

    def reached(self, point: IntegrationHookPoint, evidence: dict[str, object]) -> None:
        """Observe a durable boundary and optionally interrupt the local test process."""


@dataclass(frozen=True)
class IntegrationClassification:
    """Serializable classification evidence used for fail-closed recovery."""

    classification: IntegrationRecoveryClass
    current_allowed_digest: str | None
    source_allowed_digest: str | None
    target_allowed_digest: str | None
    current_workspace_digest: str | None
    source_workspace_digest: str | None
    target_workspace_digest: str | None
    current_head: str | None
    expected_head: str | None
    current_index_digest: str | None
    expected_index_digest: str | None
    current_non_allowed_digest: str | None
    expected_non_allowed_digest: str | None
    patch_sha256: str | None
    approval_subject_digest: str | None
    integration_apply_started: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "current_allowed_digest": self.current_allowed_digest,
            "source_allowed_digest": self.source_allowed_digest,
            "target_allowed_digest": self.target_allowed_digest,
            "current_workspace_digest": self.current_workspace_digest,
            "source_workspace_digest": self.source_workspace_digest,
            "target_workspace_digest": self.target_workspace_digest,
            "current_head": self.current_head,
            "expected_head": self.expected_head,
            "current_index_digest": self.current_index_digest,
            "expected_index_digest": self.expected_index_digest,
            "current_non_allowed_digest": self.current_non_allowed_digest,
            "expected_non_allowed_digest": self.expected_non_allowed_digest,
            "patch_sha256": self.patch_sha256,
            "approval_subject_digest": self.approval_subject_digest,
            "integration_apply_started": self.integration_apply_started,
            "reason": self.reason,
        }


def hook_evidence(
    *, run_id: str, patch_sha256: str, classification: Mapping[str, Any]
) -> dict[str, object]:
    """Return a non-sensitive immutable context for a test-only hook."""

    return {
        "run_id": run_id,
        "patch_sha256": patch_sha256,
        "classification": str(classification.get("classification")),
        "current_allowed_digest": classification.get("current_allowed_digest"),
        "current_workspace_digest": classification.get("current_workspace_digest"),
    }
