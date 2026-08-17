"""Canonical, hash-pinned identity for evaluation Candidate artifacts.

This is deliberately NOT a filesystem scanner: it does not pick "the
newest-looking file" in ``runs/``. Each entry here was fixed by reading the
actual evidence document and the actual export JSON's own declared fields
(see ``docs/evidence/offline-training-v1-long-run-20260718.md`` and
``runs/offline-training-v1/offline-long-run-actual-20260718-r1/export/
neural-student-v1.json`` in the sibling canonical worktree, read on
2026-07-21). The artifact bytes themselves are never embedded or copied
here -- only their identity. Callers must still supply the real file path
at runtime and this module's hashes are used to fail-closed verify it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

NOT_APPLICABLE = "NOT_APPLICABLE"


class O5CandidateRegistryError(ValueError):
    """Raised for an unknown or malformed candidate artifact identity lookup."""


@dataclass(frozen=True, slots=True)
class CandidateArtifactIdentity:
    candidate_artifact_id: str
    model_hash: str
    feature_schema_hash: str
    feature_schema_version: str
    dataset_artifact_id: str
    dataset_hash: str
    training_config_hash: str
    action_schema_version: str
    model_format_version: str
    source_commit: str

    def __post_init__(self) -> None:
        for name in (
            "candidate_artifact_id", "model_hash", "feature_schema_hash", "feature_schema_version",
            "dataset_artifact_id", "dataset_hash", "training_config_hash", "action_schema_version",
            "model_format_version", "source_commit",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise O5CandidateRegistryError(f"{name} must be a non-blank string")


# NEURAL_ACTUAL_TRAINED (the "offline-long-run-actual-20260718-r1" lineage).
# model_hash / feature_schema_hash / config_hash / dataset_hash / schema_version
# were read verbatim from the export document's own top-level fields.
# training_config_hash maps to the export document's "config_hash" field
# (the export format has no separate field named "training_config_hash").
# There is no "action schema" concept anywhere in this codebase's export or
# ActionKey contracts, so action_schema_version is fixed to NOT_APPLICABLE
# rather than invented.
NEURAL_ACTUAL_TRAINED = CandidateArtifactIdentity(
    candidate_artifact_id="neural_actual_trained",
    model_hash="94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4",
    feature_schema_hash="552d3bf4c4792d84fc509bfa51c322e23e84dd6c04697f0dab8dca80ea864484",
    feature_schema_version="student-v0-features-v1",
    dataset_artifact_id="offline-long-run-actual-20260718-r1",
    dataset_hash="a3ba4c1cd2903491d2e5e3489907ac8a4b179fba840cecbb3332a8a7b942ff60",
    training_config_hash="22e08ebebb9f59134ecb5e61330d7757e5a7ae9f5dc934449476445643a2bd78",
    action_schema_version=NOT_APPLICABLE,
    model_format_version="offline-training-v1-neural-export-v1",
    source_commit="062533feee8ac91914d10fd67231181f6ef7949e",
)

CANDIDATE_ARTIFACT_REGISTRY: Mapping[str, CandidateArtifactIdentity] = {
    "neural_actual_trained": NEURAL_ACTUAL_TRAINED,
}


def resolve_candidate_identity(candidate_artifact_id: str) -> CandidateArtifactIdentity:
    identity = CANDIDATE_ARTIFACT_REGISTRY.get(candidate_artifact_id)
    if identity is None:
        raise O5CandidateRegistryError(f"unknown candidate artifact id: {candidate_artifact_id!r}")
    return identity


__all__ = [
    "CANDIDATE_ARTIFACT_REGISTRY",
    "NOT_APPLICABLE",
    "CandidateArtifactIdentity",
    "NEURAL_ACTUAL_TRAINED",
    "O5CandidateRegistryError",
    "resolve_candidate_identity",
]
