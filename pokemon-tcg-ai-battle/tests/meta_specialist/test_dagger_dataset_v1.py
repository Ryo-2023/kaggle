from __future__ import annotations

from mage_ptcg.meta_specialist.dagger_dataset_v1 import DAggerDatasetV1, DAggerRecordV1


def test_dagger_dataset_deduplicates_state_and_policy_version() -> None:
    dataset = DAggerDatasetV1()
    record = DAggerRecordV1("a" * 64, "policy-v1", {"play": 1.0}, 1.0, "high_entropy", "opp-v1")
    assert dataset.add(record)
    assert not dataset.add(record)
    assert len(dataset) == 1
    assert dataset.records() == (record,)
