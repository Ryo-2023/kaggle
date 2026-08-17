from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import run_resource_aware_tomato_policy_setup_priority_weighted_v1 as lane


def test_setup_priority_variants_are_bounded_and_distinct() -> None:
    variants = lane.build_setup_priority_variants()
    assert [item["candidate_id"] for item in variants] == [
        "setup-duraludon-first-v1",
        "setup-relicanth-first-v1",
    ]
    assert all(item["parameter_name"] == "_SETUP_ACTIVE_PRIORITY" for item in variants)
    assert all(item["policy_sha256"] != lane.TOMATO_PARENT_POLICY_SHA256 for item in variants)
    assert all(item["research_only"] and not item["training_authority"] for item in variants)
    assert all(set(item["priorities"]) == {"CINDERACE", "DURALUDON", "RELICANTH"} for item in variants)


def test_policy_copy_replaces_only_sealed_setup_priority_block(tmp_path: Path) -> None:
    destination = tmp_path / "main.py"
    priorities = {"CINDERACE": 20000, "DURALUDON": 100000, "RELICANTH": 5000}
    sha = lane.materialize_setup_priority_policy_copy(
        source=lane.TOMATO_PARENT_POLICY,
        destination=destination,
        priorities=priorities,
    )
    assert sha == hashlib.sha256(destination.read_bytes()).hexdigest()
    text = destination.read_text(encoding="utf-8")
    assert "CINDERACE: (20000" in text
    assert "DURALUDON: (100000" in text
    assert "RELICANTH: (5000" in text
    assert "CINDERACE: (100000" not in text
    assert "def agent(" in text


def test_policy_copy_rejects_unknown_or_out_of_range_priority(tmp_path: Path) -> None:
    with pytest.raises(lane.SetupPriorityError):
        lane.materialize_setup_priority_policy_copy(
            source=lane.TOMATO_PARENT_POLICY,
            destination=tmp_path / "unknown.py",
            priorities={"UNKNOWN": 1},
        )
    with pytest.raises(lane.SetupPriorityError):
        lane.materialize_setup_priority_policy_copy(
            source=lane.TOMATO_PARENT_POLICY,
            destination=tmp_path / "bad.py",
            priorities={"CINDERACE": 100001, "DURALUDON": 20000, "RELICANTH": 5000},
        )


def test_build_games_uses_local_metadata_rebinding() -> None:
    subset = lane.surface.load_meta_train_subset(lane.META_MANIFEST)
    games = lane._build_games(
        arm="parent",
        policy_path=lane.TOMATO_PARENT_POLICY,
        policy_sha=lane.TOMATO_PARENT_POLICY_SHA256,
        deck_path=lane.TOMATO_PARENT_DECK,
        deck_sha=lane.TOMATO_PARENT_DECK_SHA256,
        refs=tuple(str(item) for item in subset["selected_ids"]),
    )
    assert len(games) == 48
    assert all(game.metadata["comparison_arm"] == "parent" for game in games)


def test_common24_reference_ids_are_evaluation_only() -> None:
    refs, heldout = lane.build_common24_reference_ids()
    assert len(refs) == 24
    assert len(set(refs)) == 24
    assert len(heldout) == 4
    assert set(heldout).issubset(set(refs))
