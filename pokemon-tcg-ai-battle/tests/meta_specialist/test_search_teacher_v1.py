from __future__ import annotations

from mage_ptcg.meta_specialist.search_teacher_v1 import soft_search_target_v1
from mage_ptcg.meta_specialist.dagger_dataset_v1 import DAggerDatasetV1, DAggerRecordV1


def test_search_soft_target_is_normalized_and_confidence_bounded() -> None:
    target = soft_search_target_v1({"a": 1.0, "b": 0.0}, standard_errors={"a": 0.1, "b": 0.1}, current_policy={"a": 0.5, "b": 0.5})
    assert abs(sum(target.probabilities.values()) - 1.0) < 1e-8
    assert 0 <= target.confidence <= 1
    assert target.probabilities["a"] > target.probabilities["b"]


def test_dagger_dataset_deduplicates_state_policy_pairs() -> None:
    dataset = DAggerDatasetV1()
    record = DAggerRecordV1("a" * 64, "policy", {"a": 1.0}, 1.0, "entropy", "opp")
    assert dataset.add(record)
    assert not dataset.add(record)
    assert len(dataset) == 1
