"""Sealed experiment lineage and conservative promotion gate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentManifestV1:
    representation_version: int
    source_diff_sha256: str
    checkpoint_sha256: str
    dataset_manifest_sha256: str
    critic_manifest_sha256: str

    def __post_init__(self) -> None:
        if self.representation_version != 3:
            raise ValueError("promotion manifest requires representation_version=3")
        for name in ("source_diff_sha256", "checkpoint_sha256", "dataset_manifest_sha256", "critic_manifest_sha256"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{name} must be a lowercase sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "meta-specialist-experiment-manifest-v1",
            "representation_version": self.representation_version,
            "source_diff_sha256": self.source_diff_sha256,
            "checkpoint_sha256": self.checkpoint_sha256,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "critic_manifest_sha256": self.critic_manifest_sha256,
        }

    @property
    def manifest_sha256(self) -> str:
        return _sha256(self.to_dict())


def promotion_gate_v1(
    *, paired_delta: float, ci_lower: float, fault_rate: float, seat_delta: float,
    training_seed_consistency: bool, max_fault_rate: float = 0.01, max_seat_delta: float = 0.10,
) -> bool:
    return (
        paired_delta > 0 and ci_lower >= 0 and fault_rate <= max_fault_rate
        and abs(seat_delta) <= max_seat_delta and training_seed_consistency
    )


__all__ = ["ExperimentManifestV1", "promotion_gate_v1"]
