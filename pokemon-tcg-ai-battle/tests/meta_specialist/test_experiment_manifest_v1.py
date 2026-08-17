from __future__ import annotations

from mage_ptcg.meta_specialist.experiment_manifest_v1 import ExperimentManifestV1, promotion_gate_v1


def test_manifest_canonical_hash_and_gate_require_zero_faults() -> None:
    manifest = ExperimentManifestV1(
        representation_version=3, source_diff_sha256="a" * 64,
        checkpoint_sha256="b" * 64, dataset_manifest_sha256="c" * 64,
        critic_manifest_sha256="d" * 64,
    )
    assert len(manifest.manifest_sha256) == 64
    assert promotion_gate_v1(paired_delta=0.1, ci_lower=0.01, fault_rate=0.0, seat_delta=0.0, training_seed_consistency=True)
    assert not promotion_gate_v1(paired_delta=0.1, ci_lower=0.01, fault_rate=0.02, seat_delta=0.0, training_seed_consistency=True)
