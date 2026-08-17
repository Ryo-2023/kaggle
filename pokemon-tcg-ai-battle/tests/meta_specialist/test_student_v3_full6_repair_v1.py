from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.student_v3_full6_repair_v1 import (
    Full6RepairError,
    build_full6_repair_manifest_v1,
    partition_full6_decisions_v1,
    repair_component_splits_v1,
    verify_full6_repair_manifest_v1,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _row(
    label: str,
    *,
    episode: str,
    near: str,
    split: str,
    ubiquitous: bool = False,
) -> dict[str, object]:
    return {
        "record_id": _sha(label),
        "episode_id": _sha(episode),
        "near_duplicate_id": _sha(near),
        "near_duplicate_ubiquitous": ubiquitous,
        "split": split,
    }


def test_component_repair_moves_the_whole_nonubiquitous_connected_component() -> None:
    rows = [
        _row("a1", episode="ep-a", near="cross", split="train"),
        _row("a2", episode="ep-a", near="local-a", split="train"),
        _row("b1", episode="ep-b", near="cross", split="validation"),
        _row("c1", episode="ep-c", near="local-c", split="test"),
    ]

    repaired, audit = repair_component_splits_v1(
        rows,
        ubiquitous_near_duplicate_ids=(),
        seed="full6-component-repair-test",
    )

    by_episode: dict[str, set[str]] = {}
    by_near: dict[str, set[str]] = {}
    for row in repaired:
        by_episode.setdefault(str(row["episode_id"]), set()).add(str(row["split"]))
        by_near.setdefault(str(row["near_duplicate_id"]), set()).add(str(row["split"]))
    assert all(len(splits) == 1 for splits in by_episode.values())
    assert all(len(splits) == 1 for splits in by_near.values())
    assert audit["source_non_ubiquitous_cross_count"] == 1
    assert audit["output_non_ubiquitous_cross_count"] == 0
    assert audit["output_episode_cross_count"] == 0
    assert audit["cross_component_count"] == 1
    assert audit["moved_record_count"] == 1
    assert audit["assignment_sha256"] == _sha(audit["assignment_canonical_json"])


def test_ubiquitous_near_duplicate_does_not_collapse_unrelated_episodes() -> None:
    ubiquitous = _sha("ubiquitous")
    rows = [
        _row("a", episode="ep-a", near="ubiquitous", split="train", ubiquitous=True),
        _row("b", episode="ep-b", near="ubiquitous", split="test", ubiquitous=True),
    ]

    repaired, audit = repair_component_splits_v1(
        rows,
        ubiquitous_near_duplicate_ids=(ubiquitous,),
        seed="seed",
    )

    assert {row["record_id"]: row["split"] for row in repaired} == {
        _sha("a"): "train",
        _sha("b"): "test",
    }
    assert audit["source_non_ubiquitous_cross_count"] == 0
    assert audit["output_non_ubiquitous_cross_count"] == 0
    assert audit["declared_ubiquitous_cross_count"] == 1


def test_component_repair_is_deterministic_and_does_not_mutate_input() -> None:
    rows = [
        _row("a", episode="ep-a", near="cross", split="train"),
        _row("b", episode="ep-b", near="cross", split="validation"),
    ]
    before = copy.deepcopy(rows)
    first = repair_component_splits_v1(
        rows, ubiquitous_near_duplicate_ids=(), seed="stable-seed"
    )
    second = repair_component_splits_v1(
        list(reversed(rows)),
        ubiquitous_near_duplicate_ids=(),
        seed="stable-seed",
    )
    assert rows == before
    assert first[0] == second[0]
    assert first[1]["assignment_sha256"] == second[1]["assignment_sha256"]


def test_only_ordered_pointer_head_gap_may_enter_explicit_quarantine() -> None:
    decisions = [
        {"record_id": _sha("supported"), "status": "SUPPORTED_SET", "reason": None},
        {
            "record_id": _sha("ordered"),
            "status": "UNSUPPORTED",
            "reason": "ordered_selection_requires_pointer_head",
            "selection_schema": "5:34",
            "target_action_digests": [_sha("first"), _sha("second")],
        },
    ]
    supported, quarantine = partition_full6_decisions_v1(decisions)
    assert [row["record_id"] for row in supported] == [_sha("supported")]
    assert quarantine == [decisions[1]]

    bad = decisions + [
        {
            "record_id": _sha("bad"),
            "status": "UNSUPPORTED",
            "reason": "target_action_alias_collision",
        }
    ]
    with pytest.raises(Full6RepairError, match="non-ordered unsupported"):
        partition_full6_decisions_v1(bad)


def test_actual_full6_lightweight_descriptor_stays_blocked_and_separates_tomato(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "manifest.json"
    manifest = build_full6_repair_manifest_v1(
        repo_root=root,
        blocked_bridge_manifest_path=(
            root
            / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-full6/bridge-manifest.json"
        ),
        tomato_bridge_manifest_path=(
            root
            / "runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json"
        ),
        output_manifest_path=output,
        reproduce_primary=False,
    )
    assert verify_full6_repair_manifest_v1(
        output, root, reproduce_primary=False
    ) == manifest
    assert manifest["performance_training_ready"] is False
    assert manifest["blocked_reasons"] == [
        "component_split_assignment_unmaterialized",
        "ordered_pointer_head_quarantine_unmaterialized",
        "primary_reproduction_incomplete",
    ]
    assert manifest["derivation"]["ordered_pointer_head_quarantine"] == {
        "by_schema": {"5:34": 4},
        "count": 4,
        "record_ids": None,
        "silent_drop": False,
        "status": "BLOCKED_IDENTITIES_NOT_MATERIALIZED",
        "target_sequences": None,
    }
    assert manifest["derivation"]["component_split_repair"][
        "source_non_ubiquitous_cross_ids"
    ] == ["5a996ab25264020f3a776c00489771e41b1bfbd2a0cff63eb0c907a8953e80ed"]
    assert manifest["tomato_clean_lane"][
        "input_manifest_declares_performance_training_ready"
    ] is True
    assert manifest["materialization"]["published_rows"] == 0
    with pytest.raises(Full6RepairError, match="primary reproduction incomplete"):
        verify_full6_repair_manifest_v1(output, root, reproduce_primary=True)
