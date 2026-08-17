"""The design's §9.3 duplicate cap must be in the weight the learner actually reads.

§9.3 requires rule/checkpoint demonstrations to be weighted by "action legality,
teacher confidence, 重複 cap, matchup cap", and requires that no one thing occupy
the dataset.  The matchup cap is applied at collection time.  A position's
multiplicity is only knowable once the whole corpus is sealed, so the duplicate
cap belongs here.

§9.3 also requires *every* valid teacher decision to remain a policy-target
candidate, so duplicates are down-weighted, never dropped.
"""

from __future__ import annotations

import copy

import pytest

from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    MAX_NEAR_DUPLICATE_MULTIPLICITY_V1,
    TrainingSnapshotV1Error,
    _apply_duplicate_cap_v1,
    _duplicate_scale_v1,
    validate_training_snapshot_v1,
)

from tests.meta_specialist.test_training_snapshot_v1 import _build


def _examples(multiplicities: dict[str, int]) -> list[dict[str, object]]:
    """One example per copy, tagged with a distinct episode so nothing else groups."""
    made: list[dict[str, object]] = []
    index = 0
    for near_duplicate, count in multiplicities.items():
        for _copy in range(count):
            made.append({
                "record_id": f"{index:064d}".replace("0", "a", 1),
                "episode_id_hash": f"{index:064d}",
                "near_duplicate_id": near_duplicate,
                "pre_cap_quality_weight": 1.0,
                "example_quality_weight": 1.0,
            })
            index += 1
    return made


def test_a_position_under_the_cap_keeps_its_full_weight() -> None:
    examples = _examples({"a" * 64: MAX_NEAR_DUPLICATE_MULTIPLICITY_V1})

    report = _apply_duplicate_cap_v1(examples)

    assert report["groups_capped"] == 0
    assert report["records_capped"] == 0
    assert all(item["example_quality_weight"] == 1.0 for item in examples)


def test_a_position_over_the_cap_is_down_weighted_but_never_dropped() -> None:
    copies = 150  # the opening decision's real multiplicity in a 300-game corpus
    examples = _examples({"a" * 64: copies})

    report = _apply_duplicate_cap_v1(examples)

    assert report["groups_capped"] == 1
    assert report["records_capped"] == copies
    assert len(examples) == copies, "§9.3 keeps every valid teacher decision"
    expected = MAX_NEAR_DUPLICATE_MULTIPLICITY_V1 / copies
    assert all(item["example_quality_weight"] == expected for item in examples)
    # Total influence is capped at the cap, not at zero and not at 150.
    total = sum(item["example_quality_weight"] for item in examples)
    assert total == pytest.approx(float(MAX_NEAR_DUPLICATE_MULTIPLICITY_V1))


def test_the_cap_does_not_touch_positions_that_occur_once() -> None:
    examples = _examples({"a" * 64: 150, "b" * 64: 1})

    _apply_duplicate_cap_v1(examples)

    rare = [item for item in examples if item["near_duplicate_id"] == "b" * 64]
    assert [item["example_quality_weight"] for item in rare] == [1.0]


def test_scale_is_monotone_and_bounded() -> None:
    assert _duplicate_scale_v1(1) == 1.0
    assert _duplicate_scale_v1(MAX_NEAR_DUPLICATE_MULTIPLICITY_V1) == 1.0
    over = _duplicate_scale_v1(MAX_NEAR_DUPLICATE_MULTIPLICITY_V1 + 1)
    assert 0.0 < over < 1.0
    assert _duplicate_scale_v1(1000) < over


def test_validation_rejects_a_weight_that_ignores_the_cap(tmp_path) -> None:
    """Fails if a snapshot can ship uncapped weights while declaring a cap."""
    snapshot, *_rest = _build(tmp_path, two=True)
    tampered = copy.deepcopy(snapshot)
    tampered["examples"][0]["example_quality_weight"] = 0.5
    tampered["snapshot_id"] = snapshot["snapshot_id"]

    with pytest.raises(TrainingSnapshotV1Error, match="duplicate cap"):
        validate_training_snapshot_v1(tampered)


def test_validation_rejects_a_declared_cap_that_does_not_describe_the_examples(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path, two=True)
    tampered = copy.deepcopy(snapshot)
    tampered["duplicate_cap"]["groups_capped"] = 99

    with pytest.raises(TrainingSnapshotV1Error, match="duplicate_cap"):
        validate_training_snapshot_v1(tampered)


def test_a_snapshot_cannot_exempt_a_real_leak_by_declaring_it_ubiquitous(tmp_path) -> None:
    """The ubiquitous set is re-derived, never read from the declared block."""
    snapshot, *_rest = _build(tmp_path, two=True)
    tampered = copy.deepcopy(snapshot)
    leaked = tampered["examples"][0]["near_duplicate_id"]
    tampered["examples"][1]["near_duplicate_id"] = leaked
    other = next(
        name for name in tampered["split_names"] if name != tampered["examples"][0]["split"]
    )
    tampered["examples"][1]["split"] = other
    # Claim the shared position is ubiquitous so the straddle check should skip it.
    tampered["duplicate_cap"]["ubiquitous_near_duplicate_ids"] = [leaked]

    with pytest.raises(TrainingSnapshotV1Error):
        validate_training_snapshot_v1(tampered)
