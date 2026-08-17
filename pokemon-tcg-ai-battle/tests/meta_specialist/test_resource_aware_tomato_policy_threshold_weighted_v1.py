from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import run_resource_aware_tomato_policy_threshold_weighted_v1 as lane


def test_threshold_variants_are_bounded_and_distinct() -> None:
    variants = lane.build_threshold_variants()
    assert [item["candidate_id"] for item in variants] == [
        "ice-threshold-lower-v1",
        "ice-threshold-higher-v1",
    ]
    assert all(item["parameter_name"] == "_ICE_CREAM_HP_THRESHOLD" for item in variants)
    assert all(item["policy_sha256"] != lane.TOMATO_PARENT_POLICY_SHA256 for item in variants)
    assert all(item["research_only"] and not item["training_authority"] for item in variants)


def test_policy_copy_replaces_only_sealed_threshold_block(tmp_path: Path) -> None:
    source = lane.TOMATO_PARENT_POLICY
    destination = tmp_path / "main.py"
    sha = lane.materialize_threshold_policy_copy(
        source=source,
        destination=destination,
        thresholds={"lucario": 260, "starmie": 200, "crustle": 120, "hop": 210, "generic": 220},
    )
    assert sha == hashlib.sha256(destination.read_bytes()).hexdigest()
    text = destination.read_text(encoding="utf-8")
    assert '"lucario": 260' in text
    assert '"starmie": 200' in text
    assert '"lucario": 270' not in text
    assert "def agent(" in text


def test_policy_copy_rejects_unknown_threshold_key(tmp_path: Path) -> None:
    with pytest.raises(lane.PolicyThresholdError):
        lane.materialize_threshold_policy_copy(
            source=lane.TOMATO_PARENT_POLICY,
            destination=tmp_path / "main.py",
            thresholds={"unknown": 1},
        )
